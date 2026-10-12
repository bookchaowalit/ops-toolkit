#!/usr/bin/env python3
"""Explicit append-only evidence records and fail-closed projections. No provider calls."""
import argparse
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from _env import (ARCHITECTURE_DIRS, ARCHITECTURE_ROOT, KNOWLEDGE_DIRS, KNOWLEDGE_ROOT, PROJECT_ROOT, SKILLS_ROOT,
                  repo_file, sibling_repo_root)
from cell_store import CellStoreError, open_cell_store
from task_store import ClientConfigError
from universal_step_contract_guard import EXPECTED_STATUS_VALUES, EXPECTED_DIMENSIONS

AUTOMATION_ID = 'governance-reader'  # reads skills, portfolio contexts and projects; step records have no backend since the SQLite table was retired
REGISTRY = 'systems/architecture/book-dev-domain-workflows.yaml'
ROLE_MATRIX = 'systems/architecture/book-dev-role-step-matrix.yaml'
PROJECT_ROLE_SETS = 'systems/architecture/book-dev-project-role-sets.yaml'
ROLE_CATALOG = 'docs/registries/ROLE-CATALOG.yaml'
OPERATING_CYCLE_REGISTRY = 'systems/architecture/operating-cycle-step-workflow.yaml'
OPERATING_CYCLE_STEPS = ('plan', 'start', 'execute', 'inspect', 'review')
LEARNING_STEP_REGISTRY = 'systems/architecture/learning-step-workflow.yaml'
LEARNING_STEPS = ('orient', 'learn', 'practice', 'evidence', 'operate')
VENTURE_STEP_REGISTRY = 'systems/architecture/venture-step-workflow.yaml'
VENTURE_STEPS = ('frame', 'validate', 'design', 'build', 'launch', 'operate')
VENTURE_ROLE_MATRIX = 'systems/architecture/venture-role-step-matrix.yaml'
LABELS = dict(zip(['understanding', 'execution', 'quality', 'repeatability', 'transfer'],
                  ['เข้าใจ', 'ทำได้', 'คุณภาพผ่าน', 'ทำซ้ำได้', 'นำไปใช้ต่อได้']))
FIELDS = ('purpose entry_criteria inputs actions decisions outputs completion_rule quality_gate '
          'required_evidence dependencies risks rollback_target next_step lifecycle_stage_ids').split()
ROLE_FIELDS = ('role_id role_name applicability ' + ' '.join(FIELDS)).split()
VENTURE_ROLE_FIELDS = ('applicability purpose actions decisions outputs completion_rule quality_gate '
                       'required_evidence dependencies risks rollback_target next_step').split()
APPLICABILITY_VALUES = {'required', 'supporting', 'not-applicable'}


def _load_yaml(relative_path):
    path = PROJECT_ROOT / relative_path
    if not path.exists():  # architecture registries live in architecture-docs
        path = repo_file(relative_path)
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def is_book_dev_scope(scope):
    return scope == 'domain:book-dev' or (
        isinstance(scope, str) and scope.startswith('project:book-dev:')
        and len(scope.split(':')) == 3 and bool(scope.split(':')[-1].strip())
    )


def is_operating_cycle_scope(scope):
    return (
        isinstance(scope, str)
        and scope.startswith('operating-cycle:')
        and len(scope.split(':')) == 2
        and bool(scope.split(':')[-1].strip())
    )


def is_learning_scope(scope):
    return (
        isinstance(scope, str)
        and scope.startswith('learning:')
        and len(scope.split(':')) == 2
        and bool(scope.split(':')[-1].strip())
    )


def is_venture_scope(scope):
    return (
        isinstance(scope, str)
        and scope.startswith('venture:')
        and len(scope.split(':')) == 2
        and bool(scope.split(':')[-1].strip())
    )


def is_workflow_scope(scope):
    return is_book_dev_scope(scope) or is_operating_cycle_scope(scope) or is_learning_scope(scope) or is_venture_scope(scope)


def _cell_rows(cell, plural, what, **filters):
    """Rows of one cell resource; an unreachable or unconfigured cell fails the scope check closed."""
    try:
        return open_cell_store(cell, AUTOMATION_ID).list(plural, **filters)
    except (CellStoreError, ClientConfigError) as exc:
        raise ValueError(f'{what} requires the {cell} API: {exc}') from exc


def _skill_by_slug(slug):
    return next((row for row in _cell_rows('roles-skills', 'skills', 'learning scope') if row['slug'] == slug), None)


def _venture_context_row(context_slug):
    return next((row for row in _cell_rows('projects', 'portfolio-contexts', 'venture scope',
                                           context_type=['venture', 'freelance'])
                 if row['slug'] == context_slug), None)


def validate_scope(scope):
    if isinstance(scope, str) and scope.startswith('role:'):
        return
    if scope == 'domain:book-dev':
        return
    if is_learning_scope(scope):
        slug = scope.split(':', 1)[1]
        row = _skill_by_slug(slug)
        if not row:
            raise ValueError('learning scope must resolve to an existing skill')
        return
    if is_venture_scope(scope):
        slug = scope.split(':', 1)[1]
        context_slug = f'venture-{slug}'
        row = _venture_context_row(context_slug)
        raw_path = str(row['source_path'] or '') if row else ''
        path = Path(raw_path)
        if (not row or not raw_path.startswith('ventures/')
                or path.is_absolute() or '..' in path.parts):
            raise ValueError('venture scope must resolve to a registered ventures/<slug> context')
        return
    if is_operating_cycle_scope(scope):
        slug = scope.split(':', 1)[1]
        row = next(iter(_cell_rows('operating', 'cycles', 'operating-cycle scope', slug=slug)), None)
        if not row:
            raise ValueError('operating-cycle scope must resolve to an existing operating cycle')
        return
    if not is_book_dev_scope(scope):
        raise ValueError('unsupported scope; use domain:book-dev, project:book-dev:<project-slug>, operating-cycle:<cycle-slug>, learning:<skill-slug>, or venture:<venture-slug>')
    slug = scope.split(':', 2)[2]
    row = next((p for p in _cell_rows('projects', 'projects', 'project scope')
                if p['slug'] == slug and p.get('archived_at') is None), None)
    if not row or row['domain'] != 'book-dev':
        raise ValueError('project scope must resolve to an active book-dev project')


@lru_cache(maxsize=1)
def definitions():
    return _load_yaml(REGISTRY)


@lru_cache(maxsize=1)
def role_definitions():
    return _load_yaml(ROLE_MATRIX)


@lru_cache(maxsize=1)
def project_role_sets():
    return _load_yaml(PROJECT_ROLE_SETS)


@lru_cache(maxsize=1)
def operating_cycle_definitions():
    return _load_yaml(OPERATING_CYCLE_REGISTRY)


@lru_cache(maxsize=1)
def learning_step_definitions():
    return _load_yaml(LEARNING_STEP_REGISTRY)


@lru_cache(maxsize=1)
def venture_step_definitions():
    return _load_yaml(VENTURE_STEP_REGISTRY)


@lru_cache(maxsize=1)
def venture_role_definitions():
    return _load_yaml(VENTURE_ROLE_MATRIX)


@lru_cache(maxsize=1)
def lifecycle_definitions():
    return _load_yaml('docs/registries/DOMAIN-EVIDENCE-AUDIT-MATRIX.yaml')


@lru_cache(maxsize=1)
def role_catalog_definitions():
    return _load_yaml(ROLE_CATALOG)


def validate_definitions(registry, matrix=None):
    errors = []
    lifecycle = lifecycle_definitions()
    stage_ids = {x['id'] for x in lifecycle['lifecycle_stages']}
    role_catalog = role_catalog_definitions()
    role_ids = {row['id'] for row in role_catalog.get('roles', []) if isinstance(row, dict)}
    for pack_id in ('engineering', 'analytics', 'knowledge'):
        pack = registry['capability_packs'][pack_id]
        for stage in pack['stages']:
            definition = pack.get('step_definitions', {}).get(stage, {})
            for field in FIELDS:
                if field not in definition or (field != 'dependencies' and not definition[field]):
                    errors.append(f'{pack_id}:{stage}: missing {field}')
            if not set(definition.get('lifecycle_stage_ids', [])) <= stage_ids:
                errors.append(f'{pack_id}:{stage}: invalid lifecycle mapping')
            if any(str(item).startswith('required-signal:') for item in definition.get('required_evidence', [])):
                errors.append(f'{pack_id}:{stage}: required evidence contains an actual-evidence marker')
    try:
        cycle_registry = operating_cycle_definitions()
        if cycle_registry.get('contract') != 'solo-empire.operating-cycle-step.v1':
            errors.append('operating-cycle registry contract is invalid')
        if cycle_registry.get('status') != 'current':
            errors.append('operating-cycle registry must be current')
        ordered_steps = cycle_registry.get('ordered_steps', [])
        if ordered_steps != list(OPERATING_CYCLE_STEPS):
            errors.append('operating-cycle registry must declare the canonical five steps in order')
        cycle_definitions = cycle_registry.get('step_definitions', {})
        if set(cycle_definitions) != set(OPERATING_CYCLE_STEPS):
            errors.append('operating-cycle step definition coverage mismatch')
        for step_id in OPERATING_CYCLE_STEPS:
            definition = cycle_definitions.get(step_id, {})
            if not definition.get('title'):
                errors.append(f'operating-cycle:{step_id}: missing title')
            for field in FIELDS:
                if field not in definition or (field != 'dependencies' and not definition[field]):
                    errors.append(f'operating-cycle:{step_id}: missing {field}')
            if not set(definition.get('lifecycle_stage_ids', [])) <= stage_ids:
                errors.append(f'operating-cycle:{step_id}: invalid lifecycle mapping')
            if any(str(item).startswith('required-signal:') for item in definition.get('required_evidence', [])):
                errors.append(f'operating-cycle:{step_id}: required evidence contains an actual-evidence marker')
    except (KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
        errors.append(f'operating-cycle registry cannot be validated: {exc}')
    try:
        learning_registry = learning_step_definitions()
        if learning_registry.get('contract') != 'solo-empire.learning-step.v1':
            errors.append('learning registry contract is invalid')
        if learning_registry.get('status') != 'current':
            errors.append('learning registry must be current')
        ordered_steps = learning_registry.get('ordered_steps', [])
        if ordered_steps != list(LEARNING_STEPS):
            errors.append('learning registry must declare the canonical five steps in order')
        learning_definitions = learning_registry.get('step_definitions', {})
        if set(learning_definitions) != set(LEARNING_STEPS):
            errors.append('learning step definition coverage mismatch')
        for step_id in LEARNING_STEPS:
            definition = learning_definitions.get(step_id, {})
            if not definition.get('title'):
                errors.append(f'learning:{step_id}: missing title')
            for field in FIELDS:
                if field not in definition or (field != 'dependencies' and not definition[field]):
                    errors.append(f'learning:{step_id}: missing {field}')
            if not set(definition.get('lifecycle_stage_ids', [])) <= stage_ids:
                errors.append(f'learning:{step_id}: invalid lifecycle mapping')
            if any(str(item).startswith('required-signal:') for item in definition.get('required_evidence', [])):
                errors.append(f'learning:{step_id}: required evidence contains an actual-evidence marker')
    except (KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
        errors.append(f'learning registry cannot be validated: {exc}')
    try:
        venture_registry = venture_step_definitions()
        if venture_registry.get('contract') != 'solo-empire.venture-step.v1':
            errors.append('venture registry contract is invalid')
        if venture_registry.get('status') != 'current':
            errors.append('venture registry must be current')
        ordered_steps = venture_registry.get('ordered_steps', [])
        if ordered_steps != list(VENTURE_STEPS):
            errors.append('venture registry must declare the canonical six steps in order')
        venture_definitions = venture_registry.get('step_definitions', {})
        if set(venture_definitions) != set(VENTURE_STEPS):
            errors.append('venture step definition coverage mismatch')
        for step_id in VENTURE_STEPS:
            definition = venture_definitions.get(step_id, {})
            if not definition.get('title'):
                errors.append(f'venture:{step_id}: missing title')
            for field in FIELDS:
                if field not in definition or (field != 'dependencies' and not definition[field]):
                    errors.append(f'venture:{step_id}: missing {field}')
            if not set(definition.get('lifecycle_stage_ids', [])) <= stage_ids:
                errors.append(f'venture:{step_id}: invalid lifecycle mapping')
            if any(str(item).startswith('required-signal:') for item in definition.get('required_evidence', [])):
                errors.append(f'venture:{step_id}: required evidence contains an actual-evidence marker')
    except (KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
        errors.append(f'venture registry cannot be validated: {exc}')
    try:
        venture_roles = venture_role_definitions()
        if venture_roles.get('contract') != 'solo-empire.venture-role-step.v1':
            errors.append('venture role-set registry contract is invalid')
        if venture_roles.get('scope') != 'venture':
            errors.append('venture role-set registry scope must be venture')
        if venture_roles.get('status') != 'current':
            errors.append('venture role-set registry must be current')
        if venture_roles.get('source_role_catalog') != ROLE_CATALOG:
            errors.append('venture role-set must point to the canonical role catalog')
        if venture_roles.get('parent_workflow_registry') != VENTURE_STEP_REGISTRY:
            errors.append('venture role-set must point to the canonical venture workflow')
        if set(venture_roles.get('inherit_from_parent_fields', [])) != {'entry_criteria', 'inputs', 'lifecycle_stage_ids'}:
            errors.append('venture role-set must declare its parent-field inheritance explicitly')
        policy = venture_roles.get('selection_policy', {})
        default_profile_id = policy.get('default_profile_id')
        profiles = venture_roles.get('profiles', {})
        if not default_profile_id or default_profile_id not in profiles:
            errors.append('venture role-set default profile is missing')
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict) or not profile.get('name') or not profile.get('description'):
                errors.append(f'venture role-set profile {profile_id}: name and description are required')
                continue
            profile_packs = profile.get('packs', {})
            if set(profile_packs) != set(VENTURE_STEPS):
                errors.append(f'venture role-set profile {profile_id}: every canonical venture step must have a role pack')
                continue
            for step_id in VENTURE_STEPS:
                parent_id = f'venture:{step_id}'
                pack = profile_packs.get(step_id, {})
                if pack.get('parent_step_id') != parent_id:
                    errors.append(f'{parent_id}: venture role-set parent mapping is not explicit')
                selected_roles = pack.get('role_ids', [])
                if not isinstance(selected_roles, list) or not selected_roles or len(selected_roles) != len(set(selected_roles)):
                    errors.append(f'{parent_id}: venture role_ids must be non-empty and unique')
                    continue
                unknown_roles = set(selected_roles) - role_ids
                if unknown_roles:
                    errors.append(f'{parent_id}: unknown role ids: {sorted(unknown_roles)}')
                work = pack.get('work_packages', {})
                if set(work) != set(selected_roles):
                    errors.append(f'{parent_id}: work package missing or extra role')
                for role_id in selected_roles:
                    item = work.get(role_id, {})
                    for field in VENTURE_ROLE_FIELDS:
                        if field not in item or (field != 'dependencies' and not item[field]):
                            errors.append(f'{parent_id}@{role_id}: missing {field}')
                    if item.get('applicability') not in APPLICABILITY_VALUES:
                        errors.append(f'{parent_id}@{role_id}: invalid applicability')
                    if not str(item.get('next_step', '')).startswith('venture:'):
                        errors.append(f'{parent_id}@{role_id}: next_step must point to a venture step')
                    if any(str(value).startswith('required-signal:') for value in item.get('required_evidence', [])):
                        errors.append(f'{parent_id}@{role_id}: required evidence contains an actual-evidence marker')
    except (KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
        errors.append(f'venture role-set registry cannot be validated: {exc}')
    try:
        matrix = matrix or role_definitions()
        if matrix.get('scope') != 'domain:book-dev':
            errors.append('role matrix scope must be domain:book-dev')
        for pack_id in ('engineering', 'analytics', 'knowledge'):
            pack = registry['capability_packs'][pack_id]
            matrix_pack = matrix.get('packs', {}).get(pack_id, {})
            matrix_roles = matrix_pack.get('role_ids', [])
            if not matrix_roles or len(matrix_roles) != len(set(matrix_roles)):
                errors.append(f'{pack_id}: role_ids must be non-empty and unique')
            unknown_roles = set(matrix_roles) - role_ids
            if unknown_roles:
                errors.append(f'{pack_id}: unknown role ids: {sorted(unknown_roles)}')
            matrix_stages = matrix_pack.get('stages', {})
            for stage in pack['stages']:
                parent_id = f'book-dev:{pack_id}:{stage}'
                stage_entry = matrix_stages.get(stage, {})
                if stage_entry.get('parent_step_id') != parent_id:
                    errors.append(f'{parent_id}: role matrix parent mapping is not explicit')
                if set(stage_entry.get('role_ids', [])) != set(matrix_roles):
                    errors.append(f'{parent_id}: role matrix does not cover every selected role')
                work = stage_entry.get('work_packages', {})
                if set(work) != set(matrix_roles):
                    errors.append(f'{parent_id}: work package missing or extra role')
                for role_id in matrix_roles:
                    item = work.get(role_id, {})
                    for field in ROLE_FIELDS:
                        if field not in item or (field not in {'dependencies'} and not item[field]):
                            errors.append(f'{parent_id}@{role_id}: missing {field}')
                    if item.get('role_id') != role_id:
                        errors.append(f'{parent_id}@{role_id}: role_id mismatch')
                    if item.get('applicability') not in APPLICABILITY_VALUES:
                        errors.append(f'{parent_id}@{role_id}: invalid applicability')
                    if set(item.get('lifecycle_stage_ids', [])) != set(pack['step_definitions'][stage].get('lifecycle_stage_ids', [])):
                        errors.append(f'{parent_id}@{role_id}: lifecycle mapping mismatch')
                    if any(str(value).startswith('required-signal:') for value in item.get('required_evidence', [])):
                        errors.append(f'{parent_id}@{role_id}: required evidence contains an actual-evidence marker')
    except (KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
        errors.append(f'role matrix cannot be validated: {exc}')
    try:
        role_sets = project_role_sets()
        if role_sets.get('scope') != 'domain:book-dev':
            errors.append('project role-set scope must be domain:book-dev')
        if role_sets.get('source_role_matrix') != ROLE_MATRIX:
            errors.append('project role-set must point to the canonical role matrix')
        policy = role_sets.get('selection_policy', {})
        default_profile_id = policy.get('default_profile_id')
        profiles = role_sets.get('profiles', {})
        if not default_profile_id or default_profile_id not in profiles:
            errors.append('project role-set default profile is missing')
        if not isinstance(policy.get('project_overrides', {}), dict):
            errors.append('project role-set project_overrides must be a mapping')
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict) or not profile.get('name') or not profile.get('description'):
                errors.append(f'project role-set profile {profile_id}: name and description are required')
                continue
            profile_packs = profile.get('packs', {})
            if not isinstance(profile_packs, dict) or not profile_packs:
                errors.append(f'project role-set profile {profile_id}: packs are required')
                continue
            for pack_id, selection in profile_packs.items():
                matrix_pack = matrix.get('packs', {}).get(pack_id, {})
                matrix_roles = set(matrix_pack.get('role_ids', []))
                if not matrix_roles:
                    errors.append(f'project role-set profile {profile_id}: unknown or empty pack {pack_id}')
                    continue
                requested_roles = selection.get('role_ids') if isinstance(selection, dict) else None
                if requested_roles == 'all':
                    requested_roles = sorted(matrix_roles)
                if not isinstance(requested_roles, list) or not requested_roles or len(requested_roles) != len(set(requested_roles)):
                    errors.append(f'project role-set profile {profile_id}:{pack_id}: role_ids must be unique and non-empty')
                    continue
                unknown = set(requested_roles) - matrix_roles
                if unknown:
                    errors.append(f'project role-set profile {profile_id}:{pack_id}: unknown roles {sorted(unknown)}')
        for project_slug, profile_id in policy.get('project_overrides', {}).items():
            if not str(project_slug).strip() or profile_id not in profiles:
                errors.append(f'project role-set override {project_slug}: unknown profile')
    except (KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
        errors.append(f'project role-set cannot be validated: {exc}')
    return errors


def lifecycle_evidence(definition):
    lifecycle = lifecycle_definitions()
    return list(dict.fromkeys(req for stage in lifecycle['lifecycle_stages']
                         if stage['id'] in definition.get('lifecycle_stage_ids', [])
                         for req in stage.get('required_evidence', [])))


def role_set_for_scope(scope):
    """Resolve the declared role profile without consulting progress or allocations."""
    if is_venture_scope(scope):
        registry = venture_role_definitions()
        policy = registry.get('selection_policy', {})
        profile_id = policy.get('default_profile_id')
        profile = registry.get('profiles', {}).get(profile_id)
        if not isinstance(profile, dict):
            return {
                'status': 'unverified',
                'profile_id': profile_id,
                'name': None,
                'description': None,
                'selection_source': 'venture_default_profile',
                'source': VENTURE_ROLE_MATRIX,
                'packs': [],
                'selected_pack_ids': [],
                'selected_role_count': 0,
                'unique_role_count': 0,
                'role_package_count': 0,
                'issues': ['declared venture role-set profile is missing'],
            }
        packs = []
        unique_roles = set()
        for pack_id, selection in profile.get('packs', {}).items():
            role_ids = list(selection.get('role_ids', [])) if isinstance(selection, dict) else []
            packs.append({
                'pack_id': pack_id,
                'role_ids': role_ids,
                'role_count': len(role_ids),
            })
            unique_roles.update(role_ids)
        return {
            'status': 'configured',
            'profile_id': profile_id,
            'name': profile.get('name'),
            'description': profile.get('description'),
            'selection_source': 'venture_default_profile',
            'source': VENTURE_ROLE_MATRIX,
            'packs': packs,
            'selected_pack_ids': [pack['pack_id'] for pack in packs],
            'selected_role_count': sum(pack['role_count'] for pack in packs),
            'unique_role_count': len(unique_roles),
            'role_package_count': sum(pack['role_count'] for pack in packs),
            'issues': [],
        }
    if not is_book_dev_scope(scope):
        return None
    registry = project_role_sets()
    policy = registry.get('selection_policy', {})
    project_slug = scope.split(':', 2)[2] if scope.startswith('project:') else None
    overrides = policy.get('project_overrides', {})
    profile_id = overrides.get(project_slug) if project_slug else None
    selection_source = 'project_override' if profile_id else ('default_profile' if project_slug else 'domain_default_profile')
    profile_id = profile_id or policy.get('default_profile_id')
    profile = registry.get('profiles', {}).get(profile_id)
    if not isinstance(profile, dict):
        return {
            'status': 'unverified',
            'profile_id': profile_id,
            'name': None,
            'description': None,
            'selection_source': selection_source,
            'source': PROJECT_ROLE_SETS,
            'packs': [],
            'selected_pack_ids': [],
            'selected_role_count': 0,
            'issues': ['declared project role-set profile is missing'],
        }

    matrix = role_definitions()
    packs = []
    for pack_id, selection in profile.get('packs', {}).items():
        matrix_roles = list(matrix.get('packs', {}).get(pack_id, {}).get('role_ids', []))
        requested_roles = selection.get('role_ids', []) if isinstance(selection, dict) else []
        role_ids = matrix_roles if requested_roles == 'all' else list(requested_roles)
        packs.append({
            'pack_id': pack_id,
            'role_ids': role_ids,
            'role_count': len(role_ids),
        })
    return {
        'status': 'configured',
        'profile_id': profile_id,
        'name': profile.get('name'),
        'description': profile.get('description'),
        'selection_source': selection_source,
        'source': PROJECT_ROLE_SETS,
        'packs': packs,
        'selected_pack_ids': [pack['pack_id'] for pack in packs],
        'selected_role_count': sum(pack['role_count'] for pack in packs),
        'unique_role_count': len({role_id for pack in packs for role_id in pack['role_ids']}),
        'role_package_count': sum(pack['role_count'] for pack in packs),
        'issues': [],
    }


def role_package_definitions(parent_step_id):
    parts = parent_step_id.split(':')
    if len(parts) != 3 or parts[0] != 'book-dev':
        return []
    matrix = role_definitions()
    stage = matrix.get('packs', {}).get(parts[1], {}).get('stages', {}).get(parts[2], {})
    return [(role_id, item) for role_id, item in stage.get('work_packages', {}).items()]


def venture_role_package_definitions(parent_step_id):
    """Expand one explicit venture role pack against its canonical parent step."""
    parts = parent_step_id.split(':')
    if len(parts) != 2 or parts[0] != 'venture' or parts[1] not in VENTURE_STEPS:
        return []
    parent = venture_step_definitions().get('step_definitions', {}).get(parts[1], {})
    registry = venture_role_definitions()
    profile_id = registry.get('selection_policy', {}).get('default_profile_id')
    profile = registry.get('profiles', {}).get(profile_id, {})
    pack = profile.get('packs', {}).get(parts[1], {}) if isinstance(profile, dict) else {}
    catalog = {row.get('id'): row.get('name') for row in role_catalog_definitions().get('roles', []) if isinstance(row, dict)}
    inherited = registry.get('inherit_from_parent_fields', [])
    packages = []
    for role_id in pack.get('role_ids', []):
        raw = pack.get('work_packages', {}).get(role_id, {})
        expanded = {field: parent[field] for field in inherited if field in parent}
        expanded.update(raw)
        expanded.update({
            'role_id': role_id,
            'role_name': catalog.get(role_id, role_id),
            'parent_step_id': parent_step_id,
        })
        packages.append((role_id, expanded))
    return packages


def canonical(scope, step_id):
    if scope.startswith('role:'):
        role_id = scope[5:]
        registry = _load_yaml('docs/registries/ROLE-PATHS.yaml')
        learning = _load_yaml('docs/registries/ROLE-LEARNING.yaml')
        role = next((r for r in registry['roles'] if r['role_id'] == role_id), None)
        learn = next((r for r in learning['roles'] if r['role_id'] == role_id), None)
        keys = ['orient', *[f'learn-{i+1}' for i in range(len((learn or {}).get('skill_slugs', [])))], 'practice', 'evidence', 'operate']
        key = step_id.removeprefix(scope + ':')
        if not role or not step_id.startswith(scope + ':') or key not in keys:
            raise ValueError('unknown canonical role step')
        phase = next(p for p in registry['goal_model']['phases'] if p['id'] == ('learn' if key.startswith('learn-') else key))
        return {'purpose': phase['purpose'], 'actions': [phase['action']], 'outputs': [phase['output']],
                'completion_rule': phase['exit_gate'], 'quality_gate': 'reviewed acceptance, not skill progress',
                'required_evidence': ['learning_evidence', 'practice_artifact', 'reviewed_quality_evidence', 'operational_repeatability', 'transfer_evidence']}
    if is_learning_scope(scope):
        if not step_id.startswith('learning:'):
            raise ValueError('unknown canonical learning step')
        key = step_id.removeprefix('learning:')
        registry = learning_step_definitions()
        if key not in registry.get('step_definitions', {}):
            raise ValueError('unknown canonical learning step')
        definition = registry['step_definitions'][key]
        lifecycle_required = lifecycle_evidence(definition)
        return {**definition, 'required_lifecycle_evidence': lifecycle_required,
                'required_evidence': list(dict.fromkeys(definition['required_evidence'] + lifecycle_required))}
    if is_venture_scope(scope):
        if '@' in step_id:
            parent_step_id, role_id = step_id.rsplit('@', 1)
            parent = canonical(scope, parent_step_id)
            match = dict(venture_role_package_definitions(parent_step_id)).get(role_id)
            if not match:
                raise ValueError('unknown venture role work package')
            definition = {**match, 'parent_step_id': parent_step_id, 'role_id': role_id}
            return {**definition, 'required_lifecycle_evidence': lifecycle_evidence(parent),
                    'required_evidence': list(dict.fromkeys(definition['required_evidence'] + lifecycle_evidence(parent)))}
        if not step_id.startswith('venture:'):
            raise ValueError('unknown canonical venture step')
        key = step_id.removeprefix('venture:')
        registry = venture_step_definitions()
        if key not in registry.get('step_definitions', {}):
            raise ValueError('unknown canonical venture step')
        definition = registry['step_definitions'][key]
        lifecycle_required = lifecycle_evidence(definition)
        return {**definition, 'required_lifecycle_evidence': lifecycle_required,
                'required_evidence': list(dict.fromkeys(definition['required_evidence'] + lifecycle_required))}
    if is_operating_cycle_scope(scope):
        if not step_id.startswith('operating-cycle:'):
            raise ValueError('unknown canonical operating-cycle step')
        key = step_id.removeprefix('operating-cycle:')
        registry = operating_cycle_definitions()
        if key not in registry.get('step_definitions', {}):
            raise ValueError('unknown canonical operating-cycle step')
        definition = registry['step_definitions'][key]
        lifecycle_required = lifecycle_evidence(definition)
        return {**definition, 'required_lifecycle_evidence': lifecycle_required,
                'required_evidence': list(dict.fromkeys(definition['required_evidence'] + lifecycle_required))}
    if is_book_dev_scope(scope) and '@' in step_id:
        parent_step_id, role_id = step_id.rsplit('@', 1)
        parent = canonical(scope, parent_step_id)
        match = dict(role_package_definitions(parent_step_id)).get(role_id)
        if not match:
            raise ValueError('unknown role work package')
        definition = {**match, 'parent_step_id': parent_step_id, 'role_id': role_id}
        return {**definition, 'required_lifecycle_evidence': lifecycle_evidence(parent),
                'required_evidence': list(dict.fromkeys(definition['required_evidence'] + lifecycle_evidence(parent)))}
    parts = step_id.split(':')
    if not is_book_dev_scope(scope) or len(parts) != 3 or parts[0] != 'book-dev':
        raise ValueError('recording pilot accepts canonical domain:book-dev, project:book-dev:<slug>, operating-cycle:<slug>, learning:<skill-slug>, or venture:<venture-slug> steps only')
    registry = definitions()
    if parts[1] not in registry['domain_overrides']['book-dev']['packs']:
        raise ValueError('unknown capability pack')
    definition = registry['capability_packs'][parts[1]].get('step_definitions', {}).get(parts[2])
    if not definition:
        raise ValueError('missing canonical step definition')
    lifecycle_required = lifecycle_evidence(definition)
    return {**definition, 'required_lifecycle_evidence': lifecycle_required,
            'required_evidence': list(dict.fromkeys(definition['required_evidence'] + lifecycle_required))}


def resolve_evidence(ref, root=PROJECT_ROOT):
    """No traversal, secrets, private data, templates or nested-repository inference."""
    if not isinstance(ref, dict) or not isinstance(ref.get('requirement'), str):
        raise ValueError('evidence must name requirement and path or explicit decision/review id')
    kind = ref.get('kind', 'artifact')
    if kind in ('decision_record', 'review_record'):
        if not isinstance(ref.get('id'), int):
            raise ValueError('invalid decision/review reference')
        # Decisions and reviews live in the decisions cell.
        from decision_store import open_decision_store

        store = open_decision_store('governance-reader')
        row = store.get(ref['id']) if kind == 'decision_record' else store.review(ref['id'])
        if row is None:
            raise ValueError('unresolved decision/review reference')
        return f'{kind}:{ref["id"]}'
    raw = ref.get('path')
    if not isinstance(raw, str) or not raw.strip() or ':' in raw:
        raise ValueError('invalid evidence path')
    path = Path(raw)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('evidence outside allowed repository boundary')
    if path.parts[:5] == ('projects', 'product', 'engineering', 'book-dev', 'github'):
        raise ValueError('nested repository boundary is not allowed in this pilot')
    original = path
    if root == PROJECT_ROOT and path.parts:
        # Evidence keeps its legacy repository-relative name; the owning repository serves it.
        head = path.parts[0]
        if head in KNOWLEDGE_DIRS:
            root = KNOWLEDGE_ROOT
        elif head in ARCHITECTURE_DIRS:
            root = ARCHITECTURE_ROOT
        elif head == 'skills':
            root, path = SKILLS_ROOT, Path(*path.parts[1:])
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('evidence symlink outside repository')
    parts = resolved.relative_to(root.resolve()).parts
    blocked = {'data', 'diary', 'credentials', 'secrets', 'private', '.git', '.ssh', 'templates', 'node_modules'}
    if any(p.lower() in blocked or p.lower().startswith('.env') or any(s in p.lower() for s in ('credential', 'secret', 'private_key')) for p in parts):
        raise ValueError('secret/private/generated boundary is not allowed')
    if any((root.joinpath(*parts[:i]) / '.git').exists() for i in range(1, len(parts))):
        raise ValueError('nested repository evidence is outside this root pilot')
    if parts[:5] == ('projects', 'product', 'engineering', 'book-dev', 'github'):
        raise ValueError('nested repository boundary is not allowed in this pilot')
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError('evidence must resolve to a nonempty file')
    if resolved.suffix.lower() in ('.pem', '.key', '.p12', '.pfx', '.db', '.sqlite', '.sqlite3'):
        raise ValueError('credential/database artifacts are not allowed')
    if resolved.name.lower() in ('readme.md', 'boundary.md') and ref['requirement'] not in ('domain_readme', 'record_or_requirement', 'primary_source'):
        raise ValueError('boundary README is not delivery/quality evidence')
    return original.as_posix()


def parse_record(record):
    parsed = dict(record)
    if parsed.get('status') not in EXPECTED_STATUS_VALUES:
        raise ValueError('invalid status')
    for name, expected in [('outcome', dict), ('evidence_refs', list), ('quality', dict),
                           ('exception_or_waiver', dict), ('risks', list), ('controls', list)]:
        value = json.loads(parsed.get(name + '_json', '{}' if expected is dict else '[]'))
        if not isinstance(value, expected):
            raise ValueError(f'invalid {name} JSON shape')
        parsed[name] = value
    for name in ('risks', 'controls'):
        if any(not isinstance(value, str) for value in parsed[name]):
            raise ValueError(f'{name} must contain strings')
    quality = parsed['quality']
    if set(quality) != EXPECTED_DIMENSIONS or any(v not in ('passed', 'missing', 'unknown', 'not-applicable') for v in quality.values()):
        raise ValueError('quality must contain exactly five valid dimensions')
    return parsed


def evaluate(definition, record=None, root=PROJECT_ROOT):
    result = {'status': 'unverified', 'completed': False, 'owner': None, 'evidence_refs': [],
              'actual_evidence': [], 'required_evidence': definition.get('required_evidence', []),
              'issues': [], 'accepted_exception': False,
              'quality': {'readiness': 'unknown', 'dimensions': [
                  {'id': k, 'label': v, 'status': 'unknown', 'note': 'ยังไม่มี reviewed evidence'} for k, v in LABELS.items()]}}
    if record is None:
        result['issues'] = ['ยังไม่มี runtime record: รวบรวมหลักฐานจริงแล้วบันทึกด้วย task step:record']
        return result
    try:
        r = parse_record(record)
        for field in ('owner', 'reviewer', 'accountable_approver', 'decision', 'outcome', 'review_due_at',
                      'exception_or_waiver', 'risks', 'controls', 'rollback_target', 'environment', 'method', 'integrity_ref'):
            result[field] = r.get(field)
        result['risks'] = list(dict.fromkeys(definition.get('risks', []) + r['risks']))
        result['rollback_target'] = r.get('rollback_target') or definition.get('rollback_target')
        found = set()
        for ref in r['evidence_refs']:
            try:
                resolved = resolve_evidence(ref, root)
                result['actual_evidence'].append({**ref, 'resolved': resolved})
                result['evidence_refs'].append(resolved)
                found.add(ref['requirement'])
            except (ValueError, OSError):
                result['issues'].append('evidence reference ไม่ผ่าน boundary หรือ resolve ไม่ได้')
        missing = set(result['required_evidence']) - found
        if missing:
            result['issues'].append('missing required evidence: ' + ', '.join(sorted(missing)))
        if not str(r.get('owner') or '').strip():
            result['issues'].append('missing owner')
        if not str(r.get('reviewer') or r.get('accountable_approver') or '').strip():
            result['issues'].append('missing reviewer/accountable approver')
        if r.get('decision') not in ('accepted', 'accepted-exception'):
            result['issues'].append('missing accepted gate decision')
        if r['outcome'].get('completion_rule_passed') is not True or not r['outcome'].get('acceptance_result'):
            result['issues'].append('completion rule/acceptance result not recorded')
        if any(value != 'passed' for value in r['quality'].values()):
            result['issues'].append('quality dimensions ยังไม่ได้ review ผ่านครบทั้งห้าด้าน')
        waiver = r['exception_or_waiver']
        if waiver or r.get('decision') == 'accepted-exception':
            due = datetime.fromisoformat(str(r.get('review_due_at') or '').replace('Z', '+00:00'))
            valid = (waiver.get('status') == 'accepted' and waiver.get('owner') and waiver.get('reason')
                     and waiver.get('compensating_control') and due.replace(tzinfo=due.tzinfo or timezone.utc) > datetime.now(timezone.utc))
            if not valid:
                result['issues'].append('unresolved or expired exception')
            else:
                result['accepted_exception'] = True
        gate_ok = not result['issues'] and bool(result['evidence_refs'])
        for dim in result['quality']['dimensions']:
            dim['status'] = r['quality'][dim['id']] if gate_ok else 'missing'
            dim['note'] = 'reviewed gate evidence' if gate_ok else 'ต้องมีหลักฐานครบและผล acceptance ก่อน'
        result['quality']['readiness'] = 'passed' if gate_ok and all(v == 'passed' for v in r['quality'].values()) else 'missing'
        result['status'] = r['status'] if gate_ok or r['status'] == 'blocked' else 'needs-review'
        result['completed'] = result['status'] == 'complete' and gate_ok
    except (ValueError, TypeError, KeyError):
        result.update(status='needs-review', completed=False, evidence_refs=[], actual_evidence=[])
        result['issues'].append('invalid runtime record; fail closed')
    return result


def latest(scope, step_id):
    """The newest persisted record for a step.

    The local ``universal_step_records`` table was retired with the SQLite database and no cell stores step
    records yet, so every step projects as unverified until a records backend exists.
    """
    return None


def project_role_packages(scope, parent_step_id, root=PROJECT_ROOT):
    """Project only the roles selected by the canonical scope role-set profile."""
    role_set = role_set_for_scope(scope)
    pack_id = parent_step_id.split(':')[1] if len(parent_step_id.split(':')) == 3 else None
    if is_venture_scope(scope):
        pack_id = parent_step_id.split(':')[1] if len(parent_step_id.split(':')) == 2 else None
    selected_pack = next((pack for pack in (role_set or {}).get('packs', []) if pack['pack_id'] == pack_id), None)
    if not role_set or role_set.get('status') != 'configured' or not selected_pack:
        return []
    selected_roles = set(selected_pack['role_ids'])
    packages = []
    definitions_for_parent = venture_role_package_definitions(parent_step_id) if is_venture_scope(scope) else role_package_definitions(parent_step_id)
    for role_id, definition in definitions_for_parent:
        if role_id not in selected_roles:
            continue
        role_step_id = f'{parent_step_id}@{role_id}'
        projected = {**definition, **evaluate(
            canonical(scope, role_step_id), latest(scope, role_step_id), root=root
        )}
        projected.update({
            'step_id': role_step_id,
            'parent_step_id': parent_step_id,
            'role_id': role_id,
            'role_name': definition['role_name'],
            'applicability': definition['applicability'],
        })
        if definition['applicability'] == 'not-applicable':
            projected.update(status='not-applicable', completed=False)
        packages.append(projected)
    return packages


def apply_role_gate(parent, role_packages):
    """A parent cannot be complete while its declared role profile is unresolved."""
    role_set = parent.get('role_set')
    if role_set and role_set.get('status') != 'configured':
        parent['role_gate_blocked'] = True
        parent.setdefault('issues', []).append('project role-set profile is not configured')
        parent['quality']['readiness'] = 'missing'
        for dimension in parent['quality']['dimensions']:
            dimension['status'] = 'missing'
            dimension['note'] = 'ต้องมี role-set profile ที่ประกาศและตรวจได้ก่อน'
        if parent.get('completed') is True:
            parent['completed'] = False
            parent['status'] = 'needs-review'
        return parent

    if role_set:
        parent_parts = parent.get('step_id', '').split(':')
        pack_id = parent_parts[1] if len(parent_parts) in (2, 3) else None
        if pack_id not in role_set.get('selected_pack_ids', []):
            parent.update(status='not-applicable', completed=False, role_gate_blocked=False)
            parent['quality']['readiness'] = 'not-applicable'
            for dimension in parent['quality']['dimensions']:
                dimension['status'] = 'not-applicable'
                dimension['note'] = 'pack นี้ไม่ได้อยู่ใน project role-set profile'
            return parent

    required = [item for item in role_packages if item.get('applicability') == 'required']
    parent['role_total_count'] = len(role_packages)
    parent['role_completed_count'] = sum(1 for item in role_packages if item.get('completed') is True)
    parent['role_evidence_count'] = len(set(ref for item in role_packages for ref in item.get('evidence_refs', [])))
    parent['aggregated_evidence_refs'] = list(dict.fromkeys(
        parent.get('evidence_refs', []) + [ref for item in role_packages for ref in item.get('evidence_refs', [])]
    ))
    missing = [item['role_id'] for item in required if item.get('completed') is not True]
    if parent.get('completed') is True and missing:
        parent['completed'] = False
        parent['status'] = 'needs-review'
        parent['role_gate_blocked'] = True
        parent.setdefault('issues', []).append('required role work packages incomplete: ' + ', '.join(missing))
        parent['quality']['readiness'] = 'missing'
        for dimension in parent['quality']['dimensions']:
            dimension['status'] = 'missing'
            dimension['note'] = 'ต้องผ่าน required role work packages ก่อน'
    return parent


def learning_context(skill):
    """Expose bounded learning signals without treating them as gate evidence."""
    context = {
        'skill_id': int(skill['id']),
        'slug': skill['slug'],
        'name': skill['name'],
        'status': skill['status'],
        'progress_percent': skill['progress_percent'],
        'progress_log_count': 0,
        'evidence_log_count': 0,
        'demonstrated_evidence_count': 0,
        'latest_evidence_at': None,
        'learning_sources': [],
        'signals_are_context_only': True,
    }
    logs = _cell_rows('roles-skills', 'progress-log', 'learning context', skill_id=int(skill['id']))
    evidence = [log for log in logs if str(log.get('evidence_url') or '').strip()]
    demonstrated = [log for log in evidence if log.get('activity_type') in ('practice', 'project', 'teaching')]
    context.update({
        'progress_log_count': len(logs),
        'evidence_log_count': len(evidence),
        'demonstrated_evidence_count': len(demonstrated),
        'latest_evidence_at': max((str(log['logged_at']) for log in evidence if log.get('logged_at')), default=None),
    })

    try:
        resources = json.loads(skill['resources'] or '[]')
    except (TypeError, json.JSONDecodeError):
        resources = []
    if isinstance(resources, list):
        for raw in resources:
            if not isinstance(raw, str) or not raw.strip():
                continue
            path = Path(raw)
            resolves = False
            if not path.is_absolute() and '..' not in path.parts:
                resolved = repo_file(path).resolve()
                allowed = (
                    resolved.is_relative_to(repo_file('learning').resolve())
                    or resolved.is_relative_to(repo_file('projects/life/learning/book-learning').resolve())
                )
                resolves = allowed and resolved.is_file()
            context['learning_sources'].append({'path': raw, 'resolves': resolves})
    return context


def venture_context(venture_slug):
    """Resolve one explicit ventures/ context without scanning venture contents."""
    row = _venture_context_row(f'venture-{venture_slug}')
    if not row or not str(row['source_path'] or '').startswith('ventures/'):
        return None
    raw_path = str(row['source_path'])
    # ventures/ lives in business-catalog (SOLO_CATALOG_ROOT, default ../business-catalog).
    catalog_root = sibling_repo_root('business-catalog')
    source_root = (catalog_root / 'ventures').resolve()
    source_path = (catalog_root / raw_path).resolve()
    return {
        'id': int(row['id']),
        'context_slug': str(row['slug']),
        'slug': venture_slug,
        'name': str(row['name']),
        'context_type': str(row['context_type']),
        'scope': str(row['scope']),
        'description': row['description'],
        'source_path': raw_path,
        'source_path_resolves': source_path.is_dir() and source_path.is_relative_to(source_root),
        'source': row['source'],
        'status': row['status'],
        'updated_at': row['updated_at'],
        'strategy_registry': 'ventures/VENTURES-REGISTRY.md',
        'strategy_registry_resolves': (catalog_root / 'ventures/VENTURES-REGISTRY.md').is_file(),
        'signals_are_context_only': True,
    }


def project(scope):
    if scope.startswith('role:'):
        learning = _load_yaml('docs/registries/ROLE-LEARNING.yaml')
        role = next((r for r in learning['roles'] if r['role_id'] == scope[5:]), None)
        if not role:
            return {}
        keys = ['orient', *[f'learn-{i+1}' for i in range(len(role.get('skill_slugs', [])))], 'practice', 'evidence', 'operate']
        return {key: evaluate(canonical(scope, scope + ':' + key), latest(scope, scope + ':' + key)) for key in keys}
    if is_venture_scope(scope):
        validate_scope(scope)
        venture_slug = scope.split(':', 1)[1]
        context = venture_context(venture_slug)
        if not context:
            return {}
        registry = venture_step_definitions()
        role_set = role_set_for_scope(scope)
        result = {}
        for order, key in enumerate(registry.get('ordered_steps', []), 1):
            step_id = f'venture:{key}'
            definition = canonical(scope, step_id)
            projected = {**definition, **evaluate(definition, latest(scope, step_id))}
            phase_id = f'venture-{key}'
            projected.update({
                'step_id': step_id,
                'scope': scope,
                'phase_id': phase_id,
                'phase_name': definition['title'],
                'phase_order': order,
                'title': definition['title'],
                'detail_field_count': len(FIELDS),
                'detail_field_total': len(FIELDS),
                'artifact_signals': [
                    'portfolio_contexts.status, source_path และ description เป็น venture context เท่านั้น',
                    'VENTURES-REGISTRY.md เป็น strategy context; ไม่ใช่ actual delivery หรือ outcome evidence',
                ],
                'boundary_status': 'venture context แยกจาก universal runtime evidence; ไม่ใช้ strategy, task, progress หรือ directory เพื่อ mark completion',
                'venture_context': context,
                'role_set': role_set,
                'source': VENTURE_STEP_REGISTRY,
            })
            role_packages = project_role_packages(scope, step_id)
            projected['role_work_packages'] = role_packages
            projected = apply_role_gate(projected, role_packages)
            projected['status_label'] = {
                'complete': 'เสร็จแล้ว', 'current': 'กำลังทำ', 'upcoming': 'ถัดไป',
                'blocked': 'ติดขัด', 'unverified': 'ยังไม่ยืนยัน', 'needs-review': 'รอ review',
                'operating': 'ทำซ้ำอยู่', 'not-applicable': 'ไม่เกี่ยวข้อง',
            }.get(projected.get('status'), projected.get('status', 'ยังไม่ยืนยัน'))
            result[step_id] = projected
        return result
    if is_learning_scope(scope):
        validate_scope(scope)
        skill_slug = scope.split(':', 1)[1]
        skill = _skill_by_slug(skill_slug)
        if not skill:
            return {}
        registry = learning_step_definitions()
        context = learning_context(skill)
        result = {}
        for order, key in enumerate(registry.get('ordered_steps', []), 1):
            step_id = f'learning:{key}'
            definition = canonical(scope, step_id)
            projected = {**definition, **evaluate(definition, latest(scope, step_id))}
            phase_id = f'learning-{key}'
            projected.update({
                'step_id': step_id,
                'scope': scope,
                'phase_id': phase_id,
                'phase_name': definition['title'],
                'phase_order': order,
                'title': definition['title'],
                'detail_field_count': len(FIELDS),
                'detail_field_total': len(FIELDS),
                'artifact_signals': [
                    'skills.progress_percent และ status เป็น activity/self-assessment context เท่านั้น',
                    'progress_log และ challenge status เป็น learning signals; ไม่ใช่ gate evidence',
                ],
                'boundary_status': 'learning context แยกจาก universal runtime evidence; ไม่ใช้ progress หรือ note เพื่อ mark completion',
                'learning_context': context,
                'source': LEARNING_STEP_REGISTRY,
            })
            projected['status_label'] = {
                'complete': 'เสร็จแล้ว', 'current': 'กำลังทำ', 'upcoming': 'ถัดไป',
                'blocked': 'ติดขัด', 'unverified': 'ยังไม่ยืนยัน', 'needs-review': 'รอ review',
                'operating': 'ทำซ้ำอยู่', 'not-applicable': 'ไม่เกี่ยวข้อง',
            }.get(projected.get('status'), projected.get('status', 'ยังไม่ยืนยัน'))
            result[step_id] = projected
        return result
    if is_operating_cycle_scope(scope):
        validate_scope(scope)
        cycle_slug = scope.split(':', 1)[1]
        cycle = next(iter(_cell_rows('operating', 'cycles', 'operating-cycle scope', slug=cycle_slug)), None)
        if not cycle:
            return {}
        registry = operating_cycle_definitions()
        result = {}
        for order, key in enumerate(registry.get('ordered_steps', []), 1):
            step_id = f'operating-cycle:{key}'
            definition = canonical(scope, step_id)
            projected = {**definition, **evaluate(definition, latest(scope, step_id)),
                         'step_id': step_id,
                         'scope': scope,
                         'phase_id': 'operating-cycle',
                         'phase_name': 'Weekly Planning Cycle',
                         'phase_order': 1,
                         'title': definition['title'],
                         'detail_field_count': 14,
                         'detail_field_total': 14,
                         'artifact_signals': ['operating cycle state, task status และ KPI status เป็น context เท่านั้น'],
                         'boundary_status': 'cycle state และ KPI เป็น context; universal step completion ต้องมี explicit runtime record',
                         'operating_cycle_status': cycle['status'],
                         'operating_cycle_owner': cycle['owner'],
                         'operating_cycle_decision': cycle['decision'],
                         'operating_cycle_start_date': cycle['start_date'],
                         'operating_cycle_end_date': cycle['end_date'],
                         'source': OPERATING_CYCLE_REGISTRY}
            projected['status_label'] = {
                'complete': 'เสร็จแล้ว', 'current': 'กำลังทำ', 'upcoming': 'ถัดไป',
                'blocked': 'ติดขัด', 'unverified': 'ยังไม่ยืนยัน', 'needs-review': 'รอ review',
                'operating': 'ทำซ้ำอยู่', 'not-applicable': 'ไม่เกี่ยวข้อง',
            }.get(projected.get('status'), projected.get('status', 'ยังไม่ยืนยัน'))
            result[step_id] = projected
        return result
    if not is_book_dev_scope(scope):
        return {}
    validate_scope(scope)
    registry = definitions()
    result = {}
    for pack_id in registry['domain_overrides']['book-dev']['packs']:
        pack = registry['capability_packs'][pack_id]
        for stage in pack['stages']:
            step_id = f'book-dev:{pack_id}:{stage}'
            definition = canonical(scope, step_id)
            role_set = role_set_for_scope(scope)
            parent = {**definition, **evaluate(definition, latest(scope, step_id)),
                      'step_id': step_id,
                      'artifact_signals': pack['evidence'],
                      'boundary_status': 'root pilot; nested repositories not inspected',
                      'role_set': role_set}
            role_packages = project_role_packages(scope, step_id)
            parent['role_work_packages'] = role_packages
            result[step_id] = apply_role_gate(parent, role_packages)
    return result


def save(record):
    validate_scope(record['scope'])
    definition = canonical(record['scope'], record['step_id'])
    role_set = role_set_for_scope(record['scope']) if (is_book_dev_scope(record['scope']) or is_venture_scope(record['scope'])) else None
    if '@' in record['step_id'] and (is_book_dev_scope(record['scope']) or is_venture_scope(record['scope'])):
        parent_step_id, role_id = record['step_id'].rsplit('@', 1)
        selected_pack = next((pack for pack in (role_set or {}).get('packs', [])
                              if pack['pack_id'] == parent_step_id.split(':')[1]), None)
        if not role_set or role_set.get('status') != 'configured' or role_id not in (selected_pack or {}).get('role_ids', []):
            raise ValueError('role work package is not selected by the canonical project role-set profile')
    parsed = parse_record(record)
    for ref in parsed['evidence_refs']:
        resolve_evidence(ref)
    evaluation = evaluate(definition, record)
    if '@' not in record['step_id']:
        evaluation['step_id'] = record['step_id']
        evaluation['role_set'] = role_set
        role_packages = project_role_packages(record['scope'], record['step_id'])
        evaluation = apply_role_gate(evaluation, role_packages)
    if record['status'] == 'complete' and not evaluation['completed']:
        raise ValueError('; '.join(evaluation['issues']))
    if (parsed['exception_or_waiver'] or parsed.get('decision') == 'accepted-exception') and not evaluation['accepted_exception']:
        raise ValueError('exception requires owner, reason, compensating control and future review_due_at')
    raise ValueError(
        'step records are not persisted: the universal_step_records table was retired and no records backend '
        'exists yet; the validated evaluation above is a dry run'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', required=True)
    parser.add_argument('--step', '--step-id', dest='step_id')
    parser.add_argument('--project', action='store_true')
    parser.add_argument('--status', choices=sorted(EXPECTED_STATUS_VALUES), default='unverified')
    for field in ('owner', 'accountable_approver', 'reviewer', 'decision', 'environment', 'method', 'integrity_ref', 'review_due_at', 'rollback_target'):
        parser.add_argument('--' + field.replace('_', '-'))
    for field, default in [('outcome', '{}'), ('evidence_refs', '[]'), ('quality', json.dumps(dict.fromkeys(LABELS, 'unknown'))), ('exception_or_waiver', '{}'), ('risks', '[]'), ('controls', '[]')]:
        parser.add_argument('--' + field.replace('_', '-'), dest=field + '_json', default=default)
    parser.add_argument('--privacy-class', default='internal')
    parser.add_argument('--retention-class', default='evidence')
    args = vars(parser.parse_args())
    try:
        if args.pop('project'):
            print(json.dumps(project(args['scope']), ensure_ascii=False))
        else:
            args['source_of_truth'] = (
                'docs/registries/ROLE-PATHS.yaml' if args['scope'].startswith('role:')
                else LEARNING_STEP_REGISTRY if args['scope'].startswith('learning:')
                else VENTURE_ROLE_MATRIX if args['scope'].startswith('venture:') and '@' in args['step_id'] else VENTURE_STEP_REGISTRY if args['scope'].startswith('venture:')
                else OPERATING_CYCLE_REGISTRY if args['scope'].startswith('operating-cycle:')
                else ROLE_MATRIX if '@' in args['step_id'] else REGISTRY
            )
            print(json.dumps({'revision': save(args)}, ensure_ascii=False))
    except ValueError as exc:
        parser.exit(2, f'ไม่บันทึก record: {exc}\n')


if __name__ == '__main__':
    main()
