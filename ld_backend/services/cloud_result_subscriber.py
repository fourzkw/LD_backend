"""Background WebSocket subscriber for cloud inference results."""

import asyncio
import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from ld_backend.config import (
    CLOUD_RESULT_WS_ENABLED,
    CLOUD_RESULT_WS_HOST,
    CLOUD_RESULT_WS_PORT,
    CLOUD_RESULT_WS_RECONNECT_SECONDS,
    CLOUD_RESULT_WS_URL,
)

try:
    import websockets
except ImportError:  # pragma: no cover - exercised only when dependency is missing.
    websockets = None


logger = logging.getLogger(__name__)

GLOBAL_DEVICE_KEY = "__global__"

_lock = threading.Lock()
_started = False
_latest_by_device: Dict[str, Dict[str, Any]] = {}
_connection_status: Dict[str, Any] = {
    "enabled": CLOUD_RESULT_WS_ENABLED,
    "connected": False,
    "url": "",
    "last_connected_at": None,
    "last_message_at": None,
    "last_error": "",
}


def _ws_url() -> str:
    if CLOUD_RESULT_WS_URL:
        return CLOUD_RESULT_WS_URL
    return f"ws://{CLOUD_RESULT_WS_HOST}:{CLOUD_RESULT_WS_PORT}"


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _storage_key(device_id: Optional[str]) -> str:
    key = (device_id or "").strip()
    return key if key else GLOBAL_DEVICE_KEY


def _normalise_result(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if data.get("type") != "inference_result":
        return None

    details: Dict[str, float] = {}
    raw_details = data.get("details") or {}
    if isinstance(raw_details, dict):
        for key, value in raw_details.items():
            details[str(key)] = _coerce_float(value)

    timestamp = _coerce_float(data.get("timestamp"), time.time())
    behaviour = str(data.get("behaviour") or "Unknown")
    confidence = _coerce_float(data.get("confidence"), 0.0)
    device_id = (data.get("device_id") or data.get("deviceId") or "").strip() or None

    return {
        "type": "inference_result",
        "timestamp": timestamp,
        "behaviour": behaviour,
        "confidence": round(confidence, 4),
        "details": details,
        "device_id": device_id,
    }


def update_cloud_result(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Store a cloud inference result and return the normalized payload."""
    result = _normalise_result(data)
    if result is None:
        return None

    storage_key = _storage_key(result.get("device_id"))
    received_at = time.time()
    with _lock:
        _latest_by_device[storage_key] = dict(result)
        _connection_status["last_message_at"] = received_at
        _connection_status["last_error"] = ""
    return result


def _lookup_latest(device_id: Optional[str]) -> Optional[Dict[str, Any]]:
    with _lock:
        if device_id:
            specific = _latest_by_device.get(device_id)
            if specific is not None:
                return dict(specific)
        global_result = _latest_by_device.get(GLOBAL_DEVICE_KEY)
        return dict(global_result) if global_result is not None else None


def get_cloud_public_status(device_id: Optional[str] = None) -> Dict[str, Any]:
    device_id = (device_id or "").strip() or None
    latest = _lookup_latest(device_id)
    with _lock:
        status = dict(_connection_status)
    if not status.get("url"):
        status["url"] = _ws_url()

    return {
        "ok": True,
        "has_inference": latest is not None,
        "behaviour": (latest or {}).get("behaviour"),
        "last": latest,
        "source": "cloud" if latest else None,
        "cloud": status,
    }


def _set_connection_status(**updates: Any) -> None:
    with _lock:
        _connection_status.update(updates)


async def _subscribe_loop(ws_url: str, reconnect_delay: float) -> None:
    while True:
        try:
            logger.info("Connecting to cloud inference publisher: %s", ws_url)
            _set_connection_status(
                enabled=True,
                connected=False,
                url=ws_url,
                last_error="",
            )

            async with websockets.connect(ws_url) as ws:
                now = time.time()
                _set_connection_status(
                    connected=True,
                    last_connected_at=now,
                    last_error="",
                )
                logger.info("Cloud inference publisher connected: %s", ws_url)

                async for message in ws:
                    if isinstance(message, bytes):
                        message = message.decode("utf-8", errors="replace")
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        logger.warning("Cloud inference message is not JSON: %s", message)
                        continue

                    result = update_cloud_result(data)
                    if result is None:
                        logger.warning("Ignored cloud message with unexpected type: %s", data)
                        continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Cloud inference subscriber disconnected: %s; reconnecting in %.1fs",
                exc,
                reconnect_delay,
            )
            _set_connection_status(
                connected=False,
                url=ws_url,
                last_error=str(exc),
            )

        await asyncio.sleep(reconnect_delay)


def _run_subscriber(ws_url: str, reconnect_delay: float) -> None:
    try:
        asyncio.run(_subscribe_loop(ws_url, reconnect_delay))
    except Exception:
        logger.exception("Cloud inference subscriber stopped unexpectedly")
        _set_connection_status(connected=False, last_error="subscriber_stopped")


def start_cloud_result_subscriber() -> None:
    """Start the cloud WebSocket subscriber once per process."""
    global _started

    ws_url = _ws_url()
    with _lock:
        _connection_status["enabled"] = CLOUD_RESULT_WS_ENABLED
        _connection_status["url"] = ws_url
        if _started:
            return
        if not CLOUD_RESULT_WS_ENABLED:
            logger.info("Cloud inference subscriber disabled")
            return
        if websockets is None:
            _connection_status["last_error"] = "missing_websockets_dependency"
            logger.warning("Cloud inference subscriber disabled: install websockets")
            return
        _started = True

    thread = threading.Thread(
        target=_run_subscriber,
        args=(ws_url, CLOUD_RESULT_WS_RECONNECT_SECONDS),
        name="cloud-result-subscriber",
        daemon=True,
    )
    thread.start()
    logger.info("Cloud inference subscriber started: %s", ws_url)
