#!/usr/bin/env python3
"""Run a command with the repository's preferred Python 3.10+ interpreter."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PYTHON_CHECK = "import sys; assert sys.version_info >= (3, 10)"


def _candidate_paths() -> list[str]:
    candidates: list[str] = []
    override = os.environ.get("SOLO_EMPIRE_PYTHON3")
    if override:
        candidates.append(override)

    if os.name == "nt":
        candidates.append(str(ROOT / ".venv" / "Scripts" / "python.exe"))
    else:
        candidates.extend(
            [
                str(ROOT / ".venv" / "bin" / "python"),
                "/usr/bin/python3",
                "/usr/local/bin/python3",
            ]
        )

    path_python = shutil.which("python3")
    if path_python:
        candidates.append(path_python)
    candidates.append(sys.executable)
    return candidates


def _resolve_executable(candidate: str) -> str | None:
    if "windowsapps" in candidate.lower():
        return None
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path)
    return shutil.which(candidate)


def _is_usable(candidate: str) -> bool:
    try:
        result = subprocess.run(
            [candidate, "-c", PYTHON_CHECK],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


def find_python3() -> str | None:
    checked: set[str] = set()
    for candidate in _candidate_paths():
        executable = _resolve_executable(candidate)
        if executable is None or executable in checked:
            continue
        checked.add(executable)
        if _is_usable(executable):
            return executable
    return None


def main(argv: list[str]) -> int:
    if not argv:
        print(f"Usage: {Path(sys.argv[0]).name} <script-or-args...>", file=sys.stderr)
        return 2

    python = find_python3()
    if python is None:
        print("ERROR: Could not find a real Python 3.10+ interpreter.", file=sys.stderr)
        print("Install Python 3.10+ or set SOLO_EMPIRE_PYTHON3.", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.call([python, *argv], env=env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
