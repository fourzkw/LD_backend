"""Health check, pet status, and IMU frame ingestion."""

import threading
from datetime import datetime, timezone
from typing import Dict, Optional

from flask import Blueprint, jsonify, request

from ld_backend.auth_utils import require_phone
from ld_backend.blueprints.devices import device_owned_by, find_device_by_id
from ld_backend.config import MODEL_DIR, STRIDE
from ld_backend.db.mongo import touch_device_last_seen
from ld_backend.services.cloud_result_subscriber import get_cloud_public_status
from inference.pipeline import InferencePipeline

api_bp = Blueprint("api", __name__)

_pipelines: Dict[str, InferencePipeline] = {}
_pipeline_lock = threading.Lock()

_EMPTY_BUFFER = {
    "current_size": 0,
    "window_size": None,
    "ready": False,
    "sample_counter": 0,
    "next_infer_at": None,
}


def get_pipeline(device_id: str) -> InferencePipeline:
    with _pipeline_lock:
        if device_id not in _pipelines:
            _pipelines[device_id] = InferencePipeline(model_dir=MODEL_DIR, stride=STRIDE)
        return _pipelines[device_id]


def _extract_device_id() -> Optional[str]:
    header_id = (request.headers.get("X-Device-Id") or "").strip()
    if header_id:
        return header_id
    query_id = (request.args.get("device_id") or "").strip()
    if query_id:
        return query_id
    if request.is_json:
        data = request.get_json(silent=True) or {}
        body_id = (data.get("device_id") or "").strip()
        if body_id:
            return body_id
    return None


def _json_error(message: str, http: int = 400):
    return jsonify({"ok": False, "error": message, "success": False, "message": message}), http


def _pipeline_buffer(device_id: str) -> dict:
    if device_id in _pipelines:
        return _pipelines[device_id].get_public_status()["buffer"]
    return dict(_EMPTY_BUFFER)


@api_bp.get("/health")
@api_bp.post("/health")
def health():
    return jsonify({"status": "ok"})


@api_bp.get("/api/pet/status")
def api_pet_status():
    """Latest inferred behaviour for a user-owned device (cloud first, local fallback)."""
    phone, err = require_phone()
    if err is not None:
        return err

    device_id = _extract_device_id()
    if not device_id:
        return _json_error("missing_device_id", 400)

    if not device_owned_by(device_id, phone):
        return _json_error("not_owner", 403)

    cloud_status = get_cloud_public_status(device_id)
    if cloud_status["has_inference"]:
        cloud_status["device_id"] = device_id
        cloud_status["buffer"] = _pipeline_buffer(device_id)
        return jsonify(cloud_status)

    pl = get_pipeline(device_id)
    local_status = pl.get_public_status()
    local_status["device_id"] = device_id
    local_status["source"] = "local" if local_status.get("has_inference") else None
    local_status["cloud"] = cloud_status["cloud"]
    return jsonify(local_status)


@api_bp.post("/api/imu/frame")
def api_imu_frame():
    device_id = _extract_device_id()
    if not device_id:
        return _json_error("missing_device_id", 400)

    if not find_device_by_id(device_id):
        return _json_error("device_not_found", 404)

    ct = (request.content_type or "").lower()
    if "application/json" in ct:
        data = request.get_json(silent=True) or {}
        text = data.get("payload") or data.get("text") or ""
    else:
        text = request.get_data(as_text=True) or ""

    if not (text or "").strip():
        return _json_error("empty_body", 400)

    pl = get_pipeline(device_id)
    results, meta = pl.process_frame_text(text)
    touch_device_last_seen(device_id, datetime.now(timezone.utc).isoformat())

    body = {
        "ok": bool(meta.get("ok")),
        "device_id": device_id,
        "meta": meta,
        "results": results,
    }
    if results:
        body["latest"] = results[-1]
    return jsonify(body)
