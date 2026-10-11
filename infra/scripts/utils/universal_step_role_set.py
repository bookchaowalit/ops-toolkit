#!/usr/bin/env python3
"""Inspect canonical project or venture role-set profiles without writing state."""

from __future__ import annotations

import argparse
import json

from cell_store import open_cell_store
from universal_step_record import (PROJECT_ROLE_SETS, VENTURE_ROLE_MATRIX, is_venture_scope,
                                   is_workflow_scope, project_role_sets, role_set_for_scope,
                                   validate_scope, venture_role_definitions)


def profile_catalog() -> list[dict]:
    registry = project_role_sets()
    return [
        {
            'profile_id': profile_id,
            'name': profile.get('name'),
            'description': profile.get('description'),
            'packs': profile.get('packs', {}),
        }
        for profile_id, profile in registry.get('profiles', {}).items()
    ]


def registry_for_scope(scope: str) -> dict:
    return venture_role_definitions() if is_venture_scope(scope) else project_role_sets()


def project_catalog() -> list[dict]:
    rows = sorted(
        open_cell_store('projects', 'portfolio-reader').list('projects', domain='book-dev', null=['archived_at']),
        key=lambda row: (row['name'], row['slug']),
    )
    projects = []
    for row in rows:
        scope = f'project:book-dev:{row["slug"]}'
        validate_scope(scope)
        projects.append({
            'id': row['id'],
            'name': row['name'],
            'slug': row['slug'],
            'status': row['status'],
            'scope': scope,
            'role_set': role_set_for_scope(scope),
        })
    return projects


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', help='domain:book-dev or project:book-dev:<project-slug>')
    parser.add_argument('--list-profiles', action='store_true', help='list available canonical profiles')
    parser.add_argument('--projects', action='store_true', help='inspect every non-archived book-dev project')
    parser.add_argument('--json', action='store_true', help='emit JSON')
    args = parser.parse_args()

    if args.list_profiles and (args.scope or args.projects):
        parser.error('--list-profiles cannot be combined with --scope or --projects')
    if args.scope and args.projects:
        parser.error('--scope cannot be combined with --projects')

    if args.list_profiles:
        payload = {
            'contract': project_role_sets().get('contract'),
            'source': PROJECT_ROLE_SETS,
            'default_profile_id': project_role_sets().get('selection_policy', {}).get('default_profile_id'),
            'profiles': profile_catalog(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else json.dumps(payload, ensure_ascii=False))
        return 0

    if args.projects:
        try:
            projects = project_catalog()
        except (OSError, ValueError) as exc:
            parser.exit(2, f'ไม่สามารถอ่าน project role-set inventory: {exc}\n')
        payload = {'contract': project_role_sets().get('contract'), 'source': PROJECT_ROLE_SETS, 'projects': projects}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for project in projects:
                role_set = project['role_set'] or {}
                print(f"{project['scope']}: {role_set.get('profile_id', 'ไม่ระบุ')} · {role_set.get('selection_source', 'unverified')} · {role_set.get('selected_role_count', 0)} roles")
        return 0 if all((project['role_set'] or {}).get('status') == 'configured' for project in projects) else 2

    if not args.scope or not is_workflow_scope(args.scope):
        parser.error('--scope must be domain:book-dev, project:book-dev:<project-slug>, or venture:<venture-slug>')

    try:
        validate_scope(args.scope)
        role_set = role_set_for_scope(args.scope)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'ไม่สามารถ resolve role-set: {exc}\n')

    registry = registry_for_scope(args.scope)
    payload = {'contract': registry.get('contract'), 'scope': args.scope,
               'source': VENTURE_ROLE_MATRIX if is_venture_scope(args.scope) else PROJECT_ROLE_SETS,
               'role_set': role_set}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"scope: {args.scope}")
        print(f"profile: {role_set.get('profile_id') if role_set else 'ไม่ระบุ'}")
        print(f"status: {role_set.get('status') if role_set else 'unverified'}")
        print(f"roles: {role_set.get('selected_role_count', 0) if role_set else 0}")
        print(f"packs: {', '.join(role_set.get('selected_pack_ids', [])) if role_set else 'ไม่ระบุ'}")
        print(f"source: {role_set.get('source') if role_set else (VENTURE_ROLE_MATRIX if is_venture_scope(args.scope) else PROJECT_ROLE_SETS)}")
    return 0 if role_set and role_set.get('status') == 'configured' else 2


if __name__ == '__main__':
    raise SystemExit(main())
