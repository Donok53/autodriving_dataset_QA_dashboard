from pathlib import Path

import pandas as pd

from app.services.loader import load_sensor_log
from app.services.quality_checker import (
    check_duplicate_timestamps,
    check_missing_values,
    check_sampling_gaps,
    detect_sampling_gap_segments,
)
from app.services.schema import validate_sensor_schema

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "data" / "sample_sensor_log.csv"


def test_sample_log_schema_is_valid():
    frame = load_sensor_log(SAMPLE_PATH)

    report = validate_sensor_schema(frame)

    assert report.is_valid
    assert report.missing_columns == []


def test_missing_value_metric_detects_sample_nulls():
    frame = load_sensor_log(SAMPLE_PATH)

    metric = check_missing_values(frame)

    assert metric.value > 0
    assert "결측" in metric.detail


def test_duplicate_timestamp_metric_detects_duplicates():
    frame = load_sensor_log(SAMPLE_PATH)

    metric = check_duplicate_timestamps(frame)

    assert metric.value > 0
    assert metric.status in {"주의", "위험"}


def test_sampling_gap_metric_and_segments_detect_long_gap():
    frame = load_sensor_log(SAMPLE_PATH)

    metric = check_sampling_gaps(frame)
    segments = detect_sampling_gap_segments(frame)

    assert metric.value > 0
    assert any(segment.category == "sampling_gap" for segment in segments)


def test_schema_validation_reports_missing_columns():
    frame = pd.DataFrame({"timestamp": ["2026-04-01T09:00:00.000"]})

    report = validate_sensor_schema(frame)

    assert not report.is_valid
    assert "speed_mps" in report.missing_columns
