import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import main
import update


class ReadmeIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.readme = self.root / "README.md"
        self.notes = self.root / "notes"
        self.notes.mkdir()
        self.md_path = self.notes / "New note.md"
        self.meta = {
            "title": "New | title",
            "uploader": "An | uploader",
            "pub_date": "2026-10-16 08:00:00",
            "duration_fmt": "10:00",
            "bvid": "BVnew",
        }
        self.url = "https://www.bilibili.com/video/BVnew"

    def write_table(self, rows=()):
        self.readme.write_text(
            "Before\n" + main.TABLE_START + "\n"
            "| Date | Source | Uploader | Title | Duration | Notes |\n"
            "|---|---|---|---|---|---|\n"
            + "\n".join(rows) + "\n" + main.TABLE_END + "\nAfter\n",
            encoding="utf-8",
        )

    def update_table(self, **kwargs):
        return main.update_readme_table(
            self.meta, self.url, str(self.md_path),
            readme_path=str(self.readme), **kwargs,
        )

    def data_rows(self):
        content = self.readme.read_text(encoding="utf-8")
        block = content.split(main.TABLE_START)[1].split(main.TABLE_END)[0]
        return block.strip().splitlines()[2:]

    def test_migration_preserves_escaped_pipes_and_existing_method(self):
        self.write_table([
            r"| 2026-08-01 | source | A \| B | [Old \| title](old) | 2:00 | [Old](notes/old.md) |",
            "| 2026-09-01 | source | U | [Other](other) | 3:00 | [Other](notes/other.md) | skill |",
        ])
        self.assertTrue(self.update_table(processing_method="skill"))
        rows = self.data_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual([main._table_cells(row)[-1] for row in rows], ["skill", "skill", "api"])
        self.assertEqual(main._table_cells(rows[-1])[2], r"A \| B")
        self.assertEqual(main._table_cells(rows[0])[2], r"An \| uploader")
        self.assertIn("处理方式", self.readme.read_text(encoding="utf-8"))

    def test_upsert_is_idempotent_and_switches_only_target_method(self):
        self.write_table([
            "| 2026-09-01 | source | U | [Other](other) | 3:00 | [Other](notes/other.md) | skill |",
        ])
        self.update_table(processing_method="skill")
        first = self.readme.read_bytes()
        self.update_table(processing_method="skill")
        self.assertEqual(self.readme.read_bytes(), first)
        self.update_table()
        rows = self.data_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([main._table_cells(row)[-1] for row in rows], ["api", "skill"])

    def test_short_url_upsert_also_matches_exact_video_url(self):
        self.url = "https://b23.tv/short"
        self.write_table([
            "| 2026-10-16 | source | U | [Old](https://b23.tv/short) | 3:00 | [Old](old.md) |",
        ])
        self.update_table(processing_method="skill")
        self.assertEqual(len(self.data_rows()), 1)

    def test_custom_readme_uses_escaped_relative_links(self):
        self.readme = self.root / "docs" / "README.md"
        self.readme.parent.mkdir()
        self.md_path = self.notes / "A note (first).md"
        self.write_table()
        self.update_table(note_base_url=None)
        self.assertIn("(../notes/A%20note%20%28first%29.md)", self.data_rows()[0])
        self.assertTrue(self.readme.read_text(encoding="utf-8").startswith("Before\n"))
        self.assertTrue(self.readme.read_text(encoding="utf-8").endswith("\nAfter\n"))

    def test_default_and_custom_site_links(self):
        self.write_table()
        self.update_table()
        self.assertIn(main.NOTE_BASE_URL + "?note=" + quote("New note"), self.data_rows()[0])
        self.update_table(note_base_url="https://example.test/view?mode=reader")
        self.assertIn("https://example.test/view?mode=reader&note=New%20note", self.data_rows()[0])

    def test_missing_or_reversed_markers_do_not_change_readme(self):
        for content in ("Unrelated README\n", main.TABLE_END + "\n" + main.TABLE_START):
            with self.subTest(content=content):
                self.readme.write_text(content, encoding="utf-8")
                self.assertFalse(self.update_table())
                self.assertEqual(self.readme.read_text(encoding="utf-8"), content)

    def test_missing_readme_is_not_created(self):
        self.assertFalse(self.update_table())
        self.assertFalse(self.readme.exists())

    def test_step6_only_reports_success_when_updated(self):
        task = {"meta": self.meta, "url": self.url, "md_path": str(self.md_path),
                "processing_method": "skill", "readme_path": str(self.readme),
                "note_base_url": None}
        with patch.object(main, "update_readme_table", return_value=False) as updater:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main._step6_readme(task)
            self.assertNotIn("已更新", output.getvalue())
            self.assertEqual(updater.call_args.kwargs["processing_method"], "skill")
            self.assertIsNone(updater.call_args.kwargs["note_base_url"])

    def test_notes_index_defaults_beside_output_and_can_be_overridden(self):
        for name in ("z.md", "A.md", "a.json"):
            (self.notes / name).write_text("", encoding="utf-8")
        main.write_notes_index(str(self.notes))
        expected = {"notes": ["A.md", "z.md"]}
        self.assertEqual(json.loads((self.root / "notes-index.json").read_text()), expected)
        custom = self.root / "custom" / "index.json"
        main.write_notes_index(str(self.notes), index_path=str(custom))
        self.assertEqual(json.loads(custom.read_text()), expected)

    def test_partial_api_regeneration_updates_method(self):
        self.write_table()
        self.update_table(processing_method="skill")
        task = update.rebuild_task(
            {"stem": "New note", "video_url": self.url},
            {"meta": self.meta, "results": []}, str(self.notes),
        )
        task["readme_path"] = str(self.readme)
        with patch.object(update, "render_document", return_value="# Updated\n"):
            update._save_and_render(task)
        self.assertEqual(main._table_cells(self.data_rows()[0])[-1], "api")

    def test_api_cli_keeps_all_five_artifacts_by_default(self):
        with patch("sys.argv", ["main.py", "-l", self.url, "-c", "config.json"]):
            with patch.object(main, "run") as run:
                main.main()
        args = run.call_args.args[0]
        self.assertTrue(all((args.keep_srt, args.keep_json, args.keep_meta, args.keep_segments)))

    def test_api_processing_writes_standard_artifacts(self):
        self.write_table()
        with patch("sys.argv", ["main.py", "-l", self.url, "-c", "config.json",
                                "--output-dir", str(self.notes)]):
            with patch.object(main, "run") as run:
                main.main()
        args = run.call_args.args[0]
        meta = {**self.meta, "duration": 10}
        srt_content = "1\n00:00:00,000 --> 00:00:10,000\nExample subtitle.\n"
        results = [{"batch_title": "Example", "sections": [
            {"title": "Example", "tldr": "Example.", "start_seconds": 0,
             "content": ["Example subtitle."]},
        ]}]

        def structure(task, *args):
            task["results"] = results

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(main, "download_subtitle", return_value=(srt_content, None)))
            stack.enter_context(patch.object(main, "fetch_video_meta", return_value=meta))
            stack.enter_context(patch.object(main, "segment_video", return_value=(
                [{"title": "Example", "start_seconds": 0}], "meta",
            )))
            stack.enter_context(patch.object(main, "_step4_structure", side_effect=structure))
            stack.enter_context(patch.object(main, "README_PATH", str(self.readme)))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            md_path = main.process_single_video(
                self.url, {"base_url": "https://example.test", "api_key": "test"}, args,
            )
        self.assertTrue(md_path)
        stem = Path(md_path).stem
        self.assertEqual(
            {path.name for path in self.notes.iterdir()},
            {stem + suffix for suffix in (".srt", ".meta.json", ".segments.json", ".json", ".md")},
        )
        self.assertEqual(main._table_cells(self.data_rows()[0])[-1], "api")


if __name__ == "__main__":
    unittest.main()
