"""Shared JWT helpers for authenticated routes."""

from functools import wraps
from typing import Any, Callable, Optional, Tuple

import jwt
from flask import jsonify, request

from ld_backend.blueprints.auth import JWT_ALG, JWT_SECRET


def extract_phone_from_request() -> Optional[str]:
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


def require_phone() -> Tuple[Optional[str], Any]:
    """Return (phone, None) or (None, error_response)."""
    phone = extract_phone_from_request()
    if not phone:
        return None, (
            jsonify({"code": 401, "success": False, "message": "unauthorized"}),
            401,
        )
    return phone, None


def require_phone_decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        phone, err = require_phone()
        if err is not None:
            return err
        return fn(phone, *args, **kwargs)

    return wrapper
