from pathlib import Path

from app.services.event_detector import (
    detect_gps_jump_events,
    detect_hard_acceleration_events,
    detect_hard_braking_events,
    detect_sensor_dropout_segments,
)
from app.services.loader import load_sensor_log
from app.services.sync_checker import analyze_sensor_sync, detect_desync_segments

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "data" / "sample_sensor_log.csv"


def test_hard_acceleration_and_braking_events_are_detected():
    frame = load_sensor_log(SAMPLE_PATH)

    acceleration_events = detect_hard_acceleration_events(frame)
    braking_events = detect_hard_braking_events(frame)

    assert any(event.event_type == "hard_acceleration" for event in acceleration_events)
    assert any(event.event_type == "hard_braking" for event in braking_events)


def test_gps_jump_event_is_detected():
    frame = load_sensor_log(SAMPLE_PATH)

    events = detect_gps_jump_events(frame)

    assert events
    assert events[0].event_type == "gps_jump"
    assert events[0].value > 80


def test_sensor_dropout_segments_are_grouped():
    frame = load_sensor_log(SAMPLE_PATH)

    segments = detect_sensor_dropout_segments(frame)

    assert any(segment.category == "sensor_dropout" for segment in segments)
    assert any("lidar" in segment.description for segment in segments)


def test_sensor_sync_status_and_desync_segments_are_detected():
    frame = load_sensor_log(SAMPLE_PATH)

    statuses = analyze_sensor_sync(frame)
    segments = detect_desync_segments(frame)

    lidar = next(status for status in statuses if status.sensor == "lidar")
    assert lidar.status == "위험"
    assert any(segment.category == "sensor_desync" for segment in segments)
