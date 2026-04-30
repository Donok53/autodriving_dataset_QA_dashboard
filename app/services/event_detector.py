import math

import pandas as pd

from app.models import AnomalySegment, DrivingEvent
from app.services.loader import SENSOR_NAMES


def detect_hard_acceleration_events(
    frame: pd.DataFrame,
    threshold_mps2: float = 3.0,
) -> list[DrivingEvent]:
    if "accel_mps2" not in frame.columns or "timestamp" not in frame.columns:
        return []

    events: list[DrivingEvent] = []
    event_rows = frame[frame["accel_mps2"] >= threshold_mps2]

    for _, row in event_rows.iterrows():
        value = round(float(row["accel_mps2"]), 2)
        events.append(
            DrivingEvent(
                event_type="hard_acceleration",
                timestamp=_format_timestamp(row["timestamp"]),
                severity="주의" if value < threshold_mps2 * 1.5 else "위험",
                description=f"급가속 이벤트가 감지되었습니다. accel={value}m/s^2",
                value=value,
            )
        )

    return events


def detect_hard_braking_events(
    frame: pd.DataFrame,
    threshold_mps2: float = -4.0,
) -> list[DrivingEvent]:
    if "accel_mps2" not in frame.columns or "timestamp" not in frame.columns:
        return []

    events: list[DrivingEvent] = []
    event_rows = frame[frame["accel_mps2"] <= threshold_mps2]

    for _, row in event_rows.iterrows():
        value = round(float(row["accel_mps2"]), 2)
        events.append(
            DrivingEvent(
                event_type="hard_braking",
                timestamp=_format_timestamp(row["timestamp"]),
                severity="주의" if value > threshold_mps2 * 1.5 else "위험",
                description=f"급제동 이벤트가 감지되었습니다. accel={value}m/s^2",
                value=value,
            )
        )

    return events


def _format_timestamp(value: pd.Timestamp) -> str:
    if pd.isna(value):
        return "unknown"
    return value.isoformat(timespec="milliseconds")
