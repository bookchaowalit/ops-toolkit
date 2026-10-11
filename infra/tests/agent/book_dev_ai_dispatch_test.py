#!/usr/bin/env python3
"""Offline tests for Book Dev AI dispatch (Codex/Grok/Claude Code)."""

from __future__ import annotations

import json
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
)


def sample_ticket(tmp: Path) -> Path:
    selected = {
        "task": {
            "id": 2135,
            "title": "[Book Dev] owner/repo — review dirty changes",
            "status": "todo",
            "priority": 2,
        },
        "repository": {
            "path": "projects/product/engineering/book-dev/github/owner/repo",
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

    print("Book Dev AI dispatch tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
