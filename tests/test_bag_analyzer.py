from types import SimpleNamespace

from app.models import CameraFramePreview, DrivingEvent
from app.services.bag_analyzer import (
    BagReadResult,
    BagTopicSeries,
    _select_camera_preview_topic,
    analyze_bag,
    build_bag_summary,
    infer_sensor_category,
)


def test_infer_sensor_category_from_topic_and_msgtype():
    assert infer_sensor_category("/camera/front/image_raw", "sensor_msgs/msg/Image") == "camera"
    assert infer_sensor_category("/ouster/points", "sensor_msgs/msg/PointCloud2") == "lidar"
    assert infer_sensor_category("/imu/data", "sensor_msgs/msg/Imu") == "imu"
    assert infer_sensor_category("/ublox/fix", "sensor_msgs/msg/NavSatFix") == "gps"
    assert infer_sensor_category("/cmd_vel", "geometry_msgs/msg/Twist") == "vehicle_motion"
    assert infer_sensor_category("/cmd/vel", "geometry_msgs/msg/TwistStamped") == "vehicle_motion"
    assert infer_sensor_category("/diagnostics", "diagnostic_msgs/msg/DiagnosticArray") == "other"


def test_select_camera_preview_topic_prefers_xai_overlay():
    topics = {
        "/camera/color/image_raw": SimpleNamespace(msgtype="sensor_msgs/msg/Image", msgcount=300),
        "/student_xai/rich_overlay": SimpleNamespace(msgtype="sensor_msgs/msg/Image", msgcount=30),
        "/camera/depth/image_raw": SimpleNamespace(msgtype="sensor_msgs/msg/Image", msgcount=300),
    }

    assert _select_camera_preview_topic(topics) == "/student_xai/rich_overlay"


def test_build_bag_summary_summarizes_xai_records():
    base = 1_700_000_000_000_000_000
    summary = build_bag_summary(
        BagReadResult(
            topic_series=[
                BagTopicSeries(
                    topic="/xai/vlm_log",
                    msgtype="std_msgs/msg/String",
                    sensor="other",
                    message_count=2,
                    timestamps_ns=[base, base + 100_000_000],
                )
            ],
            total_message_count=2,
            processed_message_count=2,
            start_time_ns=base,
            end_time_ns=base + 100_000_000,
            imu_events=[],
            gps_events=[],
            camera_frames=[
                CameraFramePreview(
                    topic="/camera/color/image_raw",
                    timestamp="2023-11-14T22:13:20.050+00:00",
                    width=640,
                    height=480,
                    encoding="rgb8",
                    data_url="data:image/jpeg;base64,test",
                )
            ],
            xai_records=[
                {
                    "_topic": "/xai/vlm_log",
                    "_timestamp": "2023-11-14T22:13:20.000+00:00",
                    "model_name": "xai_student_model",
                    "model_version": "v2",
                    "event_label": "avoidance",
                    "driving_mode_ko": "좌측 회피",
                    "driving_reason_ko": "전방 장애물을 피해 좌측 회피한다.",
                },
                {
                    "_topic": "/xai/vlm_log",
                    "_timestamp": "2023-11-14T22:13:20.100+00:00",
                    "event_label": "safety_stop",
                    "driving_mode_ko": "안전모드 정지",
                    "driving_reason_ko": "안전모드가 활성화되어 정지한다.",
                },
            ],
        )
    )

    payload = summary.to_dict()

    assert payload["xai_summary"]["total_explanations"] == 2
    assert payload["xai_summary"]["avoidance_count"] == 1
    assert payload["xai_summary"]["safety_stop_count"] == 1
    assert payload["xai_summary"]["topics"] == ["/xai/vlm_log"]
    assert payload["xai_summary"]["model"]["version"] == "v2"
    assert payload["camera_frames"][0]["xai_overlay"]["driving_mode_ko"] == "좌측 회피"
    assert "전방 장애물" in payload["camera_frames"][0]["xai_overlay"]["explanation"]


def test_build_bag_summary_generates_xai_log_from_topics_when_missing():
    base = 1_700_000_000_000_000_000
    summary = build_bag_summary(
        BagReadResult(
            topic_series=[
                BagTopicSeries(
                    topic="/camera/color/image_raw",
                    msgtype="sensor_msgs/msg/Image",
                    sensor="camera",
                    message_count=2,
                    timestamps_ns=[base, base + 100_000_000],
                ),
                BagTopicSeries(
                    topic="/imu/data",
                    msgtype="sensor_msgs/msg/Imu",
                    sensor="imu",
                    message_count=2,
                    timestamps_ns=[base, base + 100_000_000],
                ),
            ],
            total_message_count=4,
            processed_message_count=4,
            start_time_ns=base,
            end_time_ns=base + 100_000_000,
            imu_events=[],
            gps_events=[],
            camera_frames=[
                CameraFramePreview(
                    topic="/camera/color/image_raw",
                    timestamp="2023-11-14T22:13:20.050+00:00",
                    width=640,
                    height=480,
                    encoding="rgb8",
                    data_url="data:image/jpeg;base64,test",
                )
            ],
        )
    )

    payload = summary.to_dict()

    assert payload["xai_summary"]["topics"] == ["/xai/vlm_log"]
    assert payload["xai_summary"]["model"]["model_name"] == "dashboard_topic_xai"
    assert payload["xai_summary"]["total_explanations"] >= 1
    assert payload["camera_frames"][0]["xai_overlay"]["source_topic"]
    assert payload["camera_frames"][0]["xai_overlay"]["explanation"]


def test_build_bag_summary_detects_topic_gap_and_missing_sensor():
    base = 1_700_000_000_000_000_000
    summary = build_bag_summary(
        BagReadResult(
            topic_series=[
                BagTopicSeries(
                    topic="/ouster/points",
                    msgtype="sensor_msgs/msg/PointCloud2",
                    sensor="lidar",
                    message_count=5,
                    timestamps_ns=[
                        base,
                        base + 100_000_000,
                        base + 200_000_000,
                        base + 800_000_000,
                        base + 900_000_000,
                    ],
                ),
                BagTopicSeries(
                    topic="/imu/data",
                    msgtype="sensor_msgs/msg/Imu",
                    sensor="imu",
                    message_count=10,
                    timestamps_ns=[base + index * 10_000_000 for index in range(10)],
                ),
                BagTopicSeries(
                    topic="/ublox/fix",
                    msgtype="sensor_msgs/msg/NavSatFix",
                    sensor="gps",
                    message_count=2,
                    timestamps_ns=[base, base + 1_000_000_000],
                ),
                BagTopicSeries(
                    topic="/cmd_vel",
                    msgtype="geometry_msgs/msg/Twist",
                    sensor="vehicle_motion",
                    message_count=5,
                    timestamps_ns=[base + index * 100_000_000 for index in range(5)],
                ),
            ],
            total_message_count=27,
            processed_message_count=27,
            start_time_ns=base,
            end_time_ns=base + 1_000_000_000,
            imu_events=[
                DrivingEvent(
                    event_type="bag_imu_acceleration",
                    timestamp="2023-11-14T22:13:20.000+00:00",
                    severity="주의",
                    description="IMU 수평 가속도 3.2m/s^2가 감지되었습니다.",
                    value=3.2,
                )
            ],
            gps_events=[],
        )
    )

    payload = summary.to_dict()

    assert payload["source_type"] == "bag"
    assert payload["total_rows"] == 27
    assert len(payload["topic_profiles"]) == 4
    assert [profile["sensor"] for profile in payload["topic_profiles"]] == [
        "lidar",
        "imu",
        "gps",
        "vehicle_motion",
    ]
    assert [status["sensor"] for status in payload["sync_statuses"]] == [
        "lidar",
        "imu",
        "camera",
        "gps",
        "vehicle_motion",
    ]
    assert any(anomaly["category"] == "topic_gap" for anomaly in payload["anomalies"])
    assert any("camera" in anomaly["description"] for anomaly in payload["anomalies"])
    assert any(status["sensor"] == "camera" and status["status"] == "위험" for status in payload["sync_statuses"])
    assert payload["events"][0]["event_type"] == "bag_imu_acceleration"


def test_analyze_bag_reindexes_damaged_upload_copy(tmp_path, monkeypatch):
    bag_path = tmp_path / "damaged.bag"
    bag_path.write_bytes(b"bag")
    calls = {"read": 0, "reindex": 0}

    def fake_read_bag(path, max_messages, progress_callback, **kwargs):
        calls["read"] += 1
        if calls["read"] == 1:
            raise RuntimeError("Bag index looks damaged")
        return BagReadResult(
            topic_series=[],
            total_message_count=0,
            processed_message_count=0,
            start_time_ns=1_700_000_000_000_000_000,
            end_time_ns=1_700_000_001_000_000_000,
            imu_events=[],
            gps_events=[],
            camera_frames=[],
        )

    def fake_reindex(path):
        calls["reindex"] += 1
        assert path == bag_path

    monkeypatch.setattr("app.services.bag_analyzer.read_bag", fake_read_bag)
    monkeypatch.setattr("app.services.bag_analyzer._reindex_bag", fake_reindex)

    summary = analyze_bag(bag_path, allow_reindex=True)

    assert summary.source_type == "bag"
    assert calls == {"read": 2, "reindex": 1}
