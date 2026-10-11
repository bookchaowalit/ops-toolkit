#!/usr/bin/env python3
"""Focused tests for the local art/media intake pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys

UTILS = Path(__file__).resolve().parents[2] / "scripts" / "utils"
sys.path.insert(0, str(UTILS))

import art_pipeline  # noqa: E402


@unittest.skipUnless(shutil.which("magick"), "ImageMagick is required for image rendering")
class ArtPipelineTest(unittest.TestCase):
    def make_project(self) -> Path:
        project = Path(tempfile.mkdtemp())
        (project / "media" / "inbox" / "social-card").mkdir(parents=True)
        (project / "media" / "inbox" / "social-card" / "brief.yaml").write_text(
            "preset: image-square\n", encoding="utf-8"
        )
        source = project / "media" / "inbox" / "social-card" / "source.png"
        art_pipeline.run_command(["magick", "-size", "20x12", "xc:#ff5500", str(source)])
        return project

    def test_processes_image_to_review_and_is_idempotent(self) -> None:
        project = self.make_project()
        try:
            first = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(first["counts"], {"review": 1})
            job = first["jobs"][0]
            self.assertTrue((project / job["outputs"][0]).is_file())
            second = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(second["counts"], {"skipped": 1})
            state = json.loads((project / "data/runtime/art-pipeline/jobs.json").read_text())
            self.assertEqual(state["jobs"][job["job_id"]]["status"], "review")
        finally:
            shutil.rmtree(project)

    def test_approve_copies_review_output_to_exports(self) -> None:
        project = self.make_project()
        try:
            result = art_pipeline.process_once(project_root=project, stability_seconds=0)
            job_id = result["jobs"][0]["job_id"]
            approved = art_pipeline.approve([job_id], project_root=project)
            self.assertEqual(len(approved["approved"]), 1)
            export = project / "media" / "exports" / job_id / "preview.jpg"
            self.assertTrue(export.is_file())
            master = project / "media" / "exports" / job_id / "master.jpg"
            self.assertTrue(master.is_file())
            listed = art_pipeline.list_jobs(project_root=project)
            self.assertEqual(listed["jobs"][0]["status"], "exported")
            self.assertEqual(len(listed["jobs"][0]["master_files"]), 1)
        finally:
            shutil.rmtree(project)

    def test_unknown_input_is_recorded_as_failed_without_deleting_source(self) -> None:
        project = Path(tempfile.mkdtemp())
        try:
            source_dir = project / "media" / "inbox" / "unknown"
            source_dir.mkdir(parents=True)
            source = source_dir / "notes.txt"
            source.write_text("keep me", encoding="utf-8")
            result = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(result["counts"], {"failed": 1})
            self.assertEqual(source.read_text(encoding="utf-8"), "keep me")
        finally:
            shutil.rmtree(project)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for video rendering")
    def test_processes_trimmed_video_with_color_preset_and_approves(self) -> None:
        project = Path(tempfile.mkdtemp())
        try:
            job_dir = project / "media" / "inbox" / "video-cut"
            job_dir.mkdir(parents=True)
            (job_dir / "brief.yaml").write_text(
                "preset: video-preview\nstart: 0.5\nduration: 1\ncolor: cinematic\n"
                "graphics:\n  title: Demo title\n  subtitle: Preview\n  position: bottom\n",
                encoding="utf-8",
            )
            source = job_dir / "source.mp4"
            art_pipeline.run_command([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10",
                "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ])
            result = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(result["counts"], {"review": 1})
            job = result["jobs"][0]
            preview = project / job["outputs"][0]
            thumbnail = project / job["outputs"][1]
            self.assertTrue(preview.is_file())
            self.assertTrue(thumbnail.is_file())
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(preview)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertLessEqual(float(probe.stdout.strip()), 1.5)
            approved = art_pipeline.approve([job["job_id"]], project_root=project)
            self.assertEqual(len(approved["approved"]), 1)
            self.assertTrue((project / "media" / "exports" / job["job_id"] / "preview.mp4").is_file())
            self.assertTrue((project / "media" / "exports" / job["job_id"] / "master.mp4").is_file())
            master_probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                 str(project / "media" / "exports" / job["job_id"] / "master.mp4")],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertLessEqual(float(master_probe.stdout.strip()), 1.5)
        finally:
            shutil.rmtree(project)

    def test_graphics_overlay_is_recorded_and_rendered_for_image(self) -> None:
        project = self.make_project()
        try:
            brief = project / "media" / "inbox" / "social-card" / "brief.yaml"
            brief.write_text(
                "preset: image-square\ngraphics:\n  title: Hello\n  subtitle: World\n  position: center\n",
                encoding="utf-8",
            )
            result = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(result["counts"], {"review": 1})
            job_id = result["jobs"][0]["job_id"]
            state = json.loads((project / "data/runtime/art-pipeline/jobs.json").read_text())
            record = state["jobs"][job_id]
            self.assertEqual(record["render_spec"]["graphics"]["title"], "Hello")
            self.assertTrue((project / record["outputs"][0]).is_file())
        finally:
            shutil.rmtree(project)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for audio rendering")
    def test_video_audio_controls_can_mute_source_audio(self) -> None:
        project = Path(tempfile.mkdtemp())
        try:
            job_dir = project / "media" / "inbox" / "muted-video"
            job_dir.mkdir(parents=True)
            (job_dir / "brief.yaml").write_text(
                "preset: video-preview\nduration: 1\naudio:\n  mute: true\n",
                encoding="utf-8",
            )
            source = job_dir / "source.mp4"
            art_pipeline.run_command([
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(source),
            ])
            result = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(result["counts"], {"review": 1})
            job = result["jobs"][0]
            preview = project / job["outputs"][0]
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                 "-of", "csv=p=0", str(preview)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(probe.stdout.strip(), "")
            state = json.loads((project / "data/runtime/art-pipeline/jobs.json").read_text())
            self.assertTrue(state["jobs"][job["job_id"]]["render_spec"]["audio"]["mute"])
        finally:
            shutil.rmtree(project)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for audio rendering")
    def test_video_audio_controls_apply_volume_and_fades(self) -> None:
        project = Path(tempfile.mkdtemp())
        try:
            job_dir = project / "media" / "inbox" / "faded-video"
            job_dir.mkdir(parents=True)
            (job_dir / "brief.yaml").write_text(
                "preset: video-preview\nduration: 1\naudio:\n  volume: 0.85\n"
                "  fade_in: 0.1\n  fade_out: 0.1\n",
                encoding="utf-8",
            )
            source = job_dir / "source.mp4"
            art_pipeline.run_command([
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "1.5", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(source),
            ])
            result = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(result["counts"], {"review": 1})
            job = result["jobs"][0]
            preview = project / job["outputs"][0]
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                 "-of", "csv=p=0", str(preview)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(probe.stdout.strip(), "")
        finally:
            shutil.rmtree(project)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for montage rendering")
    def test_multiple_video_clips_are_assembled_as_one_job(self) -> None:
        project = Path(tempfile.mkdtemp())
        try:
            job_dir = project / "media" / "inbox" / "montage"
            job_dir.mkdir(parents=True)
            (job_dir / "brief.yaml").write_text(
                "preset: video-preview\nclips:\n  - intro.mp4\n  - main.mp4\n",
                encoding="utf-8",
            )
            for name, color in (("intro.mp4", "red"), ("main.mp4", "blue")):
                art_pipeline.run_command([
                    "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:size=320x180:rate=10",
                    "-t", "0.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(job_dir / name),
                ])
            result = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(result["counts"], {"review": 1})
            job = result["jobs"][0]
            state = json.loads((project / "data/runtime/art-pipeline/jobs.json").read_text())
            record = state["jobs"][job["job_id"]]
            self.assertEqual(record["sources"], [
                "media/inbox/montage/intro.mp4",
                "media/inbox/montage/main.mp4",
            ])
            self.assertTrue((project / record["assembly_source"]).is_file())
            preview = project / job["outputs"][0]
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(preview)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertGreater(float(probe.stdout.strip()), 0.9)
            approved = art_pipeline.approve([job["job_id"]], project_root=project)
            self.assertEqual(len(approved["approved"]), 1)
            self.assertTrue((project / "media/exports" / job["job_id"] / "master.mp4").is_file())
        finally:
            shutil.rmtree(project)

    def test_brief_revision_creates_a_new_deterministic_job(self) -> None:
        project = self.make_project()
        try:
            first = art_pipeline.process_once(project_root=project, stability_seconds=0)
            first_id = first["jobs"][0]["job_id"]
            brief = project / "media" / "inbox" / "social-card" / "brief.yaml"
            brief.write_text("preset: image-square\ncolor: bw\n", encoding="utf-8")
            second = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(second["counts"], {"review": 1})
            second_id = second["jobs"][0]["job_id"]
            self.assertNotEqual(first_id, second_id)
            state = json.loads((project / "data/runtime/art-pipeline/jobs.json").read_text())
            self.assertEqual(len(state["jobs"]), 2)
            self.assertEqual(state["jobs"][second_id]["render_spec"]["color"], "bw")
            self.assertTrue(state["jobs"][second_id]["render_spec_sha256"])
        finally:
            shutil.rmtree(project)

    def test_malformed_brief_does_not_block_other_deliveries(self) -> None:
        project = self.make_project()
        try:
            broken_dir = project / "media" / "inbox" / "broken-brief"
            broken_dir.mkdir(parents=True)
            (broken_dir / "brief.json").write_text("{not-json", encoding="utf-8")
            source = broken_dir / "source.png"
            art_pipeline.run_command(["magick", "-size", "12x12", "xc:#00aa44", str(source)])
            result = art_pipeline.process_once(project_root=project, stability_seconds=0)
            self.assertEqual(result["counts"], {"review": 1, "failed": 1})
            self.assertTrue(source.is_file())
        finally:
            shutil.rmtree(project)


if __name__ == "__main__":
    unittest.main()
