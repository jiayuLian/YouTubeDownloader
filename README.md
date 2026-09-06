# YouTube 批量下载器（Windows 单文件 exe）

一个面向 Windows 的、**双击即用**的 YouTube 批量下载工具：粘贴多个链接 → 并发下载 → 自动把字幕内嵌进视频。无需安装 Python / ffmpeg / yt-dlp，全部打包进单个 `.exe`。

## 功能特性

- **一次性下载多个视频**：每行贴一个链接（支持视频 / 播放列表），线程池并发下载，互不阻塞。
- **字幕自动内嵌**：默认下载中文优先字幕并**封装进视频**（mkv 内嵌轨道），不再留下散落的 `.srt/.vtt` 文件。
- **画质可选 / 也可不解析**：默认 **720p**；可选 **1080p / 最佳画质 / 仅音频(mp3)**，也可点「解析画质」拉取视频真实清晰度后精确选档（如 2160p / 1440p …）。
- **并发参数全自动**：同时下载几个视频、每视频并发片段、分片大小都由程序按视频数量自动优化，无需手动调。
- **实时进度**：整体进度条 + 百分比 + 已下/总大小 + 速度 + 预计剩余时间，每条任务单独显示状态（下载中 / 合并中 / 完成 / 已取消 / 失败）。
- **真正的「停止」**：点停止立即中断进行中的下载、跳过未开始的、清理半截临时文件，几秒后「开始下载」自动恢复可用。
- **代理支持**：可填本地代理地址（如 `http://127.0.0.1:7890`）；不填则自动读取系统 `http_proxy/https_proxy` 环境变量。
- **一键打开下载目录**：按钮直接打开当前保存文件夹。
- 附带「仅下载音频(mp3)」模式。

> 字幕为 mkv 内嵌轨道（可选轨道，播放器需在字幕/CC 里开启）。如需把字幕**烧录成画面硬字幕**（任何设备直接显示、不可关），后续可加开关。

## 下载即用

1. 下载 `YouTubeDownloader.exe`（见右侧 Releases）。
2. 双击运行（无黑框窗口）。
3. 在大框里**每行贴一个链接**，或点「从 urls.txt 导入」。
4. 选择保存目录（默认在 exe 同级的 `YoutubeDownloads`，不存在会自动创建）。
5. 点「开始下载」。

> 首次使用若个别视频提示无字幕，通常是该视频本身没有可用字幕轨道，属正常。

## 自行构建（开发者）

环境要求：Windows + Python 3.11（需自带 tkinter，python.org 官方安装包默认包含；本项目的 venv 即基于此）。

```bat
# 1. 准备 venv 并装依赖
python -m venv venv
venv\Scripts\python.exe -m pip install -U pip
venv\Scripts\pip.exe install yt-dlp pyinstaller

# 2. 把 ffmpeg.exe（Windows 64-bit 版）放到本目录
#    下载地址：https://www.gyan.dev/ffmpeg/builds/  (ffmpeg-release-essentials.zip)

# 3. 打包
build.bat
```

产物：`pyout_1/YouTubeDownloader.exe`（已内嵌 ffmpeg + yt_dlp + tcl/tk + app.ico 图标）。

> 如需重新生成图标：`venv\Scripts\pip.exe install Pillow` 后运行 `make_icon.py`，会生成 `app.ico`。

## 目录结构

```
yt-downloader/
├── yt_downloader.py     # 主程序源码（GUI + 下载逻辑）
├── app.ico              # 程序图标（已内嵌进 exe）
├── make_icon.py         # 用 Pillow 重新生成 app.ico 的脚本
├── build.bat            # PyInstaller 打包脚本（onefile / windowed / 内嵌图标）
├── requirements.txt     # 依赖：yt-dlp、pyinstaller
├── urls.txt.example     # 链接示例（每行一个）
├── .gitignore
└── README.md
```

## 说明 / 免责

- 本工具基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 封装，仅用于下载**你自己拥有版权或已获授权**的内容，请遵守当地法律法规与 YouTube 服务条款。
- 下载速度受网络 / 代理 / YouTube 服务端节流影响。
