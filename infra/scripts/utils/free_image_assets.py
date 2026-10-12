#!/usr/bin/env python3
"""Fetch free stock images through official provider APIs.

This command never scrapes HTML. Openverse works without a key. Pexels and
Unsplash are used only when their API keys are configured. Downloads are
opt-in with ``--apply``. License, creator, and source URLs are stored next
to each file.

Usage:
    python3 infra/scripts/utils/free_image_assets.py "japanese temple" --json
    python3 infra/scripts/utils/free_image_assets.py "matcha" --apply --limit 3
    task media:stock -- "city night" --json
    task media:stock -- "city night" --apply --limit 3
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from _env import KNOWLEDGE_ROOT, PROJECT_ROOT, get_env, sibling_repo_root


CONTRACT = "solo-empire.free-image-assets.v1"
USER_AGENT = "SoloEmpire-free-image-assets/1.0 (local operator; https://bookchaowalit.com)"
OPENVERSE_SEARCH = "https://api.openverse.org/v1/images/"
PEXELS_SEARCH = "https://api.pexels.com/v1/search"
UNSPLASH_SEARCH = "https://api.unsplash.com/search/photos"
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024
# The tracked stock catalog lives in the bookchaowalit/media-assets repo (sibling checkout);
# override with SOLO_MEDIA_ROOT.
MEDIA_REPO_ROOT = sibling_repo_root("media-assets")
STOCK_ROOT = MEDIA_REPO_ROOT / "references" / "free-stock"
CATALOG_PATH = STOCK_ROOT / "catalog.jsonl"
DESIGN_CSV = (
    KNOWLEDGE_ROOT
    / "projects"
    / "product"
    / "design"
    / "book-design"
    / "data"
    / "design-assets.csv"
)
PROVIDERS = ("openverse", "pexels", "unsplash")
PACKS = {
    "editor": (
        "watercolor paper texture",
        "botanical illustration",
        "charcoal drawing texture",
    ),
    "drink": (
        "coconut matcha drink",
        "unsweetened iced tea",
    ),
    "etsy": (
        "floral line drawing",
        "geometric seamless pattern",
    ),
}
IMAGE_TYPES = {
    "jpg": "jpg",
    "jpeg": "jpg",
    "png": "png",
    "webp": "webp",
    "gif": "gif",
    "svg": "svg",
    "tif": "tif",
    "tiff": "tiff",
}


@dataclass
class StockAsset:
    provider: str
    provider_id: str
    title: str
    creator: str
    creator_url: str
    license: str
    license_url: str
    attribution: str
    source_page: str
    download_url: str
    filetype: str

    @property
    def idempotency_key(self) -> str:
        return f"{self.provider}:{self.provider_id}"


def relpath(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "asset"


def is_safe_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def guess_filetype(url: str, declared: str = "") -> str:
    declared = (declared or "").lower().lstrip(".")
    if declared in IMAGE_TYPES:
        return IMAGE_TYPES[declared]
    path = urlparse(url).path.lower()
    for ext in IMAGE_TYPES:
        if path.endswith(f".{ext}"):
            return IMAGE_TYPES[ext]
    return "jpg"


def request_json(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    fetch_impl: Optional[Callable[[str, dict[str, str]], Any]] = None,
) -> Any:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    if fetch_impl is not None:
        return fetch_impl(url, merged)
    request = urllib.request.Request(url, headers=merged, method="GET")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def download_bytes(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    download_impl: Optional[Callable[[str, dict[str, str]], bytes]] = None,
) -> bytes:
    if not is_safe_http_url(url):
        raise ValueError("blocked_download_url")
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    if download_impl is not None:
        payload = download_impl(url, merged)
        if len(payload) > MAX_DOWNLOAD_BYTES:
            raise ValueError("download_too_large")
        return payload
    request = urllib.request.Request(url, headers=merged, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise ValueError("download_too_large")
    return payload


def parse_openverse(body: Any) -> list[StockAsset]:
    items: list[StockAsset] = []
    for row in body.get("results") or []:
        if not isinstance(row, dict):
            continue
        download = str(row.get("url") or "")
        if not is_safe_http_url(download):
            continue
        license_code = str(row.get("license") or "unknown")
        license_url = str(row.get("license_url") or "")
        creator = str(row.get("creator") or "unknown")
        title = str(row.get("title") or creator or "untitled")
        attribution = str(row.get("attribution") or "").strip() or (
            f"{title} by {creator} ({license_code}). {license_url}".strip()
        )
        items.append(
            StockAsset(
                provider="openverse",
                provider_id=str(row.get("id") or hashlib.sha256(download.encode()).hexdigest()[:16]),
                title=title,
                creator=creator,
                creator_url=str(row.get("creator_url") or ""),
                license=license_code,
                license_url=license_url,
                attribution=attribution,
                source_page=str(row.get("foreign_landing_url") or row.get("detail_url") or ""),
                download_url=download,
                filetype=guess_filetype(download, str(row.get("filetype") or "")),
            )
        )
    return items


def parse_pexels(body: Any) -> list[StockAsset]:
    items: list[StockAsset] = []
    for row in body.get("photos") or []:
        if not isinstance(row, dict):
            continue
        src = row.get("src") if isinstance(row.get("src"), dict) else {}
        download = str(src.get("large") or src.get("original") or "")
        if not is_safe_http_url(download):
            continue
        photographer = str(row.get("photographer") or "unknown")
        page = str(row.get("url") or "")
        items.append(
            StockAsset(
                provider="pexels",
                provider_id=str(row.get("id") or ""),
                title=str(row.get("alt") or photographer),
                creator=photographer,
                creator_url=str(row.get("photographer_url") or ""),
                license="Pexels License",
                license_url="https://www.pexels.com/license/",
                attribution=f"Photo by {photographer} on Pexels. {page}".strip(),
                source_page=page,
                download_url=download,
                filetype=guess_filetype(download),
            )
        )
    return items


def parse_unsplash(body: Any) -> list[StockAsset]:
    items: list[StockAsset] = []
    for row in body.get("results") or []:
        if not isinstance(row, dict):
            continue
        urls = row.get("urls") if isinstance(row.get("urls"), dict) else {}
        download = str(urls.get("regular") or urls.get("full") or "")
        if not is_safe_http_url(download):
            continue
        user = row.get("user") if isinstance(row.get("user"), dict) else {}
        creator = str(user.get("name") or "unknown")
        page = str(row.get("links", {}).get("html") or "") if isinstance(row.get("links"), dict) else ""
        items.append(
            StockAsset(
                provider="unsplash",
                provider_id=str(row.get("id") or ""),
                title=str(row.get("description") or row.get("alt_description") or creator),
                creator=creator,
                creator_url=str(user.get("links", {}).get("html") or "") if isinstance(user.get("links"), dict) else "",
                license="Unsplash License",
                license_url="https://unsplash.com/license",
                attribution=f"Photo by {creator} on Unsplash. {page}".strip(),
                source_page=page,
                download_url=download,
                filetype="jpg",
            )
        )
    return items


def search_openverse(
    query: str,
    limit: int,
    *,
    fetch_impl: Optional[Callable[[str, dict[str, str]], Any]] = None,
) -> list[StockAsset]:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "page_size": str(limit),
            "license_type": "commercial",
        }
    )
    body = request_json(f"{OPENVERSE_SEARCH}?{params}", fetch_impl=fetch_impl)
    return parse_openverse(body)[:limit]


def search_pexels(
    query: str,
    limit: int,
    api_key: str,
    *,
    fetch_impl: Optional[Callable[[str, dict[str, str]], Any]] = None,
) -> list[StockAsset]:
    params = urllib.parse.urlencode({"query": query, "per_page": str(limit)})
    body = request_json(
        f"{PEXELS_SEARCH}?{params}",
        headers={"Authorization": api_key},
        fetch_impl=fetch_impl,
    )
    return parse_pexels(body)[:limit]


def search_unsplash(
    query: str,
    limit: int,
    access_key: str,
    *,
    fetch_impl: Optional[Callable[[str, dict[str, str]], Any]] = None,
) -> list[StockAsset]:
    params = urllib.parse.urlencode({"query": query, "per_page": str(limit)})
    body = request_json(
        f"{UNSPLASH_SEARCH}?{params}",
        headers={"Authorization": f"Client-ID {access_key}"},
        fetch_impl=fetch_impl,
    )
    return parse_unsplash(body)[:limit]


def provider_status() -> dict[str, str]:
    pexels = str(get_env("PEXELS_API_KEY", default="") or "").strip()
    unsplash = str(get_env("UNSPLASH_ACCESS_KEY", default="") or "").strip()
    return {
        "openverse": "available",
        "pexels": "available" if pexels else "credentials_not_configured",
        "unsplash": "available" if unsplash else "credentials_not_configured",
    }


def search_provider(
    provider: str,
    query: str,
    limit: int,
    *,
    fetch_impl: Optional[Callable[[str, dict[str, str]], Any]] = None,
) -> tuple[list[StockAsset], str]:
    status = provider_status()
    if provider == "openverse":
        return search_openverse(query, limit, fetch_impl=fetch_impl), "ok"
    if provider == "pexels":
        key = str(get_env("PEXELS_API_KEY", default="") or "").strip()
        if not key:
            return [], status["pexels"]
        return search_pexels(query, limit, key, fetch_impl=fetch_impl), "ok"
    if provider == "unsplash":
        key = str(get_env("UNSPLASH_ACCESS_KEY", default="") or "").strip()
        if not key:
            return [], status["unsplash"]
        return search_unsplash(query, limit, key, fetch_impl=fetch_impl), "ok"
    raise ValueError(f"unknown_provider:{provider}")


def asset_dir(asset: StockAsset) -> Path:
    return STOCK_ROOT / asset.provider / slugify(f"{asset.provider_id}-{asset.title}")


def write_sidecar(asset: StockAsset, image_path: Path, retrieved_at: str) -> None:
    folder = image_path.parent
    folder.mkdir(parents=True, exist_ok=True)
    metadata = asdict(asset)
    metadata.update(
        {
            "contract": CONTRACT,
            "retrieved_at": retrieved_at,
            "local_path": relpath(image_path),
            "idempotency_key": asset.idempotency_key,
        }
    )
    (folder / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (folder / "ATTRIBUTION.md").write_text(
        "\n".join(
            [
                f"# {asset.title}",
                "",
                f"- Provider: {asset.provider}",
                f"- Creator: {asset.creator}",
                f"- Creator URL: {asset.creator_url or 'n/a'}",
                f"- License: {asset.license}",
                f"- License URL: {asset.license_url or 'n/a'}",
                f"- Source page: {asset.source_page or 'n/a'}",
                f"- Retrieved: {retrieved_at}",
                "",
                asset.attribution,
                "",
            ]
        ),
        encoding="utf-8",
    )


def append_catalog(asset: StockAsset, image_path: Path, retrieved_at: str) -> None:
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = asset.idempotency_key
    existing = set()
    if CATALOG_PATH.exists():
        for line in CATALOG_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            existing.add(str(row.get("idempotency_key") or ""))
    if key in existing:
        return
    record = {
        "idempotency_key": key,
        "provider": asset.provider,
        "title": asset.title,
        "creator": asset.creator,
        "license": asset.license,
        "license_url": asset.license_url,
        "source_page": asset.source_page,
        "local_path": relpath(image_path),
        "retrieved_at": retrieved_at,
    }
    with CATALOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def register_design_csv(asset: StockAsset, image_path: Path, retrieved_at: str) -> None:
    DESIGN_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "asset_id",
        "name",
        "type",
        "format",
        "created_date",
        "client",
        "project",
        "file_path",
        "notes",
    ]
    rows: list[dict[str, str]] = []
    if DESIGN_CSV.exists() and DESIGN_CSV.stat().st_size > 0:
        with DESIGN_CSV.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    rel = relpath(image_path)
    if any(row.get("file_path") == rel for row in rows):
        return
    next_id = f"FA{len(rows) + 1:03d}"
    rows.append(
        {
            "asset_id": next_id,
            "name": asset.title[:80],
            "type": "reference",
            "format": asset.filetype,
            "created_date": retrieved_at[:10],
            "client": "",
            "project": "free-stock",
            "file_path": rel,
            "notes": f"{asset.license}; {asset.creator}; {asset.source_page}",
        }
    )
    with DESIGN_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def save_asset(
    asset: StockAsset,
    *,
    retrieved_at: str,
    download_impl: Optional[Callable[[str, dict[str, str]], bytes]] = None,
) -> dict[str, Any]:
    folder = asset_dir(asset)
    image_path = folder / f"original.{asset.filetype}"
    if image_path.exists() and (folder / "metadata.json").exists():
        return {"idempotency_key": asset.idempotency_key, "status": "exists", "path": str(image_path)}
    payload = download_bytes(asset.download_url, download_impl=download_impl)
    folder.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(payload)
    write_sidecar(asset, image_path, retrieved_at)
    append_catalog(asset, image_path, retrieved_at)
    register_design_csv(asset, image_path, retrieved_at)
    return {
        "idempotency_key": asset.idempotency_key,
        "status": "downloaded",
        "path": relpath(image_path),
    }


def list_catalog(limit: int = 50) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if CATALOG_PATH.exists():
        for line in CATALOG_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                items.append(row)
    items = items[-max(1, min(limit, 200)) :]
    return {
        "contract": CONTRACT,
        "action": "list",
        "scraped_html": False,
        "count": len(items),
        "catalog": relpath(CATALOG_PATH),
        "assets": items,
        "provider_status": provider_status(),
    }


def run(
    query: str,
    *,
    provider: str = "openverse",
    limit: int = DEFAULT_LIMIT,
    apply: bool = False,
    as_json: bool = False,
    fetch_impl: Optional[Callable[[str, dict[str, str]], Any]] = None,
    download_impl: Optional[Callable[[str, dict[str, str]], bytes]] = None,
    print_output: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_LIMIT))
    chosen = list(PROVIDERS) if provider == "auto" else [provider]
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    found: list[StockAsset] = []
    provider_results: dict[str, str] = {}
    for name in chosen:
        try:
            assets, status = search_provider(name, query, limit, fetch_impl=fetch_impl)
        except Exception as exc:  # noqa: BLE001
            provider_results[name] = f"error:{type(exc).__name__}"
            continue
        provider_results[name] = status
        found.extend(assets)
        if len(found) >= limit:
            break
    found = found[:limit]
    saved: list[dict[str, Any]] = []
    if apply:
        for asset in found:
            saved.append(save_asset(asset, retrieved_at=retrieved_at, download_impl=download_impl))
    payload = {
        "contract": CONTRACT,
        "query": query,
        "provider": provider,
        "apply": apply,
        "scraped_html": False,
        "provider_status": {**provider_status(), **provider_results},
        "count": len(found),
        "assets": [asdict(asset) for asset in found],
        "saved": saved,
        "catalog": relpath(CATALOG_PATH),
        "next_action": "Use ATTRIBUTION.md next to each file in editor/art work",
    }
    if print_output:
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"{len(found)} asset(s) from official APIs. apply={apply}. HTML scraping is off.")
            for asset in found:
                print(f"- {asset.provider} / {asset.title} ({asset.license}) {asset.source_page}")
            if apply:
                for row in saved:
                    print(f"  {row['status']}: {row.get('path')}")
    return payload


def run_pack(
    pack: str,
    *,
    provider: str = "openverse",
    limit: int = 2,
    apply: bool = False,
    as_json: bool = False,
    fetch_impl: Optional[Callable[[str, dict[str, str]], Any]] = None,
    download_impl: Optional[Callable[[str, dict[str, str]], bytes]] = None,
) -> dict[str, Any]:
    if pack not in PACKS:
        raise ValueError(f"unknown_pack:{pack}")
    queries = list(PACKS[pack])
    batches: list[dict[str, Any]] = []
    for query in queries:
        batch = run(
            query,
            provider=provider,
            limit=limit,
            apply=apply,
            as_json=False,
            fetch_impl=fetch_impl,
            download_impl=download_impl,
            print_output=False,
        )
        batches.append(
            {
                "query": query,
                "count": batch["count"],
                "assets": batch["assets"],
                "saved": batch["saved"],
                "provider_status": batch["provider_status"],
            }
        )
    payload = {
        "contract": CONTRACT,
        "action": "pack",
        "pack": pack,
        "queries": queries,
        "apply": apply,
        "scraped_html": False,
        "batches": batches,
        "count": sum(int(batch["count"]) for batch in batches),
        "catalog": relpath(CATALOG_PATH),
        "next_action": "Use ATTRIBUTION.md next to each file in editor/art work",
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"pack {pack}: {payload['count']} asset(s). apply={apply}.")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch free stock images from official APIs")
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--list", action="store_true", help="List the local catalog")
    parser.add_argument(
        "--pack",
        choices=tuple(PACKS.keys()),
        help="Download a curated editor/art query set",
    )
    parser.add_argument(
        "--provider",
        choices=("openverse", "pexels", "unsplash", "auto"),
        default="openverse",
        help="openverse needs no key; pexels/unsplash need API keys",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="Download files into media-assets references/free-stock/")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        payload = list_catalog(limit=args.limit or 50)
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"{payload['count']} catalogued asset(s) in {payload['catalog']}")
            for row in payload["assets"]:
                print(f"- {row.get('provider')} / {row.get('title')} ({row.get('license')})")
        return 0
    if args.pack:
        run_pack(
            args.pack,
            provider=args.provider,
            limit=args.limit or 2,
            apply=args.apply,
            as_json=args.as_json,
        )
        return 0
    if not args.query:
        parser.error("query is required unless --list or --pack is set")
    run(
        args.query,
        provider=args.provider,
        limit=args.limit or DEFAULT_LIMIT,
        apply=args.apply,
        as_json=args.as_json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
