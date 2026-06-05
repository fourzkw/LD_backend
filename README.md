# LD_backend

Flask service that subscribes to cloud inference results over WebSocket (`ws://8.156.34.152:4535` by default) and exposes the latest pet status to the mini program. It also keeps the local IMU **text frame** endpoint in the same format as `LD_innovation/src/cat_predict.py` (`FRAME,TS:...` through `END`), applies the same `/10` acceleration scaling, and runs `CatPosturePredictor` in `inference/predictor.py` (logic copied from `LD_innovation`).

## Project layout

| Path | Role |
|------|------|
| `app.py` | Thin entry: `create_app()` and dev server |
| `ld_backend/` | Flask app factory, blueprints, DB helpers |
| `ld_backend/blueprints/auth.py` | `/api/auth/register`, `/api/auth/login` |
| `ld_backend/blueprints/api.py` | `/health`, `/api/pet/status`, `/api/imu/frame` |
| `ld_backend/blueprints/video.py` | `/api/video/tasks`, `/api/video/tasks/<state>` |
| `ld_backend/db/mongo.py` | MongoDB client and `users` collection |
| `ld_backend/services/cloud_result_subscriber.py` | WebSocket subscriber for cloud `inference_result` messages |
| `inference/` | Frame parsing, model, `InferencePipeline` |
| `models/` | Default weights (`100HZ/`) |

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

`PORT`, `STRIDE`, and other settings come from `.env` unless overridden in the shell.

## API

- `GET` or `POST` `/health` — `{ "status": "ok" }`
- `GET` `/api/pet/status` — latest pet behaviour for the mini program. Cloud WebSocket results are returned first; local IMU inference is used as a fallback.
- `POST` `/api/imu/frame` — body either JSON `{"payload": "<multiline frame>"}` / `{"text": "..."}` or `text/plain` with the raw frame.
- `POST` `/api/auth/register` — JSON: `phone`, `password`, `pet_name`, `pet_image` (requires MongoDB)
- `POST` `/api/auth/login` — JSON: `phone`, `password` → `token` + public `user` fields
- `POST` `/api/video/tasks` — Header `Authorization: Bearer <token>`; body: `state` (required). Creates/reuses a Seedance task with registered `pet_image` and a backend-fixed prompt derived from `state`.
- `GET` `/api/video/tasks/<state>` — Header `Authorization: Bearer <token>`. Poll task status; backend also polls Seedance and persists latest status/video URL.

Response for `/api/imu/frame` includes `results` (array of `inference_result` objects, same shape as innovation) when the stride triggers; otherwise `meta.status` is `buffering`.

The cloud subscriber expects messages like `wx_test_sub.py` receives:

```json
{
  "type": "inference_result",
  "timestamp": 1234567890.123,
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
| `MONGO_URI` | *(empty)* | MongoDB connection string (may include `user:pass@` in URI) |
| `MONGO_DB` | `ld_backend` | Database name |
| `MONGO_SERVER_SELECTION_MS` | `5000` | Server selection / connect timeout (ms) |
| `MONGO_USERNAME` | *(empty)* | If set, authenticate with password (alternative to credentials inside `MONGO_URI`) |
| `MONGO_PASSWORD` | *(empty)* | Password for `MONGO_USERNAME` |
| `MONGO_AUTH_SOURCE` | *(empty)* | Auth database (`admin` if your user lives there) |
| `JWT_SECRET` | `change-me-in-production` | HS256 signing key |
| `JWT_EXPIRE_DAYS` | `7` | Token lifetime |
| `MAX_PET_IMAGE_CHARS` | `2500000` | Max length of `pet_image` string on register |
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
| `LOG_LEVEL` | `INFO` | Python logging level |
| `PORT` | `5000` | Dev server port (`python app.py`) |
| `MODEL_DIR` | *(see predictor)* | Model directory override |
| `STRIDE` | `25` | Inference stride for `/api/imu/frame` |
| `CLOUD_RESULT_WS_ENABLED` | `true` | Start the cloud result WebSocket subscriber |
| `CLOUD_RESULT_WS_HOST` | `8.156.34.152` | Cloud result publisher host |
| `CLOUD_RESULT_WS_PORT` | `4535` | Cloud result publisher port |
| `CLOUD_RESULT_WS_URL` | *(empty)* | Full WebSocket URL override, e.g. `ws://host:port` |
| `CLOUD_RESULT_WS_RECONNECT_SECONDS` | `3` | Reconnect delay after WebSocket disconnects |
