from app.models import DrivingEvent
from app.services.bag_analyzer import (
    BagReadResult,
    BagTopicSeries,
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
