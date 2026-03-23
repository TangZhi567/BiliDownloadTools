# B站视频下载工具（Python + PySide6）

哔站下载工具，测试AI生成  
当前版本为桌面 GUI 版，已实现：

- 批量粘贴链接（每行一个）
- 链接解析与合集识别（支持从合集内单视频回溯合集）
- 下载任务列表展示
- 并发数可配置
- 下载进度与状态更新（真实 `yt-dlp` 下载）
- 命名模板可配置（支持占位符）

## 1. 环境要求

- Python 3.10+
- Windows 10/11

## 2. 安装依赖

```bash
pip install -r requirements.txt
```

## 3. 运行

```bash
python -m app.main
```

## 4. 当前项目结构

```text
app/
  main.py
  models/
    task.py
  services/
    link_parser.py
    scheduler.py
  ui/
    main_window.py
plans/
  bilibili-gui-plan.md
scripts/
  build_win64.py
build-win64.bat
requirements.txt
README.md
```

## 5. 命名模板占位符

- `{index}`：任务序号（从 1 开始）
- `{title}`：视频标题
- `{shorttitle}` / `{shoutitle}`：短标题（前 30 字）
- `{collection}`：合集名
- `{shortcollection}`：短合集名（前 30 字）
- `{quality}`：清晰度
- `{bvid}`：BV号
- `{date}`：日期（YYYYMMDD）
- `{time}`：时间（HHMMSS）

默认命名模板：`({index})- {title}`

## 6. Win64 打包（内置 ffmpeg，可在无 ffmpeg 环境运行）

### 6.1 一键打包

在项目根目录执行：

```bat
build-win64.bat
```

执行内容：
1. 安装依赖（含 `pyinstaller`）
2. 自动下载 B 站风格图标（`assets/app.ico`）
3. 自动下载并提取 `ffmpeg.exe` / `ffprobe.exe` 到 `build_resources/ffmpeg`
4. 用 PyInstaller 打包到 `dist/BiliDownloader`

### 6.2 产物目录

- 程序目录：`dist/BiliDownloader`
- 主程序：`dist/BiliDownloader/BiliDownloader.exe`
- 内置 ffmpeg：`dist/BiliDownloader/ffmpeg/`

> 这样最终用户机器即使未安装 ffmpeg，也可直接使用。

## 7. 内置 ffmpeg 发现策略

下载器会优先尝试以下目录查找 ffmpeg：
- 环境变量 `BILI_FFMPEG_DIR`
- 打包后程序目录下的 `ffmpeg/`
- 开发环境下的 `build_resources/ffmpeg`

实现见 [`DownloadScheduler._resolve_ffmpeg_location()`](app/services/scheduler.py:182)

## 8. 合规提示

请仅下载你有权访问和使用的内容，并遵守 B 站平台条款及当地法律法规。

