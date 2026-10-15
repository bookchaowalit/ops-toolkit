#!/usr/bin/env python3
"""Validate bookchaowalit.document-metadata.v1 records in one repository, or print the next free id.

check <repo>    Markdown front matter whose `schema` is bookchaowalit.document-metadata.* and every
                <file>.meta.yaml sidecar: JSON Schema, category list, unique ids, classification
                within the repository visibility, and sidecar `file` targets. Exit 1 on violations,
                2 when the check cannot run (missing registry, unknown visibility, GitHub unreadable).
next-id <repo>  Print DOC-NNNN after the highest id already used in the repository.

Only front matter and sidecars are read. Symlinked files and directories are skipped, and Git
repositories are listed with `git ls-files` so ignored paths are not checked.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess


SCHEMA = 'bookchaowalit.document-metadata-check.v1'
SCHEMA_PREFIX = 'bookchaowalit.document-metadata.'
SCHEMA_FILE = 'registry/schemas/document-metadata.v1.schema.json'
CATEGORIES_FILE = 'registry/schemas/document-categories.v1.json'
SIDECAR_SUFFIX = '.meta.yaml'
SKIP_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__'}
SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')
REPOSITORY_SLUG = re.compile(r'\.?[A-Za-z0-9][A-Za-z0-9_.-]*\Z')
GITHUB_REMOTE = re.compile(r'(?:https://(?:[^@/]+@)?|ssh://git@|git@)github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?\Z')
OWN_SCHEMA_LINE = re.compile(r'^schema:\s*["\']?' + re.escape(SCHEMA_PREFIX), re.MULTILINE)
DOC_ID = re.compile(r'DOC-([0-9]{4,})\Z')
# Highest classification a repository of each visibility may hold.
CLASSIFICATION_RANK = {'public': 0, 'internal': 1, 'confidential': 2, 'restricted': 3}
VISIBILITY_CEILING = {'public': 'internal', 'internal': 'restricted', 'private': 'restricted'}
FRONT_MATTER_LIMIT = 1 << 16


class MetadataError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _yaml_loader():
    import yaml

    class Loader(yaml.SafeLoader):
        """SafeLoader that keeps ISO dates as strings, as the JSON Schema expects."""

    Loader.yaml_implicit_resolvers = {
        key: [(tag, regexp) for tag, regexp in resolvers if tag != 'tag:yaml.org,2002:timestamp']
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    return yaml, Loader


def _command(args: list[str], code: str) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60,
                              env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GH_PROMPT_DISABLED': '1'})
    except (OSError, subprocess.SubprocessError) as exc:
        raise MetadataError(code) from exc
    if proc.returncode:
        raise MetadataError(code)
    return proc.stdout


def github_json(endpoint: str) -> dict:
    try:
        result = json.loads(_command(['gh', 'api', endpoint], 'github_read_failed'))
    except ValueError as exc:
        raise MetadataError('invalid_github_json') from exc
    if not isinstance(result, dict):
        raise MetadataError('invalid_github_json')
    return result


def repository_slug(remote: str) -> str | None:
    match = GITHUB_REMOTE.fullmatch(remote.strip())
    if not match or not SLUG.fullmatch(match[1]) or not REPOSITORY_SLUG.fullmatch(match[2]):
        return None
    return f'{match[1]}/{match[2]}'


def origin_slug(repo: Path) -> str | None:
    try:
        return repository_slug(_command(['git', '-C', str(repo), 'remote', 'get-url', 'origin'], 'no_origin'))
    except MetadataError:
        return None


def repository_visibility(repo: Path) -> str:
    slug = origin_slug(repo)
    if slug is None:
        raise MetadataError('visibility_unknown')
    visibility = github_json(f'repos/{slug}').get('visibility')
    if visibility not in VISIBILITY_CEILING:
        raise MetadataError('visibility_unknown')
    return visibility


def _symlinked(repo: Path, relative: PurePosixPath) -> bool:
    path = repo
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            return True
    return False


def candidate_files(repo: Path) -> tuple[list[PurePosixPath], int]:
    """Markdown files and sidecars under repo, sorted, without symlinks. Returns (files, symlinks skipped)."""
    if (repo / '.git').exists():
        listed = _command(['git', '-C', str(repo), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                          'git_command_failed')
        names = sorted({name for name in listed.split('\0') if name})
    else:
        names = []
        for directory, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            names += [Path(directory, f).relative_to(repo).as_posix() for f in files]
        names.sort()
    result, symlinks = [], 0
    for name in names:
        relative = PurePosixPath(name)
        if not (name.endswith('.md') or name.endswith(SIDECAR_SUFFIX)) or '..' in relative.parts:
            continue
        if _symlinked(repo, relative):
            symlinks += 1
        elif (repo / relative).is_file():
            result.append(relative)
    return result, symlinks


def front_matter_text(path: Path) -> str | None:
    """The YAML between a leading --- line and the next --- or ... line, or None."""
    lines = []
    with path.open(encoding='utf-8', errors='replace') as handle:
        if handle.readline().rstrip('\r\n') != '---':
            return None
        size = 0
        for line in handle:
            if line.rstrip('\r\n') in ('---', '...'):
                return ''.join(lines)
            size += len(line)
            if size > FRONT_MATTER_LIMIT:
                return None
            lines.append(line)
    return None


def load_record(repo: Path, relative: PurePosixPath) -> tuple[object, str | None]:
    """(record, error code) for a file this standard covers, or (None, None) to skip it."""
    yaml, loader = _yaml_loader()
    path = repo / relative
    if relative.name.endswith(SIDECAR_SUFFIX):
        text = path.read_text(encoding='utf-8', errors='replace')
        try:
            value = yaml.load(text, Loader=loader)
        except yaml.YAMLError:
            return None, 'invalid_yaml'
        return (value, None) if isinstance(value, dict) else (None, 'not_a_mapping')
    text = front_matter_text(path)
    if text is None:
        return None, None
    try:
        value = yaml.load(text, Loader=loader)
    except yaml.YAMLError:
        return None, 'invalid_yaml' if OWN_SCHEMA_LINE.search(text) else None
    if isinstance(value, dict) and isinstance(value.get('schema'), str) and value['schema'].startswith(SCHEMA_PREFIX):
        return value, None
    return None, None


def load_registry(empire: Path) -> tuple[dict, set[str]]:
    try:
        schema = json.loads((empire / SCHEMA_FILE).read_text(encoding='utf-8'))
        categories = json.loads((empire / CATEGORIES_FILE).read_text(encoding='utf-8'))['categories']
    except FileNotFoundError as exc:
        raise MetadataError('registry_missing') from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MetadataError('registry_invalid') from exc
    if not isinstance(schema, dict) or not isinstance(categories, list) \
            or not all(isinstance(c, str) for c in categories):
        raise MetadataError('registry_invalid')
    return schema, set(categories)


def _sidecar_file_error(repo: Path, sidecar: PurePosixPath, target: object) -> tuple[str, str] | None:
    if not isinstance(target, str) or '://' in target:
        return None
    joined = PurePosixPath(os.path.normpath((sidecar.parent / target).as_posix()))
    if PurePosixPath(target).is_absolute() or joined.parts[:1] == ('..',) or _symlinked(repo, joined):
        return 'sidecar_file_unsafe', f'file {target!r} must be a regular path inside the repository'
    if not (repo / joined).is_file():
        return 'sidecar_file_missing', f'file {target!r} does not exist'
    return None


def check_repository(repo: Path, schema: dict, categories: set[str], visibility: str) -> dict:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise MetadataError('jsonschema_missing') from exc
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    ceiling = CLASSIFICATION_RANK[VISIBILITY_CEILING[visibility]]
    files, symlinks = candidate_files(repo)
    errors, seen, documents, skipped = [], {}, 0, 0

    def error(path: PurePosixPath, code: str, message: str) -> None:
        errors.append({'path': path.as_posix(), 'code': code, 'message': message})

    for relative in files:
        record, code = load_record(repo, relative)
        if code:
            error(relative, code, 'metadata is not a readable YAML mapping')
            continue
        if record is None:
            skipped += 1
            continue
        documents += 1
        problems = sorted(f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
                          for e in validator.iter_errors(record))
        if problems:
            error(relative, 'schema', '; '.join(problems))
        listed = record.get('categories')
        unknown = sorted({c for c in listed if isinstance(c, str)} - categories) if isinstance(listed, list) else []
        if unknown:
            error(relative, 'unknown_category', 'not in the category list: ' + ', '.join(unknown))
        rank = CLASSIFICATION_RANK.get(record.get('classification'))
        if rank is not None and rank > ceiling:
            error(relative, 'classification_exceeds_visibility',
                  f"{record['classification']} is not allowed in a {visibility} repository")
        doc_id = record.get('id')
        if isinstance(doc_id, str):
            if doc_id in seen:
                error(relative, 'duplicate_id', f'{doc_id} is already used by {seen[doc_id]}')
            else:
                seen[doc_id] = relative.as_posix()
        if relative.name.endswith(SIDECAR_SUFFIX):
            problem = _sidecar_file_error(repo, relative, record.get('file'))
            if problem:
                error(relative, *problem)
    return {'schema': SCHEMA, 'status': 'failed' if errors else 'ok', 'repository': str(repo),
            'visibility': visibility, 'documents': documents, 'skipped': skipped,
            'symlinks_skipped': symlinks, 'errors': errors}


def next_id(repo: Path) -> str:
    highest = 0
    for relative in candidate_files(repo)[0]:
        record, _ = load_record(repo, relative)
        match = DOC_ID.fullmatch(record.get('id', '')) if isinstance(record, dict) \
            and isinstance(record.get('id'), str) else None
        if match:
            highest = max(highest, int(match[1]))
    return f'DOC-{highest + 1:04d}'


def _repository(value: str) -> Path:
    repo = Path(value).expanduser().resolve()
    if not repo.is_dir():
        raise MetadataError('repository_missing')
    return repo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='validate document metadata in a repository')
    check.add_argument('repo')
    check.add_argument('--empire', type=Path,
                       default=Path(os.environ.get('BOOKCHAOWALIT_EMPIRE_ROOT') or Path.home() / 'bookchaowalit-empire'),
                       help='bookchaowalit-empire checkout holding registry/schemas (default: $BOOKCHAOWALIT_EMPIRE_ROOT '
                            'or ~/bookchaowalit-empire)')
    check.add_argument('--visibility', choices=sorted(VISIBILITY_CEILING),
                       help='repository visibility; skips the GitHub lookup (offline use and tests)')
    check.add_argument('--json', action='store_true', help='print the report as JSON')
    nxt = commands.add_parser('next-id', help='print the next free DOC id in a repository')
    nxt.add_argument('repo')
    args = parser.parse_args(argv)
    try:
        repo = _repository(args.repo)
        if args.command == 'next-id':
            print(next_id(repo))
            return 0
        schema, categories = load_registry(args.empire.expanduser())
        visibility = args.visibility or repository_visibility(repo)
        report = check_repository(repo, schema, categories, visibility)
    except (MetadataError, OSError) as exc:
        code = exc.code if isinstance(exc, MetadataError) else 'invalid_input_or_environment'
        if getattr(args, 'json', False):
            print(json.dumps({'schema': SCHEMA, 'status': 'blocked', 'code': code}))
        else:
            print(f'blocked: {code}')
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for item in report['errors']:
            print(f"{item['path']}: {item['code']}: {item['message']}")
        print(f"{report['status']}: {report['documents']} documents, {len(report['errors'])} errors, "
              f"{report['skipped']} without document metadata, {report['symlinks_skipped']} symlinks skipped "
              f"({report['visibility']} repository)")
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
