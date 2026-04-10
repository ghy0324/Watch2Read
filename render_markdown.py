"""
将结构化 JSON 渲染为带折叠与跳转链接的 Markdown

用法:
    python render_markdown.py -l <video_url> -i <structured.json> [-o output.md]
    python render_markdown.py -l https://www.bilibili.com/video/BV11LPWzNEkm/ -i world_model.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def fmt_time(seconds: int) -> str:
    """将秒数格式化为 mm:ss 或 hh:mm:ss"""
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def make_link(video_url: str, seconds: int) -> str:
    base = video_url.rstrip("/")
    return f"{base}/?t={seconds}"


def render_content(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_section(section: dict, video_url: str) -> str:
    t = section["start_seconds"]
    link = make_link(video_url, t)
    title = section["title"]
    tldr = section.get("tldr", "")
    content = section.get("content", [])

    lines = [f'### <a href="{link}">{title}</a> `{fmt_time(t)}`', ""]
    summary_text = tldr if tldr else "展开详情"
    lines.append("<details>")
    lines.append(f"<summary>{summary_text}</summary>")
    lines.append("")
    if content:
        lines.append(render_content(content))
        lines.append("")
    lines.append("</details>")
    return "\n".join(lines)


def render_batch(batch: dict, video_url: str) -> str:
    lines = [f"## {batch['batch_title']}", ""]
    for section in batch.get("sections", []):
        lines.append(render_section(section, video_url))
        lines.append("")
    return "\n".join(lines)


def render_all(data: list[dict], video_url: str) -> str:
    parts = []
    for batch in data:
        parts.append(render_batch(batch, video_url))
    return "\n".join(parts)


def render_document(data: list[dict], video_url: str, meta: dict) -> str:
    """渲染完整的 Markdown 文档，包含视频元信息头部和结构化内容"""
    uploader = meta["uploader"]
    uploader_url = meta.get("uploader_url", "")
    uploader_md = f"[{uploader}]({uploader_url})" if uploader_url else uploader

    lines = [
        f"# {meta['title']}",
        "",
        f"> **UP主**: {uploader_md} | **发布日期**: {meta['pub_date']} | **时长**: {meta['duration_fmt']}",
        ">",
        f"> **链接**: [Bilibili - {meta['bvid']}]({video_url})",
    ]
    desc = (meta.get("desc") or "").strip()
    if desc:
        desc_parts = desc.split("\n")
        lines.append(">")
        lines.append(f"> **简介**: {desc_parts[0]}")
        for dp in desc_parts[1:]:
            lines.append(f"> {dp}")

    pinned = meta.get("pinned_comment")
    if pinned:
        comment_parts = pinned["content"].split("\n")
        lines.append(">")
        lines.append(f"> **置顶评论** (@{pinned['user']}): {comment_parts[0]}")
        for cp in comment_parts[1:]:
            lines.append(f"> {cp}")

    lines.extend(["", "---", "", render_all(data, video_url)])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="将结构化 JSON 渲染为 Markdown")
    parser.add_argument("-l", "--link", required=True, help="视频链接")
    parser.add_argument("-i", "--input", required=True, help="结构化 JSON 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出 Markdown 文件路径")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    video_url = args.link.split("?")[0].rstrip("/")

    md = render_all(data, video_url)

    if not args.output:
        args.output = str(Path(args.input).with_suffix(".md"))

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Markdown 已保存至: {args.output}")


if __name__ == "__main__":
    main()
