"""
Pydantic v2 models for the YouTube Downloader API.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


# ──────────────────────────────────────────────
# Validate endpoint
# ──────────────────────────────────────────────
class ValidateRequest(BaseModel):
    url: str = Field(..., description="YouTube video or playlist URL")


class VideoInfo(BaseModel):
    """Metadata returned after URL validation."""
    title: str
    channel: str
    duration: Optional[int] = None          # seconds
    duration_string: Optional[str] = None   # human-readable "mm:ss"
    thumbnail: Optional[str] = None
    view_count: Optional[int] = None
    upload_date: Optional[str] = None
    is_playlist: bool = False
    playlist_count: Optional[int] = None


class ValidateResponse(BaseModel):
    valid: bool
    info: Optional[VideoInfo] = None
    error: Optional[str] = None


# ──────────────────────────────────────────────
# Download endpoint
# ──────────────────────────────────────────────
class DownloadStartRequest(BaseModel):
    url: str
    mode: str = Field("video", description="'video' or 'audio'")
    quality: str = Field("best", description="'best', '1080', '720', '480'")
    audio_format: str = Field("mp3", description="'mp3' or 'm4a'")


class DownloadStartResponse(BaseModel):
    task_id: str
    message: str = "Download started"


# ──────────────────────────────────────────────
# Status endpoint
# ──────────────────────────────────────────────
class TaskStatus(BaseModel):
    task_id: str
    status: str              # "pending" | "downloading" | "processing" | "done" | "error"
    progress: float = 0.0   # 0–100
    speed: Optional[str] = None
    eta: Optional[str] = None
    filename: Optional[str] = None
    error: Optional[str] = None


# ──────────────────────────────────────────────
# History endpoint
# ──────────────────────────────────────────────
class HistoryItem(BaseModel):
    filename: str
    size: int                # bytes
    size_human: str          # e.g. "150.3 MiB"
    modified: str            # ISO timestamp
    download_url: str        # relative URL to download
