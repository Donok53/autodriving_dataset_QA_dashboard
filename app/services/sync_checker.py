import pandas as pd

from app.models import AnomalySegment, SensorSyncStatus
from app.services.loader import SENSOR_NAMES


def analyze_sensor_sync(
    frame: pd.DataFrame,
    warning_ms: int = 50,
    critical_ms: int = 120,
) -> list[SensorSyncStatus]:
    statuses: list[SensorSyncStatus] = []

    for sensor in SENSOR_NAMES:
        offsets = _sensor_offsets(frame, sensor)
        missing_count = int(offsets.isna().sum())
        valid_offsets = offsets.dropna()

        mean_offset_ms = round(float(valid_offsets.mean()), 2) if not valid_offsets.empty else 0.0
        max_offset_ms = round(float(valid_offsets.max()), 2) if not valid_offsets.empty else 0.0

        if missing_count > 0 or max_offset_ms >= critical_ms:
            status = "위험"
        elif max_offset_ms >= warning_ms:
            status = "주의"
        else:
            status = "정상"

        statuses.append(
            SensorSyncStatus(
                sensor=sensor,
                status=status,
                mean_offset_ms=mean_offset_ms,
                max_offset_ms=max_offset_ms,
                missing_count=missing_count,
            )
        )

    return statuses


def detect_desync_segments(
    frame: pd.DataFrame,
    critical_ms: int = 120,
) -> list[AnomalySegment]:
    segments: list[AnomalySegment] = []

    for sensor in SENSOR_NAMES:
        offsets = _sensor_offsets(frame, sensor)
        desynced_rows = frame[offsets >= critical_ms]

        for _, row in desynced_rows.iterrows():
            offset_ms = round(float(abs(row[f"{sensor}_timestamp"] - row["timestamp"]).total_seconds() * 1000), 2)
            timestamp = row["timestamp"].isoformat(timespec="milliseconds")
            segments.append(
                AnomalySegment(
                    category="sensor_desync",
                    start=timestamp,
                    end=timestamp,
                    severity="위험",
                    description=f"{sensor} 센서가 기준 timestamp와 {offset_ms}ms 어긋났습니다.",
                )
            )

    return segments


def _sensor_offsets(frame: pd.DataFrame, sensor: str) -> pd.Series:
    sensor_column = f"{sensor}_timestamp"
    if "timestamp" not in frame.columns or sensor_column not in frame.columns:
        return pd.Series([pd.NA] * len(frame), dtype="Float64")

    return (frame[sensor_column] - frame["timestamp"]).abs().dt.total_seconds() * 1000
