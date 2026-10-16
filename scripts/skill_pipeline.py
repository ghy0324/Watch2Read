"""File-based Watch2Read workflow. Intelligence is supplied by the host agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from download_subtitle import download_subtitle
from main import (NOTE_BASE_URL, TABLE_END, TABLE_START, make_filename, save_file,
                  save_json, update_readme_table, write_notes_index)
from render_markdown import render_document
from structure_subtitle import format_batch_for_prompt, parse_srt_content, split_into_batches
from video_meta import extract_aid, extract_bvid, fetch_video_meta, normalize_video_url


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def prepare(args):
    url = normalize_video_url(args.link)
    meta = load_json(args.meta) if args.meta else fetch_video_meta(url)
    for key in ("title", "uploader", "pub_date", "duration_fmt"):
        require_text(meta.get(key), f"meta.{key}")
    meta.setdefault("bvid", "")
    if meta["bvid"]:
        if urlparse(url).hostname == "www.bilibili.com":
            bvid = extract_bvid(url)
            matches = (bvid == meta["bvid"] if bvid else
                       extract_aid(url) == str(meta.get("aid", "")))
            if matches:
                url = f"https://www.bilibili.com/video/{meta['bvid']}"
        else:
            matches = parse_qs(urlparse(url).query).get("v", [""])[0] == meta["bvid"]
        if not matches:
            raise ValueError("Metadata video ID does not match the requested URL")
    stem = args.name or make_filename(meta["title"])
    if stem in {".", ".."} or Path(stem).name != stem or any(c in stem for c in '\\/:*?"<>|'):
        raise ValueError("--name must be a filename, not a path")
    out = Path(args.output_dir).resolve()
    video_id = make_filename(meta.get("bvid") or digest(url)[:16])
    work = out / ".watch2read" / video_id
    work.mkdir(parents=True, exist_ok=True)
    save_json(str(work / "meta.json"), meta)
    if args.srt:
        srt = Path(args.srt).read_text(encoding="utf-8-sig")
    elif (work / "source.srt").exists():
        srt = (work / "source.srt").read_text(encoding="utf-8")
    else:
        srt, _ = download_subtitle(url, output=str(work / "source.srt"))
    entries = parse_srt_content(srt)
    if not entries:
        raise ValueError("No subtitle entries; do not fabricate notes from a title or description")
    if any(a["start_seconds"] > b["start_seconds"] for a, b in zip(entries, entries[1:])):
        raise ValueError("Subtitle timestamps are not chronological")
    save_file(str(work / "source.srt"), srt)
    save_json(str(work / "entries.json"), entries)
    # Only the overview is sampled; every batch below retains every subtitle entry.
    step = max(1, (len(format_batch_for_prompt(entries)) + 79999) // 80000)
    save_file(str(work / "overview.txt"), format_batch_for_prompt(entries[::step]))
    save_json(str(work / "task.json"), {
        "url": url, "stem": stem, "output_dir": str(out),
        "source_hash": digest(entries), "processing_method": "skill",
    })
    print(f"WORK={work}\nTITLE={meta['title']}\nENTRIES={len(entries)}\n"
          f"OVERVIEW_SAMPLE_STEP={step}")


def validate_segments(segments, entries):
    if not isinstance(segments, dict) or segments.get("source") not in {"meta", "llm"}:
        raise ValueError("segments.source must be meta or llm")
    chapters = segments.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("segments.chapters must not be empty")
    previous = -1
    times = {entry["start_seconds"] for entry in entries}
    for index, chapter in enumerate(chapters):
        require_text(chapter.get("title"), f"chapter {index + 1} title")
        start = chapter.get("start_seconds")
        if type(start) is not int or start <= previous or start > entries[-1]["start_seconds"]:
            raise ValueError("Chapter timestamps must be nonnegative, strictly increasing and within subtitles")
        if index == 0 and start > entries[0]["start_seconds"]:
            raise ValueError("First chapter must include the opening subtitles (use 0)")
        if index > 0 and segments["source"] == "llm" and start not in times:
            raise ValueError("Agent chapter timestamps must occur in the subtitles")
        previous = start
    return chapters


def split(args):
    work = Path(args.work).resolve()
    entries = load_json(work / "entries.json")
    task = load_json(work / "task.json")
    if task["source_hash"] != digest(entries):
        raise ValueError("Subtitle entries changed; run prepare again")
    segments = load_json(args.chapters)
    chapters = validate_segments(segments, entries)
    if args.max_batch_minutes <= 0:
        raise ValueError("--max-batch-minutes must be positive")
    plan = digest({"source": task["source_hash"], "segments": segments,
                   "max_batch_minutes": args.max_batch_minutes})[:16]
    units = []
    for index, chapter in enumerate(chapters):
        end = chapters[index + 1]["start_seconds"] if index + 1 < len(chapters) else float("inf")
        batch = [e for e in entries if chapter["start_seconds"] <= e["start_seconds"] < end]
        if not batch:
            raise ValueError(f"Chapter {index + 1} has no subtitles; merge or correct its boundary")
        for sub, part in enumerate(split_into_batches(batch, args.max_batch_minutes * 60)):
            name = f"{index + 1:03d}_{sub + 1:02d}"
            batch_file = f"batches/{plan}/{name}.txt"
            result_file = f"results/{plan}/{name}.json"
            save_file(str(work / batch_file), format_batch_for_prompt(part) + "\n")
            (work / result_file).parent.mkdir(parents=True, exist_ok=True)
            units.append({"chapter": index, "sub": sub, "batch_file": batch_file,
                          "result_file": result_file, "start_seconds": part[0]["start_seconds"],
                          "end_seconds": part[-1]["start_seconds"], "entry_count": len(part),
                          "timestamps": sorted({e["start_seconds"] for e in part}),
                          "batch_hash": digest((work / batch_file).read_text(encoding="utf-8"))})
    save_json(str(work / "segments.json"), segments)
    manifest = {"source_hash": task["source_hash"], "segments": segments, "units": units}
    save_json(str(work / "manifest.json"), manifest)
    task["manifest_hash"] = digest(manifest)
    save_json(str(work / "task.json"), task)
    print(f"MANIFEST={work / 'manifest.json'}\nCHAPTERS={len(chapters)}\nUNITS={len(units)}")
    for unit in units:
        print(f"{unit['batch_file']} -> {unit['result_file']} ({unit['entry_count']} entries)")


def validate_result(result, unit):
    if not isinstance(result, dict):
        raise ValueError("Result must be an object")
    require_text(result.get("batch_title"), "batch_title")
    sections = result.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("sections must not be empty")
    previous = -1
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            raise ValueError("Each section must be an object")
        for key in ("title", "tldr"):
            require_text(section.get(key), key)
        start = section.get("start_seconds")
        if type(start) is not int or start not in unit["timestamps"] or start < previous:
            raise ValueError(
                f"Section {index + 1} ({section['title']!r}) timestamp {start!r} must be an actual "
                f"chronological subtitle timestamp in {unit['timestamps'][0]}..{unit['timestamps'][-1]} "
                f"(previous: {previous}); consult this unit's timestamps in the manifest"
            )
        if index == 0 and start != unit["start_seconds"]:
            raise ValueError(f"First section ({section['title']!r}) must start at "
                             f"{unit['start_seconds']}, not {start}")
        points = section.get("content")
        if not isinstance(points, list) or not points:
            raise ValueError("content must be a non-empty list of strings")
        for point in points:
            require_text(point, "content item")
        previous = start


def merge_results(work, manifest):
    chapters = manifest["segments"]["chapters"]
    final = [{"batch_title": ch["title"], "sections": []} for ch in chapters]
    if not manifest.get("units"):
        raise ValueError("No work units")
    for unit in manifest["units"]:
        batch_path = work / unit["batch_file"]
        result_path = work / unit["result_file"]
        if digest(batch_path.read_text(encoding="utf-8")) != unit["batch_hash"]:
            raise ValueError(f"Batch was modified: {batch_path}; run split again")
        if not result_path.is_file():
            raise ValueError(f"Missing result: {result_path}; no final files were published")
        result = load_json(result_path)
        try:
            validate_result(result, unit)
        except ValueError as exc:
            raise ValueError(f"{result_path}: {exc}") from exc
        final[unit["chapter"]]["sections"].extend(result["sections"])
    if any(not chapter["sections"] for chapter in final):
        raise ValueError("At least one chapter has no results")
    return final


def finalize(args):
    work = Path(args.work).resolve()
    task = load_json(work / "task.json")
    manifest = load_json(work / "manifest.json")
    if task.get("manifest_hash") != digest(manifest):
        raise ValueError("Manifest changed or is outdated; run split again instead of editing it")
    entries = parse_srt_content((work / "source.srt").read_text(encoding="utf-8"))
    if digest(entries) != manifest["source_hash"] or task["source_hash"] != manifest["source_hash"]:
        raise ValueError("Stale manifest: subtitles changed; run split again")
    results = merge_results(work, manifest)
    meta = load_json(work / "meta.json")
    output = Path(task["output_dir"])
    stem = task["stem"]
    md_path = output / f"{stem}.md"
    artifacts = {
        ".md": render_document(results, task["url"], meta),
        ".srt": (work / "source.srt").read_text(encoding="utf-8"),
        ".meta.json": json.dumps(meta, ensure_ascii=False, indent=2),
        ".segments.json": json.dumps(manifest["segments"], ensure_ascii=False, indent=2),
        ".json": json.dumps(results, ensure_ascii=False, indent=2),
    }
    for suffix, content in artifacts.items():
        path = output / f"{stem}{suffix}"
        if path.exists() and path.read_text(encoding="utf-8") != content and not args.overwrite:
            raise ValueError(f"Output already exists: {path}. Use another --name or explicitly --overwrite")
    for suffix, content in artifacts.items():
        save_file(str(output / f"{stem}{suffix}"), content)
    if args.to_pdf:
        from md2pdf import md_to_pdf
        md_to_pdf(str(md_path))
    if not args.skip_index:
        readme = Path(args.readme).resolve() if args.readme else output.parent / "README.md"
        if args.readme:
            content = readme.read_text(encoding="utf-8") if readme.exists() else "# Video Notes\n"
            if TABLE_START not in content and TABLE_END not in content:
                save_file(str(readme), content.rstrip() + f"\n\n## 已整理视频\n\n{TABLE_START}\n{TABLE_END}\n")
            elif TABLE_START not in content or TABLE_END not in content:
                raise ValueError("README has incomplete table markers; repair them before indexing")
        # Preserve the existing repository's published-reader links; use local links elsewhere.
        base_url = None
        if readme.is_file() and NOTE_BASE_URL in readme.read_text(encoding="utf-8"):
            base_url = NOTE_BASE_URL
        updated = update_readme_table(meta, task["url"], str(md_path),
                                      processing_method="skill", readme_path=str(readme),
                                      note_base_url=base_url)
        print(f"README_INDEX={'updated' if updated else 'skipped'}")
        write_notes_index(str(output))
    print(f"MARKDOWN={md_path}\nCHAPTERS={len(results)}\n"
          f"SECTIONS={sum(len(r['sections']) for r in results)}\n"
          f"POINTS={sum(len(s['content']) for r in results for s in r['sections'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="Fetch subtitles and metadata, no LLM API")
    p.add_argument("-l", "--link", required=True)
    p.add_argument("--output-dir", default="notes")
    p.add_argument("--name")
    p.add_argument("--srt", help="Use a local SRT instead of downloading")
    p.add_argument("--meta", help="Use verified local metadata instead of fetching")
    p.set_defaults(fn=prepare)
    p = commands.add_parser("split", help="Validate agent chapters and prepare lossless work units")
    p.add_argument("--work", required=True)
    p.add_argument("--chapters", required=True)
    p.add_argument("--max-batch-minutes", type=int, default=20)
    p.set_defaults(fn=split)
    p = commands.add_parser("finalize", help="Validate all results, render shared formats, update indices")
    p.add_argument("--work", required=True)
    p.add_argument("--readme", help="Explicit README path; adds table markers only when requested")
    p.add_argument("--skip-index", action="store_true")
    p.add_argument("--to-pdf", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(fn=finalize)
    args = parser.parse_args()
    try:
        args.fn(args)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
