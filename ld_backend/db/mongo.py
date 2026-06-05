"""MongoDB connection and users collection (singleton)."""

import logging
from typing import Any, Dict, Optional

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from ld_backend.config import (
    MONGO_AUTH_SOURCE,
    MONGO_DB,
    MONGO_PASSWORD,
    MONGO_SERVER_SELECTION_MS,
    MONGO_URI,
    MONGO_USERNAME,
)

_client: Optional[MongoClient] = None


def _create_and_ping_client() -> MongoClient:
    """Build client, verify with ping, set singleton. Caller handles errors."""
    global _client
    client = MongoClient(MONGO_URI, **_mongo_client_options())
    client.admin.command("ping")
    logging.info("MongoDB connected successfully (database=%s)", MONGO_DB)
    _client = client
    return client


def _mongo_client_options() -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "serverSelectionTimeoutMS": MONGO_SERVER_SELECTION_MS,
        "connectTimeoutMS": MONGO_SERVER_SELECTION_MS,
    }
    if MONGO_USERNAME:
        opts["username"] = MONGO_USERNAME
        opts["password"] = MONGO_PASSWORD
        opts["authSource"] = MONGO_AUTH_SOURCE or MONGO_DB
    return opts


def get_mongo_client() -> MongoClient:
    if _client is not None:
        return _client
    try:
        return _create_and_ping_client()
    except Exception:
        logging.exception("MongoDB connection failed (could not ping server)")
        raise


def ping_mongo_at_startup() -> None:
    """Eager connect so startup logs show DB status. Failure is non-fatal (e.g. IMU-only dev)."""
    if _client is not None:
        return
    try:
        _create_and_ping_client()
    except Exception:
        logging.warning(
            "MongoDB unreachable at startup; /api/auth will fail until the database is available",
            exc_info=True,
        )


def get_db() -> Database[Any]:
    return get_mongo_client()[MONGO_DB]


def users_collection() -> Collection[Any]:
    return get_db()["users"]


def ensure_user_indexes() -> None:
    users_collection().create_index("phone", unique=True)


def video_tasks_collection() -> Collection[Any]:
    return get_db()["video_tasks"]


def ensure_video_task_indexes() -> None:
    video_tasks_collection().create_index([("device_id", 1), ("state", 1)], unique=True)
    video_tasks_collection().create_index([("device_id", 1), ("updated_at", -1)])


def devices_collection() -> Collection[Any]:
    return get_db()["devices"]


def ensure_device_indexes() -> None:
    devices_collection().create_index("device_id", unique=True)
    devices_collection().create_index("pairing_code", unique=True, sparse=True)
    devices_collection().create_index("phone")


def touch_device_last_seen(device_id: str, when: str) -> None:
    """Update last_seen_at for a registered device (best-effort)."""
    try:
        devices_collection().update_one(
            {"device_id": device_id},
            {"$set": {"last_seen_at": when}},
        )
    except Exception:
        logging.exception("touch_device_last_seen device_id=%s", device_id)
