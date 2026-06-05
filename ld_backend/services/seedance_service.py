"""Seedance video generation service wrapper."""

from typing import Any, Dict, Optional

from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime._exceptions import ArkAPIError, ArkNotFoundError

from ld_backend.config import (
    ARK_API_KEY,
    SEEDANCE_DURATION,
    SEEDANCE_GENERATE_AUDIO,
    SEEDANCE_MODEL_ID,
    SEEDANCE_RATIO,
    SEEDANCE_RESOLUTION,
    SEEDANCE_WATERMARK,
)

_client: Optional[Ark] = None


class SeedanceServiceError(Exception):
    """User-facing Seedance service errors."""


def _get_client() -> Ark:
    global _client
    if _client is not None:
        return _client
    if not ARK_API_KEY:
        raise SeedanceServiceError("missing_ark_api_key")
    _client = Ark(api_key=ARK_API_KEY)
    return _client


def _safe_get_video_url(get_result: Any) -> str:
    content = getattr(get_result, "content", None)
    if content is None:
        return ""
    return getattr(content, "video_url", "") or ""


def create_video_task(reference_image: str, prompt: str) -> Dict[str, Any]:
    client = _get_client()
    if not (reference_image or "").strip():
        raise SeedanceServiceError("missing_reference_image")
    if not (prompt or "").strip():
        raise SeedanceServiceError("missing_prompt")

    try:
        create_result = client.content_generation.tasks.create(
            model=SEEDANCE_MODEL_ID,
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": reference_image},
                    "role": "reference_image",
                },
            ],
            generate_audio=SEEDANCE_GENERATE_AUDIO,
            duration=SEEDANCE_DURATION,
            ratio=SEEDANCE_RATIO,
            resolution=SEEDANCE_RESOLUTION,
            watermark=SEEDANCE_WATERMARK,
        )
    except ArkAPIError as exc:
        raise SeedanceServiceError(f"ark_api_error:{exc}") from exc
    return {
        "task_id": create_result.id,
        "model": SEEDANCE_MODEL_ID,
        "status": "submitted",
    }


def get_video_task(task_id: str) -> Dict[str, Any]:
    client = _get_client()
    if not (task_id or "").strip():
        raise SeedanceServiceError("missing_task_id")
    try:
        get_result = client.content_generation.tasks.get(task_id=task_id)
    except ArkNotFoundError:
        return {"status": "failed", "error": "task_not_found"}
    except ArkAPIError as exc:
        raise SeedanceServiceError(f"ark_api_error:{exc}") from exc
    status = getattr(get_result, "status", "") or "unknown"
    payload: Dict[str, Any] = {"status": status}
    if status == "succeeded":
        payload["video_url"] = _safe_get_video_url(get_result)
    elif status == "failed":
        payload["error"] = getattr(get_result, "error", "") or "task_failed"
    return payload
