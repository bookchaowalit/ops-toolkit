#!/usr/bin/env python3
"""Collect read-only product facts for every nested Book Dev repository.

Feeds Product Card writing and value triage: stack, size, activity, tests,
deploy configuration, README presence, and a structure signature that groups
template clones. Never reads secret files and strips credentials from remote URLs.

usage: python3 repo_facts.py [--root <dir>] > facts.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_override = os.environ.get("SOLO_BOOK_DEV_GITHUB_ROOT")
DEFAULT_ROOT = Path(_override).expanduser() if _override else (
    PROJECT_ROOT.parents[2] if PROJECT_ROOT.parent.name in {"private", "public"} else PROJECT_ROOT / "book-dev-github-unavailable"
)
SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build", ".venv", "venv", "__pycache__", ".expo", "ios", "android", "coverage", ".turbo", "DOCPACK"}
SOURCE_EXT = {".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".dart", ".cs", ".vue", ".svelte", ".kt", ".swift"}
DEPLOY_FILES = ["vercel.json", "railway.toml", "railway.json", "render.yaml", "Dockerfile", "docker-compose.yml", "fly.toml", "netlify.toml", "app.json", "eas.json"]
STACK_MARKERS = {
    "next": "Next.js", "react-native": "React Native", "expo": "Expo", "vite": "Vite", "react": "React",
    "@nestjs/core": "NestJS", "express": "Express", "vue": "Vue", "svelte": "Svelte", "electron": "Electron",
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask",
}


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def walk(repo: Path):
    stack = [repo]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name not in SKIP_DIRS and not e.is_symlink():
                    stack.append(e)
            elif e.is_file() and not e.name.startswith(".env"):
                yield e


def facts(repo: Path, root: Path) -> dict:
    files = list(walk(repo))
    rel = [f.relative_to(repo).as_posix() for f in files]
    src = [f for f in files if f.suffix in SOURCE_EXT]
    tests = [r for r in rel if re.search(r"(^|/)(tests?|__tests__)/|\.(test|spec)\.\w+$|_test\.(py|go)$", r)]
    stack: set[str] = set()
    homepage = None
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            stack |= {label for key, label in STACK_MARKERS.items() if key in deps}
            homepage = data.get("homepage")
        except (ValueError, OSError):
            stack.add("Node (unparsed package.json)")
    for name, label in [("pyproject.toml", "Python"), ("requirements.txt", "Python"), ("go.mod", "Go"), ("Cargo.toml", "Rust"), ("pubspec.yaml", "Flutter")]:
        if (repo / name).is_file():
            stack.add(label)
            text = (repo / name).read_text(encoding="utf-8", errors="ignore").lower()
            stack |= {label2 for key, label2 in STACK_MARKERS.items() if key in ("fastapi", "django", "flask") and key in text}
    if any(r.startswith("Assets/") and r.endswith(".unity") for r in rel):
        stack.add("Unity")
    readme = next((f for f in files if f.parent == repo and f.name.lower().startswith("readme")), None)
    readme_text = readme.read_text(encoding="utf-8", errors="ignore") if readme else ""
    urls = sorted(set(re.findall(r"https?://[\w.-]+\.(?:vercel\.app|up\.railway\.app|onrender\.com|netlify\.app|bookchaowalit\.com)[\w/.-]*", readme_text)))
    remote = re.sub(r"//[^@/]+@", "//", git(repo, "remote", "get-url", "origin"))
    top = sorted(p.name for p in repo.iterdir() if p.name not in {".git", "node_modules"})
    signature = hashlib.sha1(("|".join(top) + "|" + "|".join(sorted(stack))).encode()).hexdigest()[:10]
    return {
        "path": repo.relative_to(PROJECT_ROOT).as_posix(),
        "group": repo.relative_to(root).parts[0] + "/" + repo.relative_to(root).parts[1],
        "name": repo.name,
        "remote": remote,
        "branch": git(repo, "branch", "--show-current"),
        "dirty": bool(git(repo, "status", "--porcelain")),
        "last_commit": git(repo, "log", "-1", "--format=%cs"),
        "first_commit": (git(repo, "log", "--reverse", "--format=%cs").splitlines() or [""])[0],
        "commits": int(git(repo, "rev-list", "--count", "HEAD") or 0),
        "stack": sorted(stack),
        "source_files": len(src),
        "source_lines": sum(sum(1 for _ in f.open(encoding="utf-8", errors="ignore")) for f in src[:4000]),
        "test_files": len(tests),
        "ci_workflows": len([r for r in rel if r.startswith(".github/workflows/")]),
        "deploy_files": [d for d in DEPLOY_FILES if (repo / d).is_file()],
        "readme_lines": len(readme_text.splitlines()),
        "readme_title": next((l.lstrip("# ").strip() for l in readme_text.splitlines() if l.startswith("#")), ""),
        "live_urls": urls + ([homepage] if homepage and homepage.startswith("http") else []),
        "has_docpack": (repo / "DOCPACK").is_dir(),
        "structure_signature": signature,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()
    root = args.root.resolve()
    repos = sorted(p.parent for p in root.glob("*/*/**/.git") if p.is_dir())
    rows = [facts(r, root) for r in repos]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["structure_signature"]] = counts.get(r["structure_signature"], 0) + 1
    for r in rows:
        r["template_family_size"] = counts[r["structure_signature"]]
    print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
