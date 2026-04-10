"""视频分段 — 基于语义的视频内容分段

优先从视频简介/置顶评论中提取已有的章节划分（通过 LLM 解析）。
如无现成划分，则通过 LLM 分析字幕内容进行语义分段。
"""

from __future__ import annotations

import re

import requests

from structure_subtitle import format_batch_for_prompt, extract_json_from_response


def _text_has_timestamps(text: str) -> bool:
    """快速检查文本中是否包含足够多的时间戳模式（至少 3 个）。"""
    return len(re.findall(r"\d{1,2}:\d{2}(?::\d{2})?", text)) >= 3


def _call_llm(
    system: str,
    user: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int = 120,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


CHAPTER_EXTRACT_PROMPT = """\
你是一个视频内容分析助手。以下文本来自视频的简介或置顶评论。
请判断其中是否包含视频的章节/段落划分（通常以时间戳标记）。

如果包含，请提取章节信息并输出 JSON。注意：
1. 只提取**顶级章节**作为主要分段点。带有 "-"、"·"、"•" 等标记的子条目、
   或明显从属于上一个条目的内容，应归入其上级章节，不要单独提取。
2. 将时间戳转换为秒数（整数）。
3. 保留原始章节标题。

如果文本中**没有**章节划分信息，输出：
{"has_chapters": false}

如果有，输出：
{
  "has_chapters": true,
  "chapters": [
    {"start_seconds": 0, "title": "章节标题"},
    {"start_seconds": 120, "title": "章节标题"}
  ]
}

请严格按照上述 JSON 格式输出，不要输出 JSON 之外的任何内容。"""


def extract_chapters_from_meta(
    meta: dict,
    base_url: str,
    api_key: str,
    model: str,
) -> list[dict] | None:
    """将视频简介/置顶评论交给 LLM 提取章节划分。

    返回 [{start_seconds, title}, ...] 或 None。
    """
    parts: list[str] = []
    desc = (meta.get("desc") or "").strip()
    if desc:
        parts.append(f"【视频简介】\n{desc}")
    pinned = meta.get("pinned_comment")
    if pinned and pinned.get("content", "").strip():
        parts.append(f"【置顶评论】\n{pinned['content']}")

    if not parts:
        return None

    combined = "\n\n".join(parts)
    if not _text_has_timestamps(combined):
        return None

    raw = _call_llm(CHAPTER_EXTRACT_PROMPT, combined, base_url, api_key, model)
    result = extract_json_from_response(raw)

    if not result.get("has_chapters"):
        return None

    chapters = result.get("chapters", [])
    if len(chapters) < 2:
        return None

    for i in range(1, len(chapters)):
        if chapters[i]["start_seconds"] < chapters[i - 1]["start_seconds"]:
            return None

    return chapters


def split_entries_by_chapters(
    entries: list[dict], chapters: list[dict]
) -> list[list[dict]]:
    """按章节时间边界将字幕条目分批，每个批次对应一个章节。"""
    if not chapters or not entries:
        return [entries] if entries else []

    boundaries = [ch["start_seconds"] for ch in chapters]
    batches: list[list[dict]] = []
    ch_idx = 0
    current_batch: list[dict] = []

    for entry in entries:
        while (
            ch_idx + 1 < len(boundaries)
            and entry["start_seconds"] >= boundaries[ch_idx + 1]
        ):
            if current_batch:
                batches.append(current_batch)
            current_batch = []
            ch_idx += 1
        current_batch.append(entry)

    if current_batch:
        batches.append(current_batch)

    return batches


SEGMENT_SYSTEM_PROMPT = """\
你是一个视频内容分析助手。请根据提供的视频字幕，将内容按主题划分为若干段落。

要求：
1. 根据内容的主题变化来划分段落，每个段落应对应一个相对独立的话题或讨论内容。
2. 段落数量要合理：对于一小时的视频，通常 5-15 个段落；更长的视频可以更多。
3. 每个段落需提供起始时间戳（秒数）和简短标题。

请严格按照以下 JSON 格式输出，不要输出 JSON 之外的任何内容：

{
  "chapters": [
    {"start_seconds": 0, "title": "段落标题"},
    {"start_seconds": 120, "title": "段落标题"}
  ]
}

注意：
- chapters 按 start_seconds 升序排列。
- 第一个 chapter 的 start_seconds 应接近字幕开始时间。
- 标题应简洁明了，概括该段核心主题。
- start_seconds 必须取自字幕中实际出现的时间戳对应的秒数，不要四舍五入到 10、30、60 等整数倍。例如字幕在 0:02:33 处发生主题切换，start_seconds 应为 153 而不是 150 或 160。"""


def segment_by_subtitle(
    entries: list[dict],
    base_url: str,
    api_key: str,
    model: str,
) -> list[dict]:
    """通过 LLM 分析字幕内容进行语义分段。返回 [{start_seconds, title}, ...]。"""
    subtitle_text = format_batch_for_prompt(entries)

    max_chars = 100_000
    if len(subtitle_text) > max_chars:
        step = max(1, int(len(subtitle_text) / max_chars) + 1)
        subtitle_text = format_batch_for_prompt(entries[::step])

    user_prompt = (
        "以下是一段视频的完整字幕，每行方括号内是时间戳（时:分:秒）。\n"
        "请根据内容的主题变化，将其划分为若干段落。\n\n"
        f"{subtitle_text}"
    )

    raw = _call_llm(
        SEGMENT_SYSTEM_PROMPT, user_prompt, base_url, api_key, model, timeout=300
    )
    return extract_json_from_response(raw)["chapters"]


SUB_SEGMENT_PROMPT = """\
你是一个视频内容分析助手。以下字幕属于视频中的同一章节，但内容较长，需要拆分为若干子段落以便后续处理。

要求：
1. 根据内容的主题变化来划分子段落，每个子段落应对应一个相对独立的话题。
2. 每个子段落时长大致控制在 {max_minutes} 分钟左右，但优先在主题自然切换处划分，不要强行按固定时长切断。
3. 每个子段落需提供起始时间戳（秒数）和简短标题。

请严格按照以下 JSON 格式输出，不要输出 JSON 之外的任何内容：

{{
  "chapters": [
    {{"start_seconds": 0, "title": "子段落标题"}},
    {{"start_seconds": 120, "title": "子段落标题"}}
  ]
}}

注意：
- chapters 按 start_seconds 升序排列。
- 第一个 chapter 的 start_seconds 应等于字幕起始时间。
- start_seconds 必须取自字幕中实际出现的时间戳。"""


def sub_segment_chapter(
    entries: list[dict],
    max_batch_minutes: int,
    base_url: str,
    api_key: str,
    model: str,
) -> list[dict]:
    """用 LLM 对长章节内部做语义子切分。返回 [{start_seconds, title}, ...]。"""
    subtitle_text = format_batch_for_prompt(entries)

    # 超长时降采样，避免超出上下文窗口
    max_chars = 100_000
    if len(subtitle_text) > max_chars:
        step = max(1, int(len(subtitle_text) / max_chars) + 1)
        subtitle_text = format_batch_for_prompt(entries[::step])

    system = SUB_SEGMENT_PROMPT.format(max_minutes=max_batch_minutes)
    user_prompt = (
        "以下是一个章节的完整字幕，每行方括号内是时间戳（时:分:秒）。\n"
        "请根据内容的主题变化，将其拆分为若干子段落。\n\n"
        f"{subtitle_text}"
    )

    raw = _call_llm(system, user_prompt, base_url, api_key, model, timeout=300)
    return extract_json_from_response(raw)["chapters"]


def segment_video(
    entries: list[dict],
    meta: dict,
    base_url: str,
    api_key: str,
    model: str,
) -> tuple[list[dict], str]:
    """执行视频分段。

    返回 (chapters, source)。
    source: "meta" 表示从简介/评论提取，"subtitle" 表示由 LLM 分析字幕。
    """
    chapters = extract_chapters_from_meta(meta, base_url, api_key, model)
    if chapters:
        return chapters, "meta"

    chapters = segment_by_subtitle(entries, base_url, api_key, model)
    return chapters, "llm"
