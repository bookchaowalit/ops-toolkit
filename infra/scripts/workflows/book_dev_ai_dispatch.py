#!/usr/bin/env python3
"""Dispatch a Book Dev execution ticket to Codex, Grok, and/or Claude Code.

Safety defaults:
    - never pass --always-approve / danger bypass flags
    - prompt requires scoped commit, push, documented deploy, and post-deploy
        verification after implementation checks; protected actions remain gated
  - provider sandboxes stay bounded (codex workspace-write / grok acceptEdits /
    claude acceptEdits with an explicit tool allowlist and repository hooks)
  - logs are written under data/runtime only

Usage:
    python3 book_dev_ai_dispatch.py --dry-run --providers grok,codex
    python3 book_dev_ai_dispatch.py --ticket data/runtime/agent-loop/book-dev/execution/current.json
    python3 book_dev_ai_dispatch.py --providers grok --mode plan --max-turns 4
    python3 book_dev_ai_dispatch.py --providers claude --mode plan
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKFLOW_DIR = Path(__file__).resolve().parent
UTILS_DIR = WORKFLOW_DIR.parent / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))

from _env import PROJECT_ROOT  # noqa: E402

CONTRACT = "solo-empire.book-dev-engineering-loop-dispatch.v1"
DEFAULT_TICKET = (
    PROJECT_ROOT / "data" / "runtime" / "agent-loop" / "book-dev" / "execution" / "current.json"
)
DEFAULT_PROVIDERS = ("grok", "codex")
SUPPORTED_PROVIDERS = {"grok", "codex", "claude"}
# Claude Code tools for headless implementation. The repository PreToolUse
# hook still denies secrets and destructive or production-capable commands.
CLAUDE_IMPLEMENT_TOOLS = "Read,Grep,Glob,Edit,Write,Bash,PowerShell"
CLAUDE_PLAN_TOOLS = "Read,Grep,Glob"
MODES = {"plan", "implement"}


def _clean(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit].rstrip()


def _path_label(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_ticket(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"missing execution ticket: {_path_label(path)}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("execution ticket must be a JSON object")
    return data


def resolve_codex_cmd() -> list[str] | None:
    codex = shutil.which("codex")
    if codex:
        return [codex]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "@openai/codex"]
    return None


def resolve_grok_cmd() -> list[str] | None:
    grok = shutil.which("grok")
    return [grok] if grok else None


def resolve_claude_cmd() -> list[str] | None:
    claude = shutil.which("claude")
    return [claude] if claude else None


def provider_availability() -> dict[str, dict[str, Any]]:
    codex = resolve_codex_cmd()
    grok = resolve_grok_cmd()
    claude = resolve_claude_cmd()
    return {
        "claude": {
            "available": bool(claude),
            "command": claude,
        },
        "grok": {
            "available": bool(grok),
            "command": grok,
        },
        "codex": {
            "available": bool(codex),
            "command": codex,
        },
    }


def parse_providers(raw: str) -> list[str]:
    items = [part.strip().lower() for part in (raw or "").split(",") if part.strip()]
    if not items:
        raise ValueError("providers must not be empty")
    unknown = [item for item in items if item not in SUPPORTED_PROVIDERS]
    if unknown:
        raise ValueError(f"unsupported providers: {', '.join(unknown)}")
    # preserve order, unique
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            ordered.append(item)
            seen.add(item)
    return ordered


def build_prompt(ticket: dict[str, Any], *, mode: str) -> str:
    selected = ticket.get("selected") or {}
    task = selected.get("task") or {}
    repo = selected.get("repository") or {}
    task_id = task.get("id")
    task_label = f"#{task_id}" if task_id is not None else "synthetic"
    markdown = (ticket.get("outputs") or {}).get("markdown") or (
        "data/runtime/agent-loop/book-dev/execution/current.md"
    )
    startup = ticket.get("agent_startup_prompt") or ticket.get("agent_prompt") or ""
    mode_line = (
        "Mode: PLAN ONLY. Inspect evidence, produce a disposition and verification plan. "
        "Do not edit files."
        if mode == "plan"
        else "Mode: IMPLEMENT. You may edit files inside the selected nested repository only."
    )
    return f"""You are the Book Dev engineering-loop dispatcher target.

{mode_line}

Hard stop rules (non-negotiable):
- Work on exactly one candidate from the execution ticket.
- Read skills/book-dev-engineering-loop/SKILL.md first.
- Read {markdown}.
- Stay inside the selected nested repository only.
- Preserve unrelated dirty work. Never git reset/clean.
- After verification, follow the automatic release closeout in the skill:
    commit only scoped files, push the configured upstream, deploy only through
    the repository's documented target, and run its post-deploy check.
- Never merge, tag, archive, transfer, delete, force-push, open PRs, spend
    money, send external messages, or change production without the required
    approval. Do not guess a deployment target or command.
- Stop and report when verification or a required release gate is blocked.

Ticket status: {ticket.get('status')}
Task: {task_label} {_clean(task.get('title'), 200)}
Repository: {repo.get('path')}
Source: {selected.get('source') or 'open_task'}
Recommendation: {_clean(selected.get('recommendation'), 300)}

Startup brief:
{startup}

Return a compact report with:
1) what you inspected
2) files changed (if any) and why
3) verification commands + results
4) remaining blockers
5) whether owner approval is required next
"""


def write_prompt_file(prompt: str, dispatch_dir: Path) -> Path:
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    path = dispatch_dir / "prompt.md"
    path.write_text(prompt, encoding="utf-8")
    return path


def build_provider_command(
    provider: str,
    *,
    prompt_file: Path,
    mode: str,
    max_turns: int,
    cwd: Path,
    last_message_path: Path,
) -> list[str]:
    if provider == "grok":
        cmd = resolve_grok_cmd()
        if not cmd:
            raise RuntimeError("grok CLI not found on PATH")
        permission = "plan" if mode == "plan" else "acceptEdits"
        # Multi-turn headless via prompt-file + max-turns. Avoid always-approve.
        return [
            *cmd,
            "--cwd",
            str(cwd),
            "--prompt-file",
            str(prompt_file),
            "--permission-mode",
            permission,
            "--max-turns",
            str(max_turns),
            "--output-format",
            "plain",
            "--no-subagents",
        ]

    if provider == "codex":
        cmd = resolve_codex_cmd()
        if not cmd:
            raise RuntimeError("codex CLI not found (install CLI or ensure npx is available)")
        sandbox = "read-only" if mode == "plan" else "workspace-write"
        # Feed prompt via stdin for portability across codex versions.
        return [
            *cmd,
            "exec",
            "--cd",
            str(cwd),
            "--sandbox",
            sandbox,
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            str(last_message_path),
            "-",  # read prompt from stdin
        ]

    if provider == "claude":
        cmd = resolve_claude_cmd()
        if not cmd:
            raise RuntimeError("claude CLI not found on PATH")
        # Headless print mode; prompt arrives on stdin, without bypass flags.
        # --tools restricts availability; --allowedTools alone only grants
        # permissions. No permission host or ambient MCP servers may expand it.
        tools = CLAUDE_PLAN_TOOLS if mode == "plan" else CLAUDE_IMPLEMENT_TOOLS
        return [
            *cmd,
            "-p",
            "--permission-mode",
            "plan" if mode == "plan" else "acceptEdits",
            "--permission-prompts",
            "none",
            "--tools",
            tools,
            "--allowedTools",
            tools,
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--max-turns",
            str(max_turns),
            "--output-format",
            "text",
        ]

    raise ValueError(f"unsupported provider: {provider}")


def run_provider(
    provider: str,
    *,
    prompt: str,
    prompt_file: Path,
    mode: str,
    max_turns: int,
    timeout_sec: int,
    dry_run: bool,
    dispatch_dir: Path,
) -> dict[str, Any]:
    log_path = dispatch_dir / f"{provider}.log"
    last_message_path = dispatch_dir / f"{provider}.last-message.md"
    result: dict[str, Any] = {
        "provider": provider,
        "status": "pending",
        "command": None,
        "exit_code": None,
        "log": _path_label(log_path),
        "last_message": _path_label(last_message_path),
        "error": None,
    }

    try:
        command = build_provider_command(
            provider,
            prompt_file=prompt_file,
            mode=mode,
            max_turns=max_turns,
            cwd=PROJECT_ROOT,
            last_message_path=last_message_path,
        )
    except RuntimeError as exc:
        result["status"] = "unavailable"
        result["error"] = str(exc)
        return result

    result["command"] = command
    if dry_run:
        result["status"] = "dry_run"
        log_path.write_text(
            "DRY RUN\n" + " ".join(command) + "\n",
            encoding="utf-8",
        )
        return result

    stdin_data = prompt if provider in {"codex", "claude"} else None
    try:
        completed = subprocess.run(
            command,
            input=stdin_data,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        log_path.write_text(
            f"TIMEOUT after {timeout_sec}s\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}\n",
            encoding="utf-8",
        )
        result["status"] = "timeout"
        result["error"] = f"timeout after {timeout_sec}s"
        return result
    except OSError as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        return result

    log_path.write_text(
        "COMMAND:\n"
        + " ".join(command)
        + f"\n\nEXIT: {completed.returncode}\n\nSTDOUT:\n{completed.stdout}\n\nSTDERR:\n{completed.stderr}\n",
        encoding="utf-8",
    )
    if provider == "claude" and completed.stdout:
        # Print mode writes the final message to stdout.
        last_message_path.write_text(completed.stdout, encoding="utf-8")
    result["exit_code"] = completed.returncode
    if completed.returncode == 0:
        result["status"] = "dispatched"
    else:
        result["status"] = "failed"
        result["error"] = _clean(completed.stderr or completed.stdout, 400)
    return result


def update_ticket_dispatch(
    ticket_path: Path,
    ticket: dict[str, Any],
    dispatch_report: dict[str, Any],
) -> None:
    """Persist dispatch metadata back onto the execution ticket when possible."""

    ticket = dict(ticket)
    ticket["dispatch"] = {
        "contract": CONTRACT,
        "generated_at": dispatch_report.get("generated_at"),
        "mode": dispatch_report.get("mode"),
        "providers": dispatch_report.get("providers"),
        "overall_status": dispatch_report.get("status"),
        "results": dispatch_report.get("results"),
        "report": dispatch_report.get("outputs", {}).get("report"),
    }
    if dispatch_report.get("status") in {"dispatched", "partial"}:
        ticket["status"] = "dispatched"
    elif dispatch_report.get("status") == "dry_run":
        ticket.setdefault("status", ticket.get("status") or "queued")
    ticket["hook"] = {
        "configured": True,
        "invoked": True,
        "status": dispatch_report.get("status"),
        "env_var": "book_dev_ai_dispatch",
    }
    ticket_path.write_text(json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dispatch(
    *,
    ticket_path: Path = DEFAULT_TICKET,
    providers: list[str] | None = None,
    mode: str = "implement",
    max_turns: int = 24,
    timeout_sec: int = 1800,
    dry_run: bool = False,
    write_ticket: bool = True,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}")
    if max_turns < 1 or max_turns > 200:
        raise ValueError("max_turns must be between 1 and 200")
    if timeout_sec < 30 or timeout_sec > 7200:
        raise ValueError("timeout_sec must be between 30 and 7200")

    selected_providers = providers or list(DEFAULT_PROVIDERS)
    ticket = load_ticket(ticket_path)
    if not ticket.get("selected"):
        raise ValueError("execution ticket has no selected candidate to dispatch")

    stamp = _now_stamp()
    dispatch_dir = ticket_path.parent / "dispatch" / stamp
    prompt = build_prompt(ticket, mode=mode)
    prompt_file = write_prompt_file(prompt, dispatch_dir)

    results: list[dict[str, Any]] = []
    for provider in selected_providers:
        results.append(
            run_provider(
                provider,
                prompt=prompt,
                prompt_file=prompt_file,
                mode=mode,
                max_turns=max_turns,
                timeout_sec=timeout_sec,
                dry_run=dry_run,
                dispatch_dir=dispatch_dir,
            )
        )

    statuses = {item["status"] for item in results}
    if dry_run and statuses <= {"dry_run", "unavailable"}:
        overall = "dry_run" if "dry_run" in statuses else "unavailable"
    elif "dispatched" in statuses and len(statuses) == 1:
        overall = "dispatched"
    elif "dispatched" in statuses:
        overall = "partial"
    elif statuses <= {"unavailable"}:
        overall = "unavailable"
    elif "timeout" in statuses:
        overall = "timeout"
    else:
        overall = "failed"

    report = {
        "contract": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "providers": selected_providers,
        "status": overall,
        "ticket": _path_label(ticket_path),
        "availability": provider_availability(),
        "policy": {
            "automatic_release_closeout": True,
            "automatic_merge": False,
            "danger_bypass": False,
            "always_approve": False,
            "max_turns": max_turns,
            "timeout_sec": timeout_sec,
        },
        "results": results,
        "outputs": {
            "dir": _path_label(dispatch_dir),
            "prompt": _path_label(prompt_file),
            "report": _path_label(dispatch_dir / "report.json"),
        },
        "next_action": (
            "Review provider logs under the dispatch directory and inspect the nested-repo "
            "commit/push/deploy evidence; stop on a protected release gate."
        ),
    }
    (dispatch_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if write_ticket and not dry_run:
        update_ticket_dispatch(ticket_path, ticket, report)
    elif write_ticket and dry_run:
        # Keep ticket status, but record the dry-run metadata.
        update_ticket_dispatch(ticket_path, ticket, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispatch Book Dev ticket to Codex, Grok, and/or Claude Code")
    parser.add_argument("--ticket", type=Path, default=DEFAULT_TICKET)
    parser.add_argument(
        "--providers",
        default="grok,codex",
        help="Comma-separated providers: grok,codex,claude",
    )
    parser.add_argument("--mode", choices=sorted(MODES), default="implement")
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--no-write-ticket",
        action="store_true",
        help="Do not mutate execution/current.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = dispatch(
            ticket_path=args.ticket,
            providers=parse_providers(args.providers),
            mode=args.mode,
            max_turns=args.max_turns,
            timeout_sec=args.timeout_sec,
            dry_run=args.dry_run,
            write_ticket=not args.no_write_ticket,
        )
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        payload = {"contract": CONTRACT, "status": "error", "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json else None))
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "Book Dev AI dispatch: "
            f"status={report['status']} providers={','.join(report['providers'])} "
            f"dir={report['outputs']['dir']}"
        )
        for item in report["results"]:
            print(
                f"  - {item['provider']}: {item['status']}"
                + (f" ({item['error']})" if item.get("error") else "")
            )
    return 0 if report.get("status") in {"dispatched", "partial", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
