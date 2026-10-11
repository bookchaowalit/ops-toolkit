#!/usr/bin/env python3
"""Inspect and bind Matrix sync targets without changing the canonical template.

The command manages workspace-local binding files. It never stores credentials.
Provider creation is explicit and currently implemented for Google Sheets;
other providers can be linked after their resource is created.

Examples:
    python3 matrix_targets.py status --show-links --json
    python3 matrix_targets.py plan --target google-sheets --json
    python3 matrix_targets.py create --target google-sheets --name "Solo Empire Matrix" \
        --output data/runtime/matrix-sync.local.yaml --apply --json
    python3 matrix_targets.py link --target google-sheets \
        --url https://docs.google.com/spreadsheets/d/<id>/edit \
        --output data/runtime/matrix-sync.yaml --enable --apply --json
"""

from __future__ import annotations

import argparse
import csv
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from matrix_sync import (  # noqa: E402
    DEFAULT_MAPPING,
    MatrixSyncError,
    TARGETS,
    column_letter,
    load_contract,
    resolve_path,
    request_json,
    target_map,
    target_resource_id,
)
from _env import get_google_creds  # noqa: E402
from _http import get_access_token  # noqa: E402

LINKABLE_TARGETS = tuple(target for target in TARGETS if target != "markdown")


URL_PATTERNS = {
    "google-sheets": re.compile(r"^https://docs\.google\.com/spreadsheets/d/([^/]+)"),
    "microsoft-excel": re.compile(r"^https://[^/]+/.*(?:resid|id)=([^&]+)", re.IGNORECASE),
    "notion": re.compile(r"^https://www\.notion\.so/", re.IGNORECASE),
}


class MatrixTargetError(RuntimeError):
    """Safe, user-facing target binding error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def selected_targets(target: str | None) -> list[str]:
    if target in (None, "all"):
        return list(TARGETS)
    if target not in TARGETS:
        raise MatrixTargetError(f"unknown_target:{target}")
    return [target]


def target_url(target: dict[str, Any], resource_id: str | None) -> str | None:
    direct = target.get("resource_url")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    template = target.get("resource_url_template")
    if isinstance(template, str) and resource_id:
        return template.replace("{resource_id}", resource_id)
    return None


def target_status(target_id: str, target: dict[str, Any], show_links: bool = False) -> dict[str, Any]:
    resource_id: str | None = None
    resolution = "missing"
    env_name = target.get("resource_id_env")
    if target.get("resource_id"):
        resource_id = target_resource_id(target, str(env_name or ""))
        resolution = "configured" if resource_id else "missing"
    elif not isinstance(env_name, str) or not env_name.strip():
        resolution = "not_applicable"
    else:
        try:
            resource_id = target_resource_id(target, env_name)
            if resource_id:
                resolution = "configured"
        except Exception as exc:
            resolution = f"unavailable:{exc.__class__.__name__}"
    cli: dict[str, Any] = {
        "status": "task matrices:targets -- status",
        "plan": f"task matrices:targets -- plan --target {target_id}",
    }
    if target_id in LINKABLE_TARGETS:
        cli["link"] = f"task matrices:targets -- link --target {target_id} --url <url> --output <workspace-mapping> --apply"
    if target_id == "google-sheets":
        cli["create"] = "task matrices:targets -- create --target google-sheets --name \"Solo Empire Matrix\" --output data/runtime/matrix-sync.local.yaml --apply"
    result: dict[str, Any] = {
        "target": target_id,
        "provider": target.get("provider", target_id),
        "enabled": bool(target.get("enabled")),
        "resource_id_env": target.get("resource_id_env"),
        "resource_resolution": resolution,
        "resource_id_source": "binding" if target.get("resource_id") else ("env/secret-provider" if env_name else "not_applicable"),
        "cli": cli,
    }
    if show_links:
        result["resource_url"] = target_url(target, resource_id)
    return result


def load_mapping(path: Path) -> dict[str, Any]:
    mapping, _, _ = load_contract(path)
    return mapping


def build_plan(mapping: dict[str, Any], target_ids: list[str]) -> dict[str, Any]:
    maps = target_map(mapping)
    plans: list[dict[str, Any]] = []
    for target_id in target_ids:
        target = maps.get(target_id)
        if not target:
            raise MatrixTargetError(f"missing_target_mapping:{target_id}")
        creation = target.get("creation", {
            "mode": "provider-adapter",
            "note": "Create the provider resource with its adapter, then bind its URL/ID locally.",
        })
        if target_id == "google-sheets":
            creation = google_sheet_create_plan(target, "Solo Empire Matrix")
            creation["command"] = "task matrices:targets -- create --target google-sheets --name \"Solo Empire Matrix\" --output data/runtime/matrix-sync.local.yaml --apply --json"
        plans.append({
            "target": target_id,
            "provider": target.get("provider", target_id),
            "operation": "create-or-link",
            "write_requires": "--apply",
            "creation": creation,
            "link_command": f"task matrices:targets -- link --target {target_id} --url <url> --output <workspace-mapping> --apply",
            "sync_command": f"task matrices:sync -- --source <matrix.csv> --mapping <workspace-mapping> --target {target_id} --apply --json",
        })
    return {"status": "ok", "dry_run": True, "operation": "plan", "targets": plans}


def google_sheet_create_plan(target: dict[str, Any], name: str) -> dict[str, Any]:
    worksheet = target.get("worksheet")
    if not isinstance(worksheet, str) or not worksheet.strip():
        raise MatrixTargetError("missing_google_sheets_worksheet")
    return {
        "target": "google-sheets",
        "provider": target.get("provider", "Google Sheets"),
        "operation": "create-and-bind",
        "name": name,
        "worksheet": worksheet,
        "steps": [
            "create spreadsheet through Sheets API",
            f"rename the first tab to {worksheet}",
            "seed the canonical CSV header",
            "write a workspace-local binding with enabled=true",
        ],
        "write_requires": "--apply",
    }


def create_google_sheet(target: dict[str, Any], name: str, mapping: dict[str, Any], output_path: Path, apply: bool) -> dict[str, Any]:
    plan = google_sheet_create_plan(target, name)
    if not apply:
        return {"status": "planned", "dry_run": True, **plan}
    try:
        client_id, client_secret, refresh_token = get_google_creds()
        token = get_access_token(client_id, client_secret, refresh_token)
        created = request_json(
            "https://sheets.googleapis.com/v4/spreadsheets",
            method="POST",
            token=token,
            body={"properties": {"title": name}},
            headers={},
        )
        spreadsheet_id = created.get("spreadsheetId")
        spreadsheet_url = created.get("spreadsheetUrl")
        sheets = created.get("sheets")
        if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
            raise MatrixSyncError("google_create_missing_spreadsheet_id")
        if not isinstance(spreadsheet_url, str) or not spreadsheet_url:
            spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
        worksheet = str(target["worksheet"])
        first_sheet = sheets[0] if isinstance(sheets, list) and sheets and isinstance(sheets[0], dict) else {}
        first_properties = first_sheet.get("properties", {}) if isinstance(first_sheet, dict) else {}
        first_sheet_id = first_properties.get("sheetId") if isinstance(first_properties, dict) else None
        first_sheet_title = first_properties.get("title") if isinstance(first_properties, dict) else None
        if isinstance(first_sheet_id, int) and first_sheet_title != worksheet:
            request_json(
                "https://sheets.googleapis.com/v4/spreadsheets/"
                + f"{spreadsheet_id}:batchUpdate",
                method="POST",
                token=token,
                body={
                    "requests": [{
                        "updateSheetProperties": {
                            "properties": {"sheetId": first_sheet_id, "title": worksheet},
                            "fields": "title",
                        }
                    }]
                },
                headers={},
            )
        with (DEFAULT_MAPPING.parent / "matrix-template.csv").open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        range_name = f"{worksheet}!A1:{column_letter(len(header))}1"
        request_json(
            "https://sheets.googleapis.com/v4/spreadsheets/"
            + f"{spreadsheet_id}/values/"
            + re.sub(r"[^A-Za-z0-9!:$._-]", "", range_name)
            + "?valueInputOption=RAW",
            method="PUT",
            token=token,
            body={"range": range_name, "majorDimension": "ROWS", "values": [header]},
            headers={},
        )
    except MatrixSyncError:
        raise
    except Exception:
        raise MatrixTargetError("google_create_or_api_error") from None
    binding = copy.deepcopy(mapping)
    linked = link_target(binding, "google-sheets", spreadsheet_url, spreadsheet_id, True)
    write_mapping(output_path, binding)
    return {
        "status": "written",
        "dry_run": False,
        "target": "google-sheets",
        "name": name,
        "worksheet": worksheet,
        "binding": linked,
        "mapping": str(output_path),
    }


def extract_resource_id(target_id: str, url: str) -> str | None:
    pattern = URL_PATTERNS.get(target_id)
    if not pattern:
        return None
    match = pattern.search(url)
    return match.group(1) if match and match.groups() else None


def link_target(mapping: dict[str, Any], target_id: str, url: str, resource_id: str | None, enable: bool) -> dict[str, Any]:
    if target_id not in LINKABLE_TARGETS:
        raise MatrixTargetError(f"not_linkable_target:{target_id}")
    maps = target_map(mapping)
    target = maps.get(target_id)
    if not target:
        raise MatrixTargetError(f"missing_target_mapping:{target_id}")
    normalized_url = url.strip()
    if not normalized_url.startswith("https://"):
        raise MatrixTargetError("resource_url_must_use_https")
    inferred_id = resource_id.strip() if resource_id else extract_resource_id(target_id, normalized_url)
    if not inferred_id:
        raise MatrixTargetError(f"resource_id_required:{target_id}")
    target["resource_id"] = inferred_id
    target["resource_url"] = normalized_url
    if enable:
        target["enabled"] = True
    return {
        "target": target_id,
        "resource_id": inferred_id,
        "resource_url": normalized_url,
        "enabled": bool(target.get("enabled")),
    }


def write_mapping(path: Path, mapping: dict[str, Any]) -> None:
    try:
        import yaml
    except ImportError:
        raise MatrixTargetError("yaml_dependency_missing") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect, create, and bind Matrix sync targets")
    parser.add_argument("operation", choices=["status", "plan", "create", "link"])
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--target", choices=[*TARGETS, "all"])
    parser.add_argument("--show-links", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--resource-id")
    parser.add_argument("--name", default="Solo Empire Matrix", help="Provider resource name for create")
    parser.add_argument("--output", help="Workspace-local mapping path for link operation")
    parser.add_argument("--enable", action="store_true", help="Enable this target in the workspace binding")
    parser.add_argument("--apply", action="store_true", help="Write the workspace-local binding")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        mapping_path = resolve_path(args.mapping)
        target_ids = selected_targets(args.target)
        if args.operation == "status":
            mapping = load_mapping(mapping_path)
            payload: dict[str, Any] = {
                "status": "ok",
                "mapping": str(mapping_path),
                "targets": [target_status(target_id, target_map(mapping)[target_id], args.show_links) for target_id in target_ids],
            }
        elif args.operation == "plan":
            payload = build_plan(load_mapping(mapping_path), target_ids)
        elif args.operation == "create":
            if len(target_ids) != 1 or target_ids[0] != "google-sheets":
                raise MatrixTargetError("create_currently_supports_google_sheets_only")
            if not args.output:
                raise MatrixTargetError("create_output_required")
            if not args.name.strip():
                raise MatrixTargetError("create_name_required")
            output_path = resolve_path(args.output)
            if output_path == DEFAULT_MAPPING:
                raise MatrixTargetError("do_not_modify_source_template")
            mapping = load_mapping(mapping_path)
            target = target_map(mapping).get("google-sheets")
            if not target:
                raise MatrixTargetError("missing_target_mapping:google-sheets")
            payload = create_google_sheet(target, args.name.strip(), mapping, output_path, args.apply)
        else:
            if not args.apply:
                raise MatrixTargetError("link_requires_apply")
            if not args.output:
                raise MatrixTargetError("link_output_required")
            if not args.url:
                raise MatrixTargetError("link_url_required")
            if len(target_ids) != 1 or target_ids[0] not in LINKABLE_TARGETS:
                raise MatrixTargetError("link_requires_one_target")
            source = load_mapping(mapping_path)
            binding = copy.deepcopy(source)
            linked = link_target(binding, target_ids[0], args.url, args.resource_id, args.enable)
            output_path = resolve_path(args.output)
            if output_path == DEFAULT_MAPPING:
                raise MatrixTargetError("do_not_modify_source_template")
            write_mapping(output_path, binding)
            payload = {"status": "written", "mapping": str(output_path), "binding": linked}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else json.dumps(payload, ensure_ascii=False))
        return 0
    except (MatrixTargetError, MatrixSyncError) as exc:
        payload = {"status": "error", "error_class": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"Matrix target failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
