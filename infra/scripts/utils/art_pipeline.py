#!/usr/bin/env python3
"""Small, deterministic media intake pipeline.

The pipeline is intentionally local and provider-neutral.  A file dropped in
``media/inbox/<job>/`` is fingerprinted, rendered with a named preset, and
placed in ``media/review/<job-id>/``.  Nothing is published to ``media/exports``
until an explicit approval command is used.

Examples:
    task media:pipeline -- --once
    task media:pipeline -- --watch
    task media:pipeline -- --approve JOB_ID
    task media:pipeline -- --list --json

The source file is never modified or deleted.  Runtime state is kept under
``data/runtime/art-pipeline`` and is safe to rebuild from the inbox.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

from _env import PROJECT_ROOT


CONTRACT = "solo-empire.art-pipeline.v1"
MEDIA_ROOT = PROJECT_ROOT / "media"
INBOX_ROOT = MEDIA_ROOT / "inbox"
WORK_ROOT = MEDIA_ROOT / "work"
REVIEW_ROOT = MEDIA_ROOT / "review"
EXPORT_ROOT = MEDIA_ROOT / "exports"
STATE_ROOT = PROJECT_ROOT / "data" / "runtime" / "art-pipeline"
STATE_PATH = STATE_ROOT / "jobs.json"
LOCK_NAME = "pipeline.lock"

IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".webm"}
BRIEF_NAMES = {"brief.yaml", "brief.yml", "brief.json"}
IGNORED_SUFFIXES = {".partial", ".tmp", ".crdownload"}

PRESETS = {
    "image-preview": {"kind": "image", "width": 1920, "height": 1080, "square": False},
    "image-square": {"kind": "image", "width": 1080, "height": 1080, "square": True},
    "image-web": {"kind": "image", "width": 2048, "height": 2048, "square": False},
    "video-preview": {"kind": "video", "width": 1280, "height": 720},
    "video-proxy": {"kind": "video", "width": 1920, "height": 1080},
}

IMAGE_COLOR_OPTIONS = {"neutral", "warm", "cool", "bw", "cinematic"}
VIDEO_COLOR_FILTERS = {
    "neutral": [],
    "warm": ["colorbalance=rs=.08:gs=.02:bs=-.04"],
    "cool": ["colorbalance=rs=-.04:gs=.02:bs=.08"],
    "bw": ["hue=s=0"],
    "cinematic": ["eq=contrast=1.08:saturation=1.12:gamma=0.98"],
}

MASTER_IMAGE_QUALITY = 95
MASTER_VIDEO_CRF = 18
MASTER_VIDEO_SPEED = "medium"
GRAPHICS_MAX_CHARS = 240
GRAPHICS_MAX_FONT_SIZE = 256
GRAPHICS_FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
)
GRAPHICS_FONT = next((str(path) for path in GRAPHICS_FONT_CANDIDATES if path.is_file()), None)
GRAPHICS_POSITIONS = {"top": "north", "center": "center", "bottom": "south"}
GRAPHICS_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
AUDIO_MAX_VOLUME = 4.0
AUDIO_MAX_FADE_SECONDS = 300.0


class PipelineBusy(RuntimeError):
    """Raised when another pipeline operation owns the runtime lock."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def relative(path: Path, root: Path = PROJECT_ROOT) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def ensure_roots() -> None:
    for path in (INBOX_ROOT, WORK_ROOT, REVIEW_ROOT, EXPORT_ROOT, STATE_ROOT):
        path.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def pipeline_lock(state_root: Path) -> Iterator[None]:
    """Serialize state transitions and renders for one project.

    The watcher is intentionally single-process, but commands such as an
    explicit approval can be invoked while it is running.  A non-blocking
    advisory lock makes that race visible instead of allowing two writers to
    overwrite the JSON state or the same output directory.
    """

    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / LOCK_NAME
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PipelineBusy("pipeline_busy") from exc
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def read_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"contract": CONTRACT, "updated_at": now_iso(), "jobs": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot_read_state:{path}:{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs", {}), dict):
        raise RuntimeError(f"invalid_state:{path}")
    payload.setdefault("contract", CONTRACT)
    return payload


def write_state(payload: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["contract"] = CONTRACT
    payload["updated_at"] = now_iso()
    fd, temp_name = tempfile.mkstemp(prefix="jobs.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_brief(job_dir: Path) -> dict[str, Any]:
    for name in BRIEF_NAMES:
        path = job_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            value = json.loads(text)
        else:
            try:
                import yaml  # type: ignore

                value = yaml.safe_load(text)
            except ImportError:
                # Keep the intake usable on a bare Python install for the
                # common ``preset: image-preview`` brief.
                value = {}
                for line in text.splitlines():
                    if ":" in line and not line.lstrip().startswith("#"):
                        key, item = line.split(":", 1)
                        value[key.strip()] = item.strip().strip("'\"")
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"brief_must_be_mapping:{path}")
        return value
    return {}


def brief_path_for(job_dir: Path) -> Path | None:
    for name in BRIEF_NAMES:
        path = job_dir / name
        if path.is_file():
            return path
    return None


def infer_preset(job_dir: Path, source: Path, brief: dict[str, Any]) -> str:
    configured = brief.get("preset")
    if configured is not None:
        return str(configured).strip()
    name = job_dir.name.lower()
    for preset in PRESETS:
        if preset in name:
            return preset
    if source.suffix.lower() in IMAGE_EXTENSIONS:
        return "image-preview"
    if source.suffix.lower() in VIDEO_EXTENSIONS:
        return "video-preview"
    return ""


def iter_sources(inbox: Path = INBOX_ROOT, stability_seconds: float = 0) -> Iterable[tuple[Path, Path]]:
    if not inbox.exists():
        return
    assembly_dirs: set[Path] = set()
    for path in sorted(inbox.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.name.startswith("."):
            continue
        if path.name.lower() in BRIEF_NAMES or path.suffix.lower() in IGNORED_SUFFIXES:
            continue
        brief_path = brief_path_for(path.parent)
        if brief_path:
            try:
                brief = parse_brief(path.parent)
            except Exception:
                brief = None
            if isinstance(brief, dict) and brief.get("clips") is not None:
                if path.parent in assembly_dirs:
                    continue
                assembly_dirs.add(path.parent)
                try:
                    clip_paths = canonical_clips(brief)
                except ValueError:
                    yield path, path.parent
                    continue
                yield path.parent / clip_paths[0], path.parent
                continue
        if stability_seconds > 0:
            try:
                if time.time() - path.stat().st_mtime < stability_seconds:
                    continue
            except OSError:
                continue
        yield path, path.parent


def render_spec_digest(spec: dict[str, Any]) -> str:
    encoded = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def job_id_for(
    source: Path,
    digest: str,
    preset: str,
    project_root: Path = PROJECT_ROOT,
    *,
    render_spec: dict[str, Any] | None = None,
) -> str:
    spec = render_spec or {"preset": preset or None, "color": "neutral"}
    spec_digest = render_spec_digest(spec)
    value = f"{relative(source, project_root)}\0{digest}\0{preset}\0{spec_digest}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:16]


def run_command(command: list[str], timeout: int = 3600) -> None:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError(f"missing_tool:{command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command_timeout:{command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().replace("\n", " ")
        raise RuntimeError(f"command_failed:{command[0]}:{detail[-500:]}") from exc


def render_image(
    source: Path,
    destination: Path,
    preset: str,
    color: str,
    *,
    quality: int = 88,
    graphics: dict[str, Any] | None = None,
) -> list[Path]:
    config = PRESETS[preset]
    if color not in IMAGE_COLOR_OPTIONS:
        raise ValueError(f"unknown_color_preset:{color}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{os.getpid()}.tmp.jpg"
    geometry = f"{config['width']}x{config['height']}"
    command = ["magick", str(source), "-auto-orient", "-strip"]
    if config["square"]:
        command += ["-resize", f"{geometry}^", "-gravity", "center", "-extent", geometry]
    else:
        command += ["-resize", f"{geometry}>"]
    if color == "warm":
        command += ["-fill", "#fff1df", "-colorize", "8"]
    elif color == "cool":
        command += ["-fill", "#dfefff", "-colorize", "8"]
    elif color == "bw":
        command += ["-colorspace", "Gray"]
    elif color == "cinematic":
        command += ["-contrast-stretch", "0x2%", "-modulate", "100,112,100"]
    overlay = graphics_text(graphics)
    if overlay:
        position = GRAPHICS_POSITIONS.get(str(graphics.get("position", "bottom")), "south")
        command += [
            "-gravity", position,
            "-font", GRAPHICS_FONT or "sans",
            "-fill", str(graphics.get("font_color", "#ffffff")),
            "-undercolor", "#00000099",
            "-pointsize", str(int(graphics.get("font_size", 52))),
            "-annotate", "+0+40", overlay,
        ]
    command += ["-background", "white", "-alpha", "remove", "-alpha", "off", "-quality", str(quality), str(temporary)]
    try:
        run_command(command)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return [destination]


def seconds(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_seconds:{field}") from exc
    if parsed < 0:
        raise ValueError(f"invalid_seconds:{field}")
    return parsed


def canonical_render_spec(preset: str, brief: dict[str, Any]) -> dict[str, Any]:
    """Keep only deterministic, safe-to-replay render inputs in job state."""

    color = str(brief.get("color", "neutral")).strip().lower() or "neutral"
    spec: dict[str, Any] = {"preset": preset or None, "color": color}
    aliases = {
        "start": ("start", "start_seconds"),
        "duration": ("duration", "duration_seconds"),
        "end": ("end", "end_seconds"),
    }
    for canonical, candidates in aliases.items():
        for candidate in candidates:
            if candidate in brief and brief[candidate] not in (None, ""):
                spec[canonical] = seconds(brief[candidate], canonical)
                break
    graphics = canonical_graphics(brief)
    if graphics:
        spec["graphics"] = graphics
    audio = canonical_audio(brief)
    if audio:
        if PRESETS.get(preset, {}).get("kind") != "video":
            raise ValueError("audio_requires_video_preset")
        spec["audio"] = audio
    clips = canonical_clips(brief)
    if clips:
        if PRESETS.get(preset, {}).get("kind") != "video":
            raise ValueError("clips_require_video_preset")
        spec["clips"] = clips
    return spec


def canonical_graphics(brief: dict[str, Any]) -> dict[str, Any]:
    raw = brief.get("graphics", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("graphics_must_be_mapping")

    def text_value(key: str, fallback: Any = None) -> str:
        value = raw.get(key, fallback)
        if value is None:
            return ""
        if not isinstance(value, (str, int, float)):
            raise ValueError(f"graphics_{key}_must_be_text")
        normalized = str(value).replace("\r", "").strip()
        if len(normalized) > GRAPHICS_MAX_CHARS:
            raise ValueError(f"graphics_{key}_too_long")
        return normalized

    title = text_value("title", brief.get("title"))
    subtitle = text_value("subtitle", brief.get("subtitle"))
    if not title and not subtitle:
        return {}
    position = str(raw.get("position", "bottom")).strip().lower()
    if position not in GRAPHICS_POSITIONS:
        raise ValueError(f"unknown_graphics_position:{position}")
    try:
        font_size = int(raw.get("font_size", 52))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_graphics_font_size") from exc
    if not 12 <= font_size <= GRAPHICS_MAX_FONT_SIZE:
        raise ValueError("invalid_graphics_font_size")
    font_color = str(raw.get("font_color", "#ffffff")).strip().lower()
    if not GRAPHICS_COLOR_PATTERN.fullmatch(font_color):
        raise ValueError("invalid_graphics_font_color")
    return {
        "title": title,
        "subtitle": subtitle,
        "position": position,
        "font_size": font_size,
        "font_color": font_color,
    }


def canonical_audio(brief: dict[str, Any]) -> dict[str, Any]:
    raw = brief.get("audio", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("audio_must_be_mapping")
    if not raw and not any(key in brief for key in ("mute", "volume", "fade_in", "fade_out")):
        return {}

    mute_value = raw.get("mute", brief.get("mute", False))
    if isinstance(mute_value, bool):
        mute = mute_value
    elif isinstance(mute_value, str) and mute_value.strip().lower() in {"true", "yes", "1", "on"}:
        mute = True
    elif isinstance(mute_value, str) and mute_value.strip().lower() in {"false", "no", "0", "off"}:
        mute = False
    else:
        raise ValueError("invalid_audio_mute")

    try:
        volume = float(raw.get("volume", brief.get("volume", 1.0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_audio_volume") from exc
    if not 0 <= volume <= AUDIO_MAX_VOLUME:
        raise ValueError("invalid_audio_volume")

    fade_in = seconds(raw.get("fade_in", brief.get("fade_in")), "audio_fade_in") or 0.0
    fade_out = seconds(raw.get("fade_out", brief.get("fade_out")), "audio_fade_out") or 0.0
    if fade_in > AUDIO_MAX_FADE_SECONDS or fade_out > AUDIO_MAX_FADE_SECONDS:
        raise ValueError("invalid_audio_fade")
    return {
        "mute": mute,
        "volume": volume,
        "fade_in": fade_in,
        "fade_out": fade_out,
    }


def canonical_clips(brief: dict[str, Any]) -> list[str]:
    raw = brief.get("clips")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("clips_requires_at_least_two_files")
    clips: list[str] = []
    for item in raw:
        value = item.get("path") if isinstance(item, dict) else item
        if not isinstance(value, str) or not value.strip():
            raise ValueError("clip_path_must_be_text")
        path = Path(value.strip())
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("clip_path_must_stay_in_job_folder")
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(f"clip_input_mismatch:{path.suffix.lower()}")
        normalized = path.as_posix()
        if normalized in clips:
            raise ValueError(f"duplicate_clip:{normalized}")
        clips.append(normalized)
    return clips


def graphics_text(graphics: dict[str, Any] | None) -> str:
    if not isinstance(graphics, dict):
        return ""
    return "\n".join(part for part in (graphics.get("title", ""), graphics.get("subtitle", "")) if part)


def ffmpeg_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def ffmpeg_graphics_filter(graphics: dict[str, Any], text_path: Path) -> str:
    position = str(graphics.get("position", "bottom"))
    y_expression = {
        "top": "40",
        "center": "(h-text_h)/2",
        "bottom": "h-text_h-40",
    }.get(position, "h-text_h-40")
    font = f"fontfile='{ffmpeg_filter_value(GRAPHICS_FONT)}':" if GRAPHICS_FONT else ""
    color = str(graphics.get("font_color", "#ffffff")).replace("#", "0x")
    textfile = ffmpeg_filter_value(str(text_path))
    return (
        "drawtext="
        f"{font}textfile='{textfile}':fontcolor={color}:fontsize={int(graphics.get('font_size', 52))}:"
        f"box=1:boxcolor=black@0.55:boxborderw=16:x=(w-text_w)/2:y={y_expression}"
    )


def ffmpeg_audio_filters(audio: dict[str, Any], duration: float | None) -> list[str]:
    filters: list[str] = []
    volume = float(audio.get("volume", 1.0))
    if volume != 1.0:
        filters.append(f"volume={volume:g}")
    fade_in = float(audio.get("fade_in", 0.0))
    fade_out = float(audio.get("fade_out", 0.0))
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in:g}")
    if fade_out > 0:
        if duration is None:
            raise ValueError("audio_fade_out_requires_duration")
        fade_start = max(0.0, duration - fade_out)
        filters.append(f"afade=t=out:st={fade_start:g}:d={fade_out:g}")
    return filters


def validate_output(path: Path, kind: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"invalid_output:{path}")
    if kind != "video":
        return
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("missing_tool:ffprobe") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"invalid_output:{path}") from exc
    try:
        duration = float(probe.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"invalid_output:{path}") from exc
    if duration <= 0:
        raise RuntimeError(f"invalid_output:{path}")


def validate_outputs(outputs: Iterable[Path], kind: str) -> None:
    for output in outputs:
        # Thumbnails are image files even when the primary output is a video.
        validate_output(output, "image" if output.name.endswith(".jpg") else kind)


def resolve_clip_paths(job_dir: Path, clips: list[str]) -> list[Path]:
    root = job_dir.resolve()
    resolved: list[Path] = []
    for clip in clips:
        path = (job_dir / clip).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("clip_path_must_stay_in_job_folder") from exc
        if not path.is_file():
            raise FileNotFoundError(clip)
        resolved.append(path)
    return resolved


def source_digest(source: Path, job_dir: Path, render_spec: dict[str, Any]) -> str:
    clips = render_spec.get("clips")
    if not isinstance(clips, list) or not clips:
        return sha256(source)
    digest = hashlib.sha256()
    for clip, path in zip(clips, resolve_clip_paths(job_dir, clips), strict=True):
        digest.update(str(clip).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def assemble_clips(job_dir: Path, work_dir: Path, clips: list[str]) -> Path:
    paths = resolve_clip_paths(job_dir, clips)
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest = work_dir / "concat.txt"
    assembled = work_dir / "assembled-source.mp4"
    temporary = work_dir / f".assembled-source.{os.getpid()}.tmp.mp4"

    def concat_quote(path: Path) -> str:
        return str(path).replace("\\", "\\\\").replace("'", "'\\''")

    manifest.write_text("".join(f"file '{concat_quote(path)}'\n" for path in paths), encoding="utf-8")
    try:
        run_command([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-c", "copy", "-movflags", "+faststart", str(temporary),
        ])
        os.replace(temporary, assembled)
        validate_output(assembled, "video")
    finally:
        temporary.unlink(missing_ok=True)
    return assembled


def render_video(
    source: Path,
    destination: Path,
    thumbnail: Path,
    preset: str,
    brief: dict[str, Any],
    *,
    crf: int = 28,
    speed: str = "veryfast",
    graphics: dict[str, Any] | None = None,
) -> list[Path]:
    config = PRESETS[preset]
    color = str(brief.get("color", "neutral")).strip().lower()
    if color not in VIDEO_COLOR_FILTERS:
        raise ValueError(f"unknown_color_preset:{color}")
    start = seconds(brief.get("start", brief.get("start_seconds")), "start")
    duration = seconds(brief.get("duration", brief.get("duration_seconds")), "duration")
    end = seconds(brief.get("end", brief.get("end_seconds")), "end")
    if start is not None and end is not None and end < start:
        raise ValueError("invalid_clip_range:end_before_start")
    if duration is None and start is not None and end is not None:
        duration = end - start
    audio = brief.get("audio") if isinstance(brief.get("audio"), dict) else {}
    audio_filters = [] if audio.get("mute") else ffmpeg_audio_filters(audio, duration)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_video = destination.parent / f".{destination.name}.{os.getpid()}.tmp.mp4"
    temporary_thumb = thumbnail.parent / f".{thumbnail.name}.{os.getpid()}.tmp.jpg"
    temporary_text: Path | None = None
    filters = [f"scale='min({config['width']},iw)':-2"] + VIDEO_COLOR_FILTERS[color]
    overlay = graphics_text(graphics)
    if overlay:
        temporary_text = destination.parent / f".{destination.name}.{os.getpid()}.txt"
        temporary_text.write_text(overlay, encoding="utf-8")
        filters.append(ffmpeg_graphics_filter(graphics or {}, temporary_text))
    command = ["ffmpeg", "-y"]
    if start is not None:
        command += ["-ss", str(start)]
    command += ["-i", str(source), "-vf", ",".join(filters)]
    if duration is not None:
        command += ["-t", str(duration)]
    if audio.get("mute"):
        command += ["-an"]
    elif audio_filters:
        command += ["-af", ",".join(audio_filters)]
    command += [
        "-c:v", "libx264", "-preset", speed, "-crf", str(crf),
        *([] if audio.get("mute") else ["-c:a", "aac"]),
        "-movflags", "+faststart",
        str(temporary_video),
    ]
    thumbnail_seek = (start or 0) + 1
    try:
        run_command(command)
        run_command(["ffmpeg", "-y", "-ss", str(thumbnail_seek), "-i", str(source), "-frames:v", "1", str(temporary_thumb)])
        os.replace(temporary_video, destination)
        os.replace(temporary_thumb, thumbnail)
    finally:
        temporary_video.unlink(missing_ok=True)
        temporary_thumb.unlink(missing_ok=True)
        if temporary_text:
            temporary_text.unlink(missing_ok=True)
    return [destination, thumbnail]


def render(
    source: Path,
    output_dir: Path,
    preset: str,
    brief: dict[str, Any],
    *,
    output_stem: str = "preview",
    image_quality: int = 88,
    video_crf: int = 28,
    video_speed: str = "veryfast",
) -> list[Path]:
    if preset not in PRESETS:
        raise ValueError(f"unknown_preset:{preset}")
    expected_kind = PRESETS[preset]["kind"]
    suffix = source.suffix.lower()
    if expected_kind == "image" and suffix not in IMAGE_EXTENSIONS:
        raise ValueError(f"preset_input_mismatch:{preset}:{suffix}")
    if expected_kind == "video" and suffix not in VIDEO_EXTENSIONS:
        raise ValueError(f"preset_input_mismatch:{preset}:{suffix}")
    if expected_kind == "image":
        color = str(brief.get("color", "neutral")).strip().lower()
        outputs = render_image(
            source,
            output_dir / f"{output_stem}.jpg",
            preset,
            color,
            quality=image_quality,
            graphics=brief.get("graphics") if isinstance(brief.get("graphics"), dict) else None,
        )
    else:
        thumbnail_name = "thumbnail.jpg" if output_stem == "preview" else f"{output_stem}-thumbnail.jpg"
        outputs = render_video(
            source,
            output_dir / f"{output_stem}.mp4",
            output_dir / thumbnail_name,
            preset,
            brief,
            crf=video_crf,
            speed=video_speed,
            graphics=brief.get("graphics") if isinstance(brief.get("graphics"), dict) else None,
        )
    validate_outputs(outputs, expected_kind)
    return outputs


def process_source(
    source: Path,
    job_dir: Path,
    state: dict[str, Any],
    *,
    project_root: Path = PROJECT_ROOT,
    dry_run: bool = False,
    retry_failed: bool = False,
    save_state: Callable[[], None] | None = None,
) -> dict[str, Any]:
    persist = save_state or (lambda: None)
    source_rel = relative(source, project_root)
    brief_error: str | None = None
    try:
        brief = parse_brief(job_dir)
    except Exception as exc:  # keep one malformed delivery from stopping the queue
        brief = {}
        brief_error = f"brief_parse_failed:{exc}"
    preset = infer_preset(job_dir, source, brief)
    try:
        render_spec = canonical_render_spec(preset, brief)
    except ValueError as exc:
        render_spec = {"preset": preset or None, "color": "neutral", "brief_error": str(exc)}
        brief_error = brief_error or str(exc)
    if brief_error:
        render_spec["brief_error"] = brief_error
    try:
        digest = source_digest(source, job_dir, render_spec)
    except (OSError, ValueError) as exc:
        digest = hashlib.sha256(source_rel.encode("utf-8")).hexdigest()
        prefix = "source_manifest_invalid" if render_spec.get("clips") else "source_unreadable"
        brief_error = brief_error or f"{prefix}:{exc}"
    if brief_error:
        render_spec["brief_error"] = brief_error
    spec_digest = render_spec_digest(render_spec)
    job_id = job_id_for(source, digest, preset, project_root, render_spec=render_spec)
    existing = state["jobs"].get(job_id)
    if existing and existing.get("status") in {"review", "exported"}:
        return {"job_id": job_id, "status": "skipped", "reason": "already_processed"}
    if existing and existing.get("status") == "failed" and not retry_failed:
        return {"job_id": job_id, "status": "skipped", "reason": "failed_requires_retry", "error": existing.get("error")}
    record: dict[str, Any] = {
        "job_id": job_id,
        "source": source_rel,
        "source_sha256": digest,
        "preset": preset or None,
        "render_spec": render_spec,
        "render_spec_sha256": spec_digest,
        "status": "queued",
        "created_at": existing.get("created_at", now_iso()) if existing else now_iso(),
        "updated_at": now_iso(),
        "review_dir": relative(REVIEW_ROOT / job_id, project_root),
        "work_dir": relative(WORK_ROOT / job_id, project_root),
        "outputs": [],
    }
    if isinstance(render_spec.get("clips"), list):
        record["sources"] = [relative(job_dir / clip, project_root) for clip in render_spec["clips"]]
    brief_path = brief_path_for(job_dir)
    if brief_path:
        record["brief"] = relative(brief_path, project_root)
        try:
            record["brief_sha256"] = sha256(brief_path)
        except OSError:
            pass
    state["jobs"][job_id] = record
    persist()
    if dry_run:
        record["status"] = "planned" if preset else "failed"
        if not preset:
            record["error"] = "no_preset_for_input"
        if brief_error:
            record["status"] = "failed"
            record["error"] = brief_error
        record["updated_at"] = now_iso()
        persist()
        return {"job_id": job_id, "status": record["status"], "preset": preset or None, "source": record["source"]}
    if brief_error:
        record["status"] = "failed"
        record["error"] = brief_error
        record["updated_at"] = now_iso()
        persist()
        return {"job_id": job_id, "status": "failed", "error": record["error"]}
    if not preset:
        record["status"] = "failed"
        record["error"] = "no_preset_for_input"
        record["updated_at"] = now_iso()
        persist()
        return {"job_id": job_id, "status": "failed", "error": record["error"]}
    review_dir = REVIEW_ROOT / job_id
    work_dir = WORK_ROOT / job_id
    review_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    record["status"] = "processing"
    record["updated_at"] = now_iso()
    persist()
    try:
        render_source = source
        clips = render_spec.get("clips")
        if isinstance(clips, list):
            render_source = assemble_clips(job_dir, work_dir, clips)
            record["assembly_source"] = relative(render_source, project_root)
        outputs = render(render_source, review_dir, preset, render_spec)
        record["outputs"] = [relative(path, project_root) for path in outputs]
        record["status"] = "review"
        record["updated_at"] = now_iso()
        (work_dir / "job.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        persist()
        return {"job_id": job_id, "status": "review", "preset": preset, "outputs": record["outputs"]}
    except (OSError, RuntimeError, ValueError) as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        record["updated_at"] = now_iso()
        (work_dir / "job.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        persist()
        return {"job_id": job_id, "status": "failed", "error": record["error"]}


def process_once(
    *,
    project_root: Path = PROJECT_ROOT,
    dry_run: bool = False,
    retry_failed: bool = False,
    stability_seconds: float = 0,
) -> dict[str, Any]:
    global INBOX_ROOT, WORK_ROOT, REVIEW_ROOT, EXPORT_ROOT, STATE_ROOT, STATE_PATH
    original = (INBOX_ROOT, WORK_ROOT, REVIEW_ROOT, EXPORT_ROOT, STATE_ROOT, STATE_PATH)
    media_root = project_root / "media"
    INBOX_ROOT, WORK_ROOT, REVIEW_ROOT, EXPORT_ROOT = (media_root / name for name in ("inbox", "work", "review", "exports"))
    STATE_ROOT = project_root / "data" / "runtime" / "art-pipeline"
    STATE_PATH = STATE_ROOT / "jobs.json"
    try:
        ensure_roots()
        try:
            with pipeline_lock(STATE_ROOT):
                state = read_state(STATE_PATH)
                recovered = 0
                for record in state["jobs"].values():
                    if record.get("status") == "processing":
                        record["status"] = "queued"
                        record["error"] = "interrupted_previous_run"
                        record["updated_at"] = now_iso()
                        recovered += 1
                if recovered:
                    write_state(state, STATE_PATH)

                results: list[dict[str, Any]] = []

                def save_state() -> None:
                    write_state(state, STATE_PATH)

                for source, job_dir in iter_sources(INBOX_ROOT, stability_seconds):
                    try:
                        result = process_source(
                            source,
                            job_dir,
                            state,
                            project_root=project_root,
                            dry_run=dry_run,
                            retry_failed=retry_failed,
                            save_state=save_state,
                        )
                    except Exception as exc:  # isolate an unexpected file-level failure
                        result = {"status": "failed", "source": relative(source, project_root), "error": str(exc)}
                    results.append(result)
                    save_state()
                counts: dict[str, int] = {}
                for result in results:
                    counts[result["status"]] = counts.get(result["status"], 0) + 1
                return {
                    "contract": CONTRACT,
                    "dry_run": dry_run,
                    "recovered": recovered,
                    "count": len(results),
                    "counts": counts,
                    "jobs": results,
                }
        except PipelineBusy:
            return {"contract": CONTRACT, "status": "busy", "error": "pipeline_busy", "count": 0, "jobs": []}
    finally:
        INBOX_ROOT, WORK_ROOT, REVIEW_ROOT, EXPORT_ROOT, STATE_ROOT, STATE_PATH = original


def render_master(
    source: Path,
    export_dir: Path,
    record: dict[str, Any],
    *,
    project_root: Path = PROJECT_ROOT,
) -> list[Path]:
    preset = str(record.get("preset") or "")
    spec = record.get("render_spec")
    if not isinstance(spec, dict):
        spec = {"preset": preset, "color": "neutral"}
    if spec.get("brief_error"):
        raise ValueError(str(spec["brief_error"]))
    render_source = source
    clips = spec.get("clips")
    if isinstance(clips, list):
        work_dir_value = record.get("work_dir")
        work_dir = project_root / str(work_dir_value) if work_dir_value else project_root / "media" / "work" / str(record.get("job_id"))
        render_source = assemble_clips(source.parent, work_dir, clips)
    return render(
        render_source,
        export_dir,
        preset,
        spec,
        output_stem="master",
        image_quality=MASTER_IMAGE_QUALITY,
        video_crf=MASTER_VIDEO_CRF,
        video_speed=MASTER_VIDEO_SPEED,
    )


def approve(job_ids: list[str], *, project_root: Path = PROJECT_ROOT, approve_all: bool = False) -> dict[str, Any]:
    state_root = project_root / "data" / "runtime" / "art-pipeline"
    state_path = state_root / "jobs.json"
    try:
        with pipeline_lock(state_root):
            state = read_state(state_path)
            selected = list(state["jobs"].keys()) if approve_all else job_ids
            approved: list[dict[str, Any]] = []
            errors: list[dict[str, str]] = []
            for job_id in selected:
                record = state["jobs"].get(job_id)
                if not record:
                    errors.append({"job_id": job_id, "error": "unknown_job"})
                    continue
                if record.get("status") != "review":
                    if approve_all:
                        continue
                    errors.append({"job_id": job_id, "error": f"not_ready:{record.get('status')}"})
                    continue
                export_dir = project_root / "media" / "exports" / job_id
                export_dir.mkdir(parents=True, exist_ok=True)
                exported: list[str] = []
                masters: list[str] = []
                try:
                    for output in record.get("outputs", []):
                        source = project_root / output
                        if not source.is_file():
                            raise FileNotFoundError(output)
                        destination = export_dir / source.name
                        shutil.copy2(source, destination)
                        validate_output(destination, "image" if destination.suffix.lower() in {".jpg", ".jpeg", ".png"} else "video")
                        exported.append(relative(destination, project_root))
                    source_path = project_root / str(record.get("source"))
                    if not source_path.is_file():
                        raise FileNotFoundError(str(record.get("source")))
                    master_outputs = render_master(source_path, export_dir, record, project_root=project_root)
                    masters = [relative(path, project_root) for path in master_outputs]
                    record["status"] = "exported"
                    record["exported_at"] = now_iso()
                    record["exported_files"] = exported
                    record["master_files"] = masters
                    record["updated_at"] = now_iso()
                    approved.append({"job_id": job_id, "status": "exported", "files": exported + masters})
                except (OSError, RuntimeError, ValueError) as exc:
                    record["status"] = "review"
                    record["error"] = str(exc)
                    record["updated_at"] = now_iso()
                    errors.append({"job_id": job_id, "error": str(exc)})
                write_state(state, state_path)
            return {"contract": CONTRACT, "approved": approved, "errors": errors}
    except PipelineBusy:
        return {"contract": CONTRACT, "status": "busy", "approved": [], "errors": [{"error": "pipeline_busy"}]}


def list_jobs(*, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    path = project_root / "data" / "runtime" / "art-pipeline" / "jobs.json"
    state = read_state(path)
    return {"contract": CONTRACT, "count": len(state["jobs"]), "jobs": list(state["jobs"].values())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local art/media intake pipeline")
    parser.add_argument("--once", action="store_true", help="process the inbox once (default)")
    parser.add_argument("--watch", action="store_true", help="poll the inbox until interrupted")
    parser.add_argument("--interval", type=float, default=5, help="watch polling interval in seconds")
    parser.add_argument("--stability-seconds", type=float, default=2, help="ignore files still being copied")
    parser.add_argument("--dry-run", action="store_true", help="report planned jobs without rendering")
    parser.add_argument("--retry-failed", action="store_true", help="retry previously failed jobs")
    parser.add_argument("--approve", action="append", default=[], metavar="JOB_ID", help="publish a review job")
    parser.add_argument("--approve-all", action="store_true", help="publish all review-ready jobs")
    parser.add_argument("--list", action="store_true", dest="list_jobs", help="list job state")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        if args.list_jobs:
            payload = list_jobs()
        elif args.approve or args.approve_all:
            payload = approve(args.approve, approve_all=args.approve_all)
        elif args.watch:
            while True:
                run = process_once(dry_run=args.dry_run, retry_failed=args.retry_failed, stability_seconds=args.stability_seconds)
                if args.as_json:
                    print(json.dumps(run, ensure_ascii=False), flush=True)
                elif run.get("status") == "busy":
                    print("art pipeline: busy; another run owns the lock", flush=True)
                time.sleep(max(0.1, args.interval))
        else:
            payload = process_once(dry_run=args.dry_run, retry_failed=args.retry_failed, stability_seconds=args.stability_seconds)
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        payload = {"contract": CONTRACT, "status": "failed", "error": str(exc)}
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"art pipeline failed: {exc}")
        return 1
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if args.list_jobs:
            for job in payload["jobs"]:
                print(f"{job['job_id']}\t{job.get('status')}\t{job.get('source')}")
        elif "counts" in payload:
            print(f"art pipeline: {payload['count']} file(s); {payload['counts']}")
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
