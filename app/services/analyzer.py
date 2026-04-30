from pathlib import Path
from typing import IO

import pandas as pd

from app.models import AnalysisSummary, QualityMetric
from app.services.event_detector import (
    detect_gps_jump_events,
    detect_hard_acceleration_events,
    detect_hard_braking_events,
    detect_sensor_dropout_segments,
)
from app.services.loader import load_sensor_log
from app.services.quality_checker import (
    check_duplicate_timestamps,
    check_missing_values,
    check_sampling_gaps,
    detect_sampling_gap_segments,
)
from app.services.schema import validate_sensor_schema
from app.services.sync_checker import analyze_sensor_sync, detect_desync_segments


class InvalidSensorLogError(ValueError):
    pass


def analyze_csv(source: str | Path | IO[bytes]) -> AnalysisSummary:
    frame = load_sensor_log(source)
    return analyze_frame(frame)


def analyze_frame(frame: pd.DataFrame) -> AnalysisSummary:
    schema_report = validate_sensor_schema(frame)
    if not schema_report.is_valid:
        missing = ", ".join(schema_report.missing_columns)
        raise InvalidSensorLogError(f"필수 컬럼이 누락되었습니다: {missing}")

    metrics = [
        check_missing_values(frame),
        check_duplicate_timestamps(frame),
        check_sampling_gaps(frame),
    ]
    sync_statuses = analyze_sensor_sync(frame)
    anomalies = [
        *detect_sampling_gap_segments(frame),
        *detect_desync_segments(frame),
        *detect_sensor_dropout_segments(frame),
    ]
    events = [
        *detect_hard_acceleration_events(frame),
        *detect_hard_braking_events(frame),
        *detect_gps_jump_events(frame),
    ]

    return AnalysisSummary(
        total_rows=len(frame),
        duration_seconds=_duration_seconds(frame),
        quality_score=_quality_score(metrics, sync_statuses, anomalies),
        metrics=metrics,
        sync_statuses=sync_statuses,
        anomalies=anomalies,
        events=events,
    )


def _duration_seconds(frame: pd.DataFrame) -> float:
    if "timestamp" not in frame.columns:
        return 0.0
    timestamps = frame["timestamp"].dropna()
    if timestamps.empty:
        return 0.0
    return round(float((timestamps.max() - timestamps.min()).total_seconds()), 2)


def _quality_score(metrics: list[QualityMetric], sync_statuses: list, anomalies: list) -> float:
    score = 100.0

    for metric in metrics:
        if metric.status == "위험":
            score -= 15 + metric.value
        elif metric.status == "주의":
            score -= 5 + metric.value * 0.5

    for status in sync_statuses:
        if status.status == "위험":
            score -= 7
        elif status.status == "주의":
            score -= 3

    score -= min(len(anomalies) * 2, 20)
    return round(max(score, 0.0), 2)
