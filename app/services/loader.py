from pathlib import Path
from typing import IO

import pandas as pd

SENSOR_NAMES = ("camera", "lidar", "radar", "imu", "gps")

BASE_COLUMNS = (
    "timestamp",
    "speed_mps",
    "accel_mps2",
    "latitude",
    "longitude",
)

SENSOR_TIMESTAMP_COLUMNS = tuple(f"{sensor}_timestamp" for sensor in SENSOR_NAMES)
SENSOR_STATUS_COLUMNS = tuple(f"{sensor}_ok" for sensor in SENSOR_NAMES)
REQUIRED_COLUMNS = BASE_COLUMNS + SENSOR_TIMESTAMP_COLUMNS + SENSOR_STATUS_COLUMNS


def load_sensor_log(source: str | Path | IO[bytes]) -> pd.DataFrame:
    """Load a sensor log CSV and normalize columns used by the analyzers."""
    frame = pd.read_csv(source)
    frame.columns = [column.strip() for column in frame.columns]
    return normalize_sensor_log(frame)


def normalize_sensor_log(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()

    for column in ("timestamp", *SENSOR_TIMESTAMP_COLUMNS):
        if column in normalized:
            normalized[column] = pd.to_datetime(normalized[column], errors="coerce")

    for column in ("speed_mps", "accel_mps2", "latitude", "longitude"):
        if column in normalized:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    for column in SENSOR_STATUS_COLUMNS:
        if column in normalized:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(0).astype(int)

    return normalized
