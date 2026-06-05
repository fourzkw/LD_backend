"""
IMU text frame parsing — aligned with LD_innovation/src/cat_predict.py parse_frame.
"""

import re
import time
from dataclasses import dataclass
from typing import List, Optional

HEADER_PATTERN = re.compile(r"FRAME,TS:([0-9.]+),SEQ:(\d+),LEN:(\d+)")


@dataclass
class IMUSample:
    timestamp: float
    accel_x: float
    accel_y: float
    accel_z: float
    gyro_x: float
    gyro_y: float
    gyro_z: float


@dataclass
class IMUFrame:
    sequence: int
    payload_len: int
    header_ts: float
    received_at: float
    samples: List[IMUSample]


def parse_frame(text: str, received_at: Optional[float] = None) -> Optional[IMUFrame]:
    if received_at is None:
        received_at = time.time()
    lines = text.strip().split("\n")
    if len(lines) < 3:
        return None

    header_match = HEADER_PATTERN.search(lines[0].strip())
    if not header_match:
        scope = "\n".join(lines[:2])
        header_match = HEADER_PATTERN.search(scope)
    if not header_match:
        return None

    header_ts = float(header_match.group(1))
    seq = int(header_match.group(2))
    declared_len = int(header_match.group(3))

    if lines[-1].strip() != "END":
        return None

    samples: List[IMUSample] = []
    for line in lines[1:-1]:
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split(",")
        if len(parts) != 7:
            continue
        try:
            raw_t = float(parts[0])
            ts = header_ts + raw_t if abs(raw_t) < 1e6 else raw_t
            samples.append(
                IMUSample(
                    timestamp=ts,
                    accel_x=float(parts[1]) / 10.0,
                    accel_y=float(parts[2]) / 10.0,
                    accel_z=float(parts[3]) / 10.0,
                    gyro_x=float(parts[4]),
                    gyro_y=float(parts[5]),
                    gyro_z=float(parts[6]),
                )
            )
        except ValueError:
            continue

    return IMUFrame(
        sequence=seq,
        payload_len=declared_len,
        header_ts=header_ts,
        received_at=received_at,
        samples=samples,
    )
