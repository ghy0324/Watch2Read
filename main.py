"""
Watch2Read — 将 B 站视频转化为结构化 Markdown 阅读笔记

用法:
    python main.py -l <bilibili_video_url> -c <api_config.json>
    python main.py -l <url1> <url2> <url3> -c api_config.json --keep-all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import requests

from download_subtitle import extract_subtitle, pick_chinese_track, download_srt
from video_meta import fetch_video_meta
from structure_subtitle import parse_srt_content, format_batch_for_prompt, call_model
from segment_video import segment_video, split_entries_by_chapters
from render_markdown import render_document
from md2pdf import md_to_pdf

OUTPUT_DIR = "notes"
README_PATH = "README.md"
TABLE_START = "<!-- VIDEO_TABLE_START -->"
TABLE_END = "<!-- VIDEO_TABLE_END -->"


def make_filename(title: str) -> str:
    """从视频标题生成文件名：在冒号处截断，移除文件系统不允许的字符"""
    for sep in ("：", ":"):
        if sep in title:
            title = title.split(sep, 1)[0]
            break
    title = re.sub(r'[\\/*?"<>|]', "", title).strip()
    return title[:100] if title else "untitled"


def log_step(step: int, total: int, msg: str):
    print(f"\n{'=' * 60}")
    print(f"  [{step}/{total}] {msg}")
    print(f"{'=' * 60}")


def save_file(path: str, content: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def save_json(path: str, data):
    save_file(path, json.dumps(data, ensure_ascii=False, indent=2))


def update_readme_table(meta: dict, video_url: str, md_path: str):
    """在 README.md 视频表格中插入新记录，按发布日期从新到旧排列"""
    if not os.path.exists(README_PATH):
        print("  ⚠️  README.md 不存在，跳过表格更新")
        return

    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    start_idx = content.find(TABLE_START)
    end_idx = content.find(TABLE_END)
    if start_idx == -1 or end_idx == -1:
        print("  ⚠️  README.md 中未找到表格标记，跳过更新")
        return

    table_block = content[start_idx + len(TABLE_START) : end_idx].strip()
    lines = table_block.split("\n") if table_block else []

    header = [
        "| 发布日期 | UP主 | 视频名称 | 时长 | 笔记 |",
        "|----------|------|----------|------|------|",
    ]
    data_rows = [l for l in lines[2:] if l.strip()] if len(lines) > 2 else []

    pub_date = meta["pub_date"].split(" ")[0]
    title_escaped = meta["title"].replace("|", "\\|")
    md_basename = os.path.basename(md_path)
    md_link = md_path.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    uploader = meta["uploader"]
    uploader_url = meta.get("uploader_url", "")
    uploader_md = f"[{uploader}]({uploader_url})" if uploader_url else uploader

    new_row = (
        f"| {pub_date} "
        f"| {uploader_md} "
        f"| [{title_escaped}]({video_url}) "
        f"| {meta['duration_fmt']} "
        f"| [{md_basename}]({md_link}) |"
    )

    bvid = meta.get("bvid", "")
    data_rows = [r for r in data_rows if bvid not in r]
    data_rows.append(new_row)

    def _sort_key(row: str) -> str:
        m = re.match(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|", row)
        return m.group(1) if m else "0000-00-00"

    data_rows.sort(key=_sort_key, reverse=True)

    new_table = "\n".join(header + data_rows)
    new_content = (
        content[: start_idx + len(TABLE_START)]
        + "\n"
        + new_table
        + "\n"
        + content[end_idx:]
    )
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)


# ── Step 函数 ─────────────────────────────────────────────────────
# 每个 step 操作一个 task dict，成功时写入结果字段，失败时抛 RuntimeError。


def _step1_subtitle(task: dict) -> None:
    """下载或读取字幕，填充 task['srt_content'] 和 task['entries']"""
    video_url = task["url"]
    srt_file = task.get("srt_file")

    if srt_file:
        print(f"  使用本地 SRT 文件: {srt_file}")
        with open(srt_file, "r", encoding="utf-8") as f:
            task["srt_content"] = f.read()
        print(f"  ✅ 字幕读取完成 ({len(task['srt_content']) / 1024:.1f} KB)")
    else:
        session = requests.Session()
        print(f"  请求字幕提取: {video_url}")
        info = extract_subtitle(session, video_url)

        if info.get("status") == "解析失败":
            raise RuntimeError("字幕解析失败，该视频可能没有字幕")
        tracks = info.get("subtitleItemVoList", [])
        if not tracks:
            raise RuntimeError("未找到任何字幕轨道")

        print(
            f"  找到 {len(tracks)} 个字幕轨道: "
            + ", ".join(t.get("langDesc", "?") for t in tracks)
        )
        track = pick_chinese_track(tracks)
        if not track:
            raise RuntimeError("未找到中文字幕")

        task["srt_content"] = download_srt(session, track)
        print(f"  ✅ 字幕下载完成 ({len(task['srt_content']) / 1024:.1f} KB)")

    task["entries"] = parse_srt_content(task["srt_content"])
    if not task["entries"]:
        raise RuntimeError("未从字幕中解析到有效条目")

    total_sec = task["entries"][-1]["start_seconds"]
    print(
        f"  字幕条目: {len(task['entries'])} 条, "
        f"总时长 {total_sec // 60}:{total_sec % 60:02d}"
    )


def _step2_metadata(task: dict, output_dir: str, keep_meta: bool) -> None:
    """获取视频元数据，填充 task['meta']、文件路径等"""
    meta = fetch_video_meta(task["url"])
    task["meta"] = meta

    print(f"  标题: {meta['title']}")
    print(f"  UP主: {meta['uploader']}")
    print(f"  发布: {meta['pub_date']}")
    print(f"  时长: {meta['duration_fmt']} ({meta['duration']}s)")
    desc = meta.get("desc", "")
    if desc:
        preview = desc.replace("\n", " ")[:120]
        print(f"  简介: {preview}{'...' if len(desc) > 120 else ''}")
    pinned = meta.get("pinned_comment")
    if pinned:
        preview = pinned["content"].replace("\n", " ")[:120]
        print(
            f"  置顶评论 (@{pinned['user']}): "
            f"{preview}{'...' if len(pinned['content']) > 120 else ''}"
        )
    else:
        print("  置顶评论: 无")

    os.makedirs(output_dir, exist_ok=True)
    stem = task.get("name_override") or make_filename(meta["title"])
    task["stem"] = stem
    task["srt_path"] = os.path.join(output_dir, f"{stem}.srt")
    task["meta_path"] = os.path.join(output_dir, f"{stem}.meta.json")
    task["segments_path"] = os.path.join(output_dir, f"{stem}.segments.json")
    task["json_path"] = os.path.join(output_dir, f"{stem}.json")
    task["md_path"] = os.path.join(output_dir, f"{stem}.md")

    if keep_meta:
        save_json(task["meta_path"], meta)
        print(f"\n  📄 元数据已保存: {task['meta_path']}")


def _step3_segment(
    task: dict, base_url: str, api_key: str, model: str, keep_segments: bool
) -> None:
    """语义分段，填充 task['chapters'] / task['seg_source'] / task['batches']"""
    chapters, seg_source = segment_video(
        task["entries"], task["meta"], base_url, api_key, model
    )
    task["chapters"] = chapters
    task["seg_source"] = seg_source

    source_label = {
        "meta": "简介/置顶评论",
        "llm": "LLM 字幕分析",
    }.get(seg_source, seg_source)
    print(f"  分段来源: {source_label}")
    print(f"  共 {len(chapters)} 个章节:")
    for i, ch in enumerate(chapters):
        t = ch["start_seconds"]
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        ts = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        print(f"    {i + 1:2d}. [{ts}] {ch['title']}")

    if keep_segments:
        save_json(
            task["segments_path"],
            {"source": seg_source, "chapters": chapters},
        )
        print(f"\n  📄 分段已保存: {task['segments_path']}")

    task["batches"] = split_entries_by_chapters(task["entries"], chapters)


def _step4_structure(
    task: dict, base_url: str, api_key: str, model: str, workers: int,
    max_batch_minutes: int = 20,
) -> None:
    """AI 结构化整理，填充 task['results']

    两阶段全并行：
    1. 并行对所有超阈值章节做 LLM 语义子切分
    2. 把所有子批次铺平，一次性并行调用 call_model
    """
    from segment_video import sub_segment_chapter, split_entries_by_chapters

    batches = task["batches"]
    chapters = task["chapters"]
    seg_source = task["seg_source"]
    label = task.get("stem", "")
    max_duration = max_batch_minutes * 60
    max_retries = 5
    print_lock = Lock()
    t_global = time.time()

    def _fmt_range(batch: list[dict]) -> str:
        t0, t1 = batch[0]["start_seconds"], batch[-1]["start_seconds"]
        return f"{t0 // 60}:{t0 % 60:02d} ~ {t1 // 60}:{t1 % 60:02d}"

    # ── Phase 1: 识别长章节，并行子切分 ──────────────────────────
    long_indices = []
    for i, batch in enumerate(batches):
        dur = batch[-1]["start_seconds"] - batch[0]["start_seconds"]
        if dur > max_duration:
            long_indices.append(i)

    # sub_splits[chapter_idx] = list of sub_chapters from LLM
    sub_splits: dict[int, list[dict]] = {}

    if long_indices:
        print(f"  {len(long_indices)} 个章节超过 {max_batch_minutes} 分钟，"
              f"并行语义子切分...\n")

        def _sub_segment(idx: int) -> tuple[int, list[dict]]:
            batch = batches[idx]
            dur = batch[-1]["start_seconds"] - batch[0]["start_seconds"]
            sub_chs = sub_segment_chapter(
                batch, max_batch_minutes, base_url, api_key, model,
            )
            with print_lock:
                tag = f"[{label}] " if label else ""
                ch_title = chapters[idx]["title"] if idx < len(chapters) else ""
                print(f"  ✂️  {tag}[{idx + 1}/{len(batches)}] "
                      f"{ch_title} ({dur // 60}:{dur % 60:02d}) "
                      f"→ {len(sub_chs)} 个子段", flush=True)
            return idx, sub_chs

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_sub_segment, i) for i in long_indices]
            for f in as_completed(futures):
                idx, sub_chs = f.result()
                sub_splits[idx] = sub_chs
        print()

    # ── Phase 2: 铺平所有工作单元 ────────────────────────────────
    # work_units: [(chapter_idx, sub_idx, sub_batch, chapter_title)]
    # sub_idx = -1 表示该章节未切分，是完整批次
    work_units: list[tuple[int, int, list[dict], str]] = []

    for i, batch in enumerate(batches):
        ch_title = chapters[i]["title"] if i < len(chapters) else ""
        if i in sub_splits:
            sub_batches = split_entries_by_chapters(batch, sub_splits[i])
            for si, sb in enumerate(sub_batches):
                work_units.append((i, si, sb, ch_title))
        else:
            work_units.append((i, -1, batch, ch_title))

    total_units = len(work_units)
    print(f"  共 {len(batches)} 个章节，展开为 {total_units} 个工作单元，"
          f"并发: {workers}，模型: {model}\n")

    # ── Phase 3: 并行处理所有工作单元 ─────────────────────────────
    # results_parts[chapter_idx] = list of (sub_idx, result_dict)
    results_parts: dict[int, list[tuple[int, dict]]] = {
        i: [] for i in range(len(batches))
    }

    def _process_unit(
        ch_idx: int, sub_idx: int, batch: list[dict], ch_title: str
    ) -> tuple[int, int, dict]:
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                t_start = time.time()
                result = call_model(
                    batch_text=format_batch_for_prompt(batch),
                    batch_idx=ch_idx,
                    total_batches=len(batches),
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    chapter_title=ch_title,
                )
                if seg_source == "meta" and ch_title:
                    result["batch_title"] = ch_title
                elapsed = time.time() - t_start
                with print_lock:
                    tag = f"[{label}] " if label else ""
                    retry_tag = f" (retry {attempt - 1})" if attempt > 1 else ""
                    sub_tag = f".{sub_idx + 1}" if sub_idx >= 0 else ""
                    print(
                        f"  ✅ {tag}[{ch_idx + 1}{sub_tag}/{len(batches)}] "
                        f"{_fmt_range(batch)} "
                        f"({elapsed:.1f}s{retry_tag}) "
                        f"{result.get('batch_title', '')}",
                        flush=True,
                    )
                return ch_idx, sub_idx, result
            except Exception as exc:
                last_err = exc
                wait = min(2**attempt, 30)
                with print_lock:
                    tag = f"[{label}] " if label else ""
                    print(
                        f"  ⚠️  {tag}[{ch_idx + 1}/{len(batches)}] "
                        f"{_fmt_range(batch)} "
                        f"第 {attempt} 次失败 ({type(exc).__name__})，"
                        f"{wait}s 后重试 ..."
                        if attempt < max_retries
                        else "已达最大重试次数",
                        flush=True,
                    )
                if attempt < max_retries:
                    time.sleep(wait)
        raise RuntimeError(
            f"批次 {ch_idx + 1} ({_fmt_range(batch)}) "
            f"在 {max_retries} 次尝试后仍失败: {last_err}"
        )

    failed: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_unit, ch_idx, sub_idx, batch, ch_title): (ch_idx, sub_idx)
            for ch_idx, sub_idx, batch, ch_title in work_units
        }
        for future in as_completed(futures):
            ch_idx, sub_idx = futures[future]
            try:
                _, _, result = future.result()
                results_parts[ch_idx].append((sub_idx, result))
            except Exception as exc:
                failed.append(str(exc))

    if failed:
        raise RuntimeError(
            f"{len(failed)} 个批次最终失败: " + "; ".join(failed)
        )

    # ── Phase 4: 合并结果 ────────────────────────────────────────
    final_results: list[dict] = []
    for i in range(len(batches)):
        parts = results_parts[i]
        if len(parts) == 1 and parts[0][0] == -1:
            # 未切分的章节，直接使用
            final_results.append(parts[0][1])
        else:
            # 长章节：按 sub_idx 排序后合并 sections
            parts.sort(key=lambda x: x[0])
            ch_title = chapters[i]["title"] if i < len(chapters) else ""
            merged_sections = []
            batch_title = ch_title or ""
            for _, part_result in parts:
                merged_sections.extend(part_result.get("sections", []))
                if not batch_title:
                    batch_title = part_result.get("batch_title", "")
            if seg_source == "meta" and ch_title:
                batch_title = ch_title
            final_results.append({
                "batch_title": batch_title,
                "sections": merged_sections,
            })

    task["results"] = final_results
    elapsed_total = time.time() - t_global
    section_count = sum(len(b.get("sections", [])) for b in task["results"])
    print(
        f"\n  结构化完成，共 {section_count} 个章节，"
        f"总耗时 {elapsed_total:.1f}s"
    )


def _step5_render(
    task: dict, keep_srt: bool, keep_json: bool, to_pdf: bool = False
) -> None:
    """渲染 Markdown 并保存可选中间文件"""
    md_content = render_document(task["results"], task["url"], task["meta"])
    save_file(task["md_path"], md_content)
    print(f"  ✅ 已保存至: {task['md_path']}")

    if to_pdf:
        pdf_path = md_to_pdf(task["md_path"])
        task["pdf_path"] = pdf_path
        print(f"  ✅ PDF 已生成: {pdf_path}")

    if keep_srt:
        save_file(task["srt_path"], task["srt_content"])
        print(f"  📄 字幕已保存: {task['srt_path']}")
    if keep_json:
        save_json(task["json_path"], task["results"])
        print(f"  📄 JSON 已保存: {task['json_path']}")


def _step6_readme(task: dict) -> None:
    """更新 README 视频表格"""
    update_readme_table(task["meta"], task["url"], task["md_path"])
    print("  ✅ README.md 已更新")


# ── 执行模式 ──────────────────────────────────────────────────────


def process_single_video(
    video_url: str,
    config: dict,
    args: argparse.Namespace,
    srt_file: str | None = None,
    name_override: str | None = None,
) -> str:
    """串行处理单个视频，返回生成的 Markdown 文件路径（失败返回空字符串）"""
    task: dict = {
        "url": video_url,
        "srt_file": srt_file,
        "name_override": name_override,
    }
    base_url = config["base_url"]
    api_key = config["api_key"]
    model = config.get("model", "qwen3.5-flash")

    try:
        log_step(1, 6, "下载视频字幕")
        _step1_subtitle(task)

        log_step(2, 6, "获取视频元数据")
        _step2_metadata(task, args.output_dir, args.keep_meta)

        log_step(3, 6, "视频内容分段")
        _step3_segment(task, base_url, api_key, model, args.keep_segments)

        log_step(4, 6, "AI 结构化整理字幕")
        _step4_structure(task, base_url, api_key, model, args.workers, args.max_batch_minutes)

        log_step(5, 6, "渲染 Markdown 文档")
        _step5_render(task, args.keep_srt, args.keep_json, args.to_pdf)

        log_step(6, 6, "更新 README 视频表格")
        _step6_readme(task)

        return task["md_path"]
    except RuntimeError as e:
        print(f"  ❌ {e}")
        return ""


def run_pipeline(
    links: list[str], config: dict, args: argparse.Namespace
) -> tuple[list[str], list[str]]:
    """流水线模式：所有视频按步骤统一推进，LLM 步骤跨视频并行执行

    Returns:
        (succeeded_md_paths, failed_urls)
    """
    tasks: list[dict] = [{"url": url} for url in links]
    n = len(tasks)
    base_url = config["base_url"]
    api_key = config["api_key"]
    model = config.get("model", "qwen3.5-flash")

    def _label(task: dict) -> str:
        return task.get("stem", task["url"])

    def _for_each_serial(step_num: int, name: str, fn, *extra_args):
        log_step(step_num, 6, f"{name}（{n} 个视频，串行）")
        for i, task in enumerate(tasks):
            if task.get("failed"):
                print(f"\n  ⏭️  [{i + 1}/{n}] 跳过: {_label(task)}")
                continue
            print(f"\n  ── [{i + 1}/{n}] {_label(task)} ──")
            try:
                fn(task, *extra_args)
            except Exception as e:
                task["failed"] = True
                task["error"] = str(e)
                print(f"  ❌ {e}")

    def _for_each_parallel(step_num: int, name: str, fn, *extra_args):
        active = [(i, t) for i, t in enumerate(tasks) if not t.get("failed")]
        skipped = [(i, t) for i, t in enumerate(tasks) if t.get("failed")]
        log_step(
            step_num, 6,
            f"{name}（{len(active)} 个视频并行"
            + (f"，{len(skipped)} 个跳过" if skipped else "")
            + "）",
        )
        for i, t in skipped:
            print(f"  ⏭️  [{i + 1}/{n}] 跳过: {_label(t)}")
        if not active:
            return

        def _run(idx_and_task):
            i, task = idx_and_task
            print(f"\n  ── [{i + 1}/{n}] {_label(task)} ──")
            try:
                fn(task, *extra_args)
            except Exception as e:
                task["failed"] = True
                task["error"] = str(e)
                print(f"  ❌ [{_label(task)}] {e}")

        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            futures = [pool.submit(_run, item) for item in active]
            for f in as_completed(futures):
                f.result()

    _for_each_serial(1, "下载视频字幕", _step1_subtitle)
    _for_each_serial(
        2, "获取视频元数据", _step2_metadata,
        args.output_dir, args.keep_meta,
    )
    _for_each_parallel(
        3, "视频内容分段", _step3_segment,
        base_url, api_key, model, args.keep_segments,
    )
    _for_each_parallel(
        4, "AI 结构化整理字幕", _step4_structure,
        base_url, api_key, model, args.workers, args.max_batch_minutes,
    )
    _for_each_serial(
        5, "渲染 Markdown 文档", _step5_render,
        args.keep_srt, args.keep_json, args.to_pdf,
    )
    _for_each_serial(6, "更新 README 视频表格", _step6_readme)

    succeeded = [t["md_path"] for t in tasks if not t.get("failed")]
    failed_urls = [t["url"] for t in tasks if t.get("failed")]
    return succeeded, failed_urls


def run(args: argparse.Namespace):
    if args.keep_all:
        args.keep_srt = args.keep_json = args.keep_meta = args.keep_segments = True

    links = [url.split("?")[0].rstrip("/") for url in args.links]

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    total_videos = len(links)
    use_pipeline = args.pipeline and total_videos > 1

    if total_videos > 1:
        if args.srt:
            print("  ⚠️  多视频模式下 --srt 参数被忽略", file=sys.stderr)
        if args.name:
            print("  ⚠️  多视频模式下 --name 参数被忽略", file=sys.stderr)

    if use_pipeline:
        succeeded, failed_urls = run_pipeline(links, config, args)
    else:
        srt_file = args.srt if total_videos == 1 else None
        name_override = args.name if total_videos == 1 else None

        succeeded: list[str] = []
        failed_urls: list[str] = []

        for vi, video_url in enumerate(links):
            if total_videos > 1:
                print(f"\n{'#' * 60}")
                print(f"  视频 [{vi + 1}/{total_videos}]: {video_url}")
                print(f"{'#' * 60}")

            md_path = process_single_video(
                video_url=video_url,
                config=config,
                args=args,
                srt_file=srt_file,
                name_override=name_override,
            )
            srt_file = None
            name_override = None

            if md_path:
                succeeded.append(md_path)
            else:
                failed_urls.append(video_url)

    print(f"\n{'=' * 60}")
    if total_videos == 1:
        if succeeded:
            print(f"  🎉 完成! Markdown 笔记: {succeeded[0]}")
        else:
            print("  ❌ 处理失败")
    else:
        print(f"  🎉 全部完成! 成功 {len(succeeded)}/{total_videos} 个视频")
        for p in succeeded:
            print(f"     ✅ {p}")
        for u in failed_urls:
            print(f"     ❌ {u}")
    print(f"{'=' * 60}\n")

    if failed_urls:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Watch2Read — 将 B 站视频转化为结构化 Markdown 阅读笔记",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-l", "--link", dest="links", nargs="+", required=True,
        metavar="URL", help="B 站视频链接（支持多个，空格分隔）",
    )
    parser.add_argument("-c", "--config", required=True, help="API 配置文件 (JSON)")
    parser.add_argument(
        "--pipeline", action="store_true",
        help="流水线模式：所有视频按步骤统一推进，LLM 步骤并行（仅多视频时生效）",
    )
    parser.add_argument(
        "--keep-srt", action="store_true", help="保留中间 SRT 字幕文件"
    )
    parser.add_argument(
        "--keep-json", action="store_true", help="保留中间 JSON 结构化文件"
    )
    parser.add_argument(
        "--keep-meta", action="store_true", help="保留视频元数据 JSON 文件"
    )
    parser.add_argument(
        "--keep-segments", action="store_true", help="保留分段结果 JSON 文件"
    )
    parser.add_argument(
        "--keep-all", action="store_true",
        help="保留所有中间过程文件（等同于同时指定所有 --keep-* 参数）",
    )
    parser.add_argument(
        "--name", default=None, help="输出文件名（不含扩展名），仅单视频时有效"
    )
    parser.add_argument(
        "--output-dir", default=OUTPUT_DIR, help=f"输出目录 (默认: {OUTPUT_DIR})"
    )
    parser.add_argument(
        "--srt", default=None,
        help="使用已有的 SRT 字幕文件（跳过在线下载，仅单视频时有效）",
    )
    parser.add_argument(
        "--workers", type=int, default=5, help="并发线程数 (默认: 5)"
    )
    parser.add_argument(
        "--max-batch-minutes", type=int, default=20,
        help="单次 LLM 调用处理的最大时长（分钟），超过则自动切分为子批次 (默认: 20)",
    )
    parser.add_argument(
        "--to-pdf", action="store_true",
        help="渲染 Markdown 后同时转换为 PDF（默认关闭）",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
