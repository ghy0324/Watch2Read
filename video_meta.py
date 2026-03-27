"""
获取 Bilibili 视频元数据

用法:
    python video_meta.py -l <bilibili_video_url>
    python video_meta.py --link https://www.bilibili.com/video/BV11LPWzNEkm/
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime

import requests

BILIBILI_API = "https://api.bilibili.com/x/web-interface/view"
BILIBILI_REPLY_API = "https://api.bilibili.com/x/v2/reply"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


def extract_bvid(url: str) -> str:
    """从各种格式的 B 站链接中提取 BV 号"""
    m = re.search(r"(BV[\w]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"av(\d+)", url, re.IGNORECASE)
    if m:
        return None  # 返回 None 表示用 aid
    raise ValueError(f"无法从链接中提取视频 ID: {url}")


def extract_aid(url: str) -> str:
    m = re.search(r"av(\d+)", url, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def format_duration(seconds: int) -> str:
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fetch_pinned_comment(aid: int) -> dict | None:
    """获取视频的置顶评论（UP主置顶），无置顶或请求失败时返回 None"""
    params = {"type": 1, "oid": aid, "sort": 2, "pn": 1, "ps": 5}
    try:
        resp = requests.get(
            BILIBILI_REPLY_API, params=params, headers=HEADERS, timeout=15
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            return None
        data = result.get("data", {})

        # UP主置顶评论在 data.upper.top
        upper_top = (data.get("upper") or {}).get("top")
        if upper_top:
            return _extract_reply(upper_top)

        # 部分接口版本把置顶放在 top_replies
        top_replies = data.get("top_replies")
        if top_replies:
            return _extract_reply(top_replies[0])

        return None
    except Exception:
        return None


def _extract_reply(reply: dict) -> dict:
    return {
        "user": reply.get("member", {}).get("uname", ""),
        "content": reply.get("content", {}).get("message", ""),
        "like": reply.get("like", 0),
    }


def fetch_video_meta(url: str) -> dict:
    """通过 B 站公开 API 获取视频元数据（含简介和置顶评论）"""
    bvid = extract_bvid(url)
    aid = extract_aid(url) if not bvid else None

    params = {"bvid": bvid} if bvid else {"aid": aid}
    resp = requests.get(BILIBILI_API, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    result = resp.json()

    if result["code"] != 0:
        raise RuntimeError(f"API 错误 ({result['code']}): {result['message']}")

    d = result["data"]
    pub_ts = d.get("pubdate", 0)
    pub_date = datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d %H:%M:%S") if pub_ts else "未知"
    video_aid = d.get("aid", 0)

    pinned = fetch_pinned_comment(video_aid) if video_aid else None

    owner = d.get("owner", {})
    mid = owner.get("mid", "")

    return {
        "title": d.get("title", "未知"),
        "bvid": d.get("bvid", ""),
        "aid": video_aid,
        "uploader": owner.get("name", "未知"),
        "uploader_mid": mid,
        "uploader_url": f"https://space.bilibili.com/{mid}" if mid else "",
        "pub_date": pub_date,
        "duration": d.get("duration", 0),
        "duration_fmt": format_duration(d.get("duration", 0)),
        "desc": d.get("desc", ""),
        "cover": d.get("pic", ""),
        "pinned_comment": pinned,
    }


def main():
    parser = argparse.ArgumentParser(description="获取 Bilibili 视频元数据")
    parser.add_argument("-l", "--link", required=True, help="Bilibili 视频链接")
    args = parser.parse_args()

    meta = fetch_video_meta(args.link)

    print(f"标题:     {meta['title']}")
    print(f"UP主:     {meta['uploader']} (UID: {meta['uploader_mid']})")
    print(f"主页:     {meta['uploader_url']}")
    print(f"发布日期: {meta['pub_date']}")
    print(f"视频时长: {meta['duration_fmt']} ({meta['duration']}秒)")
    print(f"BV号:     {meta['bvid']}")
    print(f"AV号:     {meta['aid']}")
    desc = meta.get("desc", "")
    if desc:
        print(f"\n简介:\n{desc}")
    pinned = meta.get("pinned_comment")
    if pinned:
        print(f"\n置顶评论 (@{pinned['user']}):")
        print(f"  {pinned['content']}")
    else:
        print("\n（无置顶评论）")


if __name__ == "__main__":
    main()
