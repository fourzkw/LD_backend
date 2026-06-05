"""Device registration, pairing, and unbind APIs."""

import logging
import re
import secrets
import string
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request
from pymongo.errors import DuplicateKeyError, PyMongoError, ServerSelectionTimeoutError

from ld_backend.auth_utils import require_phone
from ld_backend.config import MAX_PET_IMAGE_CHARS
from ld_backend.db.mongo import devices_collection, ensure_device_indexes

devices_bp = Blueprint("devices", __name__, url_prefix="/api/devices")

_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_PAIRING_CODE_CHARS = string.ascii_uppercase + string.digits
_indexes_ready = False


def _json_error(code: int, message: str, http: int):
    return jsonify({"code": code, "success": False, "message": message}), http


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lazy_indexes() -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    ensure_device_indexes()
    _indexes_ready = True


def _generate_pairing_code() -> str:
    return "".join(secrets.choice(_PAIRING_CODE_CHARS) for _ in range(6))


def _public_device(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "device_id": doc.get("device_id", ""),
        "name": doc.get("name", ""),
        "pet_name": doc.get("pet_name", ""),
        "pet_image": doc.get("pet_image", ""),
        "pet_type": doc.get("pet_type", "cat"),
        "paired_at": doc.get("paired_at"),
        "last_seen_at": doc.get("last_seen_at"),
    }


def _public_device_with_code(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = _public_device(doc)
    out["pairing_code"] = doc.get("pairing_code", "")
    return out


def _registry_device(doc: Dict[str, Any]) -> Dict[str, Any]:
    bound = bool(doc.get("phone"))
    out: Dict[str, Any] = {
        "device_id": doc.get("device_id", ""),
        "name": doc.get("name", ""),
        "bound": bound,
        "phone": doc.get("phone") if bound else None,
        "pairing_code": doc.get("pairing_code") if not bound else None,
        "paired_at": doc.get("paired_at"),
        "last_seen_at": doc.get("last_seen_at"),
        "created_at": doc.get("created_at"),
    }
    return out


@devices_bp.route("/registry", methods=["GET"])
def list_registry_devices():
    """Dev: list all registered devices in MongoDB (no auth)."""
    try:
        _lazy_indexes()
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(503, "database_unavailable", 503)

    try:
        docs = list(devices_collection().find().sort("created_at", -1))
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("list_registry_devices")
        return _json_error(500, "database_error", 500)

    return jsonify(
        {
            "code": 200,
            "success": True,
            "data": {"devices": [_registry_device(d) for d in docs]},
        }
    )


@devices_bp.route("/register", methods=["POST"])
def register_device():
    """Dev/hardware: register or refresh an unbound device and return pairing_code."""
    try:
        _lazy_indexes()
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(503, "database_unavailable", 503)

    body = request.get_json(silent=True) or {}
    device_id = (body.get("device_id") or "").strip()
    name = (body.get("name") or "").strip()

    if not device_id:
        return _json_error(400, "missing_device_id", 400)
    if not _DEVICE_ID_RE.match(device_id):
        return _json_error(400, "invalid_device_id", 400)

    now = _utc_now()
    col = devices_collection()
    existing = col.find_one({"device_id": device_id})

    if existing:
        if existing.get("phone"):
            return _json_error(409, "already_bound", 409)
        return (
            jsonify(
                {
                    "code": 409,
                    "success": False,
                    "message": "device_already_registered",
                    "data": _public_device_with_code(existing),
                }
            ),
            409,
        )

    pairing_code = _generate_pairing_code()
    for _ in range(8):
        if not col.find_one({"pairing_code": pairing_code}):
            break
        pairing_code = _generate_pairing_code()

    doc = {
        "device_id": device_id,
        "pairing_code": pairing_code,
        "phone": None,
        "name": name or device_id,
        "paired_at": None,
        "last_seen_at": None,
        "created_at": now,
        "updated_at": now,
    }

    try:
        col.insert_one(doc)
    except DuplicateKeyError:
        dup = col.find_one({"device_id": device_id})
        if dup and dup.get("phone"):
            return _json_error(409, "already_bound", 409)
        if dup:
            return (
                jsonify(
                    {
                        "code": 409,
                        "success": False,
                        "message": "device_already_registered",
                        "data": _public_device_with_code(dup),
                    }
                ),
                409,
            )
        return _json_error(409, "device_already_registered", 409)
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("register_device")
        return _json_error(500, "database_error", 500)

    return jsonify(
        {
            "code": 200,
            "success": True,
            "data": _public_device_with_code(doc),
        }
    )


@devices_bp.route("/pair", methods=["POST"])
def pair_device():
    phone, err = require_phone()
    if err is not None:
        return err

    try:
        _lazy_indexes()
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(503, "database_unavailable", 503)

    body = request.get_json(silent=True) or {}
    pairing_code = (body.get("pairing_code") or "").strip().upper()
    pet_name = (body.get("pet_name") or "").strip()
    pet_image = body.get("pet_image") or ""
    pet_type = (body.get("pet_type") or "cat").strip().lower()
    if pet_type not in {"cat", "dog"}:
        pet_type = "cat"

    if not pairing_code:
        return _json_error(400, "missing_pairing_code", 400)
    if not pet_name or not pet_image:
        return _json_error(400, "missing_pet_fields", 400)
    if len(pet_image) > MAX_PET_IMAGE_CHARS:
        return _json_error(400, "pet_image_too_large", 400)

    col = devices_collection()
    try:
        doc: Optional[Dict[str, Any]] = col.find_one(
            {"pairing_code": pairing_code, "phone": None}
        )
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("pair_device find")
        return _json_error(500, "database_error", 500)

    if not doc:
        return _json_error(404, "invalid_pairing_code", 404)

    now = _utc_now()
    try:
        col.update_one(
            {"device_id": doc["device_id"], "phone": None},
            {
                "$set": {
                    "phone": phone,
                    "pet_name": pet_name,
                    "pet_image": pet_image,
                    "pet_type": pet_type,
                    "paired_at": now,
                    "updated_at": now,
                },
                "$unset": {"pairing_code": ""},
            },
        )
        updated = col.find_one({"device_id": doc["device_id"]})
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("pair_device update")
        return _json_error(500, "database_error", 500)

    if not updated or updated.get("phone") != phone:
        return _json_error(409, "already_bound", 409)

    return jsonify(
        {
            "code": 200,
            "success": True,
            "data": _public_device(updated),
        }
    )


@devices_bp.route("", methods=["GET"])
def list_devices():
    phone, err = require_phone()
    if err is not None:
        return err

    try:
        _lazy_indexes()
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(503, "database_unavailable", 503)

    try:
        docs = list(devices_collection().find({"phone": phone}).sort("paired_at", -1))
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("list_devices")
        return _json_error(500, "database_error", 500)

    return jsonify(
        {
            "code": 200,
            "success": True,
            "data": {"devices": [_public_device(d) for d in docs]},
        }
    )


@devices_bp.route("/active", methods=["POST"])
def set_active_device():
    phone, err = require_phone()
    if err is not None:
        return err

    body = request.get_json(silent=True) or {}
    device_id = (body.get("device_id") or "").strip()
    if not device_id:
        return _json_error(400, "missing_device_id", 400)

    try:
        doc = devices_collection().find_one({"device_id": device_id, "phone": phone})
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("set_active_device")
        return _json_error(500, "database_error", 500)

    if not doc:
        return _json_error(403, "not_owner", 403)

    return jsonify(
        {
            "code": 200,
            "success": True,
            "data": _public_device(doc),
        }
    )


@devices_bp.route("/<device_id>", methods=["DELETE"])
def unbind_device(device_id: str):
    phone, err = require_phone()
    if err is not None:
        return err

    device_id = (device_id or "").strip()
    if not device_id:
        return _json_error(400, "missing_device_id", 400)

    try:
        _lazy_indexes()
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(503, "database_unavailable", 503)

    col = devices_collection()
    try:
        doc = col.find_one({"device_id": device_id, "phone": phone})
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("unbind_device find")
        return _json_error(500, "database_error", 500)

    if not doc:
        return _json_error(403, "not_owner", 403)

    pairing_code = _generate_pairing_code()
    for _ in range(8):
        if not col.find_one({"pairing_code": pairing_code}):
            break
        pairing_code = _generate_pairing_code()

    now = _utc_now()
    try:
        col.update_one(
            {"device_id": device_id, "phone": phone},
            {
                "$set": {
                    "phone": None,
                    "pairing_code": pairing_code,
                    "paired_at": None,
                    "updated_at": now,
                },
            },
        )
    except DuplicateKeyError:
        return _json_error(409, "pairing_code_conflict", 409)
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("unbind_device update")
        return _json_error(500, "database_error", 500)

    return jsonify(
        {
            "code": 200,
            "success": True,
            "data": {"device_id": device_id, "message": "unbound"},
        }
    )


def find_device_by_id(device_id: str) -> Optional[Dict[str, Any]]:
    """Lookup a registered device (used by IMU/status routes)."""
    try:
        return devices_collection().find_one({"device_id": device_id})
    except PyMongoError:
        logging.exception("find_device_by_id")
        return None


def device_owned_by(device_id: str, phone: str) -> bool:
    doc = find_device_by_id(device_id)
    return bool(doc and doc.get("phone") == phone)
