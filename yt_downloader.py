# -*- coding: utf-8 -*-
"""
YouTube 批量下载器 (Windows 单文件 exe)
- 一次粘贴多个视频链接，多线程并发下载
- 每个视频多片段并发拉取（把下载线程开满）
- 有字幕时自动下载并内嵌进视频（mkv 容器）
- 支持本地代理、整体进度条 + 预估完成时间 + 真实成功/失败检测
依赖：yt-dlp + ffmpeg（ffmpeg.exe 已随 exe 一起打包）
"""
import os
import sys
import re
import glob
import shutil
import subprocess
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# ----------------------------------------------------------------------------
# 资源路径：打包后 ffmpeg.exe 在 _MEIPASS，开发时在脚本同目录
# ----------------------------------------------------------------------------
def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


FFMPEG = resource_path("ffmpeg.exe")
EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
DEFAULT_OUT = os.path.join(EXE_DIR, "YoutubeDownloads")

# ----------------------------------------------------------------------------
# 向 GUI 日志转发的队列 + logger
# ----------------------------------------------------------------------------
log_queue = queue.Queue()


class GuiLogger:
    def __init__(self, q):
        self.q = q

    def debug(self, msg):
        self.q.put(("log", msg))

    def info(self, msg):
        self.q.put(("log", msg))

    def warning(self, msg):
        self.q.put(("log", "⚠ " + msg))

    def error(self, msg):
        self.q.put(("log", "❌ " + msg))

    def critical(self, msg):
        self.q.put(("log", "❌ " + msg))


class TaskLogger(GuiLogger):
    """每个视频独立 logger，记录是否发生过错误，用于真实失败检测。"""
    def __init__(self, q):
        super().__init__(q)
        self.failed = False

    def error(self, msg):
        self.failed = True
        self.q.put(("log", "❌ " + msg))

    def critical(self, msg):
        self.failed = True
        self.q.put(("log", "❌ " + msg))


# ----------------------------------------------------------------------------
# 格式化辅助
# ----------------------------------------------------------------------------
def fmt_speed(bps):
    if not bps:
        return "0 B/s"
    bps = float(bps)
    for u in ["B/s", "KB/s", "MB/s", "GB/s"]:
        if bps < 1024:
            return f"{bps:.1f} {u}"
        bps /= 1024
    return f"{bps:.1f} TB/s"


def fmt_eta(sec):
    if sec is None:
        return "未知"
    sec = int(sec)
    if sec < 0:
        return "未知"
    if sec < 60:
        return f"{sec}s"
    m = sec // 60
    s = sec % 60
    if m < 60:
        return f"{m}m{s}s"
    h = m // 60
    m %= 60
    return f"{h}h{m}m"


def fmt_bytes(n):
    if not n:
        return "0B"
    n = float(n)
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


# ----------------------------------------------------------------------------
# 画质映射
# ----------------------------------------------------------------------------
QUALITY_MAP = {
    "最佳画质 (best)": "bestvideo*+bestaudio/best",
    "仅音频 (mp3)": "bestaudio",
}

# 默认字幕语言：中文优先，无中文时回退英文（用户无需手动设置）
DEFAULT_SUBTITLES = ["zh-Hans", "zh", "zh-CN", "en"]


def quality_to_format(quality):
    """把画质选项转成 yt-dlp 的 format 选择器。

    支持：
      - 720p（默认）/ 1080p：按 height 过滤，有该档用该档，没有则自动降次档
      - 最佳画质 (best) / 仅音频 (mp3)：由 QUALITY_MAP 处理
      - 动态档：解析视频后生成的其它 "1440p" / "2160p" 等，按 height 过滤
    """
    if quality == "最佳画质 (best)":
        return QUALITY_MAP["最佳画质 (best)"]
    if re.fullmatch(r"\d+p", quality or ""):
        h = int(quality[:-1])
        return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
    return QUALITY_MAP.get(quality, "bestvideo[height<=720]+bestaudio/best[height<=720]")


def build_opts(out_dir, quality, fragments, subs_langs, embed, only_audio, proxy=None, chunk_mb=0, hardsub=False):
    opts = {
        "outtmpl": os.path.join(out_dir, "%(title)s [%(id)s].%(ext)s"),
        "ffmpeg_location": FFMPEG,
        "concurrent_fragment_downloads": fragments,
        "retries": 10,
        "fragment_retries": 10,
        "skip_unavailable_fragments": True,
        "ignoreerrors": True,            # 单个失败不中断整批
        "noprogress": True,              # 用 progress_hooks 自定义进度
        "logger": GuiLogger(log_queue),
        "progress_hooks": [],            # 运行时注入
        "quiet": False,
        "no_warnings": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        },
    }
    if proxy:
        opts["proxy"] = proxy            # 走本地代理（如 http://127.0.0.1:7890）
    if chunk_mb and chunk_mb > 0:
        # 单个 HTTP 分片大小（字节）：大分片减少请求次数，快宽带更明显提速
        opts["http_chunk_size"] = int(chunk_mb * 1024 * 1024)
    if only_audio:
        opts["format"] = QUALITY_MAP["仅音频 (mp3)"]
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        opts["format"] = quality_to_format(quality)
        opts["merge_output_format"] = "mkv"   # mkv 对字幕内嵌兼容性最好
        if embed and subs_langs:
            # 始终下载字幕（手动+自动）到磁盘，供后续封装/烧录使用
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            opts["subtitlesformat"] = "srt/ass/vtt"
            opts["subtitleslangs"] = subs_langs
            if hardsub:
                # 硬字幕模式：把字幕烧录进画面，不额外加可选软轨道
                # （避免播放器开了 CC 后出现“双字幕”）
                opts["embedsubtitles"] = False
            else:
                # 软内嵌模式：字幕作为可选轨道，播放器需手动开 CC
                opts["embedsubtitles"] = True
    return opts


def get_video_heights(url, proxy=None, max_entries=20):
    """返回该链接（含播放列表前 N 个视频）所有可用视频分辨率高度集合。

    用于「解析画质」：先拉取真实清晰度，再让用户从真实档位里统一选，
    避免固定预设选不到真实存在的分辨率。
    """
    from yt_dlp import YoutubeDL
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "proxy": proxy,
    }
    heights = set()
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return heights
        entries = info.get("entries")
        samples = entries[:max_entries] if entries else [info]
        for e in samples:
            if not e:
                continue
            for f in e.get("formats", []):
                vcodec = f.get("vcodec")
                h = f.get("height")
                if vcodec not in (None, "none") and h:
                    heights.add(int(h))
    except Exception as e:
        log_queue.put(("log", f"解析画质失败 {url}: {e}"))
    return heights


# ----------------------------------------------------------------------------
# 单个视频下载
# ----------------------------------------------------------------------------
def download_one(url, base_opts, out_dir, subs_langs, hardsub, idx, total, abort, status_cb):
    """status_cb(idx, info_dict) —— info_dict: {state, pct, speed, eta, downloaded, total}"""
    if abort.is_set():
        status_cb(idx, {"state": "已取消", "pct": None})
        return
    status_cb(idx, {"state": "下载中", "pct": None})

    # 每个视频用独立的 TaskLogger，才能准确判断成功/失败
    log = TaskLogger(log_queue)
    captured = {}  # 记录视频 id / 最终文件路径，供字幕兜底使用
    opts = dict(base_opts)
    opts["logger"] = log
    opts["progress_hooks"] = [make_hook(idx, status_cb, abort, captured)]

    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadCancelled
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
        if abort.is_set():
            status_cb(idx, {"state": "已取消", "pct": None})
            return
        # ignoreerrors=True 时 yt-dlp 不会抛异常，需用 logger.error 判断是否真失败
        if log.failed:
            status_cb(idx, {"state": "失败", "pct": None})
            return
        if hardsub:
            # 硬字幕：把字幕烧录进画面，任何播放器/手机都直接显示、关不掉
            burned = burn_subtitles(captured, out_dir, subs_langs, idx)
            if not burned:
                # 烧录失败则退回软内嵌兜底（至少保证有字幕轨道）
                recover_embed_subtitles(captured, out_dir, subs_langs, idx)
            state = "完成(已烧录字幕)" if burned else "完成(已内嵌字幕)"
        else:
            # 软内嵌兜底：yt-dlp 对“自动字幕”内嵌经常静默失败，会留下独立字幕文件。
            # 这里检测残留的独立字幕，若有则用 ffmpeg 封装回视频并删除残留。
            embedded = recover_embed_subtitles(captured, out_dir, subs_langs, idx)
            state = "完成(已内嵌字幕)" if embedded else "完成"
        status_cb(idx, {"state": state, "pct": 100})
    except DownloadCancelled:
        # 用户点了停止，本视频主动取消
        status_cb(idx, {"state": "已取消", "pct": None})
    except Exception as e:
        if abort.is_set():
            status_cb(idx, {"state": "已取消", "pct": None})
        else:
            status_cb(idx, {"state": "失败", "pct": None})
            log_queue.put(("log", f"[{idx}/{total}] 异常: {e}"))


def make_hook(idx, status_cb, abort, captured):
    def hook(d):
        # 用户点了停止：在进度回调里抛 DownloadCancelled，立即中止当前下载（绕过重试）
        if abort.is_set():
            from yt_dlp.utils import DownloadCancelled
            raise DownloadCancelled()
        info = d.get("info_dict", {}) or {}
        if info.get("id"):
            captured["id"] = info["id"]
        fp = d.get("filename") or info.get("filepath")
        if fp:
            captured["video_path"] = fp
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            eta = d.get("eta")
            pct = (downloaded / total * 100) if total else None
            status_cb(idx, {
                "state": "下载中", "pct": pct, "speed": speed, "eta": eta,
                "downloaded": downloaded, "total": total,
            })
        elif d.get("status") == "finished":
            status_cb(idx, {
                "state": "合并中", "pct": 100, "speed": 0, "eta": 0,
                "downloaded": 0, "total": 0,
            })
        elif d.get("status") == "error":
            status_cb(idx, {
                "state": "失败", "pct": None, "speed": 0, "eta": None,
                "downloaded": 0, "total": 0,
            })
    return hook


# 语言代码映射：把 yt-dlp 的字幕语言标签转成 ffmpeg 用的 3 字母代码
LANG_MAP = {
    "zh-Hans": "chi", "zh": "chi", "zh-CN": "chi", "zh-TW": "chi", "zh-Hant": "chi",
    "en": "eng", "ja": "jpn", "ko": "kor", "fr": "fre", "de": "ger",
    "es": "spa", "ru": "rus", "pt": "por", "ar": "ara", "hi": "hin",
}


def _has_subtitle_stream(video_path):
    """该视频文件是否已含字幕流（避免重复封装）。"""
    try:
        r = subprocess.run(
            [FFMPEG, "-i", video_path],
            stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True,
        ).stderr
        return "Subtitle:" in r
    except Exception:
        return False


def recover_embed_subtitles(captured, out_dir, subs_langs, idx):
    """若 yt-dlp 内嵌失败、留下了独立的 .srt/.vtt/.ass 文件，则手动用 ffmpeg
    把字幕封装进视频，并删除独立文件。返回是否成功内嵌。"""
    vid = captured.get("id")
    # 1) 定位主视频文件
    videos = []
    if vid:
        videos = glob.glob(os.path.join(out_dir, f"*{vid}*.mkv"))
        if not videos:
            videos = glob.glob(os.path.join(out_dir, f"*{vid}*.mp4"))
    if not videos:
        videos = glob.glob(os.path.join(out_dir, "*.mkv")) + \
                 glob.glob(os.path.join(out_dir, "*.mp4"))
    if not videos:
        return False
    video_path = max(videos, key=os.path.getmtime)  # 刚下完的最新文件
    # 若该视频已含字幕流，说明 yt-dlp 内嵌成功了，无需重复封装；
    # 但个别版本会同时留下独立字幕文件，这里一并清理孤儿字幕。
    if _has_subtitle_stream(video_path):
        for ext in ("srt", "vtt", "ass"):
            pattern = f"*{vid}*.{ext}" if vid else f"*.{ext}"
            for sf in glob.glob(os.path.join(out_dir, pattern)):
                try:
                    os.remove(sf)
                except OSError:
                    pass
        return False
    # 2) 找该视频的独立字幕文件
    subs = []
    for ext in ("srt", "vtt", "ass"):
        if vid:
            subs += glob.glob(os.path.join(out_dir, f"*{vid}*.{ext}"))
        else:
            subs += glob.glob(os.path.join(out_dir, f"*.{ext}"))
    if not subs:
        return False  # 没有独立字幕文件：已嵌入或无字幕
    # 3) 按优先语言选一个字幕
    chosen = None
    chosen_lang = None
    for lang in subs_langs:
        for sf in subs:
            if f".{lang}." in sf:
                chosen = sf
                chosen_lang = lang
                break
        if chosen:
            break
    if not chosen:
        chosen = subs[0]
        # 从文件名反推语言
        for lang in subs_langs:
            if f".{lang}." in chosen:
                chosen_lang = lang
                break
    # 4) ffmpeg 封装
    base = os.path.splitext(video_path)[0]
    out_path = base + ".subbed.mkv"
    lang_code = LANG_MAP.get(chosen_lang, "und")
    cmd = [
        FFMPEG, "-y", "-i", video_path, "-i", chosen,
        "-map", "0", "-map", "1",
        "-c", "copy", "-c:s", "srt",
        "-metadata:s:s:0", f"language={lang_code}",
        out_path,
    ]
    try:
        subprocess.run(
            cmd, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        os.replace(out_path, video_path)
        # 删除该视频所有独立字幕残片
        for sf in subs:
            try:
                os.remove(sf)
            except OSError:
                pass
        log_queue.put(("log", f"[{idx}] 已把字幕内嵌进视频：{os.path.basename(video_path)}"))
        return True
    except Exception as e:
        log_queue.put(("log", f"[{idx}] 字幕内嵌失败（保留独立字幕文件）：{e}"))
        return False


def burn_subtitles(captured, out_dir, subs_langs, idx):
    """硬字幕：用 ffmpeg 把字幕烧录进视频画面，任何播放器打开即显示、关不掉。

    优先用 yt-dlp 写出的独立字幕文件（writesubtitles=True 保证落盘）；
    找不到独立文件时，从视频已有的内嵌字幕流抽取后再烧录。
    返回是否成功烧录。
    """
    vid = captured.get("id")
    # 1) 定位主视频文件
    videos = []
    if vid:
        videos = glob.glob(os.path.join(out_dir, f"*{vid}*.mkv"))
        if not videos:
            videos = glob.glob(os.path.join(out_dir, f"*{vid}*.mp4"))
    if not videos:
        videos = glob.glob(os.path.join(out_dir, "*.mkv")) + \
                 glob.glob(os.path.join(out_dir, "*.mp4"))
    if not videos:
        return False
    video_path = max(videos, key=os.path.getmtime)  # 刚下完的最新文件

    # 2) 找字幕源：先独立字幕文件，再内嵌字幕流
    sub_src = None
    subs = []
    for ext in ("srt", "ass", "vtt"):
        if vid:
            subs += glob.glob(os.path.join(out_dir, f"*{vid}*.{ext}"))
        else:
            subs += glob.glob(os.path.join(out_dir, f"*.{ext}"))
    if subs:
        chosen = None
        for lang in subs_langs:
            for sf in subs:
                if f".{lang}." in sf:
                    chosen = sf
                    break
            if chosen:
                break
        if not chosen:
            chosen = subs[0]
        sub_src = chosen
    elif _has_subtitle_stream(video_path):
        # 从内嵌字幕流抽取为临时 srt 再烧录
        tmp = os.path.join(out_dir, "_burn_sub.srt")
        try:
            subprocess.run(
                [FFMPEG, "-y", "-i", video_path, "-map", "0:s:0", "-c:s", "srt", tmp],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            )
            sub_src = tmp
        except Exception:
            sub_src = None
    if not sub_src or not os.path.exists(sub_src):
        return False

    # 3) 复制到简单临时名（避免 Windows 路径里的 : \ 在 subtitles 滤镜里转义出错）
    tmp_sub = os.path.join(out_dir, "_burn_sub" + os.path.splitext(sub_src)[1])
    try:
        shutil.copy(sub_src, tmp_sub)
    except OSError:
        tmp_sub = sub_src
    esc = os.path.abspath(tmp_sub).replace("\\", "\\\\").replace(":", "\\:")
    base = os.path.splitext(video_path)[0]
    out_path = base + ".hardsub.mkv"
    cmd = [
        FFMPEG, "-y", "-i", video_path,
        "-vf", f"subtitles='{esc}'",
        "-c:a", "copy", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.replace(out_path, video_path)
        # 清理所有独立字幕残片 + 临时文件
        for sf in subs:
            try:
                os.remove(sf)
            except OSError:
                pass
        for f in (sub_src, tmp_sub):
            if os.path.abspath(f) != os.path.abspath(video_path):
                try:
                    os.remove(f)
                except OSError:
                    pass
        log_queue.put(("log", f"[{idx}] 已烧录字幕进视频（硬字幕）：{os.path.basename(video_path)}"))
        return True
    except Exception as e:
        log_queue.put(("log", f"[{idx}] 字幕烧录失败（保留原视频）：{e}"))
        for f in (out_path, tmp_sub):
            try:
                os.remove(f)
            except OSError:
                pass
        return False


# ----------------------------------------------------------------------------
# 主界面
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube 批量下载器")
        self.root.geometry("780x680")
        self.root.minsize(700, 600)

        self.abort = threading.Event()
        self.executor = None
        self.running = False
        self.total = 0
        self.done = 0
        self.task_url = {}     # idx -> url
        self.task_stats = {}   # idx -> info_dict

        self._build_widgets()
        self._poll()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI ----
    def _build_widgets(self):
        pad = {"padx": 8, "pady": 4}

        # 链接区
        f_url = ttk.LabelFrame(self.root, text="视频链接（每行一个，支持播放列表）", padding=6)
        f_url.pack(fill="both", expand=False, **pad)
        self.url_text = scrolledtext.ScrolledText(f_url, height=8, wrap="word")
        self.url_text.pack(fill="both", expand=True)
        ttk.Button(f_url, text="从 urls.txt 导入", command=self._import).pack(
            side="right", pady=4
        )

        # 选项区
        f_opt = ttk.LabelFrame(self.root, text="下载选项", padding=6)
        f_opt.pack(fill="x", **pad)

        # 输出目录
        ttk.Label(f_opt, text="保存目录:").grid(row=0, column=0, sticky="w")
        self.out_var = tk.StringVar(value=DEFAULT_OUT)
        ttk.Entry(f_opt, textvariable=self.out_var, width=50).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(f_opt, text="浏览", command=self._browse).grid(row=0, column=2)

        # 画质 + 解析按钮（默认 720p，可选 1080p；也可点解析画质拉真实清晰度）
        ttk.Label(f_opt, text="画质:").grid(row=1, column=0, sticky="w")
        self.quality_var = tk.StringVar(value="720p")
        self.quality_combo = ttk.Combobox(
            f_opt, textvariable=self.quality_var,
            values=["720p", "1080p", "最佳画质 (best)", "仅音频 (mp3)"],
            state="readonly", width=22
        )
        self.quality_combo.grid(row=1, column=1, sticky="w", padx=4)
        self.parse_btn = ttk.Button(
            f_opt, text="解析画质", command=self._parse_formats
        )
        self.parse_btn.grid(row=1, column=2, sticky="w", padx=4)
        ttk.Label(f_opt, text="可先解析再选真实清晰度", foreground="#888").grid(
            row=1, column=3, sticky="w"
        )

        # 仅音频
        self.audio_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            f_opt, text="仅下载音频(mp3)", variable=self.audio_var
        ).grid(row=2, column=0, columnspan=4, sticky="w")

        # 字幕（默认中文，烧录进画面，任何播放器打开即显示、关不掉）
        ttk.Label(f_opt, text="字幕:").grid(row=3, column=0, sticky="w")
        self.hardsub_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            f_opt, text="把字幕烧录进视频（硬字幕，默认中文，关不掉）", variable=self.hardsub_var
        ).grid(row=3, column=1, columnspan=3, sticky="w")

        # 代理
        ttk.Label(f_opt, text="代理地址(可选):").grid(row=4, column=0, sticky="w")
        self.proxy_var = tk.StringVar(value="")
        ttk.Entry(f_opt, textvariable=self.proxy_var, width=50).grid(
            row=4, column=1, columnspan=2, sticky="ew", padx=4
        )
        ttk.Label(f_opt, text="如 http://127.0.0.1:7890", foreground="#888").grid(
            row=5, column=1, columnspan=2, sticky="w"
        )

        # 并发策略（按视频数量自动优化，不暴露手动控件）
        self.auto_label = ttk.Label(
            f_opt, text="并发由程序按视频数量自动优化", foreground="#888"
        )
        self.auto_label.grid(row=6, column=0, columnspan=4, sticky="w")

        f_opt.columnconfigure(1, weight=1)

        # 按钮区
        f_btn = ttk.Frame(self.root, padding=6)
        f_btn.pack(fill="x")
        self.start_btn = ttk.Button(f_btn, text="开始下载", command=self._start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(
            f_btn, text="停止", command=self._stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=4)
        self.open_btn = ttk.Button(
            f_btn, text="打开下载文件夹", command=self._open_dir
        )
        self.open_btn.pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(f_btn, textvariable=self.status_var).pack(side="right", padx=4)

        # 整体进度条
        f_prog = ttk.Frame(self.root, padding=(8, 2))
        f_prog.pack(fill="x")
        self.progress = ttk.Progressbar(f_prog, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.prog_label = ttk.Label(f_prog, text="0%", width=8)
        self.prog_label.pack(side="right")

        # 进度列表
        f_list = ttk.LabelFrame(self.root, text="任务进度", padding=6)
        f_list.pack(fill="both", expand=True, **pad)
        self.list_box = tk.Listbox(f_list)
        self.list_box.pack(fill="both", expand=True)

        # 日志
        f_log = ttk.LabelFrame(self.root, text="日志", padding=6)
        f_log.pack(fill="both", expand=True, **pad)
        self.log_box = scrolledtext.ScrolledText(
            f_log, height=8, wrap="word", state="disabled"
        )
        self.log_box.pack(fill="both", expand=True)

    # ---- 操作 ----
    def _import(self):
        path = filedialog.askopenfilename(
            title="选择 urls.txt", filetypes=[("Text", "*.txt"), ("All", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.url_text.delete("1.0", "end")
                self.url_text.insert("1.0", f.read())
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def _browse(self):
        d = filedialog.askdirectory(title="选择保存目录")
        if d:
            self.out_var.set(d)

    def _open_dir(self):
        """打开当前下载目录（开始下载前用默认目录，并开始前确保目录存在）。"""
        d = getattr(self, "out_dir", None) or self.out_var.get().strip() or DEFAULT_OUT
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        try:
            os.startfile(d)
        except AttributeError:
            # 非 Windows 环境的兜底（开发期用）
            import subprocess
            subprocess.run(["explorer", d], shell=True)
        except Exception as e:
            messagebox.showerror("无法打开文件夹", str(e))

    def _get_urls(self):
        raw = self.url_text.get("1.0", "end").strip().splitlines()
        urls = [u.strip() for u in raw if u.strip()]
        return urls

    # ---- 解析画质 ----
    def _parse_formats(self):
        if self.running:
            messagebox.showinfo("提示", "下载进行中，请先停止再解析画质")
            return
        urls = self._get_urls()
        if not urls:
            messagebox.showwarning("提示", "请先粘贴至少一个视频链接，再解析画质")
            return
        proxy = self.proxy_var.get().strip() or None
        self.parse_btn.config(state="disabled")
        self.start_btn.config(state="disabled")
        self.status_var.set("解析画质中 1/%d..." % len(urls))
        threading.Thread(
            target=self._parse_worker, args=(urls, proxy), daemon=True
        ).start()

    def _parse_worker(self, urls, proxy):
        all_sets = []
        n = len(urls)
        for i, url in enumerate(urls, 1):
            # 切回主线程更新进度文字（after 是线程安全的）
            self.root.after(
                0, lambda i=i: self.status_var.set(f"解析画质中 {i}/{n}...")
            )
            hs = get_video_heights(url, proxy)
            if hs:
                all_sets.append(hs)
        if all_sets:
            common = set.intersection(*all_sets)
            if not common:
                # 没有所有视频都有的档位时，退化为并集（仍有可选清晰度）
                common = set.union(*all_sets)
        else:
            common = set()
        self.root.after(0, self._on_parsed, common, n)

    def _on_parsed(self, common, n):
        options = ["720p", "1080p", "最佳画质 (best)"]
        for h in sorted(common):
            options.append(f"{h}p")
        options.append("仅音频 (mp3)")
        self.quality_combo["values"] = options
        if self.quality_var.get() not in options:
            self.quality_var.set("720p")
        self.parse_btn.config(state="normal")
        self.start_btn.config(state="normal")
        if len(options) > 3:
            self.status_var.set(
                f"画质已解析：可选 {', '.join(options[2:-1])}（共 {n} 个视频）"
            )
        else:
            self.status_var.set("画质已解析：仅标准/最佳/音频可选")

    def _auto_concurrency(self, n):
        """根据视频数量自动决定并发参数（不让用户手动设置）。

        视频越少，单视频并发片段越猛；视频越多，整体线程数收敛，
        避免把连接打满触发 YouTube 限流。
        """
        if n <= 2:
            parallel, frag = max(1, n), 32
        elif n <= 6:
            parallel, frag = 4, 24
        else:
            parallel, frag = 5, 16
        return parallel, frag, 10  # 分片固定 10MB，减少请求数、提速

    def _start(self):
        if self.running:
            return
        urls = self._get_urls()
        if not urls:
            messagebox.showwarning("提示", "请先粘贴至少一个视频链接")
            return
        out_dir = self.out_var.get().strip()
        if not out_dir:
            out_dir = DEFAULT_OUT
        os.makedirs(out_dir, exist_ok=True)

        # 字幕语言固定中文优先，无需用户设置
        subs = DEFAULT_SUBTITLES
        only_audio = self.audio_var.get()
        # 硬字幕（烧录）默认开启；勾选且非纯音频时生效
        hardsub = self.hardsub_var.get() and not only_audio
        embed = hardsub  # embed 控制是否下载字幕到磁盘，供烧录/兜底内嵌使用
        proxy = self.proxy_var.get().strip() or None
        # 并发参数按视频数量自动设置，不暴露给用户手动调
        parallel, frag, chunk = self._auto_concurrency(len(urls))
        self.auto_label.config(
            text=f"已自动优化：同时 {parallel} 线程 · 每视频 {frag} 片段 · 分片 {chunk}MB"
        )

        base_opts = build_opts(
            out_dir,
            self.quality_var.get(),
            frag,
            subs,
            embed,
            only_audio,
            proxy,
            chunk,
            hardsub,
        )

        self.running = True
        self.stopping = False
        self.out_dir = out_dir
        self.abort.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.total = len(urls)
        self.done = 0
        self.task_url = {i: u for i, u in enumerate(urls, 1)}
        self.task_stats = {}
        self.list_box.delete(0, "end")
        for i, u in enumerate(urls, 1):
            self.task_stats[i] = {"state": "等待中", "pct": None}
            self.list_box.insert("end", self._fmt_line(i, self.task_stats[i], u))

        parallel = max(1, min(parallel, 16))
        import concurrent.futures as cf

        def status_cb(idx, info):
            log_queue.put(("status", (idx, info)))

        def worker():
            with cf.ThreadPoolExecutor(max_workers=parallel) as ex:
                futures = [
                    ex.submit(
                        download_one, u, base_opts, out_dir, subs, hardsub, i, self.total,
                        self.abort, status_cb
                    )
                    for i, u in enumerate(urls, 1)
                ]
                for _ in cf.as_completed(futures):
                    pass
            self.root.after(0, self._finish)

        threading.Thread(target=worker, daemon=True).start()
        self.status_var.set(f"下载中 0/{self.total}")
        self._update_overall()

    def _finish(self):
        self.running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        if self.stopping:
            self._cleanup_partial()
            self.status_var.set(f"已停止（已取消 {self.done}/{self.total}，未完成文件已清理）")
            log_queue.put(("log", "==== 已停止，临时文件已清理 ===="))
        else:
            self.status_var.set(f"完成 {self.done}/{self.total}")
            log_queue.put(("log", "==== 全部任务结束 ===="))
        self._update_overall()

    def _stop(self):
        if not self.running:
            return
        # 真正取消：置位 abort 后，进行中的下载会在下一次进度回调立刻抛 DownloadCancelled 中止，
        # 尚未开始的视频在 download_one 入口直接跳过；几秒内全部结束，开始按钮随即恢复可用。
        self.stopping = True
        self.abort.set()
        self.status_var.set("正在停止…（进行中的下载会立即取消，临时文件随后清理）")

    def _cleanup_partial(self):
        """停止后清理未完成的临时文件（.part / .ytdl），避免残留半截视频。"""
        d = getattr(self, "out_dir", None)
        if not d or not os.path.isdir(d):
            return
        for fn in os.listdir(d):
            if fn.endswith(".part") or fn.endswith(".ytdl"):
                try:
                    os.remove(os.path.join(d, fn))
                except OSError:
                    pass

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno("确认退出", "下载仍在进行，确定退出？"):
                return
        self.abort.set()
        self.root.destroy()

    # ---- 进度渲染 ----
    def _fmt_line(self, idx, info, url=""):
        state = info.get("state", "")
        pct = info.get("pct")
        sp = fmt_speed(info.get("speed") or 0)
        eta = fmt_eta(info.get("eta"))
        parts = [f"[{idx}/{self.total}]", state]
        if pct is not None:
            parts.append(f"{pct:.1f}%")
        d = info.get("downloaded") or 0
        t = info.get("total") or 0
        if t > 0:
            parts.append(f"{fmt_bytes(d)}/{fmt_bytes(t)}")
        if state in ("下载中",):
            parts.append(sp)
            parts.append("剩余" + eta)
        line = "  ".join(parts)
        if url:
            line += "  " + url
        return line

    def _set_status(self, idx, info):
        self.task_stats[idx] = info
        url = self.task_url.get(idx, "")
        pos = idx - 1
        if 0 <= pos < self.list_box.size():
            self.list_box.delete(pos)
            self.list_box.insert(pos, self._fmt_line(idx, info, url))
        if info.get("state") in ("完成", "失败", "已取消"):
            self.done += 1
        self._update_overall()

    def _update_overall(self):
        # 基于已下载字节占比算整体进度（更贴近真实），无字节信息时退回完成数占比
        sum_d = 0.0
        sum_t = 0.0
        speed = 0.0
        for info in self.task_stats.values():
            t = info.get("total") or 0
            d = info.get("downloaded") or 0
            if t > 0:
                sum_d += d
                sum_t += t
            speed += (info.get("speed") or 0)
        if sum_t > 0:
            pct = sum_d / sum_t * 100
        elif self.total:
            pct = self.done / self.total * 100
        else:
            pct = 0
        self.progress["value"] = pct
        self.prog_label.config(text=f"{pct:.0f}%")
        if self.running and sum_t > 0 and speed > 0:
            eta = (sum_t - sum_d) / speed
            self.status_var.set(
                f"下载中 {self.done}/{self.total}  ·  {fmt_bytes(sum_d)}/{fmt_bytes(sum_t)}"
                f"  ·  {fmt_speed(speed)}  ·  预计剩余 {fmt_eta(eta)}"
            )
        elif self.running:
            self.status_var.set(f"下载中 {self.done}/{self.total}")

    # ---- 队列轮询 ----
    def _poll(self):
        try:
            while True:
                kind, payload = log_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    idx, info = payload
                    self._set_status(idx, info)
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _append_log(self, msg):
        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")


def main():
    global FFMPEG
    if not os.path.exists(FFMPEG):
        # 开发模式下若找不到，尝试 PATH 中的 ffmpeg
        import shutil
        p = shutil.which("ffmpeg")
        if p:
            FFMPEG = p
    root = tk.Tk()
    try:
        # 窗口标题栏图标（桌面 exe 图标由 PyInstaller --icon 负责）
        root.iconbitmap(resource_path("app.ico"))
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
