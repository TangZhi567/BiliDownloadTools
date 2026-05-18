import os
import shutil
import sys


SUPPORTED_COOKIE_BROWSERS = ["不使用", "edge", "chrome", "firefox", "brave", "chromium", "opera", "vivaldi"]


def resolve_ytdlp_command() -> list[str] | None:
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


def build_auth_args(auth_options: dict | None) -> list[str]:
    if not auth_options:
        return []

    args: list[str] = []
    cookie_file = str(auth_options.get("cookie_file") or "").strip()
    browser = str(auth_options.get("browser") or "").strip().lower()
    cookie_header = normalize_cookie_header(str(auth_options.get("cookie_header") or ""))

    if cookie_file:
        args.extend(["--cookies", cookie_file])

    if browser and browser != "不使用":
        args.extend(["--cookies-from-browser", browser])

    if cookie_header:
        args.extend(["--add-headers", f"Cookie:{cookie_header}"])

    return args


def normalize_cookie_header(value: str) -> str:
    text = value.strip()
    if not text:
        return ""

    for prefix in ("Cookie:", "cookie:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    if len(lines) == 1:
        return lines[0]

    pairs: list[str] = []
    for line in lines:
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 7:
                pairs.append(f"{parts[5]}={parts[6]}")
                continue
        if "=" in line:
            pairs.append(line.rstrip(";"))

    return "; ".join(pairs)
