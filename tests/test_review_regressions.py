import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from requests import Timeout

import main
from scripts import install_skill, skill_pipeline as skill


class PrepareIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.srt = self.root / "source.srt"
        self.srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        self.meta = {"title": "Test", "uploader": "Test", "pub_date": "2026-10-16",
                     "duration_fmt": "0:01", "bvid": "BV17x411w7KC", "aid": 170001}

    def prepare(self, url, local_meta=False):
        meta_path = self.root / "meta.json"
        meta_path.write_text(json.dumps(self.meta), encoding="utf-8")
        args = argparse.Namespace(link=url, meta=str(meta_path) if local_meta else None,
                                  srt=str(self.srt), name=None, output_dir=str(self.root / "notes"))
        with patch.object(skill, "fetch_video_meta", return_value=self.meta) as fetch, \
                contextlib.redirect_stdout(io.StringIO()):
            skill.prepare(args)
        return fetch

    def test_av_identity_is_verified_and_saved_as_bv_url(self):
        for local_meta in (False, True):
            with self.subTest(local_meta=local_meta):
                fetch = self.prepare("https://www.bilibili.com/video/av170001", local_meta)
                task = skill.load_json(self.root / "notes/.watch2read/BV17x411w7KC/task.json")
                self.assertEqual(task["url"], "https://www.bilibili.com/video/BV17x411w7KC")
                self.assertEqual(fetch.call_count, 0 if local_meta else 1)

    def test_av_identity_mismatch_is_rejected_without_output(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.prepare("https://www.bilibili.com/video/av999999")
        self.assertFalse((self.root / "notes").exists())

    def test_metadata_id_must_match_whole_video_id(self):
        cases = [("BV17x411", "https://www.bilibili.com/video/BV17x411w7KC"),
                 ("abc", "https://www.youtube.com/watch?v=abcdef")]
        for bvid, url in cases:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "does not match"):
                self.meta["bvid"] = bvid
                self.prepare(url)


class InstallerSymlinkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "skill"
        self.target.mkdir()

    def test_force_rejects_package_file_symlink_before_writing(self):
        outside = self.root / "outside.txt"
        outside.write_text("Preserve me", encoding="utf-8")
        (self.target / "SKILL.md").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "must not be symlinks"):
            install_skill.install(self.target, force=True)
        self.assertEqual(outside.read_text(encoding="utf-8"), "Preserve me")
        self.assertFalse((self.root / ".watch2read-backups").exists())

    def test_force_rejects_symlinked_package_parent(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "scripts").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not be symlinks"):
            install_skill.install(self.target, force=True)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.target / "SKILL.md").exists())

    def test_force_rejects_dangling_package_symlink(self):
        (self.target / "main.py").symlink_to(self.root / "missing.py")
        with self.assertRaisesRegex(ValueError, "must not be symlinks"):
            install_skill.install(self.target, force=True)
        self.assertFalse((self.root / "missing.py").exists())

    def test_backup_preserves_unrelated_symlinks_without_dereferencing(self):
        outside = self.root / "outside.txt"
        outside.write_text("Private content", encoding="utf-8")
        (self.target / "personal-link").symlink_to(outside)
        with contextlib.redirect_stdout(io.StringIO()):
            install_skill.install(self.target, force=True)
        backups = list((self.root / ".watch2read-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "personal-link").is_symlink())
        self.assertEqual((backups[0] / "personal-link").readlink(), outside)
        self.assertEqual(outside.read_text(encoding="utf-8"), "Private content")


class BatchNormalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"base_url": "unused", "api_key": "unused"}), encoding="utf-8")
        self.valid = "https://www.bilibili.com/video/BV17x411w7KC"

    def args(self, links, pipeline=False):
        return argparse.Namespace(links=links, keep_all=False, config=str(self.config),
                                  pipeline=pipeline, srt="single-only.srt", name="Single only",
                                  output_dir=str(self.root / "notes"))

    def test_bad_link_does_not_stop_serial_batch(self):
        for error in (Timeout("Short URL timeout"), ValueError("Unsupported URL")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(main, "normalize_video_url", side_effect=[error, self.valid]), \
                    patch.object(main, "process_single_video", return_value="notes/Test.md") as process, \
                    patch.object(main, "write_notes_index") as index, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as stopped:
                main.run(self.args(["https://b23.tv/expired", self.valid]))
            self.assertEqual(stopped.exception.code, 1)
            process.assert_called_once()
            self.assertEqual(process.call_args.kwargs["video_url"], self.valid)
            self.assertIsNone(process.call_args.kwargs["srt_file"])
            self.assertIsNone(process.call_args.kwargs["name_override"])
            index.assert_called_once()

    def test_bad_link_does_not_stop_pipeline_or_hide_processing_failures(self):
        valid2 = "https://www.youtube.com/watch?v=abcdef"
        with patch.object(main, "normalize_video_url", side_effect=[Timeout("Timeout"), self.valid, valid2]), \
                patch.object(main, "run_pipeline", return_value=(["notes/Test.md"], [valid2])) as pipeline, \
                patch.object(main, "write_notes_index"), \
                contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as stopped:
            main.run(self.args(["https://b23.tv/expired", self.valid, valid2], pipeline=True))
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(pipeline.call_args.args[0], [self.valid, valid2])
        self.assertIn(valid2, output.getvalue())
        self.assertIn("1/3", output.getvalue())

    def test_all_invalid_links_fail_cleanly_without_processing(self):
        with patch.object(main, "normalize_video_url", side_effect=ValueError("Unsupported URL")), \
                patch.object(main, "process_single_video") as process, \
                patch.object(main, "run_pipeline") as pipeline, \
                patch.object(main, "write_notes_index"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as stopped:
            main.run(self.args(["invalid", "invalid2"], pipeline=True))
        self.assertEqual(stopped.exception.code, 1)
        process.assert_not_called()
        pipeline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
