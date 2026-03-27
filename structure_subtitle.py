"""
对视频字幕进行结构化整理

用法:
    python structure_subtitle.py -i <srt_file> -c <config.json> [-o output.json]
    python structure_subtitle.py -i world_model.srt -c api_config.json
    python structure_subtitle.py -i world_model.srt -c api_config.json -o result.json --batch-minutes 8
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests


SYSTEM_PROMPT = """\
你是一个专业的视频内容整理助手。你的任务是将视频字幕整理成结构化的内容摘要。

要求：
1. 根据内容的主题和逻辑，将字幕内容组织为两级标题结构（level 2 和 level 3）。
2. 每个 section 对应一个主要话题（level 2），其下可包含若干 subsection（level 3）。
3. "content" 字段中的每一条应是一个完整的、凝练的陈述句，去除口语化用词（如"嗯""啊""就是说""对吧""那个""然后呢"等），使语句书面化、通顺。
4. 不要过度压缩，确保没有信息量损失，所有关键观点、数据、人名、术语都要保留。
5. start_seconds 取该 section/subsection 对应内容中最早出现的字幕时间戳（秒数，整数）。
6. 如果字幕中存在明显的语音识别错误，请根据上下文合理修正。

请严格按照以下 JSON 格式输出，不要输出 JSON 之外的任何内容：

{
  "batch_title": "对这一段内容的整体概括标题",
  "sections": [
    {
      "title": "主题标题",
      "tldr": "用一句话总结该 section 的核心内容",
      "start_seconds": 起始秒数,
      "level": 2,
      "content": [
        "要点1",
        "要点2"
      ],
      "subsections": [
        {
          "title": "子主题标题",
          "tldr": "用一句话总结该 subsection 的核心内容",
          "start_seconds": 起始秒数,
          "level": 3,
          "content": [
            "子要点1",
            "子要点2"
          ]
        }
      ]
    }
  ]
}

注意：
- subsections 是可选的，只有当一个 section 内容较多且可以进一步细分时才添加。
- 确保每个 section 和 subsection 都有准确的 start_seconds。
- 每个 section 和 subsection 都必须包含 tldr 字段，用一句简洁的话概括该部分的核心要点。
- batch_title 应简明扼要地概括这一段字幕的核心主题。"""


def parse_srt_timestamp(ts: str) -> float:
    """解析 SRT 时间戳，如 '0:0:5,78' -> 5.78"""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    elif len(parts) == 2:
        h, m, s = 0, int(parts[0]), float(parts[1])
    else:
        return float(ts)
    return h * 3600 + m * 60 + s


def parse_srt_content(content: str) -> list[dict]:
    """解析 SRT 格式字符串，返回 [{start_seconds, text}, ...]"""
    blocks = re.split(r"\n\s*\n", content.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
        ts_match = re.match(
            r"([\d:,\.]+)\s*-->\s*([\d:,\.]+)", lines[1] if len(lines) >= 3 else lines[0]
        )
        if not ts_match:
            continue
        start_sec = parse_srt_timestamp(ts_match.group(1))
        text_lines = lines[2:] if len(lines) >= 3 else []
        text = " ".join(l.strip() for l in text_lines if l.strip())
        if not text:
            continue
        entries.append({"start_seconds": int(start_sec), "text": text})
    return entries


def parse_srt(srt_path: str) -> list[dict]:
    """解析 SRT 文件，返回 [{start_seconds, text}, ...]"""
    with open(srt_path, "r", encoding="utf-8") as f:
        return parse_srt_content(f.read())


def split_into_batches(entries: list[dict], batch_duration: int = 600) -> list[list[dict]]:
    """按时间窗口将字幕条目分批，默认每批约 10 分钟"""
    if not entries:
        return []
    batches = []
    current_batch = []
    batch_start = entries[0]["start_seconds"]
    for entry in entries:
        if entry["start_seconds"] - batch_start >= batch_duration and current_batch:
            batches.append(current_batch)
            current_batch = [entry]
            batch_start = entry["start_seconds"]
        else:
            current_batch.append(entry)
    if current_batch:
        batches.append(current_batch)
    return batches


def format_batch_for_prompt(entries: list[dict]) -> str:
    """将一批字幕条目格式化为 [时间戳] 文本 的形式"""
    lines = []
    for e in entries:
        total = e["start_seconds"]
        h, remainder = divmod(total, 3600)
        m, s = divmod(remainder, 60)
        ts = f"{h}:{m:02d}:{s:02d}"
        lines.append(f"[{ts}] {e['text']}")
    return "\n".join(lines)


def extract_json_from_response(text: str) -> dict:
    """从模型回复中提取 JSON，兼容 markdown 代码块包裹的情况"""
    text = text.strip()
    md_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if md_match:
        text = md_match.group(1).strip()
    return json.loads(text)


def call_model(
    batch_text: str,
    batch_idx: int,
    total_batches: int,
    base_url: str,
    api_key: str,
    model: str,
    chapter_title: str = "",
    timeout: int = 300,
) -> dict:
    """调用 LLM API 对一批字幕内容进行结构化整理"""
    context = f"（主题：{chapter_title}）" if chapter_title else ""
    user_prompt = (
        f"以下是一段视频字幕的第 {batch_idx + 1}/{total_batches} 部分{context}，"
        f"每行方括号内是时间戳（时:分:秒）。请对这些内容进行结构化整理。\n\n"
        f"{batch_text}"
    )

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    result = resp.json()
    content = result["choices"][0]["message"]["content"]
    return extract_json_from_response(content)


def main():
    parser = argparse.ArgumentParser(description="对视频字幕进行结构化整理")
    parser.add_argument("-i", "--input", required=True, help="输入 SRT 字幕文件路径")
    parser.add_argument("-c", "--config", required=True, help="API 配置文件路径 (JSON，含 base_url / api_key)")
    parser.add_argument("-o", "--output", default=None, help="输出 JSON 文件路径 (默认与输入同名)")
    parser.add_argument(
        "--batch-minutes", type=int, default=10, help="每批处理的时长（分钟，默认 10）"
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)
    base_url = config["base_url"]
    api_key = config["api_key"]
    model = config.get("model", "qwen3.5-flash")

    entries = parse_srt(args.input)
    if not entries:
        print("未从 SRT 文件中解析到任何字幕条目。")
        sys.exit(1)
    total_sec = entries[-1]["start_seconds"]
    print(f"解析字幕: {len(entries)} 条, 总时长约 {total_sec // 60} 分 {total_sec % 60} 秒")

    batches = split_into_batches(entries, batch_duration=args.batch_minutes * 60)
    print(f"分为 {len(batches)} 个批次处理\n")

    results = []
    for i, batch in enumerate(batches):
        t0 = batch[0]["start_seconds"]
        t1 = batch[-1]["start_seconds"]
        print(
            f"[{i + 1}/{len(batches)}] 处理 "
            f"{t0 // 60}:{t0 % 60:02d} ~ {t1 // 60}:{t1 % 60:02d} "
            f"({len(batch)} 条字幕) ..."
        )
        try:
            result = call_model(batch_text=format_batch_for_prompt(batch),
                                batch_idx=i, total_batches=len(batches),
                                base_url=base_url, api_key=api_key, model=model)
            results.append(result)
            print(f"  ✓ {result.get('batch_title', '(无标题)')}")
        except Exception as e:
            print(f"  ✗ 失败: {e}", file=sys.stderr)
            sys.exit(1)

    if not args.output:
        args.output = str(Path(args.input).with_suffix(".json"))

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n结构化结果已保存至: {args.output}")


if __name__ == "__main__":
    main()
