"""Health check, pet status, and IMU frame ingestion."""

from typing import Optional

from flask import Blueprint, jsonify, request

from ld_backend.config import MODEL_DIR, STRIDE
from ld_backend.services.cloud_result_subscriber import get_cloud_public_status
from inference.pipeline import InferencePipeline

api_bp = Blueprint("api", __name__)

_pipeline: Optional[InferencePipeline] = None


def get_pipeline() -> InferencePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = InferencePipeline(model_dir=MODEL_DIR, stride=STRIDE)
    return _pipeline


@api_bp.get("/health")
@api_bp.post("/health")
def health():
    return jsonify({"status": "ok"})


@api_bp.get("/api/pet/status")
def api_pet_status():
    """Latest inferred behaviour for mini-program polling."""
    cloud_status = get_cloud_public_status()
    if cloud_status["has_inference"]:
        if _pipeline is not None:
            cloud_status["buffer"] = _pipeline.get_public_status()["buffer"]
        else:
            cloud_status["buffer"] = {
                "current_size": 0,
                "window_size": None,
                "ready": False,
                "sample_counter": 0,
                "next_infer_at": None,
            }
        return jsonify(cloud_status)

    if _pipeline is not None:
        local_status = _pipeline.get_public_status()
    else:
        local_status = {
            "ok": True,
            "has_inference": False,
            "behaviour": None,
            "last": None,
            "buffer": {
                "current_size": 0,
                "window_size": None,
                "ready": False,
                "sample_counter": 0,
                "next_infer_at": None,
            },
        }
    local_status["source"] = "local" if local_status.get("has_inference") else None
    local_status["cloud"] = cloud_status["cloud"]
    return jsonify(local_status)


@api_bp.post("/api/imu/frame")
def api_imu_frame():
    ct = (request.content_type or "").lower()
    if "application/json" in ct:
        data = request.get_json(silent=True) or {}
        text = data.get("payload") or data.get("text") or ""
    else:
        text = request.get_data(as_text=True) or ""

    if not (text or "").strip():
        return jsonify({"ok": False, "error": "empty_body"}), 400

    pl = get_pipeline()
    results, meta = pl.process_frame_text(text)

    body = {
        "ok": bool(meta.get("ok")),
        "meta": meta,
        "results": results,
    }
    if results:
        body["latest"] = results[-1]
    return jsonify(body)
