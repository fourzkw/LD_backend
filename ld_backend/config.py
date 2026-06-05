"""Environment-driven settings (logging, MongoDB, Seedance, auth, etc.)."""

import logging
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_path = _ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        pass


_load_dotenv()

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# MongoDB
MONGO_URI = (os.environ.get("MONGO_URI") or "mongodb://127.0.0.1:27017").strip()
MONGO_SERVER_SELECTION_MS = int(os.environ.get("MONGO_SERVER_SELECTION_MS", "5000"))
MONGO_DB = os.environ.get("MONGO_DB", "ld_backend")
MONGO_USERNAME = os.environ.get("MONGO_USERNAME", "").strip()
MONGO_PASSWORD = os.environ.get("MONGO_PASSWORD", "")
MONGO_AUTH_SOURCE = os.environ.get("MONGO_AUTH_SOURCE", "").strip() or None

# Seedance / Volcengine Ark
ARK_API_KEY = os.environ.get("ARK_API_KEY", "").strip()
SEEDANCE_MODEL_ID = os.environ.get("SEEDANCE_MODEL_ID", "doubao-seedance-2-0-fast-260128")
SEEDANCE_DURATION = int(os.environ.get("SEEDANCE_DURATION", "4"))
SEEDANCE_RATIO = os.environ.get("SEEDANCE_RATIO", "3:4")
SEEDANCE_RESOLUTION = os.environ.get("SEEDANCE_RESOLUTION", "480p")
SEEDANCE_WATERMARK = _env_bool("SEEDANCE_WATERMARK", False)
SEEDANCE_GENERATE_AUDIO = _env_bool("SEEDANCE_GENERATE_AUDIO", False)

# Auth
JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_EXPIRE_DAYS = int(os.environ.get("JWT_EXPIRE_DAYS", "7"))
MAX_PET_IMAGE_CHARS = int(os.environ.get("MAX_PET_IMAGE_CHARS", "2500000"))

# Inference / server
HOST = os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.environ.get("PORT", "5000"))
MODEL_DIR = os.environ.get("MODEL_DIR") or None
STRIDE = int(os.environ.get("STRIDE", "25"))
VIDEO_POLL_INTERVAL_SECONDS = int(os.environ.get("VIDEO_POLL_INTERVAL_SECONDS", "30"))
VIDEO_STORAGE_DIR = os.environ.get("VIDEO_STORAGE_DIR") or str(_ROOT / "data" / "videos")

# Cloud inference result WebSocket publisher
CLOUD_RESULT_WS_ENABLED = _env_bool("CLOUD_RESULT_WS_ENABLED", True)
CLOUD_RESULT_WS_HOST = os.environ.get("CLOUD_RESULT_WS_HOST", "8.156.34.152").strip()
CLOUD_RESULT_WS_PORT = int(os.environ.get("CLOUD_RESULT_WS_PORT", "4535"))
CLOUD_RESULT_WS_URL = os.environ.get("CLOUD_RESULT_WS_URL", "").strip()
CLOUD_RESULT_WS_RECONNECT_SECONDS = float(
    os.environ.get("CLOUD_RESULT_WS_RECONNECT_SECONDS", "3")
)


def get_log_level() -> int:
    return getattr(logging, os.environ.get("LOG_LEVEL", "INFO"), logging.INFO)
