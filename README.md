# LD_backend

Flask backend for the pet miniprogram: user auth (JWT + MongoDB), device pairing, cloud/local pet status, and Seedance image-to-video generation. It subscribes to cloud inference results over WebSocket (`ws://8.156.34.152:4535` by default) and exposes the latest behaviour per device. Local IMU uses the same **text frame** format as `LD_innovation/src/cat_predict.py` (`FRAME,TS:...` through `END`), the same `/10` acceleration scaling, and `CatPosturePredictor` in `inference/predictor.py` (logic copied from `LD_innovation`).

## Project layout

| Path | Role |
|------|------|
| `app.py` | Thin entry: `create_app()` and dev server |
| `ld_backend/` | Flask app factory, blueprints, DB helpers |
| `ld_backend/blueprints/auth.py` | `/api/auth/register`, `/api/auth/login` |
| `ld_backend/blueprints/devices.py` | `/api/devices/*` — register, pair, list, unbind |
| `ld_backend/blueprints/api.py` | `/health`, `/api/pet/status`, `/api/imu/frame` |
| `ld_backend/blueprints/video.py` | `/api/video/tasks`, poll, and local MP4 download |
| `ld_backend/auth_utils.py` | Shared JWT extraction (`Authorization` header or `?token=`) |
| `ld_backend/db/mongo.py` | MongoDB client, `users`, `devices`, `video_tasks` |
| `ld_backend/services/cloud_result_subscriber.py` | WebSocket subscriber for cloud `inference_result` messages |
| `ld_backend/services/seedance_service.py` | Volcengine Ark / Seedance image-to-video tasks |
| `ld_backend/services/video_storage_service.py` | Download and cache generated MP4s under `data/videos/` |
| `inference/` | Frame parsing, model, `InferencePipeline` |
| `models/` | Default weights (`100HZ/`) |
| `data/videos/` | Cached generated videos (`<device_id>/<state>.mp4`) |

## Setup

```powershell
cd LD_backend
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env with MongoDB, ARK_API_KEY, JWT_SECRET, etc.
```

Configuration is loaded from `.env` in the project root on startup (`python-dotenv` via `ld_backend/config.py`). Environment variables set in the shell override `.env`.

## Model files

Default directory: `models/100HZ/` (must contain `cat_cnn_lstm_model.pth` and `model_config.pkl`). Override with:

```bash
set MODEL_DIR=D:\path\to\models\100HZ
```

To refresh weights from your innovation tree:

```text
copy LD_innovation\models\100HZ\cat_cnn_lstm_model.pth LD_backend\models\100HZ\
copy LD_innovation\models\100HZ\model_config.pkl      LD_backend\models\100HZ\
```

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

`HOST`, `PORT`, `STRIDE`, and other settings come from `.env` unless overridden in the shell.

On startup the app also launches (daemon threads):

- **Cloud result subscriber** — connects to the inference WebSocket and caches latest results per `device_id`
- **Video task poller** — refreshes pending Seedance tasks and downloads finished MP4s to `data/videos/`

MongoDB is pinged at startup; if unreachable, IMU-only dev still works but auth/devices/video routes return `503`.

## API

Most JSON routes return `{ "code", "success", "message" }` on error or `{ "code", "success", "data" }` on success. IMU routes use `{ "ok", "meta", "results" }`. Auth accepts `Authorization: Bearer <token>` or query `?token=` / `?access_token=`.

### Health & inference

- `GET` or `POST` `/health` — `{ "status": "ok" }`
- `GET` `/api/pet/status?device_id=<id>` — latest behaviour for a user-owned device (JWT required). Cloud WebSocket results are returned first (device-specific if present, otherwise global); local IMU inference is used as a fallback.
- `POST` `/api/imu/frame` — JSON `{"payload": "<frame>", "device_id": "<id>"}` (device must be registered); also accepts `X-Device-Id` header or `device_id` query param. Body may also be `text/plain` with the raw frame.

Response for `/api/imu/frame` includes `results` (array of `inference_result` objects, same shape as innovation) when the stride triggers; otherwise `meta.status` is `buffering`. Inference state is **per device_id** (in-memory pipelines keyed by device).

### Auth

- `POST` `/api/auth/register` — JSON: `phone` (11-digit CN mobile), `password` (≥6 chars). Requires MongoDB.
- `POST` `/api/auth/login` — JSON: `phone`, `password` → `token` + public `user` fields.

### Devices

- `GET` `/api/devices/registry` — list all registered devices in MongoDB (dev, no auth)
- `POST` `/api/devices/register` — JSON: `device_id`, optional `name` → returns `pairing_code` (dev/hardware registration; no auth)
- `POST` `/api/devices/pair` — JWT; JSON: `pairing_code`, `pet_name`, `pet_image` (base64/data URL), optional `pet_type` (`cat` / `dog`) → bind device to user
- `GET` `/api/devices` — JWT → list bound devices (`device_id`, `name`, `pet_name`, `pet_image`, `pet_type`, `paired_at`, `last_seen_at`)
- `POST` `/api/devices/active` — JWT; JSON: `device_id` → validate ownership (miniprogram active device)
- `DELETE` `/api/devices/<device_id>` — JWT → unbind (device returns to unpaired state with a new `pairing_code`)

### Video (Seedance)

Requires `ARK_API_KEY`. Tasks are scoped per `(device_id, state)`; reference image comes from the bound device's `pet_image`.

- `POST` `/api/video/tasks` — JWT; JSON: `device_id`, `state` (required), optional `force` (delete cache and create a new task). Reuses an existing succeeded task when `video_ready` unless `force` is set.
- `GET` `/api/video/tasks/<state>?device_id=<id>` — JWT; poll task status (`status`, `task_id`, `video_ready`, `error`, `updated_at`). Backend also polls Seedance in a background thread and downloads finished videos locally.
- `GET` `/api/video/tasks/<state>/file?device_id=<id>` — JWT; stream cached MP4 when `video_ready` is true.

Supported `state` values (mapped to fixed Chinese prompts): `Waiting for you`, `Sleeping`, `Feeding`, `Walking`, `Grooming`, `Shaking`. Any other string uses a generic prompt.

Cached files: `data/videos/<device_id>/<state>.mp4` (override root with `VIDEO_STORAGE_DIR`).

### Device pairing flow (miniprogram + mock)

1. Register hardware or mock device: `POST /api/devices/register` with `{"device_id":"DEV-001"}` → note `pairing_code`.
2. User registers/logs in: `POST /api/auth/register` then `POST /api/auth/login` → save JWT.
3. User pairs in miniprogram **设置**: `POST /api/devices/pair` with `pairing_code`, `pet_name`, `pet_image`.
4. Mock IMU sender: `python send_mock_frames.py --device-id DEV-001` (from `LD_mock`).
5. Miniprogram home **云端同步** polls `GET /api/pet/status?device_id=DEV-001` with JWT.
6. Optional video: `POST /api/video/tasks` with `device_id` + behaviour `state`, then poll `GET /api/video/tasks/<state>/file` when `video_ready`.

The cloud subscriber expects messages like `wx_test_sub.py` receives. Optional `device_id` scopes the result to a bound device; messages without `device_id` apply to all devices as a global fallback:

```json
{
  "type": "inference_result",
  "timestamp": 1234567890.123,
  "device_id": "DEV-001",
  "behaviour": "Walk",
  "confidence": 0.92,
  "details": {
    "Rest": 0.01,
    "Sleep": 0.02,
    "Feed": 0.03,
    "Walk": 0.92,
    "Groom": 0.01,
    "Shake": 0.01
  }
}
```

### Auth / database env (optional)

| Variable | Default | Purpose |
|----------|---------|---------|
| `MONGO_URI` | `mongodb://127.0.0.1:27017` | MongoDB connection string (may include `user:pass@` in URI) |
| `MONGO_DB` | `ld_backend` | Database name |
| `MONGO_SERVER_SELECTION_MS` | `5000` | Server selection / connect timeout (ms) |
| `MONGO_USERNAME` | *(empty)* | If set, authenticate with password (alternative to credentials inside `MONGO_URI`) |
| `MONGO_PASSWORD` | *(empty)* | Password for `MONGO_USERNAME` |
| `MONGO_AUTH_SOURCE` | *(empty)* | Auth database (`admin` if your user lives there) |
| `JWT_SECRET` | `change-me-in-production` | HS256 signing key |
| `JWT_EXPIRE_DAYS` | `7` | Token lifetime |
| `MAX_PET_IMAGE_CHARS` | `2500000` | Max length of `pet_image` string on device pair |
| `ARK_API_KEY` | *(empty)* | Seedance service API key (required for `/api/video/*`) |
| `SEEDANCE_MODEL_ID` | `doubao-seedance-2-0-fast-260128` | Model ID for image-to-video task |
| `SEEDANCE_DURATION` | `4` | Target video duration (seconds) |
| `SEEDANCE_RATIO` | `3:4` | Output video ratio |
| `SEEDANCE_RESOLUTION` | `480p` | Output video resolution |
| `SEEDANCE_WATERMARK` | `false` | Enable watermark on generated video |
| `SEEDANCE_GENERATE_AUDIO` | `false` | Generate audio track |
| `VIDEO_POLL_INTERVAL_SECONDS` | `30` | Background poll interval for pending video tasks |

### Other env

| Variable | Default | Purpose |
|----------|---------|---------|
| `HOST` | `0.0.0.0` | Dev server bind address (`python app.py`) |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `PORT` | `5000` | Dev server port (`python app.py`) |
| `MODEL_DIR` | *(see predictor)* | Model directory override |
| `STRIDE` | `25` | Inference stride for `/api/imu/frame` |
| `VIDEO_STORAGE_DIR` | `data/videos` | Local cache for Seedance-generated MP4s |
| `CLOUD_RESULT_WS_ENABLED` | `true` | Start the cloud result WebSocket subscriber |
| `CLOUD_RESULT_WS_HOST` | `8.156.34.152` | Cloud result publisher host |
| `CLOUD_RESULT_WS_PORT` | `4535` | Cloud result publisher port |
| `CLOUD_RESULT_WS_URL` | *(empty)* | Full WebSocket URL override, e.g. `ws://host:port` |
| `CLOUD_RESULT_WS_RECONNECT_SECONDS` | `3` | Reconnect delay after WebSocket disconnects |
