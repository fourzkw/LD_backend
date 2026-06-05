"""
Stateful sliding-window inference — same trigger rules as LD_innovation cat_predict.process_frame.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .frame_parser import IMUFrame, parse_frame
from .predictor import CatPosturePredictor

logger = logging.getLogger(__name__)


def _build_result_payload(behaviour: str, confidence: Dict[str, float]) -> Dict[str, Any]:
    top1 = 0.0
    if confidence and behaviour in confidence:
        top1 = confidence[behaviour]
    return {
        "type": "inference_result",
        "timestamp": time.time(),
        "behaviour": behaviour,
        "confidence": round(top1, 4),
        "details": confidence,
    }


class InferencePipeline:
    def __init__(self, model_dir: Optional[str] = None, stride: int = 25):
        self._lock = threading.Lock()
        self.predictor = CatPosturePredictor(model_dir=model_dir)
        self.stride = max(1, stride)
        self.sample_counter = 0
        self._last_inference: Optional[Dict[str, Any]] = None
        window = self.predictor.get_buffer_status()["window_size"]
        self.next_infer_at = window
        logger.info("InferencePipeline window=%d stride=%d", window, self.stride)

    def process_frame_text(self, text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Parse frame, feed samples, return (list of inference_result dicts, status meta).
        """
        recv_time = time.time()
        frame = parse_frame(text, received_at=recv_time)
        if frame is None:
            return [], {"ok": False, "error": "invalid_frame", "buffer": self._buffer_snapshot()}

        results: List[Dict[str, Any]] = []
        n = len(frame.samples)
        meta: Dict[str, Any] = {
            "ok": True,
            "frame_sequence": frame.sequence,
            "samples_in_frame": n,
        }

        with self._lock:
            for sample in frame.samples:
                self.predictor.add_sample(
                    sample.accel_x,
                    sample.accel_y,
                    sample.accel_z,
                )
                status = self.predictor.get_buffer_status()
                self.sample_counter += 1

                if not status["ready"]:
                    continue

                while self.sample_counter >= self.next_infer_at:
                    behaviour, confidence = self.predictor.predict()
                    if behaviour is not None:
                        payload = _build_result_payload(behaviour, confidence)
                        results.append(payload)
                        self._last_inference = payload
                    self.next_infer_at += self.stride

            meta["buffer"] = self._buffer_snapshot()

        if not results:
            meta["status"] = "buffering"
        else:
            meta["status"] = "inference"
        return results, meta

    def _buffer_snapshot(self) -> Dict[str, Any]:
        st = self.predictor.get_buffer_status()
        return {
            "current_size": st["current_size"],
            "window_size": st["window_size"],
            "ready": st["ready"],
            "sample_counter": self.sample_counter,
            "next_infer_at": self.next_infer_at,
        }

    def get_public_status(self) -> Dict[str, Any]:
        """Snapshot for HTTP clients (e.g. miniprogram polling)."""
        with self._lock:
            last = dict(self._last_inference) if self._last_inference else None
            behaviour = (last or {}).get("behaviour")
            return {
                "ok": True,
                "has_inference": last is not None,
                "behaviour": behaviour,
                "last": last,
                "buffer": self._buffer_snapshot(),
            }
