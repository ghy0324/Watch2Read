"""
Watch2Read — 交互式重新生成已有笔记

用法:
    python update.py -c api_config.json
    python update.py -c api_config.json --pipeline
    python update.py -c api_config.json --output-dir notes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from main import (
    OUTPUT_DIR,
    _step1_subtitle,
    _step2_metadata,
    _step3_segment,
    _step4_structure,
    _step5_render,
    _step6_readme,
    log_step,
    save_file,
    save_json,
    make_filename,
)
from structure_subtitle import (
    parse_srt_content,
    format_batch_for_prompt,
    process_chapter,
)
from segment_video import split_entries_by_chapters
from render_markdown import render_document


def find_videos(output_dir: str) -> list[dict]:
    """扫描输出目录，找到所有已生成的视频笔记及其中间文件"""
    videos = []
    md_files = sorted(Path(output_dir).glob("*.md"))

    for md_path in md_files:
        stem = md_path.stem
        video_url = _extract_video_url(md_path)
        if not video_url:
            continue

        base = md_path.parent
        videos.append({
            "stem": stem,
            "md_path": str(md_path),
            "video_url": video_url,
            "has_srt": (base / f"{stem}.srt").exists(),
            "has_meta": (base / f"{stem}.meta.json").exists(),
            "has_segments": (base / f"{stem}.segments.json").exists(),
            "has_json": (base / f"{stem}.json").exists(),
        })

    return videos


def _extract_video_url(md_path: Path) -> str | None:
    """从 Markdown 文件头部提取 Bilibili 视频链接"""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            # 只读前 10 行
            for _ in range(10):
                line = f.readline()
                if not line:
                    break
                m = re.search(r"\(https://www\.bilibili\.com/video/(BV\w+)\)", line)
                if m:
                    return f"https://www.bilibili.com/video/{m.group(1)}"
    except OSError:
        pass
    return None


def prompt_choice(prompt: str, options: list[str], start: int = 1) -> int:
    """交互式选择，返回 0-based 索引。start 控制显示编号的起始值。"""
    print(f"\n{prompt}\n")
    for i, opt in enumerate(options):
        print(f"  [{start + i}] {opt}")
    print()

    lo, hi = start, start + len(options) - 1
    while True:
        try:
            raw = input("请输入编号: ").strip()
            num = int(raw)
            if lo <= num <= hi:
                return num - start
            print(f"  请输入 {lo} ~ {hi} 之间的数字")
        except ValueError:
            print("  请输入数字")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            sys.exit(0)


def load_intermediate(video: dict, output_dir: str) -> dict:
    """加载可用的中间文件，返回已加载的数据"""
    base = Path(output_dir)
    stem = video["stem"]
    data = {}

    if video["has_meta"]:
        with open(base / f"{stem}.meta.json", "r", encoding="utf-8") as f:
            data["meta"] = json.load(f)
        print(f"  [缓存] 元数据: {stem}.meta.json")

    if video["has_srt"]:
        with open(base / f"{stem}.srt", "r", encoding="utf-8") as f:
            data["srt_content"] = f.read()
        data["entries"] = parse_srt_content(data["srt_content"])
        print(f"  [缓存] 字幕: {stem}.srt ({len(data['entries'])} 条)")

    if video["has_segments"]:
        with open(base / f"{stem}.segments.json", "r", encoding="utf-8") as f:
            seg_data = json.load(f)
        data["chapters"] = seg_data["chapters"]
        data["seg_source"] = seg_data.get("source", "unknown")
        print(f"  [缓存] 分段: {stem}.segments.json ({len(data['chapters'])} 章)")

    if video["has_json"]:
        with open(base / f"{stem}.json", "r", encoding="utf-8") as f:
            data["results"] = json.load(f)
        print(f"  [缓存] 结构化: {stem}.json ({len(data['results'])} 批次)")

    return data


def rebuild_task(video: dict, cached: dict, output_dir: str) -> dict:
    """从 video 信息和缓存数据构建 task dict"""
    stem = video["stem"]
    task = {
        "url": video["video_url"],
        "srt_file": None,
        "name_override": stem,
        "stem": stem,
        "srt_path": os.path.join(output_dir, f"{stem}.srt"),
        "meta_path": os.path.join(output_dir, f"{stem}.meta.json"),
        "segments_path": os.path.join(output_dir, f"{stem}.segments.json"),
        "json_path": os.path.join(output_dir, f"{stem}.json"),
        "md_path": os.path.join(output_dir, f"{stem}.md"),
    }
    task.update(cached)
    return task


def ensure_prerequisites(task: dict, config: dict, output_dir: str, up_to: str) -> None:
    """确保 task 中有运行到指定步骤所需的所有数据，缺失的自动重新获取

    up_to: "srt", "meta", "segments", "structure"
    """
    base_url = config["base_url"]
    api_key = config["api_key"]
    model = config.get("model", "qwen3.5-flash")

    # 字幕
    if "entries" not in task:
        print("\n  字幕缓存不可用，重新下载...")
        _step1_subtitle(task)
        # 保存以便下次复用
        save_file(task["srt_path"], task["srt_content"])
        print(f"  已保存: {task['srt_path']}")

    if up_to == "srt":
        return

    # 元数据
    if "meta" not in task:
        print("\n  元数据缓存不可用，重新获取...")
        _step2_metadata(task, output_dir, keep_meta=True)
    task["stem"] = task.get("name_override") or make_filename(task["meta"]["title"])

    if up_to == "meta":
        return

    # 分段
    if "chapters" not in task:
        print("\n  分段缓存不可用，重新分段...")
        _step3_segment(task, base_url, api_key, model, keep_segments=True)
    else:
        # 仍需 batches
        if "batches" not in task:
            task["batches"] = split_entries_by_chapters(task["entries"], task["chapters"])

    if up_to == "segments":
        return


def redo_full(task: dict, config: dict, output_dir: str, workers: int, max_batch_minutes: int = 20) -> None:
    """完全重新生成：从分段开始重跑 step3~step6"""
    base_url = config["base_url"]
    api_key = config["api_key"]
    model = config.get("model", "qwen3.5-flash")

    ensure_prerequisites(task, config, output_dir, up_to="meta")

    log_step(3, 6, "视频内容分段（重新生成）")
    _step3_segment(task, base_url, api_key, model, keep_segments=True)

    log_step(4, 6, "AI 结构化整理字幕（重新生成）")
    _step4_structure(task, base_url, api_key, model, workers, max_batch_minutes)

    log_step(5, 6, "渲染 Markdown 文档")
    _step5_render(task, keep_srt=True, keep_json=True, to_pdf=task.get("to_pdf", False))

    log_step(6, 6, "更新 README 视频表格")
    _step6_readme(task)

    print(f"\n  完成! Markdown 笔记已更新: {task['md_path']}")


def _save_and_render(task: dict) -> None:
    """保存更新后的 JSON 并重新渲染 Markdown"""
    save_json(task["json_path"], task["results"])
    print(f"\n  已更新: {task['json_path']}")

    md_content = render_document(task["results"], task["url"], task["meta"])
    save_file(task["md_path"], md_content)
    print(f"  已更新: {task['md_path']}")
    if task.get("to_pdf"):
        from md2pdf import md_to_pdf

        pdf_path = md_to_pdf(task["md_path"])
        task["pdf_path"] = pdf_path
        print(f"  已更新: {pdf_path}")


def redo_batch(task: dict, config: dict, output_dir: str, batch_idx: int, max_batch_minutes: int = 20) -> None:
    """重新生成整个章节（批次）"""
    base_url = config["base_url"]
    api_key = config["api_key"]
    model = config.get("model", "qwen3.5-flash")

    ensure_prerequisites(task, config, output_dir, up_to="segments")

    if "results" not in task:
        print("\n  结构化缓存不可用（.json 文件缺失），无法单独重新生成。")
        print("  请选择「完全重新生成」（输入 0）。")
        sys.exit(1)

    results = task["results"]
    batches = task["batches"]
    chapters = task["chapters"]

    batch_title = results[batch_idx].get("batch_title", "")
    print(f"\n  重新生成整章: [{batch_idx + 1}] {batch_title}")

    batch = batches[batch_idx]
    ch_title = chapters[batch_idx]["title"] if batch_idx < len(chapters) else ""
    seg_source = task.get("seg_source", "unknown")

    print(f"  批次时间范围: "
          f"{batch[0]['start_seconds'] // 60}:{batch[0]['start_seconds'] % 60:02d} ~ "
          f"{batch[-1]['start_seconds'] // 60}:{batch[-1]['start_seconds'] % 60:02d}")
    print(f"  字幕条目数: {len(batch)}")
    print(f"  调用模型: {model}\n")

    new_result = process_chapter(
        batch=batch,
        batch_idx=batch_idx,
        total_batches=len(batches),
        base_url=base_url,
        api_key=api_key,
        model=model,
        chapter_title=ch_title,
        seg_source=seg_source,
        max_batch_minutes=max_batch_minutes,
    )

    print(f"  新标题: {new_result.get('batch_title', '(无)')}")
    print(f"  新小节数: {len(new_result.get('sections', []))}")

    results[batch_idx] = new_result
    task["results"] = results
    _save_and_render(task)

    print(f"\n  完成! 章节「{new_result.get('batch_title', '')}」已重新生成。")


def redo_section(
    task: dict, config: dict, output_dir: str, batch_idx: int, section_idx: int,
    max_batch_minutes: int = 20,
) -> None:
    """重新生成章节内的单个小节。

    利用 .segments.json 提供的可靠章节边界，将整个 batch 的字幕
    通过 process_chapter 重新生成（与首次生成完全相同的流程），
    然后只用新结果中对应位置的 section 替换目标小节。
    """
    base_url = config["base_url"]
    api_key = config["api_key"]
    model = config.get("model", "qwen3.5-flash")

    ensure_prerequisites(task, config, output_dir, up_to="segments")

    results = task["results"]
    batches = task["batches"]
    chapters = task["chapters"]
    old_batch = results[batch_idx]
    old_sections = old_batch.get("sections", [])
    old_section = old_sections[section_idx]

    section_title = old_section["title"]
    batch_entries = batches[batch_idx]
    ch_title = chapters[batch_idx]["title"] if batch_idx < len(chapters) else ""
    seg_source = task.get("seg_source", "unknown")

    print(f"\n  重新生成小节: {section_title}")
    print(f"  所属章节: {old_batch.get('batch_title', '')}")
    print(f"  章节字幕条目数: {len(batch_entries)}（基于 segments.json 边界）")
    print(f"  调用模型: {model}\n")

    # 用与首次生成完全一致的方式重新处理整个 batch
    new_result = process_chapter(
        batch=batch_entries,
        batch_idx=batch_idx,
        total_batches=len(batches),
        base_url=base_url,
        api_key=api_key,
        model=model,
        chapter_title=ch_title,
        seg_source=seg_source,
        max_batch_minutes=max_batch_minutes,
    )

    new_sections = new_result.get("sections", [])
    print(f"  LLM 返回 {len(new_sections)} 个小节 (原 {len(old_sections)} 个)")

    # 从新结果中取出对应位置的 section 替换
    if section_idx < len(new_sections):
        replacement = new_sections[section_idx]
        print(f"  替换: 「{section_title}」 → 「{replacement.get('title', '')}」")
    else:
        # 新结果 section 数量变少，取最后一个
        replacement = new_sections[-1] if new_sections else None
        if replacement:
            print(f"  新结果小节数不足，使用最后一个: 「{replacement.get('title', '')}」")
        else:
            print("  LLM 返回了空结果，保留原内容。")
            return

    old_sections[section_idx] = replacement
    old_batch["sections"] = old_sections
    results[batch_idx] = old_batch
    task["results"] = results

    _save_and_render(task)
    print(f"\n  完成! 小节「{section_title}」已重新生成。")


def redo_all(
    videos: list[dict], config: dict, output_dir: str,
    workers: int, max_batch_minutes: int, pipeline: bool, to_pdf: bool,
) -> None:
    """重新生成所有视频笔记"""
    n = len(videos)
    base_url = config["base_url"]
    api_key = config["api_key"]
    model = config.get("model", "qwen3.5-flash")

    # 构建所有 task
    tasks: list[dict] = []
    for vi, video in enumerate(videos):
        print(f"\n  ── [{vi + 1}/{n}] {video['stem']} ──")
        print(f"  加载缓存...")
        cached = load_intermediate(video, output_dir)
        task = rebuild_task(video, cached, output_dir)
        task["to_pdf"] = to_pdf
        tasks.append(task)

    def _label(task: dict) -> str:
        return task.get("stem", task["url"])

    if not pipeline:
        # 串行模式：逐个视频完整重新生成
        succeeded, failed_stems = [], []
        for vi, task in enumerate(tasks):
            print(f"\n{'#' * 60}")
            print(f"  视频 [{vi + 1}/{n}]: {_label(task)}")
            print(f"{'#' * 60}")
            try:
                redo_full(task, config, output_dir, workers, max_batch_minutes)
                succeeded.append(_label(task))
            except Exception as e:
                print(f"  ❌ {e}")
                failed_stems.append(_label(task))
    else:
        # 流水线模式：按步骤统一推进，LLM 步骤跨视频并行

        def _for_each_serial(step_num: int, name: str, fn, *extra_args):
            log_step(step_num, 4, f"{name}（{n} 个视频，串行）")
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
                step_num, 4,
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

        # 确保所有 task 有字幕和元数据
        for task in tasks:
            ensure_prerequisites(task, config, output_dir, up_to="meta")

        _for_each_parallel(
            1, "视频内容分段", _step3_segment,
            base_url, api_key, model, True,
        )
        _for_each_parallel(
            2, "AI 结构化整理字幕", _step4_structure,
            base_url, api_key, model, workers, max_batch_minutes,
        )
        _for_each_serial(
            3, "渲染 Markdown 文档", _step5_render, True, True, to_pdf,
        )
        _for_each_serial(4, "更新 README 视频表格", _step6_readme)

        succeeded = [_label(t) for t in tasks if not t.get("failed")]
        failed_stems = [_label(t) for t in tasks if t.get("failed")]

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"  全部完成! 成功 {len(succeeded)}/{n} 个视频")
    for s in succeeded:
        print(f"     ✅ {s}")
    for s in failed_stems:
        print(f"     ❌ {s}")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Watch2Read — 交互式重新生成已有笔记",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--config", required=True, help="API 配置文件 (JSON)")
    parser.add_argument(
        "--output-dir", default=OUTPUT_DIR, help=f"输出目录 (默认: {OUTPUT_DIR})"
    )
    parser.add_argument(
        "--workers", type=int, default=5, help="并发线程数 (默认: 5)"
    )
    parser.add_argument(
        "--max-batch-minutes", type=int, default=20,
        help="单次 LLM 调用处理的最大时长（分钟），超过则自动切分为子批次 (默认: 20)",
    )
    parser.add_argument(
        "--pipeline", action="store_true",
        help="更新所有视频时使用流水线模式：LLM 步骤跨视频并行执行",
    )
    parser.add_argument(
        "--to-pdf", action="store_true",
        help="渲染 Markdown 后同时转换为 PDF（默认关闭）",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 1. 扫描已有视频
    videos = find_videos(args.output_dir)
    if not videos:
        print(f"在 {args.output_dir}/ 中未找到已生成的笔记。")
        sys.exit(1)

    # 2. 选择视频
    video_options = ["更新所有视频"]
    for v in videos:
        flags = []
        if v["has_srt"]:
            flags.append("srt")
        if v["has_meta"]:
            flags.append("meta")
        if v["has_segments"]:
            flags.append("seg")
        if v["has_json"]:
            flags.append("json")
        cache_info = f" [{', '.join(flags)}]" if flags else " [无缓存]"
        video_options.append(f"{v['stem']}{cache_info}")

    video_idx = prompt_choice("选择要修改的视频 (0=更新所有):", video_options, start=0)

    mbm = args.max_batch_minutes

    if video_idx == 0:
        # 更新所有视频
        redo_all(
            videos, config, args.output_dir, args.workers, mbm, args.pipeline, args.to_pdf
        )
        return

    video = videos[video_idx - 1]
    print(f"\n  已选择: {video['stem']}")
    print(f"  视频链接: {video['video_url']}")

    # 3. 加载中间文件
    print("\n  检查缓存文件...")
    cached = load_intermediate(video, args.output_dir)

    # 4. 选择操作：0 = 全部重新生成，1~N = 重新生成第 i 个章节
    action_options = ["全部重新生成（从分段开始重跑所有 LLM 步骤）"]
    if "results" in cached:
        for i, batch_result in enumerate(cached["results"]):
            title = batch_result.get("batch_title", f"批次 {i + 1}")
            n_sections = len(batch_result.get("sections", []))
            action_options.append(f"{title} ({n_sections} 个小节)")
    else:
        print("  （.json 缓存不可用，仅支持完全重新生成）")

    action_idx = prompt_choice("选择操作 (0=全部重新生成，1~N=重新生成对应章节):",
                               action_options, start=0)

    task = rebuild_task(video, cached, args.output_dir)
    task["to_pdf"] = args.to_pdf

    if action_idx == 0:
        redo_full(task, config, args.output_dir, args.workers, mbm)
    else:
        batch_idx = action_idx - 1
        batch_result = cached["results"][batch_idx]
        sections = batch_result.get("sections", [])

        # 如果该章节有多个小节，让用户进一步选择
        if len(sections) > 1:
            sec_options = ["重新生成整章"]
            for s in sections:
                sec_options.append(s.get("title", ""))

            sec_idx = prompt_choice(
                f"「{batch_result.get('batch_title', '')}」有 {len(sections)} 个小节，"
                "选择要重新生成的部分 (0=整章，1~N=对应小节):",
                sec_options, start=0,
            )

            if sec_idx == 0:
                redo_batch(task, config, args.output_dir, batch_idx, mbm)
            else:
                redo_section(task, config, args.output_dir, batch_idx, sec_idx - 1, mbm)
        else:
            redo_batch(task, config, args.output_dir, batch_idx, mbm)


if __name__ == "__main__":
    main()
