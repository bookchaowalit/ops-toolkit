#!/usr/bin/env python3
"""Portfolio axe accessibility gate for Solo Empire.

Default mode validates that the portfolio app has an axe Playwright suite.
Full browser runs are opt-in because they start Next.js and take longer.

Usage:
    task test:a11y
    task test:a11y -- --run
"""

from __future__ import annotations

import argparse
import json
import os
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


def validate(portfolio: Path) -> list[str]:
    errors: list[str] = []
    if not portfolio.is_dir():
        return [f"portfolio missing: {portfolio}"]
    axe_spec = portfolio / "e2e" / "a11y-axe.spec.ts"
    if not axe_spec.is_file():
        errors.append("missing e2e/a11y-axe.spec.ts")
    package_path = portfolio / "package.json"
    if not package_path.is_file():
        errors.append("missing package.json")
        return errors
    package = json.loads(package_path.read_text(encoding="utf-8"))
    scripts = package.get("scripts") or {}
    if "test:a11y" not in scripts and "e2e:a11y" not in scripts:
        errors.append("package.json missing scripts.test:a11y (or e2e:a11y)")
    deps = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
    if "@axe-core/playwright" not in deps and "axe-core" not in deps:
        errors.append("missing @axe-core/playwright dependency")
    return errors


def run_axe(portfolio: Path) -> int:
    env = os.environ.copy()
    # Dedicated port — avoid collisions with other local :3000 services.
    env.setdefault("PORT", "4318")
    env.setdefault("PLAYWRIGHT_PORT", env["PORT"])
    # Let playwright.config start/manage the webServer on that port.
    env.pop("BASE_URL", None)
    cmd = [
        "npx",
        "playwright",
        "test",
        "e2e/a11y-axe.spec.ts",
        "--project=chromium",
    ]
    print(f"Running axe Playwright suite in {portfolio}")
    print(f"  port: {env.get('PORT')}")
    proc = subprocess.run(cmd, cwd=str(portfolio), env=env, check=False)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Portfolio axe a11y gate")
    parser.add_argument("--run", action="store_true", help="Execute Playwright axe suite")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    portfolio = REPO_ROOT / PORTFOLIO_REL
    errors = validate(portfolio)
    payload = {
        "portfolio": str(PORTFOLIO_REL),
        "mode": "run" if args.run else "config",
        "ok": not errors,
        "errors": errors,
    }
    if errors:
        print("A11y gate: FAIL (config)")
        for err in errors:
            print(f"  - {err}")
        if args.json:
            print(json.dumps(payload, indent=2))
        return 1

    print("A11y gate: config OK")
    print("  e2e/a11y-axe.spec.ts + @axe-core/playwright + test:a11y present")
    if not args.run:
        print("  mode: config-only (pass --run to execute Playwright axe)")
        if args.json:
            print(json.dumps(payload, indent=2))
        return 0

    code = run_axe(portfolio)
    payload["ok"] = code == 0
    payload["exit_code"] = code
    if args.json:
        print(json.dumps(payload, indent=2))
    if code == 0:
        print("A11y gate: axe suite passed")
    else:
        print(f"A11y gate: axe suite failed (exit {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
