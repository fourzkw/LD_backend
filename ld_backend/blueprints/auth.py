"""User registration and login (MongoDB + bcrypt + JWT)."""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt
from flask import Blueprint, jsonify, request
from pymongo.errors import DuplicateKeyError, PyMongoError, ServerSelectionTimeoutError

from ld_backend.config import JWT_EXPIRE_DAYS, JWT_SECRET
from ld_backend.db.mongo import ensure_user_indexes, users_collection

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

JWT_ALG = "HS256"
JWT_DAYS = JWT_EXPIRE_DAYS
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def _json_error(code: int, message: str, http: int):
    return jsonify({"code": code, "success": False, "message": message}), http


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


def _issue_token(phone: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "phone": phone,
        "iat": now,
        "exp": now + timedelta(days=JWT_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _public_user(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "phone": doc.get("phone", ""),
    }


_indexes_ready = False


def _lazy_indexes() -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    try:
        ensure_user_indexes()
    except ServerSelectionTimeoutError:
        raise
    except PyMongoError:
        logging.exception("ensure_user_indexes")
        raise
    _indexes_ready = True


@auth_bp.route("/register", methods=["POST"])
def register():
    try:
        _lazy_indexes()
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(503, "database_unavailable", 503)
    body = request.get_json(silent=True) or {}
    phone = (body.get("phone") or "").strip()
    password = body.get("password") or ""

    if not phone or not password:
        return _json_error(400, "missing_fields", 400)
    if not PHONE_RE.match(phone):
        return _json_error(400, "invalid_phone", 400)
    if len(password) < 6:
        return _json_error(400, "password_too_short", 400)

    doc = {
        "phone": phone,
        "password_hash": _hash_password(password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        users = users_collection()
        if users.find_one({"phone": phone}):
            return _json_error(409, "phone_exists", 409)
        users.insert_one(doc)
    except DuplicateKeyError:
        return _json_error(409, "phone_exists", 409)
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("register")
        return _json_error(500, "database_error", 500)
    return jsonify({"code": 200, "success": True, "data": {"message": "registered"}})


@auth_bp.route("/login", methods=["POST"])
def login():
    try:
        _lazy_indexes()
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        return _json_error(503, "database_unavailable", 503)
    body = request.get_json(silent=True) or {}
    phone = (body.get("phone") or "").strip()
    password = body.get("password") or ""

    if not phone or not password:
        return _json_error(400, "missing_fields", 400)

    try:
        users = users_collection()
        doc: Optional[Dict[str, Any]] = users.find_one({"phone": phone})
    except ServerSelectionTimeoutError:
        return _json_error(503, "database_unavailable", 503)
    except PyMongoError:
        logging.exception("login find")
        return _json_error(500, "database_error", 500)
    if not doc or not _check_password(password, doc.get("password_hash", "")):
        return _json_error(401, "invalid_credentials", 401)

    token = _issue_token(phone)
    return jsonify(
        {
            "code": 200,
            "success": True,
            "data": {
                "token": token,
                "user": _public_user(doc),
            },
        }
    )
