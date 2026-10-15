#!/usr/bin/env python3
"""Offline tests for Book Dev AI dispatch (Codex/Grok/Claude Code)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "workflows"))
from book_dev_ai_dispatch import (  # noqa: E402
    build_prompt,
    build_provider_command,
    dispatch,
    parse_providers,
    provider_availability,
    resolve_claude_cmd,
    resolve_repo_dir,
    run_provider,
)


def sample_ticket(tmp: Path) -> Path:
    (tmp / "repo" / ".git").mkdir(parents=True)
    selected = {
        "task": {
            "id": 2135,
            "title": "[Book Dev] owner/repo — review dirty changes",
            "status": "todo",
            "priority": 2,
        },
        "repository": {
            "path": str(tmp / "repo"),
            "state": "dirty",
            "dirty_file_count": 3,
        },
        "source": "open_task",
        "recommendation": "Inspect dirty diff first.",
        "agent_prompt": "Work on one task.",
    }
    ticket = {
        "contract": "solo-empire.book-dev-engineering-loop-execution.v1",
        "status": "queued",
        "selected": selected,
        "agent_startup_prompt": "startup",
        "outputs": {
            "json": str(tmp / "current.json"),
            "markdown": str(tmp / "current.md"),
        },
    }
    path = tmp / "current.json"
    path.write_text(json.dumps(ticket), encoding="utf-8")
    (tmp / "current.md").write_text("# ticket\n", encoding="utf-8")
    return path


def main() -> int:
    assert parse_providers("grok,codex") == ["grok", "codex"]
    assert parse_providers("codex,codex,grok") == ["codex", "grok"]
    assert parse_providers("claude,codex") == ["claude", "codex"]
    try:
        parse_providers("gemini")
        raise AssertionError("expected unknown provider error")
    except ValueError:
        pass

    with tempfile.TemporaryDirectory() as temp_dir:
        tmp = Path(temp_dir)
        ticket_path = sample_ticket(tmp)
        ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
        prompt = build_prompt(ticket, mode="implement")
        assert "automatic release closeout" in prompt
        assert "push the configured upstream" in prompt
        assert "owner/repo" in prompt

        # Dry-run should not require live provider success.
        report = dispatch(
            ticket_path=ticket_path,
            providers=["grok", "codex"],
            mode="plan",
            max_turns=2,
            timeout_sec=60,
            dry_run=True,
            write_ticket=True,
        )
        assert report["contract"] == "solo-empire.book-dev-engineering-loop-dispatch.v1"
        assert report["status"] in {"dry_run", "unavailable"}
        assert report["policy"]["automatic_release_closeout"] is True
        assert (tmp / "dispatch").is_dir()
        avail = provider_availability()
        assert "grok" in avail and "codex" in avail and "claude" in avail

        with mock.patch("book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]):
            for mode, permission in (("plan", "plan"), ("implement", "acceptEdits")):
                command = build_provider_command(
                    "claude",
                    prompt_file=tmp / "prompt.md",
                    mode=mode,
                    max_turns=3,
                    cwd=tmp,
                    last_message_path=tmp / "claude.last-message.md",
                )
                assert command[:2] == ["claude", "-p"]
                assert command[command.index("--permission-mode") + 1] == permission
                assert "--dangerously-skip-permissions" not in command
                tools = command[command.index("--allowedTools") + 1].split(",")
                assert "--tools" in command, "--allowedTools only grants permissions; it does not restrict availability"
                assert command[command.index("--tools") + 1].split(",") == tools
                assert command[command.index("--permission-prompts") + 1] == "none"
                assert "--strict-mcp-config" in command
                assert json.loads(command[command.index("--mcp-config") + 1]) == {"mcpServers": {}}
                if mode == "plan":
                    assert "Edit" not in tools and "Bash" not in tools
                else:
                    assert "Edit" in tools and "Bash" in tools

        # Force unavailable path with mocked resolvers.
        with mock.patch("book_dev_ai_dispatch.resolve_grok_cmd", return_value=None), mock.patch(
            "book_dev_ai_dispatch.resolve_codex_cmd", return_value=None
        ):
            unavailable = dispatch(
                ticket_path=ticket_path,
                providers=["grok", "codex"],
                mode="plan",
                dry_run=False,
                write_ticket=False,
            )
            assert unavailable["status"] == "unavailable"
            assert all(item["status"] == "unavailable" for item in unavailable["results"])

        # The selected repository must be a real checkout; providers run inside it.
        assert resolve_repo_dir(ticket) == (tmp / "repo").resolve()
        try:
            resolve_repo_dir({"selected": {"repository": {"path": str(tmp / "missing")}}})
            raise AssertionError("expected missing checkout error")
        except ValueError:
            pass
        assert str(tmp / "current.md") in build_prompt(ticket, mode="plan")

        with mock.patch("book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]):
            command = build_provider_command(
                "claude",
                prompt_file=tmp / "prompt.md",
                mode="implement",
                max_turns=3,
                cwd=tmp,
                last_message_path=tmp / "claude.last-message.md",
                read_dirs=(tmp / "exec",),
            )
            assert command[command.index("--add-dir") + 1] == str(tmp / "exec")
            completed = mock.Mock(returncode=0, stdout="done", stderr="")
            (tmp / "dispatch" / "x").mkdir(parents=True)
            with mock.patch("book_dev_ai_dispatch.subprocess.run", return_value=completed) as run:
                result = run_provider(
                    "claude",
                    prompt="p",
                    prompt_file=tmp / "prompt.md",
                    mode="implement",
                    max_turns=3,
                    timeout_sec=60,
                    dry_run=False,
                    dispatch_dir=tmp / "dispatch" / "x",
                    cwd=tmp / "repo",
                )
            assert result["status"] == "dispatched"
            assert run.call_args.kwargs["cwd"] == str(tmp / "repo")

    # Claude CLI lookup: explicit override, then the desktop app's newest build.
    with tempfile.TemporaryDirectory() as temp_dir:
        home = Path(temp_dir)
        builds = home / ".claude" / "remote" / "ccd-cli"
        builds.mkdir(parents=True)
        for name in ("2.1.9-aaa", "2.1.295-bbb", "notes.txt"):
            (builds / name).write_text("#!/bin/sh\n")
            (builds / name).chmod(0o755)
        env = {key: value for key, value in os.environ.items() if key != "BOOK_DEV_CLAUDE_CLI"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "book_dev_ai_dispatch.shutil.which", return_value=None
        ), mock.patch("book_dev_ai_dispatch.Path.home", return_value=home):
            assert resolve_claude_cmd() == [str(builds / "2.1.295-bbb")]
            os.environ["BOOK_DEV_CLAUDE_CLI"] = str(builds / "2.1.9-aaa")
            assert resolve_claude_cmd() == [str(builds / "2.1.9-aaa")]
            os.environ["BOOK_DEV_CLAUDE_CLI"] = str(home / "missing")
            assert resolve_claude_cmd() is None

    print("Book Dev AI dispatch tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
