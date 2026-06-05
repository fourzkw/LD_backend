#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Copied from LD_innovation/src/cat_realtime_classifier.py (CatPosturePredictor + CNN_LSTM_Model).
Default model directory: LD_backend/models/100HZ or MODEL_DIR env.
"""

import logging
import os
from collections import deque

import joblib
import numpy as np
import pandas as pd
import warnings

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn

    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


def _default_model_dir() -> str:
    env = os.environ.get("MODEL_DIR")
    if env and os.path.isdir(env):
        return env
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p100 = os.path.join(root, "models", "100HZ")
    if os.path.isdir(p100):
        return p100
    return os.path.join(root, "models", "cat_optimized")


BEHAVIOUR_CLASSES = ["Rest", "Sleep", "Feed", "Walk", "Groom", "Shake"]
BEHAVIOUR_TO_IDX = {b: i for i, b in enumerate(BEHAVIOUR_CLASSES)}
IDX_TO_BEHAVIOUR = {i: b for i, b in enumerate(BEHAVIOUR_CLASSES)}

BEHAVIOUR_DESC = {
    "Rest": "休息",
    "Sleep": "睡眠",
    "Feed": "进食",
    "Walk": "行走",
    "Groom": "梳理毛发",
    "Shake": "抖动身体",
}

if TORCH_AVAILABLE:

    class CNN_LSTM_Model(nn.Module):
        """1D-CNN + LSTM 混合模型"""

        def __init__(
            self,
            input_channels=3,
            seq_length=40,
            num_classes=7,
            hidden_dim=64,
            num_lstm_layers=2,
            dropout=0.3,
        ):
            super(CNN_LSTM_Model, self).__init__()

            self.conv1 = nn.Sequential(
                nn.Conv1d(input_channels, 32, kernel_size=3, padding=1),
                nn.BatchNorm1d(32),
                nn.ReLU(),
                nn.MaxPool1d(2),
            )

            self.conv2 = nn.Sequential(
                nn.Conv1d(32, 64, kernel_size=3, padding=1),
                nn.BatchNorm1d(64),
                nn.ReLU(),
                nn.MaxPool1d(2),
            )

            self.conv3 = nn.Sequential(
                nn.Conv1d(64, 128, kernel_size=3, padding=1),
                nn.BatchNorm1d(128),
                nn.ReLU(),
            )

            self.lstm = nn.LSTM(
                input_size=128,
                hidden_size=hidden_dim,
                num_layers=num_lstm_layers,
                batch_first=True,
                dropout=dropout if num_lstm_layers > 1 else 0,
                bidirectional=True,
            )

            self.fc = nn.Sequential(
                nn.Dropout(dropout),
                nn.Linear(hidden_dim * 2, 64),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(64, num_classes),
            )

        def forward(self, x):
            x = x.permute(0, 2, 1)
            x = self.conv1(x)
            x = self.conv2(x)
            x = self.conv3(x)
            x = x.permute(0, 2, 1)
            lstm_out, _ = self.lstm(x)
            x = lstm_out[:, -1, :]
            x = self.fc(x)
            return x


class CatPosturePredictor:
    """猫姿态实时预测器（CNN-LSTM / Random Forest / 随机回退）。"""

    def __init__(self, model_dir=None):
        if model_dir is None:
            model_dir = _default_model_dir()

        self.model_dir = model_dir
        self.model = None
        self.model_type = None
        self.config = None
        self.device = None
        self.behaviour_classes = BEHAVIOUR_CLASSES.copy()
        self.behaviour_to_idx = BEHAVIOUR_TO_IDX.copy()
        self.idx_to_behaviour = IDX_TO_BEHAVIOUR.copy()
        self.behaviour_desc = BEHAVIOUR_DESC.copy()
        self.scaler = None

        config_path = os.path.join(model_dir, "model_config.pkl")
        if os.path.exists(config_path):
            self.config = joblib.load(config_path)
            self.window_size = self.config.get("window_size", 100)
            self.sampling_rate = self.config.get("sampling_rate", 100)
            self.behaviour_classes = list(
                self.config.get("behaviour_classes", self.behaviour_classes)
            )
            self.behaviour_to_idx = self.config.get(
                "behaviour_to_idx",
                {b: i for i, b in enumerate(self.behaviour_classes)},
            )
            raw_idx_to_behaviour = self.config.get(
                "idx_to_behaviour",
                {i: b for i, b in enumerate(self.behaviour_classes)},
            )
            self.idx_to_behaviour = {int(k): v for k, v in raw_idx_to_behaviour.items()}
            self.behaviour_desc.update(self.config.get("label_desc", {}))
        else:
            self.window_size = 100
            self.sampling_rate = 100
            self.config = {
                "window_size": 100,
                "sampling_rate": 100,
                "behaviour_classes": BEHAVIOUR_CLASSES,
            }

        self.buffer = deque(maxlen=self.window_size)
        self._load_model()

        logger.info(
            "CatPosturePredictor ready: type=%s window=%d fs=%d dir=%s",
            self.model_type,
            self.window_size,
            self.sampling_rate,
            model_dir,
        )

    def _load_model(self):
        cnn_lstm_path = os.path.join(self.model_dir, "cat_cnn_lstm_model.pth")

        if TORCH_AVAILABLE and os.path.exists(cnn_lstm_path):
            try:
                checkpoint = torch.load(
                    cnn_lstm_path, map_location="cpu", weights_only=False
                )
            except TypeError:
                checkpoint = torch.load(cnn_lstm_path, map_location="cpu")
            self.device = torch.device("cpu")
            num_classes = checkpoint.get("num_classes", len(self.behaviour_classes))
            self.model = CNN_LSTM_Model(
                input_channels=checkpoint.get("input_channels", 3),
                seq_length=checkpoint.get("seq_length", self.window_size),
                num_classes=num_classes,
            ).to(self.device)
            self.model.load_state_dict(checkpoint["model_state_dict"])
            self.model.eval()
            self.model_type = "cnn_lstm"
            logger.info("Loaded CNN-LSTM: %s", cnn_lstm_path)
        else:
            rf_path = os.path.join(self.model_dir, "cat_rf_model.pkl")
            if os.path.exists(rf_path):
                payload = joblib.load(rf_path)
                if isinstance(payload, dict):
                    self.model = payload.get("model")
                    self.scaler = payload.get("scaler")
                else:
                    self.model = payload
                self.model_type = "random_forest"
                logger.info("Loaded Random Forest: %s", rf_path)
            else:
                logger.warning("No model file found; using random predictions")
                self.model_type = "random"

    def add_sample(self, acc_x, acc_y, acc_z, gyr_x=0.0, gyr_y=0.0, gyr_z=0.0):
        self.buffer.append([acc_x, acc_y, acc_z])
        return len(self.buffer) >= self.window_size

    def add_samples(self, data):
        data = np.array(data)
        if data.ndim == 1:
            data = data.reshape(1, -1)
        for row in data:
            if len(row) >= 3:
                self.buffer.append([row[0], row[1], row[2]])
        return len(self.buffer) >= self.window_size

    def predict(self):
        if len(self.buffer) < self.window_size:
            return None, {}
        window = np.array(list(self.buffer))
        if self.model_type == "cnn_lstm":
            return self._predict_cnn_lstm(window)
        if self.model_type in ("random_forest", "random_forest_legacy"):
            return self._predict_rf(window)
        return self._predict_random()

    def _predict_cnn_lstm(self, window):
        with torch.no_grad():
            x = torch.FloatTensor(window).unsqueeze(0).to(self.device)
            outputs = self.model(x)
            probs = torch.softmax(outputs, dim=1).cpu().numpy()[0]
            pred_idx = int(np.argmax(probs))
            pred_behaviour = self.idx_to_behaviour[pred_idx]
            confidence = {
                self.idx_to_behaviour.get(i, f"class_{i}"): float(probs[i])
                for i in range(len(probs))
            }
            return pred_behaviour, confidence

    def _predict_rf(self, window):
        import tsfel

        try:
            cfg = tsfel.get_features_by_domain()
            df = pd.DataFrame(window, columns=["acc_x", "acc_y", "acc_z"])
            features = tsfel.time_series_features_extractor(
                cfg, df, fs=self.sampling_rate, verbose=0
            )
            X = features.values
            X = np.nan_to_num(X, nan=0.0)
            if self.scaler is not None:
                X = self.scaler.transform(X)
            pred = self.model.predict(X)[0]
            if isinstance(pred, (int, np.integer)):
                pred_behaviour = self.idx_to_behaviour.get(
                    pred, self.behaviour_classes[0]
                )
            else:
                pred_behaviour = str(pred)
            if hasattr(self.model, "predict_proba"):
                probs = self.model.predict_proba(X)[0]
                classes = self.model.classes_
                confidence = {}
                for i, c in enumerate(classes):
                    if isinstance(c, (int, np.integer)):
                        key = self.idx_to_behaviour.get(c, self.behaviour_classes[0])
                    else:
                        key = str(c)
                    confidence[key] = float(probs[i])
            else:
                confidence = {pred_behaviour: 1.0}
            return pred_behaviour, confidence
        except Exception as e:
            logger.exception("RF predict error: %s", e)
            fallback = self.behaviour_classes[0]
            return fallback, {fallback: 1.0}

    def _predict_random(self):
        pred = np.random.choice(self.behaviour_classes)
        probs = np.random.dirichlet(np.ones(len(self.behaviour_classes)))
        confidence = {b: float(p) for b, p in zip(self.behaviour_classes, probs)}
        return pred, confidence

    def clear_buffer(self):
        self.buffer.clear()

    def get_buffer_status(self):
        return {
            "current_size": len(self.buffer),
            "window_size": self.window_size,
            "ready": len(self.buffer) >= self.window_size,
        }
