"""Video generation APIs backed by Seedance tasks."""

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Dict, Optional

import jwt
from flask import Blueprint, jsonify, request, send_file
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

from ld_backend.blueprints.auth import JWT_ALG, JWT_SECRET
from ld_backend.blueprints.devices import device_owned_by, find_device_by_id
from ld_backend.config import VIDEO_POLL_INTERVAL_SECONDS
from ld_backend.db.mongo import ensure_video_task_indexes, video_tasks_collection
from ld_backend.services.seedance_service import (
    SeedanceServiceError,
    create_video_task,
    get_video_task,
)
from ld_backend.services.video_storage_service import (
    VideoStorageError,
    absolute_video_path,
    delete_local_video,
    download_remote_video,
    video_file_ready,
)

video_bp = Blueprint("video", __name__, url_prefix="/api/video")

_video_indexes_ready = False
_poller_started = False
_poller_lock = threading.Lock()
_poller_interval_seconds = VIDEO_POLL_INTERVAL_SECONDS


def _json_error(code: int, message: str, http: int):
    return jsonify({"code": code, "success": False, "message": message}), http


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_phone_from_request() -> Optional[str]:
    auth = (request.headers.get("Authorization") or "").strip()
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = (request.args.get("access_token") or request.args.get("token") or "").strip()
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.InvalidTokenError:
        return None
    return (payload.get("phone") or "").strip() or None


def _default_prompt_for_state(state: str) -> str:
    prompts = {
        "Waiting for you": "使用图片1中的宠物主体，生成宠物正坐着在原地休息，头四处张望的视频，首帧与输入图片无关，首帧即为宠物正在休息",
        "Sleeping": "使用图片1中的宠物主体，生成宠物趴着闭眼熟睡的视频，展示宠物全身，呼吸平稳，首帧与输入图片无关，首帧即为宠物正在睡眠状态",
        "Feeding": "使用图片1中的宠物主体，生成宠物正在欢快进食的视频，动作自然，首帧与输入图片无关，首帧即为宠物正在进食",
        "Walking": "使用图片1中的宠物主体，生成宠物正在快速小跑的视频，步态自然，首帧与输入图片无关，首帧即为宠物正在小跑",
        "Grooming": "使用图片1中的宠物主体，生成宠物正在用舌头梳理自己毛发的视频，首帧与输入图片无关，首帧即为宠物正在梳理毛发",
        "Shaking": "使用图片1中的宠物主体，生成宠物正在用力甩动头、身体和毛发的动作视频，首帧与输入图片无关，首帧即为宠物正在抖动"
    }
    selected_prompt = prompts.get(state, f"使用图片1中的宠物主体，生成宠物处于{state}状态的视频，首帧与输入图片无关，首帧即为宠物处于{state}状态")
    print(f"使用的提示词 (state: {state}): {selected_prompt}")
    return selected_prompt


def _task_public(task_doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "state": task_doc.get("state", ""),
        "status": task_doc.get("status", "unknown"),
        "task_id": task_doc.get("task_id", ""),
        "video_ready": bool(task_doc.get("video_ready")),
        "error": task_doc.get("error", ""),
        "updated_at": task_doc.get("updated_at", ""),
    }


def _lazy_video_indexes() -> None:
    global _video_indexes_ready
    if _video_indexes_ready:
        return
    ensure_video_task_indexes()
    _video_indexes_ready = True


def _ensure_video_cached(task_doc: Dict[str, Any]) -> Dict[str, Any]:
    if task_doc.get("status") != "succeeded":
        return task_doc

    local_key = (task_doc.get("local_video_path") or "").strip()
    if task_doc.get("video_ready") and video_file_ready(local_key):
        return task_doc

    remote_url = (task_doc.get("video_url") or "").strip()
    if not remote_url:
        return task_doc

    phone = (task_doc.get("phone") or "").strip()
    device_id = (task_doc.get("device_id") or "").strip()
    state = (task_doc.get("state") or "").strip()
    if not device_id or not state:
        return task_doc

    now = _utc_now()
    try:
        rel_key = download_remote_video(remote_url, device_id, state)
        update_fields: Dict[str, Any] = {
            "local_video_path": rel_key,
            "video_ready": True,
            "cache_error": "",
            "updated_at": now,
        }
    except VideoStorageError as exc:
        logging.warning(
            "Video cache failed task_id=%s device_id=%s state=%s reason=%s",
            task_doc.get("task_id", ""),
            device_id,
            state,
            str(exc),
        )
        update_fields = {
            "video_ready": False,
            "cache_error": str(exc),
            "updated_at": now,
        }
    except Exception:
        logging.exception(
            "Video cache failed task_id=%s device_id=%s state=%s",
            task_doc.get("task_id", ""),
            device_id,
            state,
        )
        update_fields = {
            "video_ready": False,
            "cache_error": "cache_failed",
            "updated_at": now,
        }

    video_tasks_collection().update_one({"_id": task_doc["_id"]}, {"$set": update_fields})
    task_doc.update(update_fields)
    return task_doc


def _refresh_task_from_provider(task_doc: Dict[str, Any]) -> Dict[str, Any]:
    status = task_doc.get("status", "unknown")
    if status not in {"succeeded", "failed"}:
        provider = get_video_task(task_doc.get("task_id", ""))
        now = _utc_now()
        update_fields: Dict[str, Any] = {
            "status": provider.get("status", task_doc.get("status", "unknown")),
            "updated_at": now,
        }
        if provider.get("video_url"):
            update_fields["video_url"] = provider["video_url"]
        if provider.get("error"):
            update_fields["error"] = str(provider["error"])

        video_tasks_collection().update_one(
            {"_id": task_doc["_id"]},
            {"$set": update_fields},
        )
        task_doc.update(update_fields)

    if task_doc.get("status") == "succeeded":
        task_doc = _ensure_video_cached(task_doc)
    return task_doc


def _poll_pending_tasks_once() -> None:
    _lazy_video_indexes()
    tasks = list(
        video_tasks_collection().find(
            {
                "$or": [
                    {"status": {"$nin": ["succeeded", "failed"]}},
                    {"status": "succeeded", "video_ready": {"$ne": True}},
                ]
            },
            {
                "task_id": 1,
                "status": 1,
                "phone": 1,
                "device_id": 1,
                "state": 1,
                "video_url": 1,
                "video_ready": 1,
                "local_video_path": 1,
            },
        )
    )
    if not tasks:
        return
    for task_doc in tasks:
        task_id = task_doc.get("task_id", "")
        if not task_id:
            continue
        try:
            before_status = task_doc.get("status", "unknown")
            before_ready = bool(task_doc.get("video_ready"))
            latest = _refresh_task_from_provider(task_doc)
            after_status = latest.get("status", "unknown")
            after_ready = bool(latest.get("video_ready"))
            if after_status != before_status or after_ready != before_ready:
                logging.info(
                    "Video task updated task_id=%s device_id=%s state=%s status=%s->%s ready=%s->%s",
                    task_id,
                    task_doc.get("device_id", ""),
                    task_doc.get("state", ""),
                    before_status,
                    after_status,
                    before_ready,
                    after_ready,
                )
        except SeedanceServiceError as exc:
            logging.warning("Video task poll skipped task_id=%s reason=%s", task_id, str(exc))
        except Exception:
            logging.exception("Video task poll failed task_id=%s", task_id)


def _video_task_poll_loop() -> None:
    while True:
        try:
            _poll_pending_tasks_once()
        except Exception:
            logging.exception("Video task poll loop iteration failed")
        time.sleep(_poller_interval_seconds)


def start_video_task_poller() -> None:
    global _poller_started
    with _poller_lock:
        if _poller_started:
            return
        thread = threading.Thread(target=_video_task_poll_loop, name="video-task-poller", daemon=True)
        thread.start()
        _poller_started = True
        logging.info("Video task poller started (interval=%ss)", _poller_interval_seconds)


def _resolve_device_id(phone: str, raw_device_id: str):
    device_id = (raw_device_id or "").strip()
    if not device_id:
        return None, _json_error(400, "missing_device_id", 400)
    if not device_owned_by(device_id, phone):
        return None, _json_error(403, "not_owner", 403)
    device = find_device_by_id(device_id)
    if not device:
        return None, _json_error(404, "device_not_found", 404)
    return device, None


@video_bp.post("/tasks")
def create_task():
    phone = _extract_phone_from_request()
    if not phone:
        return _json_error(401, "unauthorized", 401)

    body = request.get_json(silent=True) or {}
    state = (body.get("state") or "").strip()
    force = bool(body.get("force"))
    device, err = _resolve_device_id(phone, body.get("device_id"))
    if err is not None:
        return err
    if not state:
        return _json_error(400, "missing_state", 400)

    try:
        _lazy_video_indexes()
        device_id = device["device_id"]
        ref_image = device.get("pet_image", "")
        if not ref_image:
            return _json_error(400, "missing_pet_image", 400)
        print(f"state: {state}")
        prompt = _default_prompt_for_state(state)

        tasks = video_tasks_collection()
        existing = tasks.find_one({"device_id": device_id, "state": state})
        if force and existing:
            delete_local_video(device_id, state)

        if not force and existing and existing.get("status") == "succeeded" and existing.get("video_ready"):
            return jsonify(
                {
                    "code": 200,
                    "success": True,
                    "data": {
                        **_task_public(existing),
                        "reused": True,
                    },
                }
            )

        if not force and existing and existing.get("status") not in {"failed"}:
            latest = _refresh_task_from_provider(existing)
            return jsonify(
                {
                    "code": 200,
                    "success": True,
                    "data": {
                        **_task_public(latest),
                        "reused": True,
                    },
                }
            )

        created = create_video_task(reference_image=ref_image, prompt=prompt)
        now = _utc_now()
        doc = {
            "phone": phone,
            "device_id": device_id,
            "state": state,
            "prompt": prompt,
            "task_id": created["task_id"],
            "status": created["status"],
            "model": created.get("model", ""),
            "video_url": "",
            "local_video_path": "",
            "video_ready": False,
            "cache_error": "",
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
        tasks.update_one(
            {"device_id": device_id, "state": state},
            {"$set": doc},
            upsert=True,
        )
        return jsonify({"code": 200, "success": True, "data": _task_public(doc)})
    except SeedanceServiceError as exc:
        return _json_error(503, str(exc), 503)
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(500, "database_error", 500)
    except Exception:
        return _json_error(500, "create_video_task_failed", 500)


@video_bp.get("/tasks/<state>")
def get_task(state: str):
    phone = _extract_phone_from_request()
    if not phone:
        return _json_error(401, "unauthorized", 401)
    state = (state or "").strip()
    if not state:
        return _json_error(400, "missing_state", 400)
    device_id = (request.args.get("device_id") or "").strip()
    device, err = _resolve_device_id(phone, device_id)
    if err is not None:
        return err

    try:
        _lazy_video_indexes()
        task_doc = video_tasks_collection().find_one(
            {"device_id": device["device_id"], "state": state}
        )
        if not task_doc:
            return _json_error(404, "task_not_found", 404)
        latest = _refresh_task_from_provider(task_doc)
        return jsonify({"code": 200, "success": True, "data": _task_public(latest)})
    except SeedanceServiceError as exc:
        return _json_error(503, str(exc), 503)
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(500, "database_error", 500)
    except Exception:
        return _json_error(500, "get_video_task_failed", 500)


@video_bp.get("/tasks/<state>/file")
def download_task_video(state: str):
    phone = _extract_phone_from_request()
    if not phone:
        return _json_error(401, "unauthorized", 401)
    state = (state or "").strip()
    if not state:
        return _json_error(400, "missing_state", 400)
    device_id = (request.args.get("device_id") or "").strip()
    device, err = _resolve_device_id(phone, device_id)
    if err is not None:
        return err

    try:
        _lazy_video_indexes()
        task_doc = video_tasks_collection().find_one(
            {"device_id": device["device_id"], "state": state}
        )
        if not task_doc:
            return _json_error(404, "task_not_found", 404)

        latest = _refresh_task_from_provider(task_doc)
        if latest.get("status") != "succeeded":
            return _json_error(409, "video_not_ready", 409)
        if not latest.get("video_ready"):
            return _json_error(409, "video_not_ready", 409)

        local_key = (latest.get("local_video_path") or "").strip()
        if not video_file_ready(local_key):
            return _json_error(404, "video_file_missing", 404)

        path = absolute_video_path(local_key)
        return send_file(
            path,
            mimetype="video/mp4",
            as_attachment=False,
            download_name=f"{state}.mp4",
        )
    except VideoStorageError:
        return _json_error(404, "video_file_missing", 404)
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(500, "database_error", 500)
    except Exception:
        return _json_error(500, "download_video_failed", 500)
