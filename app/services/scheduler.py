import os
import re
import subprocess
import sys
import threading
import time
import uuid
import shutil
from datetime import datetime
from collections import deque
from typing import Callable

from ..models.task import DownloadTask
from .app_logger import get_logger


logger = get_logger("scheduler")


class DownloadScheduler:
    """下载调度器：并发执行 yt-dlp 下载任务并回传进度。"""

    def __init__(self, max_concurrency: int = 2, on_task_update: Callable[[DownloadTask], None] | None = None):
        self.max_concurrency = max(1, int(max_concurrency))
        self.on_task_update = on_task_update or (lambda _task: None)
        self._queue: deque[DownloadTask] = deque()
        self._running = 0
        self._batch_counter = 0
        self._lock = threading.Lock()

    def set_concurrency(self, value: int) -> int:
        with self._lock:
            self.max_concurrency = max(1, int(value))
        self._tick()
        return self.max_concurrency

    def start_batch(self, videos: list[dict], save_path: str, quality: str, naming_template: str = "({index})- {title}") -> dict:
        with self._lock:
            self._batch_counter += 1
            batch_id = f"batch_{int(time.time() * 1000)}_{self._batch_counter}"

            tasks: list[DownloadTask] = []
            for i, video in enumerate(videos):
                task = DownloadTask(
                    id=f"task_{uuid.uuid4().hex[:10]}",
                    batch_id=batch_id,
                    queue_index=i + 1,
                    source_row=int(video.get("source_row", i)),
                    url=video.get("url", ""),
                    title=video.get("title", f"视频 {i + 1}"),
                    quality=quality,
                    save_path=save_path,
                    collection=video.get("collection", ""),
                    naming_template=str(video.get("naming_template") or naming_template),
                )
                tasks.append(task)
                self._queue.append(task)

        for task in tasks:
            self.on_task_update(task)

        self._tick()
        return {"batch_id": batch_id, "count": len(tasks), "tasks": tasks}

    def _tick(self) -> None:
        with self._lock:
            while self._running < self.max_concurrency and self._queue:
                task = self._queue.popleft()
                self._running += 1
                t = threading.Thread(target=self._run_task, args=(task,), daemon=True)
                t.start()

    def _run_task(self, task: DownloadTask) -> None:
        try:
            logger.info("任务开始 id=%s title=%s url=%s", task.id, task.title, task.url)
            base_dir = task.save_path
            if task.collection and task.collection.strip():
                base_dir = os.path.join(task.save_path, self._sanitize_filename(task.collection.strip()))

            os.makedirs(base_dir, exist_ok=True)

            task.status = "downloading"
            task.progress = 0
            task.error_message = ""
            self.on_task_update(task)

            quality_height = self._parse_quality_height(task.quality)
            if quality_height:
                fmt = f"bestvideo[height<={quality_height}]+bestaudio/best[height<={quality_height}]/best"
            else:
                fmt = "bestvideo+bestaudio/best"

            output_tpl = os.path.join(base_dir, f"{self._build_output_name(task)}.%(ext)s")

            ytdlp_cmd = self._resolve_ytdlp_command()
            if not ytdlp_cmd:
                raise RuntimeError("未找到 yt-dlp。请安装依赖或在打包目录提供 ytdlp\\yt-dlp.exe")

            # 先做可执行性自检，避免只返回“下载失败，返回码 1”
            ver_check = subprocess.run(
                [*ytdlp_cmd, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
            )
            if ver_check.returncode != 0:
                raise RuntimeError(
                    "yt-dlp 无法执行，请确认发布目录完整且未被安全软件拦截。\n"
                    f"命令: {' '.join(ytdlp_cmd)} --version\n"
                    f"输出: {(ver_check.stdout or '')[-300:]} {(ver_check.stderr or '')[-300:]}"
                )

            logger.info("下载命令 ytdlp=%s", " ".join(ytdlp_cmd))

            cmd = [
                *ytdlp_cmd,
                "--newline",
                "--no-warnings",
                "--windows-filenames",
                "--restrict-filenames",
                "--merge-output-format",
                "mp4",
                "-f",
                fmt,
                "-o",
                output_tpl,
            ]
            ffmpeg_location = self._resolve_ffmpeg_location()
            if ffmpeg_location:
                cmd.extend(["--ffmpeg-location", ffmpeg_location])

            cmd.append(task.url)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
            )

            last_p = -1
            logs: list[str] = []
            assert proc.stdout is not None
            for line in proc.stdout:
                s = line.strip()
                if s:
                    logs.append(s)
                m = re.search(r"(\d{1,3}(?:\.\d+)?)%", s)
                if m:
                    try:
                        p = int(float(m.group(1)))
                        p = max(0, min(99, p))
                        if p != last_p:
                            last_p = p
                            task.progress = p
                            self.on_task_update(task)
                    except ValueError:
                        pass

            code = proc.wait()
            if code != 0:
                tail = "\n".join(logs[-8:]) if logs else "yt-dlp 返回错误"
                logger.error("任务失败 id=%s code=%s tail=%s", task.id, code, tail)
                raise RuntimeError(
                    "下载失败。\n"
                    f"返回码: {code}\n"
                    f"ytdlp: {' '.join(ytdlp_cmd)}\n"
                    f"日志末尾:\n{tail}"
                )

            task.status = "completed"
            task.progress = 100
            self.on_task_update(task)
            logger.info("任务完成 id=%s title=%s", task.id, task.title)
        except Exception as ex:
            task.status = "failed"
            task.error_message = str(ex)
            self.on_task_update(task)
            logger.exception("任务异常 id=%s", task.id)
        finally:
            with self._lock:
                self._running -= 1
            self._tick()

    @staticmethod
    def _parse_quality_height(quality_text: str) -> int | None:
        m = re.search(r"(\d+)p", quality_text or "", re.I)
        if not m:
            return None
        try:
            h = int(m.group(1))
            return h if h > 0 else None
        except ValueError:
            return None

    @staticmethod
    def _build_output_name(task: DownloadTask) -> str:
        now = datetime.now()
        title = (task.title or "video").strip()
        collection = (task.collection or "").strip()
        shorttitle = title[:30] if title else "video"
        shortcollection = collection[:30] if collection else ""

        bvid = ""
        m = re.search(r"/video/(BV[0-9A-Za-z]{10})", task.url or "", re.I)
        if m:
            bvid = m.group(1)

        replacements = {
            "{index}": str(task.queue_index),
            "{title}": title,
            "{shorttitle}": shorttitle,
            "{shoutitle}": shorttitle,
            "{collection}": collection,
            "{shortcollection}": shortcollection,
            "{quality}": task.quality or "",
            "{bvid}": bvid,
            "{date}": now.strftime("%Y%m%d"),
            "{time}": now.strftime("%H%M%S"),
        }

        template = task.naming_template or "({index})- {title}"
        output_name = template
        for key, value in replacements.items():
            output_name = output_name.replace(key, value)

        output_name = DownloadScheduler._sanitize_filename(output_name)
        if not output_name:
            output_name = f"video_{task.queue_index}"

        return output_name

    @staticmethod
    def _sanitize_filename(value: str) -> str:
        # Windows 非法文件名字符: \ / : * ? " < > |
        cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned

    @staticmethod
    def _resolve_ffmpeg_location() -> str | None:
        env_dir = os.environ.get("BILI_FFMPEG_DIR", "").strip()
        if env_dir and os.path.exists(env_dir):
            return env_dir

        candidates: list[str] = []

        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            candidates.append(os.path.join(exe_dir, "ffmpeg"))
            candidates.append(os.path.join(exe_dir, "_internal", "ffmpeg"))
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                candidates.append(os.path.join(meipass, "ffmpeg"))
                candidates.append(os.path.join(meipass, "_internal", "ffmpeg"))
        else:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            candidates.append(os.path.join(project_root, "build_resources", "ffmpeg"))
            candidates.append(os.path.join(project_root, "ffmpeg"))

        for c in candidates:
            ffmpeg_exe = os.path.join(c, "ffmpeg.exe")
            ffprobe_exe = os.path.join(c, "ffprobe.exe")
            if os.path.exists(ffmpeg_exe) and os.path.exists(ffprobe_exe):
                return c

        return None

    @staticmethod
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

