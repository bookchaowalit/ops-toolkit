#!/usr/bin/env python3
"""Audit and verify nested mobile repositories before a production release.

The default audit is local and read-only. The check command executes only
already-installed project tools and never installs dependencies. The emulator
command requires explicit local artifact, package, flow, and AVD arguments.
It never deploys, submits, publishes, or reads credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[3]
def _github_root() -> Path:
    """`.../book-dev/github` holding the clones: SOLO_BOOK_DEV_GITHUB_ROOT, or derived from the clone layout."""
    override = os.environ.get("SOLO_BOOK_DEV_GITHUB_ROOT")
    if override:
        return Path(override).expanduser()
    if ROOT.parent.name in {"private", "public"}:
        return ROOT.parents[2]
    return ROOT / "book-dev-github-unavailable"


DEFAULT_MOBILE_ROOT = _github_root() / "bookchaowalit-mobile/public/book-apps/mobile"
DEFAULT_AVD_HOME = Path.home() / ".local" / "share" / "android-avd"
SKIP_DIRECTORIES = {
    ".dart_tool",
    ".git",
    ".gradle",
    ".idea",
    ".next",
    ".turbo",
    "Pods",
    "build",
    "coverage",
    "dist",
    "node_modules",
}
LOCKFILES = (
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "gradle.lockfile",
    "pubspec.lock",
    "Package.resolved",
    "packages.lock.json",
)
TEST_DIRECTORY_NAMES = {"__tests__", "androidTest", "e2e", "integration_test", "test", "tests"}
SOURCE_SUFFIXES = {".cs", ".dart", ".java", ".js", ".jsx", ".kt", ".swift", ".ts", ".tsx"}
TEST_FILE_PATTERNS = (
    re.compile(r"(?:^|[._-])(test|spec)[._-]", re.IGNORECASE),
    re.compile(r"Tests?\.(cs|java|kt|swift)$", re.IGNORECASE),
    re.compile(r"_test\.dart$", re.IGNORECASE),
)
ESLINT_CONFIGS = (
    ".eslintrc",
    ".eslintrc.cjs",
    ".eslintrc.js",
    ".eslintrc.json",
    ".eslintrc.yaml",
    ".eslintrc.yml",
    "eslint.config.cjs",
    "eslint.config.js",
    "eslint.config.mjs",
)


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, error.__class__.__name__
    if not isinstance(value, dict):
        return None, "expected_object"
    return value, None


def _iter_files(root: Path, max_depth: int = 8) -> Iterable[Path]:
    if not root.is_dir():
        return
    root_depth = len(root.parts)
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.parts) - root_depth
        directories[:] = [
            name for name in directories if name not in SKIP_DIRECTORIES and depth < max_depth
        ]
        for name in files:
            yield current_path / name


def _is_test_file(path: Path, app: Path) -> bool:
    relative_parts = path.relative_to(app).parts
    if any(part in TEST_DIRECTORY_NAMES for part in relative_parts[:-1]):
        return path.suffix.lower() in SOURCE_SUFFIXES
    return any(pattern.search(path.name) for pattern in TEST_FILE_PATTERNS)


def _workflow_text(app: Path) -> tuple[int, str]:
    workflow_dir = app / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return 0, ""
    workflows = sorted(
        path for path in workflow_dir.iterdir() if path.suffix.lower() in {".yaml", ".yml"}
    )
    chunks: list[str] = []
    for workflow in workflows:
        try:
            chunks.append(workflow.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return len(workflows), "\n".join(chunks)


def _classify(app: Path, package: dict[str, Any] | None) -> str:
    dependencies: dict[str, Any] = {}
    if package:
        for field in ("dependencies", "devDependencies"):
            value = package.get(field)
            if isinstance(value, dict):
                dependencies.update(value)
    if "expo" in dependencies:
        return "expo"
    if "react-native" in dependencies:
        return "react-native"
    if "@nativescript/core" in dependencies:
        return "nativescript"
    if "vite" in dependencies:
        return "vite-web"
    if (app / "pubspec.yaml").is_file():
        return "flutter"
    if (app / "build.gradle").is_file() or (app / "build.gradle.kts").is_file():
        return "android-native"
    if (app / "Package.swift").is_file():
        return "swift"
    if any(app.glob("*.csproj")):
        return "dotnet-maui"
    return "unknown"


def _expo_asset_references(app: Path, app_config: dict[str, Any] | None) -> list[str]:
    if not app_config:
        return []
    expo = app_config.get("expo", app_config)
    if not isinstance(expo, dict):
        return []
    values = {
        "icon": expo.get("icon"),
        "splash.image": (expo.get("splash") or {}).get("image")
        if isinstance(expo.get("splash"), dict)
        else None,
        "android.adaptiveIcon.foregroundImage": (
            ((expo.get("android") or {}).get("adaptiveIcon") or {}).get("foregroundImage")
            if isinstance(expo.get("android"), dict)
            and isinstance((expo.get("android") or {}).get("adaptiveIcon"), dict)
            else None
        ),
    }
    missing: list[str] = []
    for field, value in values.items():
        if isinstance(value, str) and value and not (app / value).resolve().is_file():
            missing.append(f"missing Expo asset {field}: {value}")
    return missing


def _package_scripts(package: dict[str, Any] | None) -> dict[str, str]:
    if not package or not isinstance(package.get("scripts"), dict):
        return {}
    return {
        str(name): str(command)
        for name, command in package["scripts"].items()
        if isinstance(name, str) and isinstance(command, str)
    }


def audit_app(app: Path) -> dict[str, Any]:
    package, package_error = _read_json(app / "package.json")
    framework = _classify(app, package)
    app_config, app_config_error = _read_json(app / "app.json")
    files = list(_iter_files(app))
    source_files = [path for path in files if path.suffix.lower() in SOURCE_SUFFIXES]
    test_files = [path for path in files if _is_test_file(path, app)]
    workflow_count, workflow_text = _workflow_text(app)
    scripts = _package_scripts(package)
    lockfiles = [name for name in LOCKFILES if (app / name).is_file()]
    blockers: list[str] = []
    warnings: list[str] = []

    if package_error:
        blockers.append(f"invalid package.json: {package_error}")
    if app_config_error:
        blockers.append(f"invalid app.json: {app_config_error}")
    if framework == "unknown":
        blockers.append("framework could not be classified")
    if len(source_files) <= 1:
        blockers.append("application has one or fewer source files")
    if not test_files:
        blockers.append("no automated test source found")
    if not lockfiles:
        blockers.append("no dependency lockfile found")
    if workflow_count == 0:
        blockers.append("no GitHub Actions workflow found")
    if re.search(r"\|\|\s*(?:true|echo\b)", workflow_text):
        blockers.append("CI workflow suppresses command failures")
    if workflow_text and not re.search(
        r"(?:npm|pnpm|yarn)\s+(?:run\s+)?test|flutter\s+test|swift\s+test|"
        r"dotnet\s+test|gradlew[^\n]*(?:test|connectedCheck)",
        workflow_text,
        re.IGNORECASE,
    ):
        blockers.append("CI workflow does not run automated tests")
    if "lint" in scripts and not any((app / name).is_file() for name in ESLINT_CONFIGS):
        blockers.append("lint script has no repository ESLint configuration")

    if framework == "expo":
        if not app_config:
            blockers.append("Expo app.json is missing")
        if not (app / "eas.json").is_file():
            blockers.append("Expo eas.json is missing")
        expo = app_config.get("expo", app_config) if app_config else {}
        if not isinstance(expo, dict) or not isinstance(expo.get("android"), dict):
            blockers.append("Expo Android configuration is missing")
        else:
            android = expo["android"]
            if not android.get("package"):
                blockers.append("Expo Android package identifier is missing")
        blockers.extend(_expo_asset_references(app, app_config))
    elif framework == "react-native":
        if not (app / "android").is_dir():
            blockers.append("React Native Android project is missing")
        if not (app / "ios").is_dir():
            warnings.append("React Native iOS project is missing")
    elif framework == "flutter":
        if not (app / "android").is_dir():
            blockers.append("Flutter Android project is missing")
        if not (app / "ios").is_dir():
            warnings.append("Flutter iOS project is missing")
    elif framework == "android-native":
        if not (app / "gradlew").is_file():
            blockers.append("Gradle wrapper is missing")
        if not (app / "app" / "src" / "main" / "AndroidManifest.xml").is_file():
            blockers.append("Android manifest is missing")
    elif framework == "swift":
        if not any(app.glob("*.xcodeproj")) and not any(app.glob("*.xcworkspace")):
            blockers.append("Xcode app project/workspace is missing")
    elif framework == "vite-web":
        blockers.append("repository is a web build, not a native mobile release")
    elif framework == "nativescript":
        if not (app / "App_Resources" / "Android").is_dir():
            blockers.append("NativeScript Android App_Resources are missing")
    elif framework == "dotnet-maui":
        if not (app / "Platforms" / "Android").is_dir():
            blockers.append(".NET MAUI Android platform project is missing")

    return {
        "app": app.name,
        "path": str(app.relative_to(ROOT)) if app.is_relative_to(ROOT) else str(app),
        "framework": framework,
        "status": "ready" if not blockers else "blocked",
        "source_file_count": len(source_files),
        "test_file_count": len(test_files),
        "workflow_count": workflow_count,
        "lockfiles": lockfiles,
        "scripts": sorted(scripts),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }


def build_audit_report(mobile_root: Path = DEFAULT_MOBILE_ROOT) -> dict[str, Any]:
    if not mobile_root.is_dir():
        return {
            "contract": "solo-empire.mobile-release-gate.v1",
            "status": "unavailable",
            "mobile_root": str(mobile_root),
            "network": "not-used",
            "credentials": "not-read",
            "summary": {"total": 0, "ready": 0, "blocked": 0, "frameworks": {}},
            "apps": [],
        }
    apps = [audit_app(path) for path in sorted(mobile_root.iterdir()) if path.is_dir()]
    ready = sum(1 for app in apps if app["status"] == "ready")
    return {
        "contract": "solo-empire.mobile-release-gate.v1",
        "status": "ready" if apps and ready == len(apps) else "blocked",
        "mobile_root": str(mobile_root),
        "network": "not-used",
        "credentials": "not-read",
        "summary": {
            "total": len(apps),
            "ready": ready,
            "blocked": len(apps) - ready,
            "frameworks": dict(sorted(Counter(app["framework"] for app in apps).items())),
            "apps_without_tests": sum(1 for app in apps if app["test_file_count"] == 0),
            "apps_without_lockfiles": sum(1 for app in apps if not app["lockfiles"]),
            "apps_with_ci_failure_suppression": sum(
                1
                for app in apps
                if "CI workflow suppresses command failures" in app["blockers"]
            ),
        },
        "apps": apps,
    }


def _tool_command(tool: str, app: Path, args: Sequence[str]) -> list[str] | None:
    local = app / "node_modules" / ".bin" / tool
    if local.is_file() and os.access(local, os.X_OK):
        return [str(local), *args]
    executable = shutil.which(tool)
    return [executable, *args] if executable else None


def _resolve_tool(name: str) -> str | None:
    sdk_roots = [
        Path(value)
        for value in (os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME"))
        if value
    ]
    sdk_roots.extend(
        [
            ROOT / ".android-sdk",
            Path.home() / ".local" / "share" / "android-sdk",
            Path.home() / "Android" / "Sdk",
        ]
    )
    android_relative = {
        "adb": Path("platform-tools/adb"),
        "avdmanager": Path("cmdline-tools/latest/bin/avdmanager"),
        "emulator": Path("emulator/emulator"),
        "sdkmanager": Path("cmdline-tools/latest/bin/sdkmanager"),
    }
    candidates: list[Path] = []
    if name in android_relative:
        candidates.extend(root / android_relative[name] for root in sdk_roots)
    if name in {"dart", "flutter"}:
        candidates.extend(
            [
                ROOT / ".flutter-sdk" / "flutter" / "bin" / name,
                Path.home() / ".local" / "share" / "flutter-sdk" / "flutter" / "bin" / name,
            ]
        )
    if name == "maestro":
        candidates.append(Path.home() / ".local" / "share" / "maestro" / "bin" / "maestro")
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def build_check_commands(app: Path, audit: dict[str, Any]) -> list[tuple[str, list[str] | None]]:
    framework = audit["framework"]
    package, _ = _read_json(app / "package.json")
    scripts = _package_scripts(package)
    commands: list[tuple[str, list[str] | None]] = []

    if framework in {"expo", "react-native", "vite-web", "nativescript"}:
        if "lint" in scripts:
            lint_command = (
                _tool_command("npm", app, ["run", "lint"])
                if any((app / name).is_file() for name in ESLINT_CONFIGS)
                else None
            )
            commands.append(("lint", lint_command))
        tsc = _tool_command("tsc", app, ["--noEmit"])
        if (app / "tsconfig.json").is_file():
            commands.append(("typecheck", tsc))
        if audit["test_file_count"] and "test" in scripts:
            commands.append(("unit-test", _tool_command("npm", app, ["test", "--", "--runInBand"])))
        if framework == "vite-web" and "build" in scripts:
            commands.append(("build", _tool_command("npm", app, ["run", "build"])))
    elif framework == "flutter":
        flutter = _resolve_tool("flutter")
        commands.extend(
            [
                ("analyze", [flutter, "analyze"] if flutter else None),
                ("unit-test", [flutter, "test"] if flutter and audit["test_file_count"] else None),
                ("android-build", [flutter, "build", "apk", "--debug"] if flutter else None),
            ]
        )
    elif framework == "android-native":
        gradle = app / "gradlew"
        command = [str(gradle)] if gradle.is_file() else None
        commands.extend(
            [
                ("lint", [*command, "lint"] if command else None),
                ("unit-test", [*command, "testDebugUnitTest"] if command else None),
                ("android-build", [*command, "assembleDebug"] if command else None),
            ]
        )
    elif framework == "swift":
        swift = _resolve_tool("swift")
        commands.extend(
            [
                ("unit-test", [swift, "test"] if swift and audit["test_file_count"] else None),
                ("release-build", [swift, "build", "-c", "release"] if swift else None),
            ]
        )
    elif framework == "dotnet-maui":
        dotnet = _resolve_tool("dotnet")
        commands.extend(
            [
                ("unit-test", [dotnet, "test"] if dotnet and audit["test_file_count"] else None),
                ("build", [dotnet, "build", "--no-restore"] if dotnet else None),
            ]
        )
    return commands


def _run_command(
    command: Sequence[str],
    cwd: Path,
    timeout: int,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env={**os.environ, "CI": "1", **(extra_env or {})},
        )
        output = completed.stdout or ""
        return {
            "status": "passed" if completed.returncode == 0 else "failed",
            "exit_code": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_tail": output[-6000:],
        }
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return {
            "status": "timeout",
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_tail": output[-6000:],
        }
    except OSError as error:
        return {
            "status": "unavailable",
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_tail": f"{error.__class__.__name__}: {error}",
        }


def run_app_checks(app: Path, timeout: int = 300) -> dict[str, Any]:
    audit = audit_app(app)
    results: list[dict[str, Any]] = []
    for name, command in build_check_commands(app, audit):
        if not command or not command[0]:
            results.append(
                {
                    "name": name,
                    "status": "unavailable",
                    "command": None,
                    "exit_code": None,
                    "duration_seconds": 0,
                    "output_tail": "required local tool or test source is unavailable",
                }
            )
            continue
        result = _run_command(command, app, timeout)
        results.append({"name": name, "command": list(command), **result})
    executable = bool(results) and all(item["status"] == "passed" for item in results)
    return {
        "contract": "solo-empire.mobile-release-check.v1",
        "app": audit,
        "status": "passed" if audit["status"] == "ready" and executable else "blocked",
        "network": "project-command-controlled",
        "credentials": "not-read",
        "checks": results,
    }


def environment_report() -> dict[str, Any]:
    tools = {
        name: _resolve_tool(name)
        for name in (
            "adb",
            "avdmanager",
            "dotnet",
            "emulator",
            "flutter",
            "java",
            "maestro",
            "node",
            "npm",
            "sdkmanager",
            "swift",
            "xcodebuild",
        )
    }
    kvm = Path("/dev/kvm")
    kvm_usable = kvm.exists() and os.access(kvm, os.R_OK | os.W_OK)
    android_emulator_ready = bool(tools["adb"] and tools["emulator"] and kvm_usable)
    maestro_smoke_ready = bool(android_emulator_ready and tools["maestro"])
    return {
        "contract": "solo-empire.mobile-test-environment.v1",
        "status": "smoke-ready" if maestro_smoke_ready else "setup-required",
        "capabilities": {
            "android_emulator": android_emulator_ready,
            "maestro_smoke": maestro_smoke_ready,
            "flutter": bool(tools["flutter"]),
            "ios_simulator": bool(tools["xcodebuild"]),
        },
        "tools": tools,
        "kvm": {
            "present": kvm.exists(),
            "usable": kvm_usable,
        },
        "network": "not-used",
        "credentials": "not-read",
    }


def run_emulator_smoke(
    *,
    avd: str,
    apk: Path,
    package_id: str,
    flow: Path,
    boot_timeout: int,
    test_timeout: int,
) -> dict[str, Any]:
    emulator = _resolve_tool("emulator")
    adb = _resolve_tool("adb")
    maestro = _resolve_tool("maestro")
    missing = [name for name, value in (("emulator", emulator), ("adb", adb), ("maestro", maestro)) if not value]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", package_id):
        missing.append("valid-package-id")
    if missing or not apk.is_file() or not flow.is_file():
        return {
            "contract": "solo-empire.mobile-emulator-smoke.v1",
            "status": "blocked",
            "blockers": [
                *[
                    "invalid Android package ID"
                    if name == "valid-package-id"
                    else f"missing tool: {name}"
                    for name in missing
                ],
                *([] if apk.is_file() else [f"APK not found: {apk}"]),
                *([] if flow.is_file() else [f"Maestro flow not found: {flow}"]),
            ],
            "network": "not-used",
            "credentials": "not-read",
        }

    serial = "emulator-5554"
    devices = subprocess.run(
        [adb, "devices"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    ).stdout
    running_emulators = [
        line.split()[0]
        for line in devices.splitlines()
        if line.startswith("emulator-") and len(line.split()) >= 2
    ]
    if running_emulators:
        return {
            "contract": "solo-empire.mobile-emulator-smoke.v1",
            "status": "blocked",
            "blockers": [
                "an emulator is already running; stop it before the isolated smoke test"
            ],
            "running_emulators": running_emulators,
            "network": "not-used",
            "credentials": "not-read",
        }

    sdk_root = str(Path(emulator).resolve().parent.parent)
    avd_home = str(Path(os.environ.get("ANDROID_AVD_HOME", DEFAULT_AVD_HOME)).resolve())
    emulator_env = {
        **os.environ,
        "ANDROID_AVD_HOME": avd_home,
        "ANDROID_HOME": sdk_root,
        "ANDROID_SDK_ROOT": sdk_root,
    }
    process = subprocess.Popen(
        [
            emulator,
            f"@{avd}",
            "-port",
            "5554",
            "-no-audio",
            "-no-boot-anim",
            "-no-window",
            "-no-snapshot-save",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=emulator_env,
    )
    results: list[dict[str, Any]] = []
    try:
        deadline = time.monotonic() + boot_timeout
        booted = False
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [adb, "-s", serial, "shell", "getprop", "sys.boot_completed"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if probe.stdout.strip() == "1":
                booted = True
                break
            if process.poll() is not None:
                break
            time.sleep(2)
        if not booted:
            results.append({"name": "boot", "status": "failed", "output_tail": "AVD did not boot"})
        else:
            results.append({"name": "boot", "status": "passed", "output_tail": avd})
            results.append(
                {
                    "name": "install",
                    **_run_command(
                        [adb, "-s", serial, "install", "-r", str(apk)], ROOT, test_timeout
                    ),
                }
            )
            if results[-1]["status"] == "passed":
                results.append(
                    {
                        "name": "maestro",
                        **_run_command(
                            [maestro, "test", str(flow)],
                            ROOT,
                            test_timeout,
                            {
                                "ANDROID_SERIAL": serial,
                                "ANDROID_HOME": sdk_root,
                                "ANDROID_SDK_ROOT": sdk_root,
                                "APP_ID": package_id,
                                "MAESTRO_CLI_NO_ANALYTICS": "1",
                                "MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED": "true",
                            },
                        ),
                    }
                )
            pid = subprocess.run(
                [adb, "-s", serial, "shell", "pidof", package_id],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            ).stdout.strip().split()
            logcat_command = [adb, "-s", serial, "logcat", "-d", "-t", "2000"]
            if pid:
                logcat_command.extend(["--pid", pid[0]])
            logcat = subprocess.run(
                logcat_command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            ).stdout
            fatal_lines = [
                line
                for line in logcat.splitlines()
                if re.search(r"FATAL EXCEPTION|\bANR\b", line)
                and (bool(pid) or package_id in line)
            ]
            results.append(
                {
                    "name": "crash-log",
                    "status": "failed" if fatal_lines or not pid else "passed",
                    "output_tail": "\n".join(fatal_lines[-20:])
                    if fatal_lines
                    else ("app process not running after flow" if not pid else ""),
                }
            )
    finally:
        subprocess.run(
            [adb, "-s", serial, "emu", "kill"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()

    return {
        "contract": "solo-empire.mobile-emulator-smoke.v1",
        "status": "passed" if results and all(item["status"] == "passed" for item in results) else "failed",
        "avd": avd,
        "apk": str(apk),
        "package_id": package_id,
        "flow": str(flow),
        "network": "not-used",
        "credentials": "not-read",
        "checks": results,
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"status: {report['status']}")
    summary = report.get("summary")
    if summary:
        print(
            "apps: {total} total / {ready} ready / {blocked} blocked".format(**summary)
        )
        print("frameworks: " + ", ".join(f"{key}={value}" for key, value in summary["frameworks"].items()))
    app = report.get("app")
    if isinstance(app, dict):
        print(f"app: {app['app']} ({app['framework']})")
        for blocker in app["blockers"]:
            print(f"BLOCKER: {blocker}")
    for check in report.get("checks", []):
        print(f"{check['name']}: {check['status']}")
        if check.get("output_tail") and check["status"] != "passed":
            print(check["output_tail"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mobile-root", type=Path, default=DEFAULT_MOBILE_ROOT)
    parser.add_argument("--json", action="store_true", help="Print JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Read-only inventory and release-readiness audit")
    audit.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Print JSON")
    audit.add_argument("--strict", action="store_true", help="Exit 2 when any app is blocked")

    environment = subparsers.add_parser("environment", help="Inspect local mobile test tools")
    environment.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="Print JSON"
    )

    check = subparsers.add_parser("check", help="Run installed local checks for one app")
    check.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Print JSON")
    check.add_argument("app", help="Exact app directory name")
    check.add_argument("--timeout", type=int, default=300, help="Timeout per command in seconds")

    emulator = subparsers.add_parser("emulator", help="Run an APK and Maestro flow on an explicit AVD")
    emulator.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="Print JSON"
    )
    emulator.add_argument("--avd", required=True)
    emulator.add_argument("--apk", type=Path, required=True)
    emulator.add_argument("--package-id", required=True)
    emulator.add_argument("--flow", type=Path, required=True)
    emulator.add_argument("--boot-timeout", type=int, default=180)
    emulator.add_argument("--test-timeout", type=int, default=300)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mobile_root = args.mobile_root.resolve()
    if args.command == "audit":
        report = build_audit_report(mobile_root)
        exit_code = 2 if args.strict and report["status"] != "ready" else 0
    elif args.command == "environment":
        report = environment_report()
        exit_code = 0
    elif args.command == "check":
        app = (mobile_root / args.app).resolve()
        if app.parent != mobile_root or not app.is_dir():
            report = {"status": "unavailable", "error": "app directory not found or outside mobile root"}
            exit_code = 2
        else:
            report = run_app_checks(app, args.timeout)
            exit_code = 0 if report["status"] == "passed" else 2
    else:
        report = run_emulator_smoke(
            avd=args.avd,
            apk=args.apk.resolve(),
            package_id=args.package_id,
            flow=args.flow.resolve(),
            boot_timeout=args.boot_timeout,
            test_timeout=args.test_timeout,
        )
        exit_code = 0 if report["status"] == "passed" else 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
