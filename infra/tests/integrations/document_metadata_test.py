#!/usr/bin/env python3
"""Behavioral contracts for the document metadata validator (docs:meta:check / docs:meta:next-id).

The schema and category list come from the bookchaowalit-empire checkout
(BOOKCHAOWALIT_EMPIRE_ROOT, else ~/bookchaowalit-empire); the tests skip when it is absent.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[3] / 'infra/scripts/sync/document_metadata.py'
EMPIRE = Path(os.environ.get('BOOKCHAOWALIT_EMPIRE_ROOT') or Path.home() / 'bookchaowalit-empire')
SCHEMAS = EMPIRE / 'registry/schemas'


def front_matter(**changes) -> dict:
    value = {'schema': 'bookchaowalit.document-metadata.v1', 'id': 'DOC-0001', 'title': 'Plan',
             'type': 'plan', 'categories': ['business', 'ai'], 'status': 'draft', 'owner': 'bookchaowalit',
             'classification': 'internal', 'created': '2026-10-15', 'updated': '2026-10-15'}
    value.update(changes)
    return {k: v for k, v in value.items() if v is not None}


def yaml_text(value: dict) -> str:
    # Dates are written unquoted on purpose: that is how people write them, and they must stay strings.
    lines = []
    for key, item in value.items():
        lines.append(f'{key}: {json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item}')
    return '\n'.join(lines) + '\n'


def markdown(value: dict, body: str = '# Body\n') -> str:
    return '---\n' + yaml_text(value) + '---\n\n' + body


@unittest.skipUnless((SCHEMAS / 'document-metadata.v1.schema.json').exists(), f'empire schemas not found at {SCHEMAS}')
class DocumentMetadataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('document_metadata', SCRIPT)
            cls.module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.module
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'The document metadata validator is not implemented')
        self.m = self.module
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()

    def write(self, relative: str, text: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def run_main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.m.main(list(argv))
        return code, out.getvalue()

    def check(self, visibility: str = 'private') -> tuple[int, dict]:
        code, out = self.run_main('check', str(self.repo), '--empire', str(EMPIRE),
                                  '--visibility', visibility, '--json')
        return code, json.loads(out)

    def codes(self, report: dict) -> list[tuple[str, str]]:
        return sorted((e['path'], e['code']) for e in report['errors'])

    def test_valid_front_matter_and_sidecar_pass_with_unquoted_dates(self):
        self.write('docs/plan.md', markdown(front_matter(version='"1.0"', language='en', tags=['x'],
                                                          **{'x-review': 'weekly'})))
        self.write('data/model.xlsx', 'binary')
        self.write('data/model.xlsx.meta.yaml', yaml_text(front_matter(
            id='DOC-0002', type='dataset', categories=['finance'], file='model.xlsx')))
        self.write('data/big.mp4.meta.yaml', yaml_text(front_matter(
            id='DOC-0003', type='media', categories=['design'], file='s3://bucket/big.mp4')))
        code, report = self.check()
        self.assertEqual((code, report['errors']), (0, []))
        self.assertEqual(report['documents'], 3)

    def test_files_without_or_with_foreign_front_matter_are_skipped(self):
        self.write('README.md', '# No front matter\n')
        self.write('notes/obsidian.md', '---\ntitle: Old note\ncreated: 2026-08-11\nstatus: current\n---\nBody\n')
        self.write('notes/other.md', '---\nschema: solo-empire.service.v1\nid: x\n---\n')
        self.write('notes/unterminated.md', '---\ntitle: never closed\n')
        code, report = self.check()
        self.assertEqual((code, report['errors'], report['documents']), (0, [], 0))
        self.assertEqual(report['skipped'], 4)

    def test_schema_violations_are_reported_per_file(self):
        self.write('a.md', markdown(front_matter(title=None)))
        self.write('b.md', markdown(front_matter(id='DOC-0002', created='2026-13-45')))
        self.write('c.md', markdown(front_matter(id='DOC-0003', unknown='x')))
        self.write('d.md', markdown(front_matter(id='DOC-0004', superseded_by='DOC-0001')))
        self.write('e.md', markdown(front_matter(id='DOC-0005', schema='bookchaowalit.document-metadata.v9')))
        self.write('f.md', '---\nschema: bookchaowalit.document-metadata.v1\nid: [unclosed\n---\n')
        code, report = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(self.codes(report), [('a.md', 'schema'), ('b.md', 'schema'), ('c.md', 'schema'),
                                              ('d.md', 'schema'), ('e.md', 'schema'), ('f.md', 'invalid_yaml')])

    def test_categories_must_come_from_the_registry_list(self):
        self.write('a.md', markdown(front_matter(categories=['business', 'astrology'])))
        code, report = self.check()
        self.assertEqual((code, self.codes(report)), (1, [('a.md', 'unknown_category')]))
        self.assertIn('astrology', report['errors'][0]['message'])

    def test_ids_must_be_unique_within_the_repository(self):
        self.write('a.md', markdown(front_matter()))
        self.write('sub/b.md', markdown(front_matter(title='Other')))
        self.write('c.pdf', 'pdf')
        self.write('c.pdf.meta.yaml', yaml_text(front_matter(file='c.pdf')))
        code, report = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(self.codes(report), [('c.pdf.meta.yaml', 'duplicate_id'), ('sub/b.md', 'duplicate_id')])

    def test_classification_may_not_exceed_repository_visibility(self):
        self.write('a.md', markdown(front_matter(classification='confidential')))
        self.write('b.md', markdown(front_matter(id='DOC-0002', classification='restricted')))
        self.write('c.md', markdown(front_matter(id='DOC-0003', classification='internal')))
        self.write('d.md', markdown(front_matter(id='DOC-0004', classification='public')))
        code, report = self.check('public')
        self.assertEqual((code, self.codes(report)), (1, [('a.md', 'classification_exceeds_visibility'),
                                                          ('b.md', 'classification_exceeds_visibility')]))
        for visibility in ('private', 'internal'):
            self.assertEqual(self.check(visibility)[0], 0)

    def test_sidecar_relative_file_must_exist_inside_the_repository(self):
        (Path(self.temp.name) / 'outside.xlsx').write_text('x')
        self.write('real.xlsx', 'x')
        (self.repo / 'link.xlsx').symlink_to(self.repo / 'real.xlsx')
        self.write('a.meta.yaml', yaml_text(front_matter(file='missing.xlsx')))
        self.write('b.meta.yaml', yaml_text(front_matter(id='DOC-0002', file='../outside.xlsx')))
        self.write('c.meta.yaml', yaml_text(front_matter(id='DOC-0003', file='link.xlsx')))
        self.write('d.meta.yaml', yaml_text(front_matter(id='DOC-0004', file='/etc/hostname')))
        self.write('e.meta.yaml', '- not\n- a mapping\n')
        code, report = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(self.codes(report), [('a.meta.yaml', 'sidecar_file_missing'),
                                              ('b.meta.yaml', 'sidecar_file_unsafe'),
                                              ('c.meta.yaml', 'sidecar_file_unsafe'),
                                              ('d.meta.yaml', 'sidecar_file_unsafe'),
                                              ('e.meta.yaml', 'not_a_mapping')])

    def test_symlinked_files_and_directories_are_skipped(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'bad.md').write_text(markdown(front_matter(categories=['astrology'])))
        (outside / 'bad.meta.yaml').write_text('id: nope\n')
        (self.repo / 'linked-dir').symlink_to(outside, target_is_directory=True)
        (self.repo / 'linked.md').symlink_to(outside / 'bad.md')
        (self.repo / 'linked.meta.yaml').symlink_to(outside / 'bad.meta.yaml')
        code, report = self.check()
        self.assertEqual((code, report['errors'], report['documents']), (0, [], 0))

    def test_git_repositories_respect_gitignore(self):
        subprocess.run(['git', 'init', '--quiet', str(self.repo)], check=True)
        self.write('.gitignore', 'build/\n')
        self.write('a.md', markdown(front_matter()))
        self.write('build/a.md', markdown(front_matter()))
        code, report = self.check()
        self.assertEqual((code, report['errors'], report['documents']), (0, [], 1))

    def test_visibility_is_read_from_github_when_not_overridden(self):
        self.assertEqual(self.m.repository_slug('https://github.com/o/r.git'), 'o/r')
        self.assertEqual(self.m.repository_slug('git@github.com:o/.github.git'), 'o/.github')
        self.assertEqual(self.m.repository_slug('ssh://git@github.com/o/r'), 'o/r')
        self.assertIsNone(self.m.repository_slug('https://gitlab.com/o/r.git'))
        self.write('a.md', markdown(front_matter(classification='restricted')))
        with patch.object(self.m, 'origin_slug', return_value='o/r'), \
                patch.object(self.m, 'github_json', return_value={'visibility': 'public'}) as api:
            code, out = self.run_main('check', str(self.repo), '--empire', str(EMPIRE), '--json')
        api.assert_called_once_with('repos/o/r')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)['visibility'], 'public')

    def test_unknown_visibility_or_missing_registry_blocks(self):
        self.write('a.md', markdown(front_matter()))
        with patch.object(self.m, 'origin_slug', return_value=None):
            code, out = self.run_main('check', str(self.repo), '--empire', str(EMPIRE), '--json')
        self.assertEqual((code, json.loads(out)['code']), (2, 'visibility_unknown'))
        with patch.object(self.m, 'origin_slug', return_value='o/r'), \
                patch.object(self.m, 'github_json', side_effect=self.m.MetadataError('github_read_failed')):
            code, out = self.run_main('check', str(self.repo), '--empire', str(EMPIRE), '--json')
        self.assertEqual((code, json.loads(out)['code']), (2, 'github_read_failed'))
        code, out = self.run_main('check', str(self.repo), '--empire', self.temp.name,
                                  '--visibility', 'private', '--json')
        self.assertEqual((code, json.loads(out)['code']), (2, 'registry_missing'))
        code, out = self.run_main('check', str(self.repo / 'nope'), '--empire', str(EMPIRE),
                                  '--visibility', 'private', '--json')
        self.assertEqual((code, json.loads(out)['code']), (2, 'repository_missing'))

    def test_human_output_lists_each_error(self):
        self.write('a.md', markdown(front_matter(categories=['astrology'])))
        code, out = self.run_main('check', str(self.repo), '--empire', str(EMPIRE), '--visibility', 'private')
        self.assertEqual(code, 1)
        self.assertIn('a.md: unknown_category', out)

    def test_next_id_follows_the_highest_existing_id(self):
        self.assertEqual(self.run_main('next-id', str(self.repo)), (0, 'DOC-0001\n'))
        self.write('a.md', markdown(front_matter(id='DOC-0007')))
        self.write('b.meta.yaml', yaml_text(front_matter(id='DOC-0002', file='s3://b/x')))
        self.write('c.md', '---\ntitle: foreign\nid: DOC-9999\n---\n')
        self.assertEqual(self.run_main('next-id', str(self.repo)), (0, 'DOC-0008\n'))
        self.write('d.md', markdown(front_matter(id='DOC-12345')))
        self.assertEqual(self.run_main('next-id', str(self.repo)), (0, 'DOC-12346\n'))


if __name__ == '__main__':
    unittest.main()
