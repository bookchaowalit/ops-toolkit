#!/usr/bin/env python3
"""Run and aggregate Playwright suites owned by nested product repositories.

The parent repository owns discovery, orchestration, and evidence. Each
product repository continues to own its Playwright config, fixtures, and test
files. The runner never installs dependencies or edits a nested repository.

Usage:
    task test:playwright:list
    task test:playwright
    task test:playwright -- --only bookchaowalit-portfolio
    task test:playwright -- --strict
    task test:playwright:ui -- --only bookchaowalit-portfolio
    task test:playwright:report
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


INFRA_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import book_dev_tree  # noqa: E402

# Workspace root = the Book Dev tree that holds `github/<owner>/<visibility>/<repo>` clones (siblings of ops-toolkit).
REPO_ROOT = book_dev_tree() or INFRA_ROOT.parent
DEFAULT_MANIFEST = Path(__file__).resolve().with_name("playwright-projects.json")
DEFAULT_OUTPUT_ROOT = INFRA_ROOT / "test-results" / "playwright"
DEFAULT_TIMEOUT_SEC = 900
PRUNED_DIRS = {
    ".git",
    ".next",
    ".turbo",
    ".vercel",
    "blob-report",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "playwright-report",
    "test-results",
    "tmp",
    "vendor",
}
DEFAULT_CONFIG_NAMES = (
    "playwright.config.ts",
    "playwright.config.mts",
    "playwright.config.js",
    "playwright.config.mjs",
    "playwright.config.cjs",
)
DEFAULT_SCRIPT_PRIORITY = (
    "test:e2e",
    "e2e:ci",
    "e2e",
    "test:playwright",
    "test:browser",
)
REPORTER_ARGS = "--reporter=blob,html"


@dataclass(frozen=True)
class Project:
    id: str
    path: str
    package_name: str
    script: str | None
    config: str | None
    package_manager: str
    local_cli: str | None


@dataclass
class ProjectResult:
    id: str
    path: str
    status: str
    seconds: float
    reason: str = ""
    exit_code: int | None = None
    log: str = ""
    report: str = ""


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    data = read_json(path)
    if data is None:
        raise ValueError(f"invalid Playwright manifest: {path}")

    roots = data.get("roots")
    if not isinstance(roots, list) or not roots:
        raise ValueError("Playwright manifest must declare at least one root")

    for root in roots:
        resolved = (REPO_ROOT / str(root)).resolve()
        try:
            resolved.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise ValueError(f"Playwright root escapes repository boundary: {root}") from exc

    return data


def slugify(value: str) -> str:
    value = value.removeprefix("@")
    value = value.replace("/", "-")
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value or "playwright-project"


def relative_path(path: Path, repo_root: Path = REPO_ROOT) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def package_manager(cwd: Path) -> str:
    current = cwd.resolve()
    while True:
        package = read_json(current / "package.json")
        declared = str(package.get("packageManager", "")) if package else ""
        if declared.startswith("pnpm@"):
            return "pnpm"
        if declared.startswith("yarn@"):
            return "yarn"
        if declared.startswith("bun@"):
            return "bun"
        if (current / "pnpm-lock.yaml").is_file():
            return "pnpm"
        if (current / "yarn.lock").is_file():
            return "yarn"
        if (current / "bun.lock").is_file() or (current / "bun.lockb").is_file():
            return "bun"
        if current == REPO_ROOT or current.parent == current:
            return "npm"
        current = current.parent


def local_playwright_cli(cwd: Path) -> Path | None:
    current = cwd.resolve()
    while True:
        candidate = current / "node_modules" / ".bin" / "playwright"
        if candidate.is_file() or candidate.is_symlink():
            return candidate
        if current == REPO_ROOT or current.parent == current:
            return None
        current = current.parent


def package_script(scripts: dict[str, Any], priority: Iterable[str]) -> str | None:
    for name in priority:
        value = scripts.get(name)
        if isinstance(value, str) and value.strip():
            return name
    return None


def discover_projects(
    manifest: dict[str, Any],
    repo_root: Path = REPO_ROOT,
) -> list[Project]:
    config_names = tuple(str(x) for x in manifest.get("config_names", DEFAULT_CONFIG_NAMES))
    script_priority = tuple(str(x) for x in manifest.get("script_priority", DEFAULT_SCRIPT_PRIORITY))
    found: list[tuple[Path, dict[str, Any], str | None, Path | None]] = []

    for root_value in manifest.get("roots", []):
        root = (repo_root / str(root_value)).resolve()
        if not root.is_dir():
            continue
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if name not in PRUNED_DIRS)
            if "package.json" not in filenames:
                continue
            package_path = Path(current)
            package = read_json(package_path / "package.json")
            if package is None:
                continue
            scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
            script = package_script(scripts, script_priority)
            config_path = next(
                (package_path / name for name in config_names if (package_path / name).is_file()),
                None,
            )
            if script is None and config_path is None:
                continue
            found.append((package_path, package, script, config_path))

    found.sort(key=lambda item: item[0].as_posix())
    used_ids: set[str] = set()
    projects: list[Project] = []
    for package_path, package, script, config_path in found:
        package_name = str(package.get("name") or package_path.name)
        project_id = slugify(package_name)
        if project_id in used_ids:
            project_id = f"{project_id}-{slugify(relative_path(package_path, repo_root))[-32:]}"
        while project_id in used_ids:
            project_id += "-x"
        used_ids.add(project_id)
        cli = local_playwright_cli(package_path)
        projects.append(
            Project(
                id=project_id,
                path=relative_path(package_path, repo_root),
                package_name=package_name,
                script=script,
                config=config_path.name if config_path else None,
                package_manager=package_manager(package_path),
                local_cli=(relative_path(cli, repo_root) if cli else None),
            )
        )
    return projects


def resolve_project_path(project: Project, repo_root: Path = REPO_ROOT) -> Path:
    path = (repo_root / project.path).resolve()
    path.relative_to(repo_root.resolve())
    return path


def readiness(project: Project, repo_root: Path = REPO_ROOT) -> str | None:
    cwd = resolve_project_path(project, repo_root)
    if not cwd.is_dir():
        return "project directory missing"
    if project.script is None and project.config is None:
        return "no Playwright script or config"
    if project.local_cli is None:
        return "local Playwright CLI missing (install dependencies inside the project)"
    if shutil.which(project.package_manager) is None:
        return f"{project.package_manager} is not on PATH"
    return None


def build_run_command(project: Project, report_args: str = REPORTER_ARGS) -> list[str]:
    reporter = report_args.split()
    if project.script:
        if project.package_manager == "yarn":
            return ["yarn", project.script, *reporter]
        return [project.package_manager, "run", project.script, "--", *reporter]

    if not project.local_cli:
        raise ValueError(f"project {project.id} has no local Playwright CLI")
    return [str(REPO_ROOT / project.local_cli), "test", *reporter]


def build_ui_command(project: Project, host: str, port: int) -> list[str]:
    ui_args = ["--ui", f"--ui-host={host}", f"--ui-port={port}"]
    if project.script:
        if project.package_manager == "yarn":
            return ["yarn", project.script, *ui_args]
        return [project.package_manager, "run", project.script, "--", *ui_args]

    if not project.local_cli:
        raise ValueError(f"project {project.id} has no local Playwright CLI")
    return [str(REPO_ROOT / project.local_cli), "test", *ui_args]


def project_row(project: Project, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    reason = readiness(project, repo_root)
    return {
        **asdict(project),
        "ready": reason is None,
        "reason": reason or "ready",
    }


def tail_text(path: Path, limit: int = 2400) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:].strip()
    except OSError:
        return ""


def run_project(
    project: Project,
    run_dir: Path,
    index: int,
    timeout_sec: int,
    repo_root: Path = REPO_ROOT,
) -> ProjectResult:
    reason = readiness(project, repo_root)
    project_dir = resolve_project_path(project, repo_root)
    project_dir_out = run_dir / "projects" / project.id
    blob_dir = run_dir / "blobs" / project.id
    log_path = run_dir / "logs" / f"{project.id}.log"
    project_dir_out.mkdir(parents=True, exist_ok=True)
    blob_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if reason:
        return ProjectResult(
            id=project.id,
            path=project.path,
            status="skip",
            seconds=0.0,
            reason=reason,
            log=log_path.relative_to(run_dir).as_posix(),
            report=(project_dir_out / "report").relative_to(run_dir).as_posix(),
        )

    command = build_run_command(project)
    env = os.environ.copy()
    env.update(
        {
            "CI": "1",
            "PLAYWRIGHT_BLOB_OUTPUT_DIR": str(blob_dir),
            "PLAYWRIGHT_BLOB_OUTPUT_NAME": f"{project.id}.zip",
            "PLAYWRIGHT_HTML_OUTPUT_DIR": str(project_dir_out / "report"),
            "PLAYWRIGHT_HTML_OPEN": "never",
        }
    )
    if "BASE_URL" not in env and "PORT" not in env and "PLAYWRIGHT_PORT" not in env:
        env["PLAYWRIGHT_PORT"] = str(4318 + index)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        log_path.write_text(combined, encoding="utf-8")
        seconds = round(time.monotonic() - started, 3)
        status = "pass" if proc.returncode == 0 else "fail"
        return ProjectResult(
            id=project.id,
            path=project.path,
            status=status,
            seconds=seconds,
            reason="" if status == "pass" else f"exit {proc.returncode}",
            exit_code=proc.returncode,
            log=log_path.relative_to(run_dir).as_posix(),
            report=(project_dir_out / "report").relative_to(run_dir).as_posix(),
        )
    except subprocess.TimeoutExpired as exc:
        output = ""
        if exc.stdout:
            output += exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode(errors="replace")
        if exc.stderr:
            output += exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode(errors="replace")
        log_path.write_text(output, encoding="utf-8")
        return ProjectResult(
            id=project.id,
            path=project.path,
            status="timeout",
            seconds=round(time.monotonic() - started, 3),
            reason=f"exceeded {timeout_sec}s",
            log=log_path.relative_to(run_dir).as_posix(),
            report=(project_dir_out / "report").relative_to(run_dir).as_posix(),
        )
    except OSError as exc:
        log_path.write_text(str(exc), encoding="utf-8")
        return ProjectResult(
            id=project.id,
            path=project.path,
            status="error",
            seconds=round(time.monotonic() - started, 3),
            reason=str(exc),
            log=log_path.relative_to(run_dir).as_posix(),
            report=(project_dir_out / "report").relative_to(run_dir).as_posix(),
        )


def merge_reports(run_dir: Path) -> tuple[bool, str]:
    blob_dir = run_dir / "blobs"
    merge_input = run_dir / "merge-input"
    report_dir = run_dir / "merged-report"
    blob_files = sorted(blob_dir.rglob("*.zip")) if blob_dir.is_dir() else []
    if not blob_files:
        return False, "no Playwright blob reports were produced"

    cli = INFRA_ROOT / "node_modules" / ".bin" / "playwright"
    if not cli.is_file() and not cli.is_symlink():
        return False, "central Playwright CLI missing under infra/node_modules"

    # Playwright's merge-reports command reads zip files directly from one
    # directory; the runner keeps each project's blob output isolated so a
    # later project cannot remove an earlier report. Copy the small set of
    # report archives into a generated flat input directory before merging.
    merge_input.mkdir(parents=True, exist_ok=True)
    for source in blob_files:
        destination = merge_input / source.name
        if destination.exists():
            destination = merge_input / f"{source.parent.name}-{source.name}"
        shutil.copy2(source, destination)

    env = os.environ.copy()
    env.update({"PLAYWRIGHT_HTML_OUTPUT_DIR": str(report_dir), "PLAYWRIGHT_HTML_OPEN": "never"})
    proc = subprocess.run(
        [str(cli), "merge-reports", "--reporter=html", str(merge_input)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    merge_log = run_dir / "merge-report.log"
    merge_log.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    if proc.returncode != 0 or not (report_dir / "index.html").is_file():
        return False, f"merge-reports failed (exit {proc.returncode}); see {merge_log.relative_to(run_dir)}"
    return True, report_dir.relative_to(run_dir).as_posix()


def write_portfolio_index(run_dir: Path, results: list[ProjectResult], merge_reason: str) -> None:
    rows = []
    for result in results:
        report_link = (
            f'<a href="{html.escape(result.report)}/index.html">Playwright report</a>'
            if result.report and (run_dir / result.report / "index.html").is_file()
            else "—"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(result.id)}</td>"
            f"<td>{html.escape(result.path)}</td>"
            f"<td class=\"{html.escape(result.status)}\">{html.escape(result.status)}</td>"
            f"<td>{result.seconds:.3f}</td>"
            f"<td>{html.escape(result.reason or '—')}</td>"
            f"<td>{report_link}</td>"
            "</tr>"
        )
    merged = (
        '<p><a class="primary" href="merged-report/index.html">Open merged Playwright report</a></p>'
        if (run_dir / "merged-report" / "index.html").is_file()
        else f"<p class=\"warning\">{html.escape(merge_reason)}</p>"
    )
    content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Solo Empire Playwright Portfolio Report</title>
<style>
body{{font:15px system-ui,sans-serif;max-width:1200px;margin:40px auto;padding:0 20px;color:#172033}}
h1{{margin-bottom:4px}} .muted{{color:#667085}} table{{border-collapse:collapse;width:100%;margin-top:24px}}
th,td{{border-bottom:1px solid #e5e7eb;padding:10px;text-align:left;vertical-align:top}}
th{{background:#f8fafc}} .pass{{color:#087443}} .fail,.timeout,.error{{color:#b42318}} .skip{{color:#946200}}
.primary{{display:inline-block;background:#175cd3;color:white;padding:10px 14px;border-radius:7px;text-decoration:none}}
.warning{{background:#fffaeb;border:1px solid #fedf89;padding:12px;border-radius:7px}}
</style></head><body>
<h1>Solo Empire Playwright Portfolio Report</h1>
<p class="muted">Generated at {html.escape(datetime.now(timezone.utc).isoformat())}</p>
{merged}
<table><thead><tr><th>Project</th><th>Path</th><th>Status</th><th>Seconds</th><th>Reason</th><th>Report</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>"""
    (run_dir / "index.html").write_text(content, encoding="utf-8")


def result_exit_code(results: list[ProjectResult], strict: bool) -> int:
    hard_failures = {"fail", "timeout", "error"}
    if strict:
        hard_failures.update({"skip", "missing"})
    if any(result.status in hard_failures for result in results):
        return 1
    if not any(result.status == "pass" for result in results):
        return 1
    return 0


def print_summary(results: list[ProjectResult], merge_reason: str) -> None:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    print(f"Results: {summary} (of {len(results)})")
    print(f"Report: {merge_reason}")


def command_list(args: argparse.Namespace) -> int:
    data = load_manifest(args.manifest)
    projects = discover_projects(data)
    rows = [project_row(project) for project in projects]
    if args.only:
        selected = {item.strip() for item in args.only.split(",") if item.strip()}
        rows = [row for row in rows if row["id"] in selected]
    if args.json:
        print(json.dumps({"count": len(rows), "projects": rows}, indent=2))
        return 0
    print(f"Playwright projects discovered: {len(rows)}")
    for row in rows:
        state = "READY" if row["ready"] else "SKIP"
        print(f"  {state:5} {row['id']:36} {row['path']}")
        if not row["ready"]:
            print(f"        — {row['reason']}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    data = load_manifest(args.manifest)
    projects = discover_projects(data)
    if args.only:
        selected = {item.strip() for item in args.only.split(",") if item.strip()}
        known = {project.id for project in projects}
        unknown = selected - known
        if unknown:
            print(f"Unknown Playwright project(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        projects = [project for project in projects if project.id in selected]
    if not projects:
        print("No Playwright projects discovered.", file=sys.stderr)
        return 1

    output_root = Path(args.output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True)
    print(f"Playwright portfolio run: {run_id}")
    print(f"Projects: {len(projects)}")

    results: list[ProjectResult] = []
    for index, project in enumerate(projects):
        print(f"RUN   {project.id} ...", flush=True)
        result = run_project(project, run_dir, index, args.timeout_sec)
        results.append(result)
        print(f"{result.status.upper():7} {result.id} ({result.seconds}s)" + (f" — {result.reason}" if result.reason else ""))
        if result.status in {"fail", "timeout", "error"}:
            tail = tail_text(run_dir / result.log)
            if tail:
                print("  | " + "\n  | ".join(tail.splitlines()[-12:]))

    merged, merge_reason = merge_reports(run_dir)
    if merged:
        print(f"Merged report: {run_dir / merge_reason}")
    else:
        print(f"Merged report unavailable: {merge_reason}")
    write_portfolio_index(run_dir, results, merge_reason)
    summary = {
        "schema_version": "solo-empire.playwright-portfolio.v1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strict": bool(args.strict),
        "manifest": relative_path(args.manifest),
        "run_dir": run_dir.relative_to(output_root).as_posix(),
        "merged_report": "merged-report" if merged else None,
        "results": [asdict(result) for result in results],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output_root / "latest.json").write_text(
        json.dumps({"run_id": run_id, "run_dir": run_dir.relative_to(output_root).as_posix()}, indent=2) + "\n",
        encoding="utf-8",
    )
    print_summary(results, merge_reason)
    code = result_exit_code(results, args.strict)
    if not merged:
        code = 1
    if args.json:
        print(json.dumps(summary, indent=2))
    return code


def command_show(args: argparse.Namespace) -> int:
    output_root = Path(args.output_dir).resolve()
    latest = read_json(output_root / "latest.json")
    if latest is None or not latest.get("run_dir"):
        print("No Playwright portfolio run found. Run test:playwright first.", file=sys.stderr)
        return 1
    run_dir = output_root / str(latest["run_dir"])
    report_dir = run_dir / "merged-report"
    if not (report_dir / "index.html").is_file():
        print(f"Merged Playwright report missing: {report_dir}", file=sys.stderr)
        print(f"Portfolio index: {run_dir / 'index.html'}", file=sys.stderr)
        return 1
    cli = INFRA_ROOT / "node_modules" / ".bin" / "playwright"
    if not cli.is_file() and not cli.is_symlink():
        print(f"Central Playwright CLI missing: {cli}", file=sys.stderr)
        return 1
    print(f"Opening Playwright HTML report at http://{args.host}:{args.port}")
    return subprocess.run(
        [str(cli), "show-report", str(report_dir), "--host", args.host, "--port", str(args.port)],
        cwd=str(REPO_ROOT),
        check=False,
    ).returncode


def command_ui(args: argparse.Namespace) -> int:
    data = load_manifest(args.manifest)
    projects = discover_projects(data)
    selected = {item.strip() for item in args.only.split(",") if item.strip()}
    if len(selected) != 1:
        print("Playwright UI mode requires exactly one project; pass --only <project-id>.", file=sys.stderr)
        return 2

    known = {project.id for project in projects}
    unknown = selected - known
    if unknown:
        print(f"Unknown Playwright project(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    project = next(project for project in projects if project.id in selected)
    reason = readiness(project)
    if reason:
        print(f"Project {project.id} is not ready: {reason}", file=sys.stderr)
        return 1

    ui_host = args.ui_host or args.host
    ui_port = args.ui_port or args.port
    command = build_ui_command(project, ui_host, ui_port)
    print(f"Opening Playwright UI mode for {project.id} at http://{ui_host}:{ui_port}")
    print("Use Ctrl+C to stop UI mode.")
    return subprocess.run(
        command,
        cwd=str(resolve_project_path(project)),
        env=os.environ.copy(),
        check=False,
    ).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Central Playwright portfolio orchestrator")
    parser.add_argument("command", choices=("list", "run", "ui", "report", "show"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--only", default="", help="comma-separated project ids")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="fail when a discovered project is not runnable")
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9323)
    parser.add_argument("--ui-host", default=None, help="UI mode host; defaults to --host")
    parser.add_argument("--ui-port", type=int, default=None, help="UI mode port; defaults to --port")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list":
        return command_list(args)
    if args.command in {"report", "show"}:
        return command_show(args)
    if args.command == "ui":
        return command_ui(args)
    return command_run(args)


if __name__ == "__main__":
    sys.exit(main())
