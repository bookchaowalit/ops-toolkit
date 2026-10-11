#!/usr/bin/env python3
"""Offline tests for the universal step visualization contract."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import subprocess
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "systems/architecture", "solo-architecture checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))
sys.path.insert(0, str(ROOT / "infra" / "tests" / "support"))

from sqlite_cell_store import apply_legacy_portfolio_tables, sqlite_cell_opener  # noqa: E402  (isolates the cell APIs)

from universal_step_contract_guard import (  # noqa: E402
    CONTRACT_PATH,
    EXPECTED_DIMENSIONS,
    EXPECTED_FIELDS,
    load_yaml,
    validate_step_contract,
)


def main() -> int:
    from universal_step_record import (definitions, learning_step_definitions, operating_cycle_definitions,
                                       venture_step_definitions, venture_role_definitions,
                                       venture_role_package_definitions, role_definitions, validate_definitions,
                                       canonical, evaluate, resolve_evidence, save, latest, project,
                                       role_set_for_scope, validate_scope)
    import universal_step_record as step_record
    from universal_step_contract_guard import EXPECTED_COVERAGE_SURFACES, validate_coverage_inventory

    original_root = step_record.PROJECT_ROOT
    original_role_matrix = step_record.ROLE_MATRIX
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "matrix.yaml").write_text('marker: "Ё"\n', encoding="utf-8")
        step_record.PROJECT_ROOT = root
        step_record.ROLE_MATRIX = "matrix.yaml"
        role_definitions.cache_clear()
        try:
            assert role_definitions() == {"marker": "Ё"}
        finally:
            step_record.PROJECT_ROOT = original_root
            step_record.ROLE_MATRIX = original_role_matrix
            role_definitions.cache_clear()

    registry = definitions()
    matrix = role_definitions()
    assert not validate_definitions(registry)
    cycle_registry = operating_cycle_definitions()
    assert cycle_registry['ordered_steps'] == ['plan', 'start', 'execute', 'inspect', 'review']
    assert all(set(cycle_registry['step_definitions'][step]) >= {'title', *EXPECTED_FIELDS - {'step_id', 'scope', 'status', 'quality', 'evidence_refs', 'owner'}} for step in cycle_registry['ordered_steps'])
    assert all(not any(str(item).startswith('required-signal:') for item in cycle_registry['step_definitions'][step]['required_evidence']) for step in cycle_registry['ordered_steps'])
    learning_registry = learning_step_definitions()
    assert learning_registry['ordered_steps'] == ['orient', 'learn', 'practice', 'evidence', 'operate']
    assert all(set(learning_registry['step_definitions'][step]) >= {'title', *EXPECTED_FIELDS - {'step_id', 'scope', 'status', 'quality', 'evidence_refs', 'owner'}} for step in learning_registry['ordered_steps'])
    assert all(not any(str(item).startswith('required-signal:') for item in learning_registry['step_definitions'][step]['required_evidence']) for step in learning_registry['ordered_steps'])
    venture_registry = venture_step_definitions()
    venture_roles = venture_role_definitions()
    assert venture_registry['ordered_steps'] == ['frame', 'validate', 'design', 'build', 'launch', 'operate']
    assert all(set(venture_registry['step_definitions'][step]) >= {'title', *EXPECTED_FIELDS - {'step_id', 'scope', 'status', 'quality', 'evidence_refs', 'owner'}} for step in venture_registry['ordered_steps'])
    assert all(not any(str(item).startswith('required-signal:') for item in venture_registry['step_definitions'][step]['required_evidence']) for step in venture_registry['ordered_steps'])
    assert venture_roles['contract'] == 'solo-empire.venture-role-step.v1'
    assert venture_roles['selection_policy']['default_profile_id'] == 'venture-core'
    assert set(venture_roles['profiles']['venture-core']['packs']) == set(venture_registry['ordered_steps'])
    assert sum(len(pack['work_packages']) for pack in venture_roles['profiles']['venture-core']['packs'].values()) == 71
    assert all(set(package) >= {'applicability', 'purpose', 'actions', 'decisions', 'outputs', 'completion_rule', 'quality_gate', 'required_evidence', 'dependencies', 'risks', 'rollback_target', 'next_step'}
               for pack in venture_roles['profiles']['venture-core']['packs'].values()
               for package in pack['work_packages'].values())
    assert all(not any(str(item).startswith('required-signal:') for item in package['required_evidence'])
               for pack in venture_roles['profiles']['venture-core']['packs'].values()
               for package in pack['work_packages'].values())
    assert all(set(package) >= {'role_id', 'role_name', 'entry_criteria', 'inputs', 'lifecycle_stage_ids'}
               for step in venture_registry['ordered_steps']
               for _, package in venture_role_package_definitions(f'venture:{step}'))
    assert sum(len(stage['work_packages']) for pack in matrix['packs'].values() for stage in pack['stages'].values()) == 115
    assert all(set(stage['role_ids']) == set(stage['work_packages']) for pack in matrix['packs'].values() for stage in pack['stages'].values())
    broken_registry = copy.deepcopy(registry)
    del broken_registry['capability_packs']['engineering']['step_definitions']['verify']
    assert validate_definitions(broken_registry)
    broken_matrix = copy.deepcopy(matrix)
    del broken_matrix['packs']['engineering']['stages']['verify']['work_packages']['frontend-engineer']
    assert validate_definitions(registry, broken_matrix)
    definition = canonical('domain:book-dev', 'book-dev:engineering:verify')
    cycle_definition = canonical('operating-cycle:fixture-cycle', 'operating-cycle:plan')
    learning_definition = canonical('learning:fixture-skill', 'learning:orient')
    venture_definition = canonical('venture:fixture-venture', 'venture:frame')
    venture_role_definition = canonical('venture:fixture-venture', 'venture:frame@business-analyst')
    assert cycle_definition['title'] == 'วางผลลัพธ์และ guardrails ของรอบ'
    assert cycle_definition['required_lifecycle_evidence']
    assert learning_definition['title'] == 'กำหนดโจทย์การเรียนรู้และขอบเขต'
    assert learning_definition['required_lifecycle_evidence']
    assert venture_definition['title'] == 'กำหนด thesis และขอบเขตของ venture'
    assert venture_definition['required_lifecycle_evidence']
    assert venture_role_definition['role_id'] == 'business-analyst'
    assert venture_role_definition['entry_criteria'] == venture_definition['entry_criteria']
    assert venture_role_definition['inputs'] == venture_definition['inputs']
    assert role_set_for_scope('domain:book-dev')['selection_source'] == 'domain_default_profile'
    assert evaluate(definition)['status'] == 'unverified'
    assert len(evaluate(definition)['quality']['dimensions']) == 5
    record = dict(scope='domain:book-dev', step_id='book-dev:engineering:verify', status='complete',
                  owner='Fixture owner', reviewer='Fixture reviewer', decision='accepted',
                  source_of_truth='systems/architecture/book-dev-domain-workflows.yaml',
                  outcome_json=json.dumps({'completion_rule_passed': True, 'acceptance_result': 'fixture assertions accepted'}),
                  quality_json=json.dumps(dict.fromkeys(EXPECTED_DIMENSIONS, 'passed')),
                  evidence_refs_json='[]')
    assert not evaluate(definition, record)['completed']
    refs = [{'requirement': req, 'kind': 'test_run', 'path': 'infra/tests/quality/universal_step_contract_test.py'} for req in definition['required_evidence']]
    record['evidence_refs_json'] = json.dumps(refs)
    assert evaluate(definition, record)['completed']
    incomplete_current = {**record, 'status': 'current', 'evidence_refs_json': '[]'}
    assert evaluate(definition, incomplete_current)['status'] == 'needs-review'
    assert not evaluate(definition, {**record, 'quality_json': json.dumps(dict.fromkeys(EXPECTED_DIMENSIONS, 'unknown'))})['completed']
    for field in ('owner', 'reviewer', 'decision'):
        invalid = {**record, field: None}
        assert not evaluate(definition, invalid)['completed'], field
    for invalid in ({**record, 'status': 'invented'}, {**record, 'quality_json': '{bad'},
                    {**record, 'quality_json': '[]'}, {**record, 'outcome_json': '{}'}):
        assert not evaluate(definition, invalid)['completed']
    for path in ('required-signal:tests', '../outside', 'infra/.env', 'data/private/x',
                 'projects/product/engineering/book-dev/github/unselected/README.md', 'missing-evidence.md', 'infra/tests'):
        try:
            resolve_evidence({'requirement': 'test_or_evaluation_result', 'path': path})
            raise AssertionError(path)
        except ValueError:
            pass
    invalid = {**record, 'evidence_refs_json': json.dumps([{'requirement': req, 'path': 'missing.md'} for req in definition['required_evidence']])}
    assert not evaluate(definition, invalid)['completed']
    assert not evaluate(definition, invalid)['evidence_refs']
    waiver = dict(status='accepted', owner='Fixture owner', reason='bounded exception', compensating_control='manual check')
    accepted = {**record, 'decision': 'accepted-exception', 'exception_or_waiver_json': json.dumps(waiver), 'review_due_at': '2999-01-01'}
    assert evaluate(definition, accepted)['accepted_exception']
    assert evaluate(definition, accepted)['completed']
    for field in ('owner', 'reason', 'compensating_control'):
        incomplete = {**waiver, field: ''}
        assert not evaluate(definition, {**accepted, 'exception_or_waiver_json': json.dumps(incomplete)})['completed']
    assert not evaluate(definition, {**accepted, 'review_due_at': None})['completed']
    with tempfile.TemporaryDirectory() as temp:
        db = sqlite3.connect(str(Path(temp) / 'fixture.db'))
        db.row_factory = sqlite3.Row
        # skills, portfolio_contexts and projects live in cells; the double serves them from these legacy tables
        apply_legacy_portfolio_tables(db)
        import universal_step_record
        universal_step_record.open_cell_store = sqlite_cell_opener(db)
        db.execute("""INSERT INTO skills (id, slug, name, status, progress_percent, resources)
            VALUES (1, 'fixture-skill', 'Fixture Skill', 'proficient', 100, '[]')""")
        db.execute("""INSERT INTO progress_log (id, skill_id, activity_type, title, evidence_url, logged_at)
            VALUES (1, 1, 'practice', 'Fixture lab', 'learning/fixture-evidence.md', '2026-09-06T00:00:00Z')""")
        db.execute("""INSERT INTO portfolio_contexts
            (id, slug, name, context_type, scope, description, source_path, source, status, updated_at)
            VALUES (1, 'venture-fixture-venture', 'Fixture Venture', 'venture', 'professional',
                    'Fixture context only', 'ventures/fixture-venture', 'fixture', 'planned',
                    '2026-09-06T00:00:00Z')""")
        db.commit()
        learning_scope = 'learning:fixture-skill'
        learning_path = project(learning_scope)
        assert list(learning_path) == [f'learning:{step}' for step in learning_registry['ordered_steps']]
        assert all(step['status'] == 'unverified' for step in learning_path.values())
        assert all(step['completed'] is False and step['evidence_refs'] == [] for step in learning_path.values())
        assert all(step['quality']['readiness'] == 'unknown' and len(step['quality']['dimensions']) == 5 for step in learning_path.values())
        assert learning_path['learning:orient']['learning_context']['progress_percent'] == 100
        assert learning_path['learning:orient']['learning_context']['signals_are_context_only'] is True
        venture_path = project('venture:fixture-venture')
        assert list(venture_path) == [f'venture:{step}' for step in venture_registry['ordered_steps']]
        assert venture_path['venture:frame']['role_set']['status'] == 'configured'
        assert venture_path['venture:frame']['role_set']['profile_id'] == 'venture-core'
        assert venture_path['venture:frame']['role_set']['selected_role_count'] == 71
        assert venture_path['venture:frame']['role_set']['unique_role_count'] == 19
        assert sum(len(step['role_work_packages']) for step in venture_path.values()) == 71
        assert all(step['status'] == 'unverified' for step in venture_path.values())
        assert all(step['completed'] is False and step['evidence_refs'] == [] for step in venture_path.values())
        assert all(step['quality']['readiness'] == 'unknown' and len(step['quality']['dimensions']) == 5 for step in venture_path.values())
        assert all(step['venture_context']['signals_are_context_only'] is True for step in venture_path.values())
        assert all('context' in step['boundary_status'] for step in venture_path.values())
        assert all(package['status'] == 'unverified' for step in venture_path.values() for package in step['role_work_packages'])
        assert all(len(package['quality']['dimensions']) == 5 for step in venture_path.values() for package in step['role_work_packages'])
        venture_record = dict(scope='venture:fixture-venture', step_id='venture:frame', status='complete',
                              owner='Fixture venture owner', reviewer='Fixture venture reviewer', decision='accepted',
                              source_of_truth='systems/architecture/venture-step-workflow.yaml',
                              outcome_json=json.dumps({'completion_rule_passed': True, 'acceptance_result': 'fixture venture accepted'}),
                              quality_json=json.dumps(dict.fromkeys(EXPECTED_DIMENSIONS, 'passed')),
                              evidence_refs_json=json.dumps([{'requirement': req, 'path': 'infra/tests/quality/universal_step_contract_test.py', 'kind': 'test_run'} for req in venture_definition['required_evidence']]))
        try:
            save(venture_record)
            raise AssertionError('step records must not be persisted: the universal_step_records table was retired')
        except ValueError as exc:
            assert 'not persisted' in str(exc) or 'role gate' in str(exc) or 'role' in str(exc), exc
        assert latest(venture_record['scope'], venture_record['step_id']) is None
        db.execute("""INSERT INTO portfolio_contexts
            (id, slug, name, context_type, scope, source_path, source, status)
            VALUES (2, 'venture-unsafe', 'Unsafe Venture', 'venture', 'professional',
                    'ventures/../private', 'fixture', 'planned')""")
        db.commit()
        try:
            validate_scope('venture:unsafe')
            raise AssertionError('unsafe venture source path was accepted')
        except ValueError:
            pass
        role_packages = project('domain:book-dev')['book-dev:engineering:verify']['role_work_packages']
        assert role_packages and all(package['status'] == 'unverified' for package in role_packages)
        role_scope = 'role:software-engineer'
        assert all(step['status'] == 'unverified' for step in project(role_scope).values())
        assert project(role_scope)['learn-1']['quality']['readiness'] == 'unknown'
        db.execute("""INSERT INTO projects
            (id, name, slug, status, domain, archived_at)
            VALUES (1, 'Book Dev Pilot', 'book-dev-pilot', 'active', 'book-dev', NULL)
        """)
        # operating_cycles is the legacy table behind the operating cell double
        db.execute("""INSERT INTO operating_cycles
            (slug, name, status, owner, focus, start_date, end_date)
            VALUES ('fixture-cycle', 'Fixture cycle', 'planned', 'Fixture cycle owner', 'fixture', '2026-09-07', '2026-09-13')
        """)
        project_scope = 'project:book-dev:book-dev-pilot'
        role_set = role_set_for_scope(project_scope)
        assert role_set['status'] == 'configured'
        assert role_set['profile_id'] == 'full-book-dev'
        assert role_set['selection_source'] == 'default_profile'
        assert role_set['selected_role_count'] == 23
        project_path = project(project_scope)
        assert len(project_path) == 15
        assert sum(len(step['role_work_packages']) for step in project_path.values()) == 115
        assert all(step['status'] == 'unverified' for step in project_path.values())
        cycle_path = project('operating-cycle:fixture-cycle')
        assert list(cycle_path) == [f'operating-cycle:{step}' for step in cycle_registry['ordered_steps']]
        assert all(step['status'] == 'unverified' for step in cycle_path.values())
        assert all(step['evidence_refs'] == [] and step['completed'] is False for step in cycle_path.values())
        assert all(step['operating_cycle_status'] == 'planned' for step in cycle_path.values())
        db.commit()
        cli_env = {**os.environ, 'SOLO_EMPIRE_DB_PATH': str(Path(temp) / 'fixture.db'),
                   'EMPIRE_CLIENT_CONFIG': '/nonexistent/solo-empire-test/client.env'}
        role_set_cli = subprocess.run(
            [sys.executable, str(ROOT / 'infra/tests/support/run_with_sqlite_store.py'),
             str(ROOT / 'infra/scripts/utils/universal_step_role_set.py'),
             '--scope', project_scope, '--json'],
            cwd=ROOT, capture_output=True, text=True, check=False, env=cli_env,
        )
        assert role_set_cli.returncode == 0, role_set_cli.stderr
        assert json.loads(role_set_cli.stdout)['role_set']['profile_id'] == 'full-book-dev'
        inventory_cli = subprocess.run(
            [sys.executable, str(ROOT / 'infra/tests/support/run_with_sqlite_store.py'),
             str(ROOT / 'infra/scripts/utils/universal_step_role_set.py'),
             '--projects', '--json'],
            cwd=ROOT, capture_output=True, text=True, check=False, env=cli_env,
        )
        assert inventory_cli.returncode == 0, inventory_cli.stderr
        assert json.loads(inventory_cli.stdout)['projects'][0]['scope'] == project_scope
        db.close()
    profiles_cli = subprocess.run(
        [sys.executable, str(ROOT / 'infra/scripts/utils/universal_step_role_set.py'), '--list-profiles', '--json'],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert profiles_cli.returncode == 0
    assert json.loads(profiles_cli.stdout)['default_profile_id'] == 'full-book-dev'
    assert evaluate({'required_evidence': ['a']}, {**record, 'progress_percent': 100, 'evidence_refs_json': '[]'})['quality']['readiness'] != 'passed'
    contract = load_yaml(CONTRACT_PATH)
    errors = validate_step_contract(contract)
    assert not errors, errors
    coverage = contract['coverage_inventory']
    assert not validate_coverage_inventory(coverage)
    assert set(coverage['surfaces']) == EXPECTED_COVERAGE_SURFACES
    assert coverage['status'] == 'partial'
    assert coverage['surfaces']['learning']['status'] == 'not-implemented'
    assert coverage['surfaces']['venture']['status'] == 'not-implemented'
    broken_coverage = copy.deepcopy(coverage)
    del broken_coverage['surfaces']['venture']
    assert validate_coverage_inventory(broken_coverage)
    coverage_cli = subprocess.run(
        [sys.executable, str(ROOT / 'infra/scripts/utils/universal_step_contract_guard.py'), '--coverage', '--json'],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert coverage_cli.returncode == 0
    assert json.loads(coverage_cli.stdout)['coverage']['surfaces']['venture']['status'] == 'not-implemented'

    fields = {row["id"] for row in contract["required_fields"]}
    assert fields == EXPECTED_FIELDS
    dimensions = {row["id"] for row in contract["quality_dimensions"]}
    assert dimensions == EXPECTED_DIMENSIONS
    assert [row["id"] for row in contract["visualization"]["levels"]] == ["map", "detail", "evidence"]
    assert contract["completion_policy"]["no_inference"]
    assert contract["sources"]["project_role_set_registry"] == "systems/architecture/book-dev-project-role-sets.yaml"
    assert contract["sources"]["venture_role_step_registry"] == "systems/architecture/venture-role-step-matrix.yaml"
    assert contract["mapping"]["project_role_sets"]["relationship"] == "one_declared_role_set_profile_per_project_scope"
    assert contract["mapping"]["venture_role_work_packages"]["relationship"] == "one_explicit_role_package_per_declared_venture_step_and_role"

    broken = copy.deepcopy(contract)
    broken["required_fields"] = [row for row in broken["required_fields"] if row["id"] != "rollback_target"]
    assert validate_step_contract(broken)

    print("Universal step contract tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
