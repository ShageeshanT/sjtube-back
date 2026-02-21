# SJ TUBE - YouTube Video / Playlist / Audio Downloader (yt-dlp)
# Features:
#   - Saved settings (AppData)
#   - Single-line progress display
#   - Subtitles: download (normal/auto), language selection, convert, optional embed
#   - Thumbnails: download, optional embed
#   - Auto-update yt-dlp (pip)
#
# Requirements:
#   py -m pip install -U "yt-dlp[default]"
# Recommended:
#   deno installed (deno --version)
#   ffmpeg installed (ffmpeg -version)

from __future__ import annotations

import json
import os
import sys
import time
import shutil
import subprocess
import traceback
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import yt_dlp


APP_NAME = "SJ TUBE"


# ----------------------------
# Settings
# ----------------------------
def _settings_dir() -> Path:
    base = os.getenv("APPDATA")
    if base:
        return Path(base) / "SJ_TUBE"
    return Path.home() / ".sj_tube"


def _settings_path() -> Path:
    return _settings_dir() / "settings.json"


@dataclass
class SubtitleSettings:
    enabled: bool = False
    auto: bool = False
    langs: list[str] = None  # e.g. ["en"]
    convert_to: str = "srt"  # "srt" | "vtt" | "best"
    embed: bool = False      # embed subs into final file (ffmpeg required)

    def normalized(self) -> "SubtitleSettings":
        if self.langs is None:
            self.langs = ["en"]
        self.langs = [x.strip() for x in self.langs if x.strip()]
        if not self.langs:
            self.langs = ["en"]
        if self.convert_to not in ("srt", "vtt", "best"):
            self.convert_to = "srt"
        return self


@dataclass
class ThumbnailSettings:
    download: bool = False
    embed: bool = False      # embed into output (ffmpeg/containers required)


@dataclass
class AppSettings:
    default_save_dir: str = str(Path.cwd())
    default_quality: str = "best"      # "best" | "720" | "1080"
    default_audio_format: str = "m4a"  # "m4a" | "mp3"
    use_deno: bool = True
    auto_update_ytdlp: bool = False
    embed_metadata: bool = True
    subtitles: SubtitleSettings = None
    thumbnails: ThumbnailSettings = None

    def normalized(self) -> "AppSettings":
        if self.default_quality not in ("best", "720", "1080"):
            self.default_quality = "best"
        if self.default_audio_format not in ("m4a", "mp3"):
            self.default_audio_format = "m4a"
        if self.subtitles is None:
            self.subtitles = SubtitleSettings()
        if self.thumbnails is None:
            self.thumbnails = ThumbnailSettings()
        self.subtitles = self.subtitles.normalized()
        return self


def load_settings() -> AppSettings:
    path = _settings_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            s = AppSettings(
                default_save_dir=data.get("default_save_dir", str(Path.cwd())),
                default_quality=data.get("default_quality", "best"),
                default_audio_format=data.get("default_audio_format", "m4a"),
                use_deno=bool(data.get("use_deno", True)),
                auto_update_ytdlp=bool(data.get("auto_update_ytdlp", False)),
                embed_metadata=bool(data.get("embed_metadata", True)),
                subtitles=SubtitleSettings(**data.get("subtitles", {})),
                thumbnails=ThumbnailSettings(**data.get("thumbnails", {})),
            )
            return s.normalized()
    except Exception:
        # If settings file is corrupted, fall back to defaults
        pass
    return AppSettings(subtitles=SubtitleSettings(), thumbnails=ThumbnailSettings()).normalized()


def save_settings(s: AppSettings) -> None:
    s = s.normalized()
    d = _settings_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = _settings_path()

    payload = asdict(s)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ----------------------------
# Console UI helpers
# ----------------------------
def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def print_banner(settings: AppSettings | None = None) -> None:
    version = getattr(getattr(yt_dlp, "version", None), "__version__", "unknown")
    print("+--------------------------------------------------+")
    print(f"| {APP_NAME:<48} |")
    print(f"| yt-dlp: {version:<41} |")
    print("+--------------------------------------------------+")
    if settings:
        print(f"Settings file: {_settings_path()}")
    print("")


def pause(msg: str = "Press Enter to continue...") -> None:
    input(msg)


def ask(prompt: str, default: str | None = None) -> str:
    if default is None:
        return input(prompt).strip()
    v = input(f"{prompt} [{default}]: ").strip()
    return v if v else default


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} ({suffix}): ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("Please enter y or n.")


def ask_int(prompt: str, default: int, minv: int | None = None, maxv: int | None = None) -> int:
    while True:
        s = ask(prompt, str(default))
        try:
            v = int(s)
            if minv is not None and v < minv:
                print(f"Invalid input: must be >= {minv}")
                continue
            if maxv is not None and v > maxv:
                print(f"Invalid input: must be <= {maxv}")
                continue
            return v
        except ValueError:
            print("Invalid input: please enter a number.")


def ensure_dir(path_str: str) -> str:
    p = Path(path_str).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return str(p.resolve())


def looks_like_youtube_url(url: str) -> bool:
    u = url.lower()
    return ("youtube.com" in u) or ("youtu.be" in u)


def is_playlist_url(url: str) -> bool:
    return "list=" in url.lower()


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


# ----------------------------
# Update yt-dlp (pip)
# ----------------------------
def update_ytdlp_now() -> bool:
    in_venv = (sys.prefix != getattr(sys, "base_prefix", sys.prefix))
    cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"]
    if not in_venv:
        cmd.append("--user")

    print("Updating yt-dlp via pip...")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()

        # Show last few lines only
        def tail(text: str, n: int = 10) -> str:
            lines = [ln for ln in text.splitlines() if ln.strip()]
            return "\n".join(lines[-n:])

        if out:
            print(tail(out))
        if err:
            print(tail(err))

        if p.returncode == 0:
            print("Update completed.")
            return True

        print("Update failed (pip returned non-zero exit code).")
        return False
    except Exception as e:
        print(f"Update failed: {e}")
        return False


# ----------------------------
# Progress (single line, throttled)
# ----------------------------
def _fmt_bytes(n: int | None) -> str:
    if n is None or n < 0:
        return "?"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    v = float(n)
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    if i == 0:
        return f"{int(v)} {units[i]}"
    return f"{v:.2f} {units[i]}"


class ProgressPrinter:
    def __init__(self) -> None:
        self._last_print = 0.0
        self._last_key = None
        self._line_open = False

    def _write_line(self, text: str) -> None:
        # Overwrite current line
        sys.stdout.write("\r" + text[:200].ljust(200))
        sys.stdout.flush()
        self._line_open = True

    def _newline(self) -> None:
        if self._line_open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._line_open = False

    def update(self, d: dict) -> None:
        now = time.time()
        if now - self._last_print < 0.2:
            return

        info = d.get("info_dict") or {}
        filename = os.path.basename(d.get("filename") or info.get("_filename") or "output")
        downloaded = d.get("downloaded_bytes")
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        speed = d.get("speed")
        eta = d.get("eta")

        pct = ""
        if isinstance(downloaded, int) and isinstance(total, int) and total > 0:
            pct = f"{(downloaded / total) * 100:5.1f}%"
        else:
            pct = "  ?.?%"

        spd = _fmt_bytes(int(speed)) + "/s" if isinstance(speed, (int, float)) and speed else "?/s"
        etas = f"{int(eta)//60:02d}:{int(eta)%60:02d}" if isinstance(eta, (int, float)) else "??:??"

        # Playlist context if available
        pl_idx = info.get("playlist_index")
        pl_cnt = info.get("n_entries") or info.get("playlist_count")
        prefix = ""
        if pl_idx and pl_cnt:
            prefix = f"[{pl_idx}/{pl_cnt}] "

        line = f"{prefix}{filename}  {pct}  {_fmt_bytes(downloaded)}/{_fmt_bytes(total)}  {spd}  ETA {etas}"
        self._write_line(line)
        self._last_print = now
        self._last_key = filename

    def finished(self, d: dict) -> None:
        info = d.get("info_dict") or {}
        filename = os.path.basename(d.get("filename") or info.get("_filename") or "output")
        self._write_line(f"{filename}  done")
        self._newline()

    def clear_line(self) -> None:
        self._write_line("")
        self._newline()


# ----------------------------
# yt-dlp config and download
# ----------------------------
class SimpleLogger:
    def debug(self, msg):  # keep quiet
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        # Avoid flooding warnings; show only important ones
        if msg and "deprecated" in msg.lower():
            print(f"WARNING: {msg}")

    def error(self, msg):
        if msg:
            print(f"ERROR: {msg}")


@dataclass
class DownloadRequest:
    url: str
    save_dir: str
    kind: str                 # "video" | "playlist"
    mode: str                 # "video" | "audio"
    quality: str              # "best" | "720" | "1080" (video)
    audio_format: str         # "m4a" | "mp3" (audio)
    use_deno: bool
    embed_metadata: bool
    subtitles: SubtitleSettings
    thumbnails: ThumbnailSettings


def build_ydl_opts(req: DownloadRequest, progress: ProgressPrinter) -> dict:
    # Output template
    if req.kind == "playlist":
        outtmpl = os.path.join(req.save_dir, "%(playlist_index)03d - %(title)s.%(ext)s")
        noplaylist = False
    else:
        outtmpl = os.path.join(req.save_dir, "%(title)s.%(ext)s")
        noplaylist = True

    # Format selection
    if req.mode == "audio":
        ydl_format = "bestaudio/best"
    else:
        if req.quality == "720":
            ydl_format = "bestvideo[height<=720]+bestaudio/best"
        elif req.quality == "1080":
            ydl_format = "bestvideo[height<=1080]+bestaudio/best"
        else:
            ydl_format = "bestvideo+bestaudio/best"

    # JS runtime (Deno): dict format
    js_runtimes = None
    if req.use_deno:
        deno_path = shutil.which("deno")
        js_runtimes = {"deno": {"path": deno_path}} if deno_path else {"deno": {}}

    # Postprocessors (order matters for some cases like embedding thumbnails into extracted audio)
    postprocessors: list[dict[str, Any]] = []

    # Audio extraction (m4a/mp3) uses ffmpeg
    if req.mode == "audio":
        if req.audio_format == "mp3":
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            })
        else:
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            })

    # Embed subtitles (after download/conversion)
    if req.subtitles.enabled and req.subtitles.embed:
        postprocessors.append({
            "key": "FFmpegEmbedSubtitle",
            "already_have_subtitle": False,
        })

    # Embed thumbnail (requires thumbnail on disk first)
    if req.thumbnails.download or req.thumbnails.embed:
        # writethumbnail must be enabled in main options; embed handled here
        if req.thumbnails.embed:
            postprocessors.append({"key": "EmbedThumbnail"})

    # Embed metadata
    if req.embed_metadata:
        postprocessors.append({
            "key": "FFmpegMetadata",
            "add_chapters": True,
        })

    ydl_opts: dict[str, Any] = {
        "outtmpl": outtmpl,
        "restrictfilenames": True,
        "format": ydl_format,
        "noplaylist": noplaylist,
        "ignoreerrors": True,
        "continuedl": True,
        "quiet": True,
        "no_warnings": False,
        "logger": SimpleLogger(),
        "progress_hooks": [lambda d: _progress_router(d, progress)],
        "merge_output_format": "mp4",
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "sleep_interval": 2,
        "max_sleep_interval": 6,
        "postprocessors": postprocessors,
    }

    if js_runtimes:
        ydl_opts["js_runtimes"] = js_runtimes

    # Subtitles options
    if req.subtitles.enabled:
        ydl_opts["writesubtitles"] = True
        ydl_opts["writeautomaticsub"] = bool(req.subtitles.auto)
        ydl_opts["subtitleslangs"] = req.subtitles.langs
        # subtitlesformat chooses what to download; convertsubtitles converts after download (ffmpeg)
        if req.subtitles.convert_to in ("srt", "vtt"):
            ydl_opts["subtitlesformat"] = f"{req.subtitles.convert_to}/best"
            ydl_opts["convertsubtitles"] = req.subtitles.convert_to
        else:
            ydl_opts["subtitlesformat"] = "best"

    # Thumbnails option (download to disk)
    if req.thumbnails.download or req.thumbnails.embed:
        ydl_opts["writethumbnail"] = True

    return ydl_opts


def _progress_router(d: dict, progress: ProgressPrinter) -> None:
    status = d.get("status")
    if status == "downloading":
        progress.update(d)
    elif status == "finished":
        progress.finished(d)


def check_and_warn(req: DownloadRequest) -> bool:
    ff = has_ffmpeg()

    needs_ffmpeg = False
    if req.mode == "audio":
        needs_ffmpeg = True
    if req.mode == "video":
        # bestvideo+bestaudio generally needs ffmpeg to merge
        needs_ffmpeg = True
    if req.subtitles.enabled and (req.subtitles.embed or req.subtitles.convert_to in ("srt", "vtt")):
        needs_ffmpeg = True
    if req.thumbnails.embed:
        needs_ffmpeg = True
    if req.embed_metadata:
        needs_ffmpeg = True

    if needs_ffmpeg and not ff:
        print("WARNING: ffmpeg was not found on PATH.")
        print("Many operations may fail (merging audio+video, audio extraction, subtitle conversion/embedding, thumbnail embedding, metadata).")
        print("Install ffmpeg and ensure `ffmpeg -version` works, then retry.")
        print("")
        # If user chose audio-only, it will definitely fail without ffmpeg
        if req.mode == "audio":
            return False

    if req.use_deno and not shutil.which("deno"):
        print("WARNING: Deno is enabled but not found on PATH. You may see YouTube extraction issues.")
        print("")
    return True


def run_download(req: DownloadRequest) -> None:
    progress = ProgressPrinter()

    print("Download summary")
    print(f"  URL      : {req.url}")
    print(f"  Save dir : {req.save_dir}")
    print(f"  Type     : {req.kind}")
    print(f"  Mode     : {req.mode}")
    if req.mode == "video":
        print(f"  Quality  : {req.quality}")
    else:
        print(f"  Audio    : {req.audio_format}")
    print(f"  Deno JS  : {'on' if req.use_deno else 'off'}")
    print(f"  Subs     : {'on' if req.subtitles.enabled else 'off'}")
    if req.subtitles.enabled:
        print(f"    auto   : {'on' if req.subtitles.auto else 'off'}")
        print(f"    langs  : {', '.join(req.subtitles.langs)}")
        print(f"    conv   : {req.subtitles.convert_to}")
        print(f"    embed  : {'on' if req.subtitles.embed else 'off'}")
    print(f"  Thumb    : {'on' if (req.thumbnails.download or req.thumbnails.embed) else 'off'}")
    if req.thumbnails.download or req.thumbnails.embed:
        print(f"    file   : {'on' if req.thumbnails.download else 'off'}")
        print(f"    embed  : {'on' if req.thumbnails.embed else 'off'}")
    print(f"  Metadata : {'on' if req.embed_metadata else 'off'}")
    print("")

    if not check_and_warn(req):
        return

    ydl_opts = build_ydl_opts(req, progress)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([req.url])
        progress.clear_line()
        print(f"Done. Saved to: {req.save_dir}")
        print("")
    except Exception:
        progress.clear_line()
        print("Download failed. Traceback:")
        print("")
        traceback.print_exc()


# ----------------------------
# Menus
# ----------------------------
def choose_quality(default: str) -> str:
    print("Select quality")
    print("  1) Best available")
    print("  2) Up to 720p")
    print("  3) Up to 1080p")
    mapping = {1: "best", 2: "720", 3: "1080"}
    default_choice = {"best": 1, "720": 2, "1080": 3}.get(default, 1)
    choice = ask_int("Choose", default_choice, 1, 3)
    return mapping[choice]


def choose_audio_format(default: str) -> str:
    print("Select audio format")
    print("  1) m4a")
    print("  2) mp3 (requires ffmpeg)")
    mapping = {1: "m4a", 2: "mp3"}
    default_choice = 1 if default == "m4a" else 2
    choice = ask_int("Choose", default_choice, 1, 2)
    return mapping[choice]


def subtitles_menu(s: AppSettings) -> None:
    while True:
        clear_screen()
        print_banner(s)
        subs = s.subtitles.normalized()

        print("Subtitles settings")
        print(f"  1) Enabled            : {'on' if subs.enabled else 'off'}")
        print(f"  2) Auto-generated subs: {'on' if subs.auto else 'off'}")
        print(f"  3) Languages          : {', '.join(subs.langs)}")
        print(f"  4) Convert to         : {subs.convert_to}   (srt/vtt/best)")
        print(f"  5) Embed into file    : {'on' if subs.embed else 'off'}")
        print("  6) Back")
        print("")

        c = ask_int("Choose", 6, 1, 6)
        if c == 6:
            s.subtitles = subs
            save_settings(s)
            return
        if c == 1:
            subs.enabled = not subs.enabled
        elif c == 2:
            subs.auto = not subs.auto
        elif c == 3:
            raw = ask("Enter languages (comma-separated)", ",".join(subs.langs))
            subs.langs = [x.strip() for x in raw.split(",") if x.strip()]
        elif c == 4:
            raw = ask("Convert to (srt/vtt/best)", subs.convert_to).lower().strip()
            subs.convert_to = raw
        elif c == 5:
            subs.embed = not subs.embed

        s.subtitles = subs.normalized()
        save_settings(s)


def thumbnails_menu(s: AppSettings) -> None:
    while True:
        clear_screen()
        print_banner(s)
        th = s.thumbnails

        print("Thumbnail settings")
        print(f"  1) Download thumbnail file : {'on' if th.download else 'off'}")
        print(f"  2) Embed thumbnail         : {'on' if th.embed else 'off'}")
        print("  3) Back")
        print("")

        c = ask_int("Choose", 3, 1, 3)
        if c == 3:
            save_settings(s)
            return
        if c == 1:
            th.download = not th.download
        elif c == 2:
            th.embed = not th.embed

        s.thumbnails = th
        save_settings(s)


def settings_menu(s: AppSettings) -> None:
    while True:
        clear_screen()
        print_banner(s)

        print("Settings")
        print(f"  1) Default save folder      : {s.default_save_dir}")
        print(f"  2) Default quality          : {s.default_quality}")
        print(f"  3) Default audio format     : {s.default_audio_format}")
        print(f"  4) Use Deno JS runtime      : {'on' if s.use_deno else 'off'}")
        print(f"  5) Auto-update yt-dlp start : {'on' if s.auto_update_ytdlp else 'off'}")
        print(f"  6) Embed metadata           : {'on' if s.embed_metadata else 'off'}")
        print(f"  7) Subtitles settings       : {'on' if s.subtitles.enabled else 'off'}")
        print(f"  8) Thumbnail settings       : {'on' if (s.thumbnails.download or s.thumbnails.embed) else 'off'}")
        print("  9) Reset settings to default")
        print(" 10) Back")
        print("")

        c = ask_int("Choose", 10, 1, 10)
        if c == 10:
            save_settings(s)
            return
        if c == 1:
            s.default_save_dir = ensure_dir(ask("Default save folder", s.default_save_dir))
        elif c == 2:
            s.default_quality = choose_quality(s.default_quality)
        elif c == 3:
            s.default_audio_format = choose_audio_format(s.default_audio_format)
        elif c == 4:
            s.use_deno = not s.use_deno
        elif c == 5:
            s.auto_update_ytdlp = not s.auto_update_ytdlp
        elif c == 6:
            s.embed_metadata = not s.embed_metadata
        elif c == 7:
            subtitles_menu(s)
        elif c == 8:
            thumbnails_menu(s)
        elif c == 9:
            s = AppSettings(subtitles=SubtitleSettings(), thumbnails=ThumbnailSettings()).normalized()
            save_settings(s)

        s = s.normalized()
        save_settings(s)


def make_request_from_user(choice: int, s: AppSettings) -> DownloadRequest | None:
    url = ask("Enter YouTube URL: ")
    if not url:
        print("URL cannot be empty.")
        pause()
        return None

    if not looks_like_youtube_url(url):
        print("Note: this does not look like a YouTube URL, but yt-dlp will try.")
        print("")

    kind = "playlist" if (choice == 2 or (choice == 3 and is_playlist_url(url))) else "video"

    save_dir = ensure_dir(ask("Save folder", s.default_save_dir))

    # Quick override of extras
    use_saved_extras = ask_yes_no("Use saved extras (subs/thumbs/metadata)", True)
    subs = s.subtitles.normalized()
    thumbs = s.thumbnails
    embed_metadata = s.embed_metadata
    if not use_saved_extras:
        subs.enabled = ask_yes_no("Download subtitles", False)
        if subs.enabled:
            subs.auto = ask_yes_no("Use auto-generated subtitles if needed", subs.auto)
            raw_lang = ask("Subtitle languages (comma-separated)", ",".join(subs.langs))
            subs.langs = [x.strip() for x in raw_lang.split(",") if x.strip()]
            subs.convert_to = ask("Convert subtitles to (srt/vtt/best)", subs.convert_to).lower().strip()
            subs.embed = ask_yes_no("Embed subtitles into file (ffmpeg)", subs.embed)
        thumbs.download = ask_yes_no("Download thumbnail file", thumbs.download)
        thumbs.embed = ask_yes_no("Embed thumbnail (ffmpeg)", thumbs.embed)
        embed_metadata = ask_yes_no("Embed metadata/chapter info (ffmpeg)", embed_metadata)

    if choice == 1:
        quality = choose_quality(s.default_quality)
        return DownloadRequest(
            url=url,
            save_dir=save_dir,
            kind="video",
            mode="video",
            quality=quality,
            audio_format=s.default_audio_format,
            use_deno=s.use_deno,
            embed_metadata=embed_metadata,
            subtitles=subs.normalized(),
            thumbnails=thumbs,
        )

    if choice == 2:
        quality = choose_quality(s.default_quality)
        return DownloadRequest(
            url=url,
            save_dir=save_dir,
            kind="playlist",
            mode="video",
            quality=quality,
            audio_format=s.default_audio_format,
            use_deno=s.use_deno,
            embed_metadata=embed_metadata,
            subtitles=subs.normalized(),
            thumbnails=thumbs,
        )

    if choice == 3:
        audio_format = choose_audio_format(s.default_audio_format)
        return DownloadRequest(
            url=url,
            save_dir=save_dir,
            kind=kind,
            mode="audio",
            quality="best",
            audio_format=audio_format,
            use_deno=s.use_deno,
            embed_metadata=embed_metadata,
            subtitles=subs.normalized(),
            thumbnails=thumbs,
        )

    return None


def main() -> None:
    s = load_settings()

    if s.auto_update_ytdlp:
        clear_screen()
        print_banner(s)
        update_ytdlp_now()
        pause()

    while True:
        clear_screen()
        print_banner(s)

        print("Menu")
        print("  1) Download single video")
        print("  2) Download playlist")
        print("  3) Download audio only (video or playlist)")
        print("  4) Settings")
        print("  5) Update yt-dlp now")
        print("  6) Exit")
        print("")

        choice = ask_int("Choose", 6, 1, 6)

        if choice == 6:
            return

        if choice == 4:
            settings_menu(s)
            s = load_settings()
            continue

        if choice == 5:
            clear_screen()
            print_banner(s)
            update_ytdlp_now()
            pause()
            s = load_settings()
            continue

        req = make_request_from_user(choice, s)
        if req is None:
            continue

        clear_screen()
        print_banner(s)
        run_download(req)
        pause()


if __name__ == "__main__":
    main()
