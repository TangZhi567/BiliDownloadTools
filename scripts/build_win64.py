import os
import shutil
import subprocess
import sys
import zipfile
import stat
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "assets"
BUILD_RESOURCES_DIR = ROOT / "build_resources"
FFMPEG_DIR = BUILD_RESOURCES_DIR / "ffmpeg"
YTDLP_DIR = BUILD_RESOURCES_DIR / "ytdlp"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"

FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
ICON_URL = "https://www.bilibili.com/favicon.ico"
YTDLP_EXE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=60) as resp:
        data = resp.read()
    target.write_bytes(data)


def ensure_icon() -> Path:
    icon_path = ASSETS_DIR / "app.ico"
    if not icon_path.exists():
        print(f"[INFO] 下载图标: {ICON_URL}")
        _download(ICON_URL, icon_path)
    return icon_path


def ensure_ffmpeg() -> Path:
    ffmpeg_exe = FFMPEG_DIR / "ffmpeg.exe"
    ffprobe_exe = FFMPEG_DIR / "ffprobe.exe"
    if ffmpeg_exe.exists() and ffprobe_exe.exists():
        return FFMPEG_DIR

    # 1) 优先使用本机已安装 ffmpeg（不走网络下载）
    local_candidates = [
        os.environ.get("BILI_FFMPEG_DIR", "").strip(),
        os.environ.get("FFMPEG_DIR", "").strip(),
        r"D:\Program Files\ffmpeg\bind",
        r"D:\Program Files\ffmpeg\bin",
    ]

    for c in local_candidates:
        if not c:
            continue
        c_path = Path(c)
        local_ffmpeg = c_path / "ffmpeg.exe"
        local_ffprobe = c_path / "ffprobe.exe"
        if local_ffmpeg.exists() and local_ffprobe.exists():
            FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_ffmpeg, ffmpeg_exe)
            shutil.copy2(local_ffprobe, ffprobe_exe)
            print(f"[INFO] 使用本地 ffmpeg: {c_path}")
            return FFMPEG_DIR

    # 2) 默认不下载，避免构建依赖外网
    allow_download = os.environ.get("BILI_ALLOW_FFMPEG_DOWNLOAD", "0").strip() == "1"
    if not allow_download:
        raise RuntimeError(
            "未找到本地 ffmpeg/ffprobe。请确认路径存在（如 D:\\Program Files\\ffmpeg\\bin），"
            "或设置环境变量 BILI_FFMPEG_DIR 后重试。"
        )

    BUILD_RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = BUILD_RESOURCES_DIR / "ffmpeg.zip"
    print(f"[INFO] 下载 ffmpeg: {FFMPEG_ZIP_URL}")
    _download(FFMPEG_ZIP_URL, zip_path)

    tmp_dir = BUILD_RESOURCES_DIR / "_ffmpeg_unzip"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_dir)

    found_ffmpeg = None
    found_ffprobe = None
    for p in tmp_dir.rglob("*.exe"):
        name = p.name.lower()
        if name == "ffmpeg.exe":
            found_ffmpeg = p
        elif name == "ffprobe.exe":
            found_ffprobe = p

    if not found_ffmpeg or not found_ffprobe:
        raise RuntimeError("未在下载包中找到 ffmpeg.exe / ffprobe.exe")

    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(found_ffmpeg, ffmpeg_exe)
    shutil.copy2(found_ffprobe, ffprobe_exe)

    return FFMPEG_DIR


def ensure_ytdlp() -> Path:
    ytdlp_exe = YTDLP_DIR / "yt-dlp.exe"
    if ytdlp_exe.exists():
        # 官方独立版体积通常较大（> 5MB）。
        # 若太小，大概率是 Python 启动器（在无 Python 机器不可用）。
        if ytdlp_exe.stat().st_size >= 5 * 1024 * 1024:
            return YTDLP_DIR
        print(f"[WARN] 检测到非独立版 yt-dlp（{ytdlp_exe.stat().st_size} bytes），将替换为官方独立版")
        try:
            ytdlp_exe.unlink(missing_ok=True)
        except Exception:
            pass

    # 1) 显式指定优先
    specified = os.environ.get("BILI_YTDLP_PATH", "").strip()
    if specified:
        src = Path(specified)
        if src.exists():
            YTDLP_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, ytdlp_exe)
            print(f"[INFO] 使用指定 yt-dlp: {src}")
            return YTDLP_DIR
        raise RuntimeError(f"BILI_YTDLP_PATH 不存在: {specified}")

    # 2) 默认下载官方独立版（最适合分发到无 Python 环境机器）
    allow_download = os.environ.get("BILI_ALLOW_YTDLP_DOWNLOAD", "1").strip() == "1"
    if allow_download:
        print(f"[INFO] 下载官方 yt-dlp: {YTDLP_EXE_URL}")
        _download(YTDLP_EXE_URL, ytdlp_exe)
        return YTDLP_DIR

    # 3) 兜底：使用本地安装版本（仅在关闭下载时）
    candidates = [shutil.which("yt-dlp") or "", str(Path(sys.executable).parent / "Scripts" / "yt-dlp.exe")]

    for c in candidates:
        if not c:
            continue
        c_path = Path(c)
        if c_path.exists() and c_path.name.lower().endswith("yt-dlp.exe"):
            YTDLP_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(c_path, ytdlp_exe)
            print(f"[INFO] 使用本地 yt-dlp: {c_path}")
            return YTDLP_DIR

    raise RuntimeError("未找到 yt-dlp.exe。请设置 BILI_YTDLP_PATH，或允许下载 BILI_ALLOW_YTDLP_DOWNLOAD=1")


def run_pyinstaller(icon_path: Path, ffmpeg_dir: Path, ytdlp_dir: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "BiliDownloader",
        "--icon",
        str(icon_path),
        "--add-data",
        f"{ffmpeg_dir};ffmpeg",
        "--add-data",
        f"{ytdlp_dir};ytdlp",
        "app/main.py",
    ]
    print("[INFO] 执行 PyInstaller 打包...")
    subprocess.check_call(cmd, cwd=str(ROOT))


def copy_runtime_tools(ffmpeg_dir: Path, ytdlp_dir: Path) -> None:
    """兜底拷贝运行时工具，避免某些环境 add-data 未落盘。"""
    target_root = DIST_DIR / "BiliDownloader" / "_internal"
    target_ffmpeg = target_root / "ffmpeg"
    target_ytdlp = target_root / "ytdlp"

    target_ffmpeg.mkdir(parents=True, exist_ok=True)
    target_ytdlp.mkdir(parents=True, exist_ok=True)

    shutil.copy2(ffmpeg_dir / "ffmpeg.exe", target_ffmpeg / "ffmpeg.exe")
    shutil.copy2(ffmpeg_dir / "ffprobe.exe", target_ffmpeg / "ffprobe.exe")
    shutil.copy2(ytdlp_dir / "yt-dlp.exe", target_ytdlp / "yt-dlp.exe")

    print(f"[INFO] 已兜底拷贝 ffmpeg 到: {target_ffmpeg}")
    print(f"[INFO] 已兜底拷贝 yt-dlp 到: {target_ytdlp}")


def _safe_rmtree(path: Path) -> None:
    if not path.exists():
        return

    def _onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    try:
        shutil.rmtree(path, onerror=_onerror)
    except PermissionError as ex:
        raise RuntimeError(
            f"无法清理目录: {path}。请先关闭正在运行的程序（如 dist/BiliDownloader/BiliDownloader.exe）后重试。"
        ) from ex


def main() -> None:
    icon_path = ensure_icon()
    ffmpeg_dir = ensure_ffmpeg()
    ytdlp_dir = ensure_ytdlp()

    _safe_rmtree(DIST_DIR)
    _safe_rmtree(BUILD_DIR)

    run_pyinstaller(icon_path, ffmpeg_dir, ytdlp_dir)
    copy_runtime_tools(ffmpeg_dir, ytdlp_dir)
    print("[DONE] 打包完成: dist/BiliDownloader")


if __name__ == "__main__":
    main()

