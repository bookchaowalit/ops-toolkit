#!/usr/bin/env python3
"""Portfolio Lighthouse / LHCI gate for Solo Empire.

Default mode validates that the flagship portfolio app has a Lighthouse CI
config and npm script. Full ``lhci autorun`` is opt-in because it builds the
Next.js app and can take several minutes.

Usage:
    task test:lighthouse
    task test:lighthouse -- --run
    task test:lighthouse -- --json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

INFRA_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import book_dev_tree  # noqa: E402

# Workspace root = the Book Dev tree that holds `github/<owner>/<visibility>/<repo>` clones (siblings of solo-ops).
REPO_ROOT = book_dev_tree() or INFRA_ROOT.parent

PORTFOLIO_REL = Path(
    "github/"
    "bookchaowalit-website/public/book-apps/portfolio/"
    "bookchaowalit-portfolio-frontend"
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_chrome_path() -> str | None:
    """Prefer system Chrome, then Playwright cache Chromium."""
    env_keys = (
        "CHROME_PATH",
        "LHCI_CHROME_PATH",
        "LIGHTHOUSE_CHROMIUM_PATH",
    )
    for key in env_keys:
        value = os.environ.get(key)
        if value and Path(value).is_file():
            return value

    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        found = shutil.which(name)
        if found:
            return found

    cache = Path.home() / ".cache" / "ms-playwright"
    if cache.is_dir():
        matches = sorted(cache.glob("chromium-*/chrome-linux64/chrome"))
        if matches:
            return str(matches[-1])
        matches = sorted(cache.glob("chromium-*/chrome-linux/chrome"))
        if matches:
            return str(matches[-1])
    return None


def validate_config(portfolio: Path) -> list[str]:
    errors: list[str] = []
    config_path = portfolio / "lighthouserc.json"
    package_path = portfolio / "package.json"

    if not portfolio.is_dir():
        return [f"portfolio app missing: {portfolio}"]

    if not config_path.is_file():
        errors.append(f"missing {config_path.name}")
    else:
        try:
            cfg = load_json(config_path)
        except json.JSONDecodeError as exc:
            errors.append(f"invalid lighthouserc.json: {exc}")
            cfg = {}
        collect = (cfg.get("ci") or {}).get("collect") or {}
        urls = collect.get("url") or []
        if not urls:
            errors.append("lighthouserc.json has no ci.collect.url entries")
        categories = ((collect.get("settings") or {}).get("onlyCategories")) or []
        for required in ("performance", "accessibility", "best-practices", "seo"):
            if required not in categories:
                errors.append(f"lighthouserc missing category: {required}")

    if not package_path.is_file():
        errors.append("missing package.json")
        return errors

    try:
        package = load_json(package_path)
    except json.JSONDecodeError as exc:
        errors.append(f"invalid package.json: {exc}")
        return errors

    scripts = package.get("scripts") or {}
    if "lhci" not in scripts:
        errors.append("package.json missing scripts.lhci")

    deps = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
    if "@lhci/cli" not in deps and "lighthouse" not in deps:
        errors.append("package.json missing @lhci/cli or lighthouse dependency")

    return errors


def run_lhci(portfolio: Path) -> int:
    if not (portfolio / "node_modules" / "@lhci" / "cli").exists() and not (
        portfolio / "node_modules" / ".bin" / "lhci"
    ).exists():
        print("LHCI CLI not installed in portfolio node_modules.")
        print("Install first: npm --prefix <portfolio> ci")
        return 2

    chrome = resolve_chrome_path()
    if not chrome:
        print("Chrome/Chromium not found for LHCI.")
        print("Install Chrome, or set CHROME_PATH, or install Playwright Chromium.")
        return 2

    env = os.environ.copy()
    env["CHROME_PATH"] = chrome
    env["LHCI_CHROME_PATH"] = chrome
    env["LIGHTHOUSE_CHROMIUM_PATH"] = chrome
    print(f"Running lhci autorun in {portfolio}")
    print(f"  chrome: {chrome}")
    proc = subprocess.run(
        ["npm", "run", "lhci"],
        cwd=str(portfolio),
        check=False,
        env=env,
    )
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Portfolio Lighthouse gate")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute lhci autorun (builds Next app; slow)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    portfolio = REPO_ROOT / PORTFOLIO_REL
    errors = validate_config(portfolio)
    payload = {
        "portfolio": str(PORTFOLIO_REL),
        "mode": "run" if args.run else "config",
        "ok": not errors,
        "errors": errors,
    }

    if errors:
        print("Lighthouse gate: FAIL (config)")
        for err in errors:
            print(f"  - {err}")
        if args.json:
            print(json.dumps(payload, indent=2))
        return 1

    print("Lighthouse gate: config OK")
    print(f"  app: {PORTFOLIO_REL}")
    print("  lighthouserc.json + scripts.lhci + @lhci/cli present")

    if not args.run:
        print("  mode: config-only (pass --run to execute lhci autorun)")
        if args.json:
            print(json.dumps(payload, indent=2))
        return 0

    code = run_lhci(portfolio)
    payload["ok"] = code == 0
    payload["exit_code"] = code
    if args.json:
        print(json.dumps(payload, indent=2))
    if code == 0:
        print("Lighthouse gate: lhci autorun passed")
    else:
        print(f"Lighthouse gate: lhci autorun failed (exit {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
