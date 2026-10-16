import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import install_skill, skill_pipeline as skill
from render_markdown import render_document
from video_meta import normalize_video_url
from update import find_videos


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "notes"
        self.url = "https://www.bilibili.com/video/BV1234567890"
        self.meta = {"title": "Test: title", "bvid": "BV1234567890", "uploader": "Test",
                     "pub_date": "2026-10-16", "duration": 130, "duration_fmt": "2:10"}
        self.srt = self.root / "source.srt"
        self.srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nFirst\n\n"
                            "2\n00:01:02,000 --> 00:01:05,000\nSecond\n\n"
                            "3\n00:02:01,000 --> 00:02:03,000\nThird\n", encoding="utf-8")
        self.meta_path = self.root / "meta.json"
        self.meta_path.write_text(json.dumps(self.meta), encoding="utf-8")
        self.work = self.out / ".watch2read" / self.meta["bvid"]

    def prepare(self):
        args = argparse.Namespace(link=self.url, meta=str(self.meta_path), srt=str(self.srt),
                                  name=None, output_dir=str(self.out))
        with contextlib.redirect_stdout(io.StringIO()), patch("requests.post", side_effect=AssertionError("No LLM API")):
            skill.prepare(args)

    def split(self, segments=None, minutes=1):
        segments = segments or {"source": "meta", "chapters": [{"title": "Chapter", "start_seconds": 0}]}
        path = self.work / "chapters.json"
        path.write_text(json.dumps(segments), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            skill.split(argparse.Namespace(work=str(self.work), chapters=str(path), max_batch_minutes=minutes))
        return skill.load_json(self.work / "manifest.json")

    def results(self, manifest):
        for unit in manifest["units"]:
            skill.save_json(str(self.work / unit["result_file"]), {
                "batch_title": "Unit", "sections": [{"title": "Topic", "tldr": "Summary",
                "start_seconds": unit["start_seconds"], "content": ["A detailed point."]}]})

    def finalize(self, **kwargs):
        options = dict(work=str(self.work), overwrite=False, to_pdf=False, skip_index=False, readme=None)
        options.update(kwargs)
        with contextlib.redirect_stdout(io.StringIO()), patch("requests.post", side_effect=AssertionError("No LLM API")):
            skill.finalize(argparse.Namespace(**options))

    def test_skill_five_outputs_match_shared_renderer_and_index(self):
        self.prepare()
        manifest = self.split()
        self.assertEqual(sum(u["entry_count"] for u in manifest["units"]), 3)
        self.results(manifest)
        readme = self.root / "README.md"
        readme.write_text("# Notes\n<!-- VIDEO_TABLE_START -->\n<!-- VIDEO_TABLE_END -->\n", encoding="utf-8")
        self.finalize()
        files = {p.name for p in self.out.iterdir() if p.is_file()}
        self.assertEqual(files, {"Test" + suffix for suffix in [".srt", ".meta.json", ".segments.json", ".json", ".md"]})
        result = skill.load_json(self.out / "Test.json")
        self.assertEqual((self.out / "Test.md").read_text(), render_document(result, self.url, self.meta))
        self.assertIn("| skill |", readme.read_text())
        self.assertIn("notes/Test.md", readme.read_text())
        self.assertEqual(skill.load_json(self.root / "notes-index.json"), {"notes": ["Test.md"]})
        self.finalize()  # Identical publication and index update are idempotent.
        self.assertEqual(readme.read_text().count("| skill |"), 1)

    def test_missing_result_never_publishes_partial_note(self):
        self.prepare()
        self.split()
        with self.assertRaisesRegex(ValueError, "Missing result"):
            self.finalize()
        self.assertEqual(list(self.out.glob("*.md")), [])
        self.assertFalse((self.root / "README.md").exists())

    def test_metadata_blockquotes_have_no_trailing_whitespace(self):
        meta = dict(self.meta, desc="First line  \n\nLast line  ",
                    pinned_comment={"user": "Author", "content": "Comment  \n\nEnd  "})
        rendered = render_document([], self.url, meta)
        self.assertIn("> **简介**: First line\n>\n> Last line", rendered)
        self.assertIn("> **置顶评论** (@Author): Comment\n>\n> End", rendered)
        self.assertTrue(all(line == line.rstrip() for line in rendered.splitlines()))

    def test_empty_chapter_cannot_shift_following_titles(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "no subtitles"):
            self.split({"source": "meta", "chapters": [
                {"title": "One", "start_seconds": 0}, {"title": "Empty", "start_seconds": 4},
                {"title": "Three", "start_seconds": 6}]})

    def test_reject_invalid_chapters(self):
        self.prepare()
        for times in ([0, 0], [1, 200], [2], [0, -1]):
            with self.subTest(times=times), self.assertRaises(ValueError):
                self.split({"source": "llm", "chapters": [{"title": "T", "start_seconds": t} for t in times]})

    def test_batching_keeps_boundary_entries_once(self):
        self.prepare()
        manifest = self.split(minutes=1)
        self.assertEqual([u["timestamps"] for u in manifest["units"]], [[1], [62, 121]])

    def test_changed_plan_does_not_reuse_stale_results(self):
        self.prepare()
        old = self.split(minutes=1)
        self.results(old)
        new = self.split(minutes=2)
        self.assertNotEqual(old["units"][0]["result_file"], new["units"][0]["result_file"])
        with self.assertRaisesRegex(ValueError, "Missing result"):
            self.finalize()

    def test_timestamp_validation_and_no_partial_publish(self):
        self.prepare()
        manifest = self.split()
        self.results(manifest)
        path = self.work / manifest["units"][0]["result_file"]
        result = skill.load_json(path)
        for invalid in (True, -1, 99, "1"):
            result["sections"][0]["start_seconds"] = invalid
            skill.save_json(str(path), result)
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "timestamp"):
                self.finalize()
            self.assertFalse((self.out / "Test.md").exists())

    def test_result_shape_rejects_empty_or_wrong_content(self):
        unit = {"timestamps": [1], "start_seconds": 1}
        for result in ({}, {"batch_title": "T", "sections": []}, {"batch_title": "T", "sections": [
                {"title": "T", "tldr": "S", "start_seconds": 1, "content": "not a list"}]}):
            with self.assertRaises(ValueError):
                skill.validate_result(result, unit)

    def test_readme_opt_in_and_output_overwrite_guard(self):
        self.prepare()
        self.results(self.split())
        self.finalize()
        self.assertFalse((self.root / "README.md").exists())
        explicit = self.root / "other.md"
        explicit.write_text("# Existing text\n", encoding="utf-8")
        self.finalize(readme=str(explicit))
        self.assertTrue(explicit.read_text().startswith("# Existing text\n"))
        self.assertIn("| skill |", explicit.read_text())
        (self.out / "Test.md").write_text("User changes", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.finalize()
        self.assertEqual((self.out / "Test.md").read_text(), "User changes")

    def test_modified_batch_and_subtitles_rejected(self):
        self.prepare()
        manifest = self.split()
        self.results(manifest)
        batch = self.work / manifest["units"][0]["batch_file"]
        original = batch.read_text()
        batch.write_text(original + "changed")
        with self.assertRaisesRegex(ValueError, "Batch was modified"):
            self.finalize()
        batch.write_text(original)
        (self.work / "source.srt").write_text(self.srt.read_text().replace("First", "Changed"))
        with self.assertRaisesRegex(ValueError, "Stale manifest"):
            self.finalize()

    def test_manifest_cannot_omit_a_work_unit(self):
        self.prepare()
        manifest = self.split()
        self.results(manifest)
        manifest["units"].pop()
        skill.save_json(str(self.work / "manifest.json"), manifest)
        with self.assertRaisesRegex(ValueError, "Manifest changed"):
            self.finalize()

    def test_install_is_self_contained_and_excludes_secrets(self):
        target = self.root / "installed" / "watch2read"
        with contextlib.redirect_stdout(io.StringIO()):
            install_skill.install(target)
        self.assertTrue((target / "main.py").exists())
        self.assertFalse((target / "api_config.json").exists())
        self.assertFalse((target / "notes").exists())
        with self.assertRaises(ValueError):
            install_skill.install(target)
        run = subprocess.run([
            sys.executable, str(target / "scripts/skill_pipeline.py"), "prepare",
            "-l", self.url, "--meta", str(self.meta_path), "--srt", str(self.srt),
            "--output-dir", str(self.out),
        ], cwd=self.root, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("ENTRIES=3", run.stdout)


class VideoURLTests(unittest.TestCase):
    def test_api_update_discovers_skill_outputs_on_both_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            urls = ["https://www.bilibili.com/video/BV1R9pf6SEVh", "https://www.youtube.com/watch?v=E7qB_p1D0Xk"]
            for index, url in enumerate(urls):
                meta = {"title": "Title", "uploader": "Uploader", "uploader_url": "https://www.youtube.com/@channel",
                        "pub_date": "2026-10-16", "duration_fmt": "1:00", "bvid": "ID"}
                (root / f"{index}.md").write_text(render_document([], url, meta), encoding="utf-8")
            self.assertEqual([item["video_url"] for item in find_videos(directory)], urls)

    def test_bilibili_short_redirect_and_tracking(self):
        with patch("video_meta.requests.get") as get:
            get.return_value.url = "https://www.bilibili.com/video/BV1R9pf6SEVh/?share_source=copy"
            self.assertEqual(normalize_video_url("https://b23.tv/ELL41Yu"), "https://www.bilibili.com/video/BV1R9pf6SEVh")
            get.assert_called_once()

    def test_youtube_watch_short_live_and_id(self):
        for url in ("https://youtu.be/abc?t=90", "https://youtube.com/watch?v=abc&list=ignored",
                    "https://www.youtube.com/shorts/abc", "https://youtube.com/live/abc"):
            self.assertEqual(normalize_video_url(url), "https://www.youtube.com/watch?v=abc")

    def test_invalid_url_rejected(self):
        for url in ("https://example.com/BV123", "https://youtube.com/", "not a URL"):
            with self.assertRaises(ValueError):
                normalize_video_url(url)


if __name__ == "__main__":
    unittest.main()
