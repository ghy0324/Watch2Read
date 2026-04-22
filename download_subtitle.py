"""
统一下载视频字幕（Bilibili / YouTube），输出 SRT。

用法:
    python download_subtitle.py -l <video_url>
    python download_subtitle.py -l <video_url> -o notes/output.srt
    python download_subtitle.py -l <youtube_url> --lang zh-Hans,zh-CN,zh,en-GB,en
"""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

# --------------------------- 通用 ---------------------------

DEFAULT_OUTPUT_DIR = "notes"
SIMPLIFIED_PRIORITY = ["简体", "zh-hans", "zh-cn", "cmn-hans"]
CHINESE_PRIORITY = ["中文", "chinese", "zh", "cmn", "cn"]


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:200]


def ensure_srt_path(output: str | None, title: str, output_dir: str = DEFAULT_OUTPUT_DIR) -> str:
    if output:
        p = Path(output)
        if p.suffix.lower() != ".srt":
            p = p.with_suffix(".srt")
        if p.parent and str(p.parent) != ".":
            p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    return str(Path(output_dir) / f"{sanitize_filename(title)}.srt")


def detect_platform(video_url: str) -> str:
    host = (urlparse(video_url).hostname or "").lower()
    if host in {"www.bilibili.com", "bilibili.com", "m.bilibili.com", "b23.tv"}:
        return "bilibili"
    if host in {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"}:
        return "youtube"
    raise RuntimeError(f"不支持的平台: {host or video_url}")


# --------------------------- Bilibili ---------------------------

KEDOU_BASE = "https://www.kedou.life/api"
IV_B64 = "a2Vkb3VAODk4OSE2MzIzMw=="
B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
COMMON_HEADERS = {
    "KdSystem": "Kedou",
    "Referer": "https://www.kedou.life/caption/subtitle/bilibili",
    "Origin": "https://www.kedou.life",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


def _hex2b64(hex_str: str) -> str:
    result = ""
    i = 0
    while i + 3 <= len(hex_str):
        t = int(hex_str[i : i + 3], 16)
        result += B64_CHARS[t >> 6] + B64_CHARS[t & 63]
        i += 3
    remaining = len(hex_str) - i
    if remaining == 1:
        t = int(hex_str[i : i + 1], 16)
        result += B64_CHARS[t << 2]
    elif remaining == 2:
        t = int(hex_str[i : i + 2], 16)
        result += B64_CHARS[t >> 2] + B64_CHARS[(t & 3) << 4]
    while len(result) % 4 != 0:
        result += "="
    return result


def _get_keys(session) -> tuple[str, str]:
    resp = session.get(f"{KEDOU_BASE}/auth/keys", headers=COMMON_HEADERS, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data["code"] != 200:
        raise RuntimeError(f"获取密钥失败: {data['message']}")
    return data["data"]["k1"], data["data"]["k2"]


def _rsa_public_decrypt(k2_b64: str, pubkey_b64: str) -> str:
    from Crypto.PublicKey import RSA

    k2_bytes = base64.b64decode(k2_b64)
    der = base64.b64decode(pubkey_b64)
    key = RSA.import_key(der)

    c = int.from_bytes(k2_bytes, "big")
    m = pow(c, key.e, key.n)

    key_len = (key.n.bit_length() + 7) // 8
    m_bytes = m.to_bytes(key_len, "big")

    i = 2
    while i < len(m_bytes) and m_bytes[i] != 0:
        i += 1
    if i >= len(m_bytes):
        raise RuntimeError("PKCS#1 解填充失败")
    return m_bytes[i + 1 :].decode("utf-8")


def _aes_cbc_encrypt(plaintext: str, aes_key: str, iv_b64: str) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    key_bytes = aes_key.encode("utf-8")
    iv_bytes = base64.b64decode(iv_b64)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
    padded = pad(plaintext.encode("utf-8"), AES.block_size)
    return base64.b64encode(cipher.encrypt(padded)).decode("utf-8")


def _rsa_encrypt_long(plaintext: str, pubkey_b64: str) -> str:
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.PublicKey import RSA

    der = base64.b64decode(pubkey_b64)
    key = RSA.import_key(der)
    key_len = (key.n.bit_length() + 7) // 8
    max_chunk = key_len - 11

    data = plaintext.encode("utf-8")
    cipher = PKCS1_v1_5.new(key)
    all_hex = ""
    for offset in range(0, len(data), max_chunk):
        chunk = data[offset : offset + max_chunk]
        all_hex += cipher.encrypt(chunk).hex()
    return _hex2b64(all_hex)


def _encrypt_body(body: dict, k1: str, k2: str) -> str:
    aes_key = _rsa_public_decrypt(k2, k1)
    aes_encrypted = _aes_cbc_encrypt(json.dumps(body), aes_key, IV_B64)
    return _rsa_encrypt_long(aes_encrypted, k1)


def extract_subtitle_bilibili(session, video_url: str) -> dict:
    k1, k2 = _get_keys(session)
    encrypted = _encrypt_body({"url": video_url}, k1, k2)
    headers = {**COMMON_HEADERS, "Content-Type": "application/json"}

    resp = session.post(
        f"{KEDOU_BASE}/video/subtitleExtract",
        headers=headers,
        data=json.dumps(encrypted),
        timeout=180,
    )
    resp.raise_for_status()
    result = resp.json()
    if result["code"] != 200:
        raise RuntimeError(f"字幕提取失败: {result['message']}")
    return result["data"]


def pick_bilibili_track(tracks: list[dict]) -> dict | None:
    if not tracks:
        return None

    def _lang(track: dict) -> str:
        return (track.get("langDesc") or "").lower()

    for t in tracks:
        lang = _lang(t)
        if any(k in lang for k in SIMPLIFIED_PRIORITY):
            return t

    for t in tracks:
        lang = _lang(t)
        if any(k in lang for k in CHINESE_PRIORITY):
            return t

    return tracks[0]


def download_bilibili_srt(session, track: dict) -> str:
    if track.get("content"):
        return track["content"]
    src = track.get("srcUrl")
    if not src:
        raise RuntimeError("字幕轨道无下载链接")
    resp = session.get(src, headers={"Referer": "https://www.kedou.life/"}, timeout=120)
    resp.raise_for_status()
    return resp.text


def download_subtitle_bilibili(
    video_url: str,
    output: str | None = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> tuple[str, str]:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "处理 Bilibili 字幕需要安装 requests，请先执行: pip install requests"
        ) from exc
    try:
        import Crypto  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "处理 Bilibili 字幕需要安装 pycryptodome，请先执行: pip install pycryptodome"
        ) from exc

    session = requests.Session()

    info = extract_subtitle_bilibili(session, video_url)
    if info.get("status") == "解析失败":
        raise RuntimeError("字幕解析失败，该视频可能没有字幕")

    tracks = info.get("subtitleItemVoList", [])
    if not tracks:
        raise RuntimeError("未找到任何字幕轨道")

    track = pick_bilibili_track(tracks)
    if not track:
        raise RuntimeError("未找到可用字幕轨道")

    content = download_bilibili_srt(session, track)
    title = info.get("title", "subtitle")
    output_path = ensure_srt_path(output, title, output_dir)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return content, output_path


# --------------------------- YouTube ---------------------------

def _normalize_yt_lang_key(key: str) -> str:
    return key.lower().replace("_", "-")


def choose_youtube_lang(info: dict, preferred: list[str]) -> str | None:
    subtitles = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    available = set(_normalize_yt_lang_key(k) for k in subtitles.keys()) | set(
        _normalize_yt_lang_key(k) for k in automatic.keys()
    )
    if not available:
        return None

    normalized_preferred = [_normalize_yt_lang_key(x) for x in preferred]
    for lang in normalized_preferred:
        if lang in available:
            return lang

    for lang in available:
        if any(flag in lang for flag in ("zh-hans", "zh-cn", "cmn-hans")):
            return lang

    for lang in available:
        if lang.startswith("zh"):
            return lang

    return next(iter(available))


def _find_generated_srt(outtmpl: str, explicit_output: bool) -> list[str]:
    if explicit_output:
        parent = Path(outtmpl).parent if Path(outtmpl).parent else Path(".")
        stem = Path(outtmpl).name
        return sorted(
            glob.glob(str(parent / f"{stem}*.srt")),
            key=os.path.getmtime,
            reverse=True,
        )
    return sorted(glob.glob("*.srt"), key=os.path.getmtime, reverse=True)


def download_subtitle_youtube(
    video_url: str,
    output: str | None = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    lang_priority: str = "zh-Hans,zh-CN,zh,en-GB,en",
) -> tuple[str, str]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError(
            "处理 YouTube 字幕需要安装 yt-dlp，请先执行: pip install yt-dlp"
        ) from exc

    preferred = [x.strip() for x in lang_priority.split(",") if x.strip()]

    probe_opts = {
        "skip_download": True,
        "quiet": True,
        "noprogress": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    with YoutubeDL(probe_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    selected_lang = choose_youtube_lang(info, preferred)
    if not selected_lang:
        raise RuntimeError("未检测到可用字幕轨道")

    title = info.get("title") or "youtube_subtitle"
    final_output = ensure_srt_path(output, title, output_dir)
    outtmpl = str(Path(final_output).with_suffix(""))

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [selected_lang],
        "subtitlesformat": "srt/best",
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        "outtmpl": outtmpl,
        "restrictfilenames": False,
        "noprogress": True,
        "quiet": False,
    }

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    files = _find_generated_srt(outtmpl, explicit_output=True)
    if not files:
        raise RuntimeError("yt-dlp 未生成 .srt 文件")

    generated = files[0]
    if Path(generated).resolve() != Path(final_output).resolve():
        if Path(final_output).exists():
            os.remove(final_output)
        os.rename(generated, final_output)

    with open(final_output, "r", encoding="utf-8") as f:
        content = f.read()

    return content, final_output


# --------------------------- 统一入口 ---------------------------

def download_subtitle(
    video_url: str,
    output: str | None = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    youtube_lang_priority: str = "zh-Hans,zh-CN,zh,en-GB,en",
) -> tuple[str, str]:
    platform = detect_platform(video_url)
    if platform == "bilibili":
        return download_subtitle_bilibili(video_url, output=output, output_dir=output_dir)
    if platform == "youtube":
        return download_subtitle_youtube(
            video_url,
            output=output,
            output_dir=output_dir,
            lang_priority=youtube_lang_priority,
        )
    raise RuntimeError(f"不支持的平台: {video_url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="统一下载视频字幕（Bilibili / YouTube）")
    parser.add_argument("-l", "--link", required=True, help="视频链接（Bilibili / YouTube）")
    parser.add_argument("-o", "--output", default=None, help="输出文件路径（.srt）")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="默认输出目录（默认 notes）")
    parser.add_argument(
        "--lang",
        default="zh-Hans,zh-CN,zh,en-GB,en",
        help="YouTube 字幕语言优先级，逗号分隔（默认优先简体中文）",
    )
    args = parser.parse_args()

    print(f"正在下载字幕: {args.link}")
    try:
        content, path = download_subtitle(
            video_url=args.link,
            output=args.output,
            output_dir=args.output_dir,
            youtube_lang_priority=args.lang,
        )
    except Exception as exc:
        print(f"下载失败: {exc}")
        return 1

    print(f"字幕已保存: {path} ({len(content.encode('utf-8')) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
