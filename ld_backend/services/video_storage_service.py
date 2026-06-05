"""Download Seedance videos to local storage and resolve file paths."""

import logging
import re
from pathlib import Path
from typing import Optional

import httpx

from ld_backend.config import VIDEO_STORAGE_DIR

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^\w\-.]+")


class VideoStorageError(Exception):
    """User-facing video storage errors."""


def _safe_segment(value: str) -> str:
    cleaned = _UNSAFE.sub("_", (value or "").strip())
    return cleaned or "unknown"


def relative_video_key(phone: str, state: str) -> str:
    return f"{_safe_segment(phone)}/{_safe_segment(state)}.mp4"


def absolute_video_path(relative_key: str) -> Path:
    root = Path(VIDEO_STORAGE_DIR).resolve()
    path = (root / relative_key).resolve()
    if root not in path.parents and path != root:
        raise VideoStorageError("invalid_video_path")
    return path


def local_video_path(phone: str, state: str) -> Path:
    return absolute_video_path(relative_video_key(phone, state))


def video_file_ready(relative_key: Optional[str]) -> bool:
    key = (relative_key or "").strip()
    if not key:
        return False
    path = absolute_video_path(key)
    return path.is_file() and path.stat().st_size > 0


def delete_local_video(phone: str, state: str) -> None:
    path = local_video_path(phone, state)
    try:
        if path.is_file():
            path.unlink()
            logger.info("Deleted cached video phone=%s state=%s", phone, state)
    except OSError:
        logger.exception("Failed to delete cached video phone=%s state=%s", phone, state)


def download_remote_video(remote_url: str, phone: str, state: str) -> str:
    url = (remote_url or "").strip()
    if not url:
        raise VideoStorageError("missing_video_url")

    dest = local_video_path(phone, state)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".mp4.part")

    try:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with tmp_path.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        if not tmp_path.is_file() or tmp_path.stat().st_size <= 0:
            raise VideoStorageError("empty_video_file")
        tmp_path.replace(dest)
    except httpx.HTTPError as exc:
        raise VideoStorageError(f"download_failed:{exc}") from exc
    finally:
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    rel = relative_video_key(phone, state)
    logger.info(
        "Cached generated video phone=%s state=%s bytes=%s",
        phone,
        state,
        dest.stat().st_size,
    )
    return rel
