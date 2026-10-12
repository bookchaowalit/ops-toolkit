#!/usr/bin/env python3
"""Project a populated canonical Matrix CSV to configured sync targets.

The command is dry-run by default. External writes require ``--apply`` and a
mapping file with the target explicitly enabled. Credentials are resolved by
the repository secret resolver and are never printed.

Examples:
    python3 matrix_sync.py --source path/to/matrix.csv --target markdown \
        --output path/to/matrix.md --json
    python3 matrix_sync.py --source path/to/matrix.csv --target google-sheets \
        --mapping path/to/configured-sync-mappings.yaml --apply --json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import PROJECT_ROOT, get_env, get_google_creds, ARCHITECTURE_ROOT  # noqa: E402
from _http import get_access_token  # noqa: E402


DEFAULT_MAPPING = ARCHITECTURE_ROOT / "templates" / "matrices" / "sync-mappings.yaml"
DEFAULT_SCHEMA = ARCHITECTURE_ROOT / "templates" / "matrices" / "matrix-schema.yaml"
DEFAULT_NOTION_VERSION = "2026-03-11"
TARGETS = ("google-sheets", "microsoft-excel", "notion", "markdown")


class MatrixSyncError(RuntimeError):
    """Safe, user-facing sync error without provider response bodies."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def rel(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def resolve_path(value: str | Path, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml

        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MatrixSyncError(f"missing_file:{rel(path)}") from None
    except Exception as exc:
        if exc.__class__.__name__ == "YAMLError":
            raise MatrixSyncError(f"invalid_yaml:{rel(path)}") from None
        raise
    if not isinstance(payload, dict):
        raise MatrixSyncError(f"invalid_mapping:{rel(path)}")
    return payload


def load_contract(mapping_path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    mapping = load_yaml(mapping_path)
    schema_value = mapping.get("schema")
    if schema_value:
        local_schema_path = resolve_path(schema_value, mapping_path.parent)
        root_schema_path = resolve_path(schema_value, PROJECT_ROOT)
        schema_path = local_schema_path if local_schema_path.is_file() else root_schema_path
        if not schema_path.is_file():  # templates live in architecture-docs
            schema_path = resolve_path(schema_value, ARCHITECTURE_ROOT)
    else:
        schema_path = DEFAULT_SCHEMA
    schema = load_yaml(schema_path)
    if schema.get("kind") != "matrix-template-schema":
        raise MatrixSyncError("invalid_schema_kind")
    if mapping.get("kind") != "matrix-sync-mappings":
        raise MatrixSyncError("invalid_mapping_kind")
    return mapping, schema, schema_path


def parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed


def validate_rows(source: Path, schema: dict[str, Any], matrix_id: str | None = None) -> tuple[list[str], list[dict[str, str]]]:
    try:
        handle = source.open(newline="", encoding="utf-8")
    except FileNotFoundError:
        raise MatrixSyncError(f"missing_source:{rel(source)}") from None

    with handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        field_order = schema.get("field_order", [])
        if header != field_order:
            raise MatrixSyncError("source_header_does_not_match_schema")
        fields = {
            item.get("name"): item
            for item in schema.get("fields", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        validation = schema.get("validation", {})
        required = set(validation.get("required_fields", []))
        enums = schema.get("enums", {})
        seen: set[tuple[str, str]] = set()
        rows: list[dict[str, str]] = []
        for line_number, row in enumerate(reader, start=2):
            normalized = {key: (value or "").strip() for key, value in row.items()}
            missing = sorted(field for field in required if not normalized.get(field))
            if missing:
                raise MatrixSyncError(f"row_{line_number}_missing:{','.join(missing)}")
            identity = (normalized["matrix_id"], normalized["row_id"])
            if identity in seen:
                raise MatrixSyncError(f"duplicate_identity:matrix_id={identity[0]}:row_id={identity[1]}")
            seen.add(identity)
            for field_name, field in fields.items():
                value = normalized.get(field_name, "")
                if not value:
                    continue
                field_type = field.get("type")
                if field_type == "enum":
                    enum_name = field.get("enum")
                    allowed = enums.get(enum_name, [])
                    if value not in allowed:
                        raise MatrixSyncError(f"row_{line_number}_invalid_{field_name}:{value}")
                elif field_type == "number":
                    try:
                        float(value)
                    except ValueError:
                        raise MatrixSyncError(f"row_{line_number}_invalid_number:{field_name}") from None
                elif field_type == "date":
                    try:
                        date.fromisoformat(value)
                    except ValueError:
                        raise MatrixSyncError(f"row_{line_number}_invalid_date:{field_name}") from None
                elif field_type == "datetime":
                    try:
                        parse_datetime(value)
                    except ValueError:
                        raise MatrixSyncError(f"row_{line_number}_invalid_datetime:{field_name}") from None
                elif field_type == "json-object":
                    try:
                        parsed = json.loads(value)
                    except json.JSONDecodeError:
                        raise MatrixSyncError(f"row_{line_number}_invalid_json:{field_name}") from None
                    if not isinstance(parsed, dict):
                        raise MatrixSyncError(f"row_{line_number}_json_must_be_object:{field_name}")
            if matrix_id and normalized["matrix_id"] != matrix_id:
                continue
            rows.append(normalized)
    return header, rows


def csv_value(field: str, value: str, field_types: dict[str, dict[str, Any]]) -> object:
    if not value:
        return ""
    if field_types.get(field, {}).get("type") == "number":
        return float(value)
    return value


def table_values(header: list[str], rows: list[dict[str, str]], schema: dict[str, Any]) -> list[list[object]]:
    field_types = {
        item.get("name"): item
        for item in schema.get("fields", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    return [header] + [[csv_value(field, row.get(field, ""), field_types) for field in header] for row in rows]


def column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>").strip() or "—"


def render_markdown(rows: list[dict[str, str]], target: dict[str, Any], source: Path) -> str:
    fields = target.get("field_order", [])
    matrix_ids = sorted({row.get("matrix_id", "") for row in rows})
    title = matrix_ids[0] if len(matrix_ids) == 1 else "Matrix Projection"
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    lines = [
        "---",
        f"title: Matrix {title}",
        "status: generated",
        "tags: [matrix, generated, projection]",
        f"generated_at: {generated_at}",
        f"canonical_source: {rel(source)}",
        "---",
        "",
        f"# Matrix {title}",
        "",
        f"> Generated projection from `{rel(source)}`. Edit the canonical CSV instead of this file.",
        "",
    ]
    if not fields:
        raise MatrixSyncError("markdown_field_order_missing")
    lines.append("| " + " | ".join(field.replace("_", " ").title() for field in fields) + " |")
    lines.append("| " + " | ".join("---" for _ in fields) + " |")
    for row in rows:
        lines.append("| " + " | ".join(safe_cell(row.get(field, "")) for field in fields) + " |")
    if not rows:
        lines.append("| No rows | " + " | ".join("" for _ in fields[1:]) + " |")
    lines.extend(["", f"Rows: **{len(rows)}**", ""])
    return "\n".join(lines)


def request_json(url: str, *, method: str, token: str, body: object | None, headers: dict[str, str]) -> dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}",
        "User-Agent": "solo-empire-matrix-sync/1.0",
        **headers,
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raise MatrixSyncError(f"http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise MatrixSyncError("provider_unreachable") from None
    except json.JSONDecodeError:
        raise MatrixSyncError("provider_invalid_json") from None


def target_map(mapping: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        target.get("id"): target
        for target in mapping.get("targets", [])
        if isinstance(target, dict) and isinstance(target.get("id"), str)
    }


def target_plan(target_id: str, target: dict[str, Any], rows: list[dict[str, str]], apply: bool) -> dict[str, Any]:
    return {
        "target": target_id,
        "provider": target.get("provider", target_id),
        "enabled": bool(target.get("enabled")),
        "apply": apply,
        "rows": len(rows),
        "direction": target.get("direction"),
        "writeback": target.get("writeback"),
    }


def target_resource_id(target: dict[str, Any], default_env: str) -> str | None:
    """Resolve a non-secret resource ID from a workspace binding or env."""
    direct = target.get("resource_id")
    if isinstance(direct, str) and direct.strip() and not direct.strip().startswith("<"):
        return direct.strip()
    env_name = target.get("resource_id_env", default_env)
    if not isinstance(env_name, str) or not env_name.strip():
        return None
    return get_env(env_name.strip())


def sync_markdown(rows: list[dict[str, str]], target: dict[str, Any], source: Path, output: Path | None, apply: bool) -> dict[str, Any]:
    if output is None:
        raise MatrixSyncError("markdown_output_required")
    payload = render_markdown(rows, target, source)
    result = target_plan("markdown", target, rows, apply)
    result["output"] = str(output)
    result["status"] = "planned"
    if apply:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        result["status"] = "written"
    return result


def sync_google_sheets(rows: list[dict[str, str]], target: dict[str, Any], header: list[str], schema: dict[str, Any], apply: bool) -> dict[str, Any]:
    result = target_plan("google-sheets", target, rows, apply)
    spreadsheet_id = target_resource_id(target, "MATRIX_GOOGLE_SHEETS_RESOURCE_ID")
    if not spreadsheet_id:
        raise MatrixSyncError("missing_google_sheets_resource_id")
    sheet = target.get("worksheet")
    if not isinstance(sheet, str) or not sheet.strip():
        raise MatrixSyncError("missing_google_sheets_worksheet")
    values = table_values(header, rows, schema)
    end = f"{column_letter(len(header))}{len(values)}"
    range_name = f"{sheet}!A1:{end}"
    result.update({"status": "planned", "range": range_name, "resource_id": "[configured]"})
    if not apply:
        return result
    try:
        client_id, client_secret, refresh_token = get_google_creds()
        token = get_access_token(client_id, client_secret, refresh_token)
        url = "https://sheets.googleapis.com/v4/spreadsheets/" + urllib.parse.quote(spreadsheet_id, safe="")
        url += "/values/" + urllib.parse.quote(range_name, safe="!:$") + "?valueInputOption=RAW"
        request_json(url, method="PUT", token=token, body={"range": range_name, "majorDimension": "ROWS", "values": values}, headers={})
    except MatrixSyncError:
        raise
    except Exception:
        raise MatrixSyncError("google_credentials_or_api_error") from None
    result["status"] = "written"
    return result


def sync_excel(rows: list[dict[str, str]], target: dict[str, Any], header: list[str], schema: dict[str, Any], apply: bool) -> dict[str, Any]:
    result = target_plan("microsoft-excel", target, rows, apply)
    workbook_id = target_resource_id(target, "MATRIX_EXCEL_RESOURCE_ID")
    if not workbook_id:
        raise MatrixSyncError("missing_excel_resource_id")
    worksheet = target.get("worksheet")
    if not isinstance(worksheet, str) or not worksheet.strip():
        raise MatrixSyncError("missing_excel_worksheet")
    values = table_values(header, rows, schema)
    end = f"{column_letter(len(header))}{len(values)}"
    address = f"A1:{end}"
    result.update({"status": "planned", "worksheet": worksheet, "range": address, "resource_id": "[configured]"})
    if not apply:
        return result
    token = get_env("MICROSOFT_GRAPH_ACCESS_TOKEN")
    if not token:
        raise MatrixSyncError("missing_microsoft_graph_access_token")
    worksheet_path = urllib.parse.quote(worksheet, safe="")
    url = (
        "https://graph.microsoft.com/v1.0/me/drive/items/"
        + urllib.parse.quote(workbook_id, safe="")
        + "/workbook/worksheets/"
        + worksheet_path
        + "/range(address='"
        + address
        + "')"
    )
    request_json(url, method="PATCH", token=token, body={"values": values}, headers={})
    result["status"] = "written"
    return result


def notion_plain_value(property_value: dict[str, Any]) -> str:
    property_type = property_value.get("type")
    if property_type in {"title", "rich_text"}:
        parts = property_value.get(property_type) or []
        return "".join(
            str(item.get("plain_text") or item.get("text", {}).get("content") or "")
            for item in parts
            if isinstance(item, dict)
        )
    if property_type in {"select", "status"}:
        selected = property_value.get(property_type) or {}
        return str(selected.get("name", "")) if isinstance(selected, dict) else ""
    if property_type == "number":
        return "" if property_value.get("number") is None else str(property_value["number"])
    if property_type == "date":
        selected = property_value.get("date") or {}
        return str(selected.get("start", "")) if isinstance(selected, dict) else ""
    if property_type == "url":
        return str(property_value.get("url") or "")
    if property_type == "checkbox":
        return "true" if property_value.get("checkbox") else "false"
    return ""


def notion_property_value(property_type: str, value: str) -> dict[str, Any]:
    if property_type == "title":
        return {"title": [{"text": {"content": value}}]}
    if property_type == "rich_text":
        return {"rich_text": [{"text": {"content": value[:2000]}}]}
    if property_type == "number":
        try:
            return {"number": float(value)}
        except ValueError:
            raise MatrixSyncError("notion_number_field_requires_number") from None
    if property_type in {"select", "status"}:
        return {property_type: {"name": value}}
    if property_type == "date":
        return {"date": {"start": value}}
    if property_type == "url":
        return {"url": value}
    if property_type == "checkbox":
        return {"checkbox": value.lower() in {"1", "true", "yes", "on"}}
    raise MatrixSyncError(f"notion_unsupported_property_type:{property_type}")


def notion_query_pages(token: str, database_id: str, version: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        payload = request_json(
            f"https://api.notion.com/v1/databases/{urllib.parse.quote(database_id, safe='')}/query",
            method="POST",
            token=token,
            body=body,
            headers={"Notion-Version": version},
        )
        result = payload.get("results", [])
        if isinstance(result, list):
            pages.extend(item for item in result if isinstance(item, dict))
        if not payload.get("has_more"):
            return pages
        cursor = payload.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            return pages


def sync_notion(rows: list[dict[str, str]], target: dict[str, Any], apply: bool) -> dict[str, Any]:
    result = target_plan("notion", target, rows, apply)
    database_id = target_resource_id(target, "MATRIX_NOTION_DATABASE_ID")
    if not database_id:
        raise MatrixSyncError("missing_notion_database_id")
    result.update({"status": "planned", "resource_id": "[configured]"})
    if not apply:
        return result
    token = get_env("NOTION_BOT_TOKEN")
    if not token:
        raise MatrixSyncError("missing_notion_bot_token")
    version = os.environ.get("NOTION_VERSION", DEFAULT_NOTION_VERSION).strip() or DEFAULT_NOTION_VERSION
    database = request_json(
        f"https://api.notion.com/v1/databases/{urllib.parse.quote(database_id, safe='')}",
        method="GET",
        token=token,
        body=None,
        headers={"Notion-Version": version},
    )
    properties = database.get("properties")
    mapping = target.get("column_mapping")
    if not isinstance(properties, dict) or not isinstance(mapping, dict):
        raise MatrixSyncError("notion_database_schema_or_mapping_missing")
    canonical_to_property = {canonical: property_name for property_name, canonical in mapping.items()}
    for required in ("matrix_id", "row_id"):
        property_name = canonical_to_property.get(required)
        if not property_name or property_name not in properties:
            raise MatrixSyncError(f"notion_missing_identity_property:{required}")
    pages = notion_query_pages(token, database_id, version)
    existing: dict[tuple[str, str], str] = {}
    for page in pages:
        page_properties = page.get("properties", {})
        if not isinstance(page_properties, dict):
            continue
        values = {
            canonical: notion_plain_value(page_properties.get(property_name, {}))
            for property_name, canonical in mapping.items()
            if isinstance(property_name, str) and isinstance(canonical, str)
        }
        key = (values.get("matrix_id", ""), values.get("row_id", ""))
        if key != ("", "") and isinstance(page.get("id"), str):
            existing[key] = page["id"]
    created = updated = 0
    for row in rows:
        notion_properties: dict[str, Any] = {}
        for property_name, canonical in mapping.items():
            definition = properties.get(property_name)
            if not isinstance(definition, dict):
                raise MatrixSyncError(f"notion_missing_property:{property_name}")
            value = row.get(canonical, "")
            if not value:
                continue
            property_type = definition.get("type")
            if not isinstance(property_type, str):
                raise MatrixSyncError(f"notion_invalid_property_type:{property_name}")
            notion_properties[property_name] = notion_property_value(property_type, value)
        key = (row["matrix_id"], row["row_id"])
        page_id = existing.get(key)
        if page_id:
            request_json(
                f"https://api.notion.com/v1/pages/{urllib.parse.quote(page_id, safe='')}",
                method="PATCH",
                token=token,
                body={"properties": notion_properties},
                headers={"Notion-Version": version},
            )
            updated += 1
        else:
            request_json(
                "https://api.notion.com/v1/pages",
                method="POST",
                token=token,
                body={"parent": {"database_id": database_id}, "properties": notion_properties},
                headers={"Notion-Version": version},
            )
            created += 1
    result.update({"status": "written", "created": created, "updated": updated})
    return result


def run_sync(
    source: Path,
    mapping_path: Path,
    target_ids: list[str],
    output: Path | None,
    apply: bool,
    matrix_id: str | None,
) -> dict[str, Any]:
    mapping, schema, _ = load_contract(mapping_path)
    header, rows = validate_rows(source, schema, matrix_id)
    maps = target_map(mapping)
    selected = list(TARGETS) if "all" in target_ids else target_ids
    unknown = sorted(set(selected) - set(TARGETS))
    if unknown:
        raise MatrixSyncError(f"unknown_target:{','.join(unknown)}")
    if apply:
        disabled = [target_id for target_id in selected if not bool(maps.get(target_id, {}).get("enabled"))]
        if disabled:
            raise MatrixSyncError(f"target_disabled:{','.join(disabled)}")
    results: list[dict[str, Any]] = []
    for target_id in selected:
        target = maps.get(target_id)
        if not target:
            raise MatrixSyncError(f"missing_target_mapping:{target_id}")
        if target_id == "markdown":
            results.append(sync_markdown(rows, target, source, output, apply))
        elif not apply:
            results.append(target_plan(target_id, target, rows, apply) | {"status": "planned", "resource_id": "[dry-run]"})
        elif target_id == "google-sheets":
            results.append(sync_google_sheets(rows, target, header, schema, apply))
        elif target_id == "microsoft-excel":
            results.append(sync_excel(rows, target, header, schema, apply))
        elif target_id == "notion":
            results.append(sync_notion(rows, target, apply))
    return {
        "status": "ok",
        "dry_run": not apply,
        "source": rel(source),
        "rows": len(rows),
        "matrix_id": matrix_id,
        "targets": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync a canonical Matrix CSV to configured projections")
    parser.add_argument("--source", required=True, help="Populated Matrix CSV")
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING), help="Sync mapping YAML")
    parser.add_argument("--target", action="append", choices=[*TARGETS, "all"])
    parser.add_argument("--output", help="Markdown output path when targeting markdown")
    parser.add_argument("--matrix-id", help="Only sync rows for one matrix_id")
    parser.add_argument("--apply", action="store_true", help="Perform writes; default is dry-run")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = run_sync(
            resolve_path(args.source),
            resolve_path(args.mapping),
            args.target or ["markdown"],
            resolve_path(args.output) if args.output else None,
            args.apply,
            args.matrix_id,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"Matrix sync {payload['status']}: {payload['rows']} row(s)")
        return 0
    except MatrixSyncError as exc:
        payload = {"status": "error", "error_class": exc.code, "dry_run": not args.apply}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"Matrix sync failed: {exc.code}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
