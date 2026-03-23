import re
import subprocess
import os
import shutil
import sys
import time
import json
from urllib.parse import quote
from urllib.request import Request, urlopen
from typing import Any

from .app_logger import get_logger


logger = get_logger("parser")


_URL_IN_TEXT_RE = re.compile(r"(https?://[^\s]+)", re.I)
_BV_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b", re.I)
_AV_RE = re.compile(r"\bav(\d+)\b", re.I)

_REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json,text/plain,*/*",
}


def _normalize_input_to_url(text: str) -> str:
    raw = text.strip()
    if not raw:
        return ""

    m = _URL_IN_TEXT_RE.search(raw)
    if m:
        return m.group(1).strip().rstrip("),。；;！!？?\"'")

    if raw.lower().startswith("b23.tv/"):
        return f"https://{raw}"

    if raw.lower().startswith("www.bilibili.com/"):
        return f"https://{raw}"

    bv = _BV_RE.search(raw)
    if bv:
        return f"https://www.bilibili.com/video/{bv.group(1)}"

    return raw


def _is_bilibili_url(url: str) -> bool:
    return bool(re.search(r"https?://([\w-]+\.)?(bilibili\.com|b23\.tv)/", url, re.I))


def _extract_qualities(info: dict[str, Any]) -> list[str]:
    labels: set[str] = set()
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict):
            continue

        note = str(fmt.get("format_note") or "").strip()
        if note:
            labels.add(note)
            continue

        height = fmt.get("height")
        if isinstance(height, int) and height > 0:
            labels.add(f"{height}p")

    if not labels:
        return ["1080p", "720p", "480p"]

    def _score(label: str) -> int:
        m = re.search(r"(\d+)p", label)
        return int(m.group(1)) if m else 0

    return sorted(labels, key=_score, reverse=True)


def _extract_info_with_ytdlp(url: str) -> dict[str, Any] | None:
    # 调用系统 yt-dlp 命令解析信息（避免 PyInstaller 对 yt_dlp 模块分析异常）
    ytdlp_cmd = _resolve_ytdlp_command()
    if not ytdlp_cmd:
        logger.error("未找到 yt-dlp 可执行文件，url=%s", url)
        return None

    try:
        logger.info("解析开始 url=%s, ytdlp=%s", url, " ".join(ytdlp_cmd))

        run_kwargs = {}
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        proc = subprocess.run(
            [*ytdlp_cmd, "-J", "--no-warnings", url],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="ignore",
            **run_kwargs,
        )
        logger.info("解析命令结束 returncode=%s", proc.returncode)
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            if isinstance(data, dict):
                return data
        logger.warning("解析失败 returncode=%s stderr_tail=%s", proc.returncode, (proc.stderr or "")[-300:])
    except Exception:
        logger.exception("解析异常 url=%s", url)

    return None


def _resolve_ytdlp_command() -> list[str] | None:
    env_path = os.environ.get("BILI_YTDLP_PATH", "").strip()
    if env_path and os.path.exists(env_path):
        return [env_path]

    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "ytdlp", "yt-dlp.exe"))
        candidates.append(os.path.join(exe_dir, "_internal", "ytdlp", "yt-dlp.exe"))
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(os.path.join(meipass, "ytdlp", "yt-dlp.exe"))
            candidates.append(os.path.join(meipass, "_internal", "ytdlp", "yt-dlp.exe"))

    for c in candidates:
        if os.path.exists(c):
            return [c]

    which_cmd = shutil.which("yt-dlp")
    if which_cmd:
        return [which_cmd]

    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "yt_dlp"]

    return None


def _convert_info_to_items(
    info: dict[str, Any],
    normalized_url: str,
    line_idx: int,
    seq_start: int,
) -> list[dict[str, Any]]:
    now = int(time.time() * 1000)
    items: list[dict[str, Any]] = []

    entries = info.get("entries")
    if isinstance(entries, list) and entries:
        # 合集/播放列表
        collection = str(info.get("title") or info.get("playlist_title") or "")
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            entry_url = str(entry.get("webpage_url") or entry.get("url") or normalized_url)
            items.append(
                {
                    "id": f"video_{now}_{seq_start + len(items)}",
                    "url": entry_url,
                    "title": str(entry.get("title") or f"待下载视频 {line_idx + 1}-{i + 1}"),
                    "duration": str(entry.get("duration_string") or ""),
                    "cover": str(entry.get("thumbnail") or ""),
                    "qualities": _extract_qualities(entry),
                    "collection": collection,
                }
            )

    if items:
        return items

    # 单视频
    return [
        {
            "id": f"video_{now}_{seq_start}",
            "url": str(info.get("webpage_url") or normalized_url),
            "title": str(info.get("title") or f"待下载视频 {line_idx + 1}"),
            "duration": str(info.get("duration_string") or ""),
            "cover": str(info.get("thumbnail") or ""),
            "qualities": _extract_qualities(info),
            "collection": str(info.get("playlist_title") or ""),
        }
    ]


def _extract_bvid(url: str, info: dict[str, Any] | None = None) -> str:
    m = re.search(r"/video/(BV[0-9A-Za-z]{10})", url, re.I)
    if m:
        return m.group(1)
    m = re.search(r"[?&]bvid=(BV[0-9A-Za-z]{10})", url, re.I)
    if m:
        return m.group(1)
    if info:
        for key in ("id", "display_id", "webpage_url", "original_url", "url", "webpage_url_basename"):
            text = str(info.get(key) or "")
            if not text:
                continue
            m2 = re.search(r"/video/(BV[0-9A-Za-z]{10})", text, re.I)
            if m2:
                return m2.group(1)
            m3 = re.search(r"[?&]bvid=(BV[0-9A-Za-z]{10})", text, re.I)
            if m3:
                return m3.group(1)
            if re.fullmatch(r"BV[0-9A-Za-z]{10}", text, re.I):
                return text
    return ""


def _extract_aid(url: str, info: dict[str, Any] | None = None) -> str:
    m = _AV_RE.search(url)
    if m:
        return m.group(1)

    if info:
        for key in ("aid", "id", "display_id", "webpage_url", "original_url", "url"):
            text = str(info.get(key) or "")
            if not text:
                continue
            if text.isdigit():
                return text
            m2 = _AV_RE.search(text)
            if m2:
                return m2.group(1)
    return ""


def _expand_pages_by_id(
    bvid: str,
    aid: str,
    normalized_url: str,
    line_idx: int,
    seq_start: int,
    fallback_info: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not bvid and not aid:
        return []

    if bvid:
        api = f"https://api.bilibili.com/x/web-interface/view?bvid={quote(bvid)}"
    else:
        api = f"https://api.bilibili.com/x/web-interface/view?aid={quote(aid)}"

    try:
        req = Request(api, headers=_REQ_HEADERS)
        with urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        payload = json.loads(body)
        code = payload.get("code", -1)
        if code != 0:
            return []

        data = payload.get("data") or {}
        real_bvid = str(data.get("bvid") or bvid)
        real_aid = str(data.get("aid") or aid)
        main_title = str(data.get("title") or f"合集 {bvid}")
        qualities = _extract_qualities(fallback_info or {})
        now = int(time.time() * 1000)
        items: list[dict[str, Any]] = []

        # 1) 先尝试多P视频展开
        pages = data.get("pages") or []
        if isinstance(pages, list) and len(pages) > 1:
            for i, p in enumerate(pages):
                if not isinstance(p, dict):
                    continue
                part = str(p.get("part") or f"第{i + 1}P")
                if real_bvid:
                    page_url = f"https://www.bilibili.com/video/{real_bvid}?p={i + 1}"
                elif real_aid:
                    page_url = f"https://www.bilibili.com/video/av{real_aid}?p={i + 1}"
                else:
                    page_url = normalized_url
                items.append(
                    {
                        "id": f"video_{now}_{seq_start + len(items)}",
                        "url": page_url,
                        "title": part,
                        "duration": "",
                        "cover": "",
                        "qualities": qualities,
                        "collection": main_title,
                    }
                )

        if items:
            return items

        # 2) 再尝试 UGC 合集（课程/系列）展开
        ugc = data.get("ugc_season") or {}
        sections = ugc.get("sections") or []
        ugc_title = str(ugc.get("title") or main_title)
        if isinstance(sections, list) and sections:
            for sec in sections:
                if not isinstance(sec, dict):
                    continue
                episodes = sec.get("episodes") or []
                if not isinstance(episodes, list):
                    continue

                for ep in episodes:
                    if not isinstance(ep, dict):
                        continue
                    ep_bvid = str(ep.get("bvid") or "")
                    ep_aid = str(ep.get("aid") or "")
                    ep_title = str(
                        ep.get("title")
                        or (ep.get("arc") or {}).get("title")
                        or (ep.get("page") or {}).get("part")
                        or ""
                    ).strip()
                    if not ep_title:
                        ep_title = f"第{len(items) + 1}集"

                    if ep_bvid:
                        ep_url = f"https://www.bilibili.com/video/{ep_bvid}"
                    elif ep_aid:
                        ep_url = f"https://www.bilibili.com/video/av{ep_aid}"
                    else:
                        ep_url = normalized_url

                    items.append(
                        {
                            "id": f"video_{now}_{seq_start + len(items)}",
                            "url": ep_url,
                            "title": ep_title,
                            "duration": "",
                            "cover": "",
                            "qualities": qualities,
                            "collection": ugc_title,
                        }
                    )

        if len(items) > 1:
            return items

        items = []

        return []
    except Exception:
        return []


def parse_links(raw_text: str) -> dict[str, Any]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    valid: list[dict[str, Any]] = []
    invalid: list[str] = []

    now = int(time.time() * 1000)
    counter = 0
    for idx, line in enumerate(lines):
        normalized_url = _normalize_input_to_url(line)
        if _is_bilibili_url(normalized_url):
            info = _extract_info_with_ytdlp(normalized_url)

            # 优先尝试把单P链接扩展为整合集（多P）
            bvid = _extract_bvid(normalized_url, info)
            aid = _extract_aid(normalized_url, info)
            expanded = _expand_pages_by_id(bvid, aid, normalized_url, idx, counter, info)
            if expanded:
                valid.extend(expanded)
                counter += len(expanded)
                continue

            if info:
                items = _convert_info_to_items(info, normalized_url, idx, counter)
                valid.extend(items)
                counter += len(items)
            else:
                valid.append(
                    {
                        "id": f"video_{now}_{counter}",
                        "url": normalized_url,
                        "title": f"待下载视频 {idx + 1}",
                        "duration": "00:00:00",
                        "cover": "",
                        "qualities": ["1080p", "720p", "480p"],
                        "collection": "",
                    }
                )
                counter += 1
        else:
            invalid.append(line)

    logger.info("解析完成 total=%s valid=%s invalid=%s", len(lines), len(valid), len(invalid))
    return {"total": len(lines), "valid": valid, "invalid": invalid}

