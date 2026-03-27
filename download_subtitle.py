"""
从 kedou.life 下载 Bilibili 视频字幕 (SRT 格式)

用法:
    python download_subtitle.py -l <bilibili_video_url> [-o output.srt]
    python download_subtitle.py --link https://www.bilibili.com/video/BV11LPWzNEkm/
    python download_subtitle.py -l https://www.bilibili.com/video/BV11LPWzNEkm/ -o subtitle.srt
"""

from __future__ import annotations

import argparse
import sys
import re
import json
import base64
import requests
from Crypto.PublicKey import RSA
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.Util.Padding import pad

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
ZH_KEYWORDS = ["中文", "zh", "cn", "chinese", "cmn"]


def _hex2b64(hex_str: str) -> str:
    """JSEncrypt 风格的 hex-to-base64 转换"""
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


def _get_keys(session: requests.Session) -> tuple:
    resp = session.get(f"{KEDOU_BASE}/auth/keys", headers=COMMON_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if data["code"] != 200:
        raise RuntimeError(f"获取密钥失败: {data['message']}")
    return data["data"]["k1"], data["data"]["k2"]


def _rsa_public_decrypt(k2_b64: str, pubkey_b64: str) -> str:
    """用 RSA 公钥还原被私钥加密的 AES 密钥 (doPublic + PKCS#1 unpad)"""
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
    key_bytes = aes_key.encode("utf-8")
    iv_bytes = base64.b64decode(iv_b64)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
    padded = pad(plaintext.encode("utf-8"), AES.block_size)
    return base64.b64encode(cipher.encrypt(padded)).decode("utf-8")


def _rsa_encrypt_long(plaintext: str, pubkey_b64: str) -> str:
    """RSA 分段加密 (JSEncrypt encryptLong), 返回 hex2b64 编码"""
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


def extract_subtitle(session: requests.Session, video_url: str) -> dict:
    """调用 kedou.life API 提取视频字幕信息"""
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


def pick_chinese_track(tracks: list) -> dict:
    for t in tracks:
        lang = (t.get("langDesc") or "").lower()
        if any(kw in lang for kw in ZH_KEYWORDS):
            return t
    return tracks[0] if tracks else None


def download_srt(session: requests.Session, track: dict) -> str:
    if track.get("content"):
        return track["content"]
    src = track.get("srcUrl")
    if not src:
        raise RuntimeError("字幕轨道无下载链接")
    resp = session.get(src, headers={"Referer": "https://www.kedou.life/"})
    resp.raise_for_status()
    return resp.text


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:200]


def main():
    parser = argparse.ArgumentParser(
        description="从 kedou.life 下载 Bilibili 视频字幕 (SRT 格式)"
    )
    parser.add_argument("-l", "--link", required=True, help="Bilibili 视频链接")
    parser.add_argument("-o", "--output", default=None, help="输出文件名 (默认使用视频标题)")
    args = parser.parse_args()

    video_url = args.link
    output_path = args.output

    session = requests.Session()

    print(f"正在提取字幕: {video_url}")
    info = extract_subtitle(session, video_url)

    if info.get("status") == "解析失败":
        print("解析失败，该视频可能没有字幕。")
        sys.exit(1)

    tracks = info.get("subtitleItemVoList", [])
    if not tracks:
        print("未找到任何字幕轨道。")
        sys.exit(1)

    print(f"视频标题: {info.get('title', '未知')}")
    print(f"找到 {len(tracks)} 个字幕轨道:")
    for i, t in enumerate(tracks):
        print(f"  [{i}] {t.get('langDesc', '未知语言')}")

    track = pick_chinese_track(tracks)
    if not track:
        print("未找到中文字幕轨道。")
        sys.exit(1)
    print(f"选择字幕: {track.get('langDesc', '?')}")

    content = download_srt(session, track)

    if not output_path:
        title = sanitize_filename(info.get("title", "subtitle"))
        if not title.endswith(".srt"):
            title += ".srt" if not title.endswith(".srt") else ""
        output_path = title

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    size_kb = len(content.encode("utf-8")) / 1024
    print(f"字幕已保存至: {output_path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
