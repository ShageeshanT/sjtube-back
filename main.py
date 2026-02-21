"""
SJ Tube — FastAPI Backend
─────────────────────────
Wraps the existing youtube_downloader.py (sjtube.py) to provide
a REST API for the React frontend.

Files are downloaded temporarily, served to the browser, then
auto-deleted after 5 minutes to prevent disk usage.
"""

from __future__ import annotations

import os
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from models import (
    ValidateRequest,
    ValidateResponse,
    VideoInfo,
    DownloadStartRequest,
    DownloadStartResponse,
    TaskStatus,
    HistoryItem,
)

# Import helpers from the original sjtube script
from youtube_downloader import (
    DownloadRequest,
    SubtitleSettings,
    ThumbnailSettings,
    build_ydl_opts,
    is_playlist_url,
)


# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "./downloads")
AUTO_DELETE_SECONDS = int(os.getenv("AUTO_DELETE_SECONDS", "300"))  # 5 min default
Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="SJ Tube API",
    version="1.0.0",
    description="YouTube video/audio downloader API",
)

# CORS — configurable for deployment
_default_origins = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"
_cors_origins = os.getenv("CORS_ORIGINS", _default_origins).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Thread pool for background downloads
executor = ThreadPoolExecutor(max_workers=3)

# In-memory task tracker: task_id → TaskProgress
tasks: dict[str, dict[str, Any]] = {}
tasks_lock = threading.Lock()

# In-memory download history (persists across requests, resets on server restart)
download_history: list[dict[str, Any]] = []
history_lock = threading.Lock()


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _fmt_bytes(n: int | None) -> str:
    """Human-readable byte size."""
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


def _get_task(task_id: str) -> dict[str, Any]:
    with tasks_lock:
        return tasks.get(task_id, {})


def _set_task(task_id: str, data: dict[str, Any]) -> None:
    with tasks_lock:
        tasks[task_id] = data


def _add_to_history(filename: str, size: int) -> None:
    """Add a download record to in-memory history."""
    with history_lock:
        download_history.insert(0, {
            "filename": filename,
            "size": size,
            "size_human": _fmt_bytes(size),
            "modified": datetime.now(timezone.utc).isoformat(),
        })
        # Keep only last 100 entries
        if len(download_history) > 100:
            download_history.pop()


def _schedule_delete(filepath: Path, delay: int = AUTO_DELETE_SECONDS) -> None:
    """Delete a file after a delay (runs in background thread)."""
    def _delete():
        import time
        time.sleep(delay)
        try:
            if filepath.exists():
                filepath.unlink()
                print(f"[cleanup] Auto-deleted: {filepath.name}")
        except Exception as e:
            print(f"[cleanup] Failed to delete {filepath.name}: {e}")

    t = threading.Thread(target=_delete, daemon=True)
    t.start()


# ──────────────────────────────────────────────
# POST /api/validate — extract video metadata
# ──────────────────────────────────────────────
@app.post("/api/validate", response_model=ValidateResponse)
async def validate_url(req: ValidateRequest):
    url = req.url.strip()
    if not url:
        return ValidateResponse(valid=False, error="URL cannot be empty")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist" if is_playlist_url(url) else False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return ValidateResponse(valid=False, error="Could not extract video info")

        is_pl = info.get("_type") == "playlist" or "entries" in info
        playlist_count = None
        if is_pl:
            entries = info.get("entries")
            if entries:
                playlist_count = len(list(entries))

        # Duration formatting
        duration = info.get("duration")
        duration_string = None
        if duration:
            mins, secs = divmod(int(duration), 60)
            hours, mins = divmod(mins, 60)
            if hours:
                duration_string = f"{hours}:{mins:02d}:{secs:02d}"
            else:
                duration_string = f"{mins}:{secs:02d}"

        video_info = VideoInfo(
            title=info.get("title", "Unknown"),
            channel=info.get("uploader") or info.get("channel") or "Unknown",
            duration=duration,
            duration_string=duration_string,
            thumbnail=info.get("thumbnail"),
            view_count=info.get("view_count"),
            upload_date=info.get("upload_date"),
            is_playlist=is_pl,
            playlist_count=playlist_count,
        )
        return ValidateResponse(valid=True, info=video_info)

    except yt_dlp.utils.DownloadError as e:
        return ValidateResponse(valid=False, error=str(e))
    except Exception as e:
        return ValidateResponse(valid=False, error=f"Validation failed: {str(e)}")


# ──────────────────────────────────────────────
# Background download worker
# ──────────────────────────────────────────────
def _progress_hook(task_id: str, d: dict) -> None:
    """Called by yt-dlp during download to update task progress."""
    status = d.get("status")
    info = d.get("info_dict") or {}
    filename = os.path.basename(
        d.get("filename") or info.get("_filename") or "output"
    )

    if status == "downloading":
        downloaded = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        speed = d.get("speed")
        eta = d.get("eta")

        progress = 0.0
        if total > 0:
            progress = min((downloaded / total) * 100, 99.9)

        speed_str = None
        if isinstance(speed, (int, float)) and speed > 0:
            speed_str = _fmt_bytes(int(speed)) + "/s"

        eta_str = None
        if isinstance(eta, (int, float)):
            eta_str = f"{int(eta) // 60:02d}:{int(eta) % 60:02d}"

        _set_task(task_id, {
            "status": "downloading",
            "progress": round(progress, 1),
            "speed": speed_str,
            "eta": eta_str,
            "filename": filename,
            "error": None,
        })

    elif status == "finished":
        _set_task(task_id, {
            "status": "processing",
            "progress": 99.9,
            "speed": None,
            "eta": None,
            "filename": filename,
            "error": None,
        })


def _run_download_task(task_id: str, req: DownloadStartRequest) -> None:
    """Execute download in a background thread."""
    try:
        _set_task(task_id, {
            "status": "pending",
            "progress": 0.0,
            "speed": None,
            "eta": None,
            "filename": None,
            "error": None,
        })

        save_dir = str(Path(DOWNLOAD_DIR).resolve())
        is_pl = is_playlist_url(req.url)

        # Map quality string
        quality_map = {
            "best": "best",
            "1080": "1080",
            "1080p": "1080",
            "720": "720",
            "720p": "720",
            "480": "480",
            "480p": "480",
            "360": "360",
            "360p": "360",
            "270": "270",
            "270p": "270",
            "144": "144",
            "144p": "144",
        }
        quality = quality_map.get(req.quality, "best")

        # Build a DownloadRequest from the original script
        dl_req = DownloadRequest(
            url=req.url,
            save_dir=save_dir,
            kind="playlist" if is_pl else "video",
            mode=req.mode,
            quality=quality,
            audio_format=req.audio_format if req.mode == "audio" else "m4a",
            use_deno=True,
            embed_metadata=True,
            subtitles=SubtitleSettings(),
            thumbnails=ThumbnailSettings(),
        )

        # Build yt-dlp options using the original script's function
        from youtube_downloader import ProgressPrinter
        dummy_progress = ProgressPrinter()
        ydl_opts = build_ydl_opts(dl_req, dummy_progress)

        # Override progress hooks with our own task-aware hook
        ydl_opts["progress_hooks"] = [lambda d: _progress_hook(task_id, d)]

        # Handle lower qualities
        if quality in ("144", "270", "360", "480"):
            ydl_opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([req.url])

        # Find the most recently created file in the download directory
        download_path = Path(save_dir)
        files = sorted(download_path.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)

        latest_file = None
        file_size = 0
        if files:
            latest_file = files[0].name
            file_size = files[0].stat().st_size
            # Add to history
            _add_to_history(latest_file, file_size)
            # Schedule auto-delete after 5 minutes
            _schedule_delete(files[0])

        _set_task(task_id, {
            "status": "done",
            "progress": 100.0,
            "speed": None,
            "eta": None,
            "filename": latest_file,
            "error": None,
        })

    except Exception as e:
        _set_task(task_id, {
            "status": "error",
            "progress": 0.0,
            "speed": None,
            "eta": None,
            "filename": None,
            "error": str(e),
        })


# ──────────────────────────────────────────────
# POST /api/download — start background download
# ──────────────────────────────────────────────
@app.post("/api/download", response_model=DownloadStartResponse)
async def start_download(req: DownloadStartRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    # Validate mode
    if req.mode not in ("video", "audio"):
        raise HTTPException(status_code=400, detail="Mode must be 'video' or 'audio'")

    task_id = str(uuid.uuid4())
    executor.submit(_run_download_task, task_id, req)

    return DownloadStartResponse(task_id=task_id)


# ──────────────────────────────────────────────
# GET /api/status/{task_id} — poll progress
# ──────────────────────────────────────────────
@app.get("/api/status/{task_id}", response_model=TaskStatus)
async def get_status(task_id: str):
    data = _get_task(task_id)
    if not data:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskStatus(task_id=task_id, **data)


# ──────────────────────────────────────────────
# GET /api/history — in-memory download history
# ──────────────────────────────────────────────
@app.get("/api/history", response_model=list[HistoryItem])
async def get_history():
    with history_lock:
        return [
            HistoryItem(
                filename=h["filename"],
                size=h["size"],
                size_human=h["size_human"],
                modified=h["modified"],
                download_url=f"/downloads/{h['filename']}",
            )
            for h in download_history
        ]


# ──────────────────────────────────────────────
# DELETE /api/history/{filename} — remove from history
# ──────────────────────────────────────────────
@app.delete("/api/history/{filename}")
async def delete_file(filename: str):
    # Remove from in-memory history
    with history_lock:
        download_history[:] = [h for h in download_history if h["filename"] != filename]

    # Also delete the file if it still exists
    file_path = (Path(DOWNLOAD_DIR).resolve() / filename).resolve()
    if not str(file_path).startswith(str(Path(DOWNLOAD_DIR).resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if file_path.exists():
        file_path.unlink()

    return {"message": f"Removed {filename}"}


# ──────────────────────────────────────────────
# GET /downloads/{filename} — serve file (if still on disk)
# ──────────────────────────────────────────────
@app.get("/downloads/{filename}")
async def download_file(filename: str):
    file_path = (Path(DOWNLOAD_DIR).resolve() / filename).resolve()

    if not str(file_path).startswith(str(Path(DOWNLOAD_DIR).resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists():
        raise HTTPException(status_code=410, detail="File has been auto-deleted. Please re-download.")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


# ──────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ──────────────────────────────────────────────
# Run server
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
