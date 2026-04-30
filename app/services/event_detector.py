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


def detect_gps_jump_events(
    frame: pd.DataFrame,
    threshold_meters: float = 80.0,
) -> list[DrivingEvent]:
    required_columns = {"timestamp", "latitude", "longitude"}
    if not required_columns.issubset(frame.columns):
        return []

    gps_frame = (
        frame.dropna(subset=["timestamp", "latitude", "longitude"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    events: list[DrivingEvent] = []

    for index in range(1, len(gps_frame)):
        previous = gps_frame.iloc[index - 1]
        current = gps_frame.iloc[index]
        distance = _haversine_meters(
            previous["latitude"],
            previous["longitude"],
            current["latitude"],
            current["longitude"],
        )

        if distance >= threshold_meters:
            value = round(float(distance), 2)
            events.append(
                DrivingEvent(
                    event_type="gps_jump",
                    timestamp=_format_timestamp(current["timestamp"]),
                    severity="주의" if value < threshold_meters * 3 else "위험",
                    description=f"GPS 위치가 직전 로그 대비 {value}m 이동했습니다.",
                    value=value,
                )
            )

    return events


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_meters = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    square_half_chord = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    angular_distance = 2 * math.atan2(math.sqrt(square_half_chord), math.sqrt(1 - square_half_chord))
    return radius_meters * angular_distance


def _format_timestamp(value: pd.Timestamp) -> str:
    if pd.isna(value):
        return "unknown"
    return value.isoformat(timespec="milliseconds")
