from pathlib import Path

from app.services.bag_analyzer import analyze_bag


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def test_sample_no_gps_bag_detects_missing_gps():
    summary = analyze_bag(DATA_DIR / "sample_no_gps_5s.bag")

    missing_descriptions = [
        anomaly.description
        for anomaly in summary.anomalies
        if anomaly.category == "missing_sensor"
    ]

    assert summary.source_type == "bag"
    assert summary.duration_seconds == 5.0
    assert "gps 계열 데이터가 bag 파일에서 감지되지 않았습니다." in missing_descriptions


def test_sample_no_vehicle_motion_bag_detects_missing_vehicle_motion():
    summary = analyze_bag(DATA_DIR / "sample_no_vehicle_motion_5s.bag")

    missing_descriptions = [
        anomaly.description
        for anomaly in summary.anomalies
        if anomaly.category == "missing_sensor"
    ]

    assert summary.source_type == "bag"
    assert summary.duration_seconds == 5.0
    assert "vehicle_motion 계열 데이터가 bag 파일에서 감지되지 않았습니다." in missing_descriptions
