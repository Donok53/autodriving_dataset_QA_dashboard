from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from rosbags.highlevel import AnyReader

from app.models import (
    AnalysisSummary,
    AnomalySegment,
    BagTopicProfile,
    DrivingEvent,
    QualityMetric,
    SensorSyncStatus,
)

EXPECTED_SENSORS = ("camera", "lidar", "radar", "imu", "gps")
MAX_BAG_MESSAGES = 500_000
MAX_EVENT_COUNT = 30


class InvalidBagFileError(ValueError):
    pass


@dataclass
class BagTopicSeries:
    topic: str
    msgtype: str
    sensor: str
    message_count: int
    timestamps_ns: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class BagReadResult:
    topic_series: list[BagTopicSeries]
    total_message_count: int
    processed_message_count: int
    start_time_ns: int
    end_time_ns: int
    imu_events: list[DrivingEvent]
    gps_events: list[DrivingEvent]


def analyze_bag(path: Path, max_messages: int = MAX_BAG_MESSAGES) -> AnalysisSummary:
    if not path.exists() or path.stat().st_size == 0:
        raise InvalidBagFileError("비어 있거나 존재하지 않는 bag 파일입니다.")

    try:
        read_result = read_bag(path, max_messages=max_messages)
    except Exception as exc:
        raise InvalidBagFileError(f"bag 파일을 읽을 수 없습니다: {exc}") from exc

    return build_bag_summary(read_result)


def read_bag(path: Path, max_messages: int = MAX_BAG_MESSAGES) -> BagReadResult:
    with AnyReader([path]) as reader:
        topic_by_name = {
            topic: BagTopicSeries(
                topic=topic,
                msgtype=info.msgtype,
                sensor=infer_sensor_category(topic, info.msgtype),
                message_count=info.msgcount,
            )
            for topic, info in reader.topics.items()
        }
        imu_events: list[DrivingEvent] = []
        gps_points: list[tuple[int, float, float]] = []
        processed_count = 0

        for connection, timestamp, rawdata in reader.messages():
            message = None
            effective_timestamp = int(timestamp)
            if connection.msgtype.endswith("/Imu") or "NavSatFix" in connection.msgtype:
                message = _deserialize_message(reader, connection.msgtype, rawdata)
                if message is not None:
                    effective_timestamp = _message_timestamp_ns(message, int(timestamp))

            topic_series = topic_by_name.get(connection.topic)
            if topic_series is not None:
                topic_series.timestamps_ns.append(effective_timestamp)

            if connection.msgtype.endswith("/Imu") and message is not None and len(imu_events) < MAX_EVENT_COUNT:
                event = _detect_imu_acceleration_event(message, effective_timestamp)
                if event is not None:
                    imu_events.append(event)

            if "NavSatFix" in connection.msgtype and message is not None and len(gps_points) < 50_000:
                point = _read_gps_point(message, effective_timestamp)
                if point is not None:
                    gps_points.append(point)

            processed_count += 1
            if processed_count >= max_messages:
                break

        return BagReadResult(
            topic_series=sorted(topic_by_name.values(), key=lambda series: series.topic),
            total_message_count=int(reader.message_count),
            processed_message_count=processed_count,
            start_time_ns=int(reader.start_time),
            end_time_ns=int(reader.end_time),
            imu_events=imu_events,
            gps_events=_detect_gps_jump_events(gps_points),
        )


def build_bag_summary(read_result: BagReadResult) -> AnalysisSummary:
    profiles = [_build_topic_profile(series) for series in read_result.topic_series]
    metrics = _build_bag_quality_metrics(read_result, profiles)
    anomalies = [
        *_detect_topic_gap_segments(read_result.topic_series),
        *_detect_missing_sensor_segments(read_result.topic_series, read_result.start_time_ns, read_result.end_time_ns),
    ]
    sync_statuses = _analyze_bag_sync(read_result.topic_series)
    events = [*read_result.imu_events, *read_result.gps_events]

    return AnalysisSummary(
        total_rows=read_result.total_message_count,
        duration_seconds=_duration_seconds(read_result.start_time_ns, read_result.end_time_ns),
        quality_score=_bag_quality_score(metrics, sync_statuses, anomalies),
        metrics=metrics,
        sync_statuses=sync_statuses,
        anomalies=anomalies,
        events=events,
        source_type="bag",
        topic_profiles=profiles,
    )


def infer_sensor_category(topic: str, msgtype: str) -> str:
    text = f"{topic} {msgtype}".lower()
    if any(keyword in text for keyword in ("camera", "image", "compressed", "cam")):
        return "camera"
    if any(keyword in text for keyword in ("lidar", "velodyne", "ouster", "pointcloud", "pointcloud2", "points")):
        return "lidar"
    if "radar" in text:
        return "radar"
    if "imu" in text:
        return "imu"
    if any(keyword in text for keyword in ("gps", "gnss", "navsat", "ublox", "fix")):
        return "gps"
    return "other"


def _build_topic_profile(series: BagTopicSeries) -> BagTopicProfile:
    intervals_ms = _intervals_ms(series.timestamps_ns)
    duration_seconds = _duration_seconds(_first_or_zero(series.timestamps_ns), _last_or_zero(series.timestamps_ns))
    frequency_hz = round((len(series.timestamps_ns) - 1) / duration_seconds, 2) if duration_seconds > 0 else 0.0

    return BagTopicProfile(
        topic=series.topic,
        sensor=series.sensor,
        msgtype=series.msgtype,
        message_count=series.message_count,
        frequency_hz=frequency_hz,
        median_period_ms=round(float(median(intervals_ms)), 2) if intervals_ms else 0.0,
        max_gap_ms=round(float(max(intervals_ms)), 2) if intervals_ms else 0.0,
    )


def _build_bag_quality_metrics(
    read_result: BagReadResult,
    profiles: list[BagTopicProfile],
) -> list[QualityMetric]:
    present_sensors = {
        series.sensor for series in read_result.topic_series if series.sensor in EXPECTED_SENSORS
    }
    sensor_coverage = round((len(present_sensors) / len(EXPECTED_SENSORS)) * 100, 2)
    processed_ratio = (
        round((read_result.processed_message_count / read_result.total_message_count) * 100, 2)
        if read_result.total_message_count
        else 0.0
    )
    healthy_topics = [
        profile for profile in profiles if profile.max_gap_ms <= _topic_gap_threshold_ms(profile.median_period_ms)
    ]
    topic_health = round((len(healthy_topics) / len(profiles)) * 100, 2) if profiles else 0.0

    return [
        QualityMetric(
            name="센서 토픽 커버리지",
            status=_high_score_status(sensor_coverage),
            value=sensor_coverage,
            detail=f"{len(EXPECTED_SENSORS)}개 핵심 센서 중 {len(present_sensors)}개 감지",
        ),
        QualityMetric(
            name="bag 메시지 처리율",
            status=_high_score_status(processed_ratio),
            value=processed_ratio,
            detail=f"{read_result.total_message_count}개 메시지 중 {read_result.processed_message_count}개 분석",
        ),
        QualityMetric(
            name="topic 주기 안정성",
            status=_high_score_status(topic_health),
            value=topic_health,
            detail=f"{len(profiles)}개 토픽 중 {len(healthy_topics)}개가 안정적인 주기",
        ),
    ]


def _detect_topic_gap_segments(series_list: list[BagTopicSeries]) -> list[AnomalySegment]:
    segments: list[AnomalySegment] = []

    for series in series_list:
        intervals_ms = _intervals_ms(series.timestamps_ns)
        if not intervals_ms:
            continue

        median_period_ms = float(median(intervals_ms))
        threshold_ms = _topic_gap_threshold_ms(median_period_ms)
        indexed_intervals = list(enumerate(intervals_ms, start=1))
        gap_candidates = sorted(indexed_intervals, key=lambda item: item[1], reverse=True)

        for index, gap_ms in gap_candidates[:3]:
            if gap_ms <= threshold_ms:
                continue
            segments.append(
                AnomalySegment(
                    category="topic_gap",
                    start=_format_ns(series.timestamps_ns[index - 1]),
                    end=_format_ns(series.timestamps_ns[index]),
                    severity="주의" if gap_ms < threshold_ms * 2 else "위험",
                    description=f"{series.topic} 토픽에서 {round(gap_ms, 2)}ms gap이 감지되었습니다.",
                )
            )

    return segments


def _detect_missing_sensor_segments(
    series_list: list[BagTopicSeries],
    start_time_ns: int,
    end_time_ns: int,
) -> list[AnomalySegment]:
    present_sensors = {series.sensor for series in series_list if series.sensor in EXPECTED_SENSORS}
    return [
        AnomalySegment(
            category="missing_sensor",
            start=_format_ns(start_time_ns),
            end=_format_ns(end_time_ns),
            severity="위험",
            description=f"{sensor} 계열 토픽이 bag 파일에서 감지되지 않았습니다.",
        )
        for sensor in EXPECTED_SENSORS
        if sensor not in present_sensors
    ]


def _analyze_bag_sync(series_list: list[BagTopicSeries]) -> list[SensorSyncStatus]:
    timestamps_by_sensor = _timestamps_by_sensor(series_list)
    reference_sensor = _select_reference_sensor(timestamps_by_sensor)
    reference_timestamps = timestamps_by_sensor.get(reference_sensor, [])

    statuses: list[SensorSyncStatus] = []
    for sensor in EXPECTED_SENSORS:
        timestamps = timestamps_by_sensor.get(sensor, [])
        if not timestamps:
            statuses.append(
                SensorSyncStatus(
                    sensor=sensor,
                    status="위험",
                    mean_offset_ms=0.0,
                    max_offset_ms=0.0,
                    missing_count=1,
                )
            )
            continue

        if sensor == reference_sensor or not reference_timestamps:
            statuses.append(
                SensorSyncStatus(
                    sensor=sensor,
                    status="정상",
                    mean_offset_ms=0.0,
                    max_offset_ms=0.0,
                    missing_count=0,
                )
            )
            continue

        offsets = _nearest_offsets_ms(_downsample(timestamps), reference_timestamps)
        mean_offset = round(sum(offsets) / len(offsets), 2) if offsets else 0.0
        max_offset = round(max(offsets), 2) if offsets else 0.0

        if max_offset >= 500:
            status = "위험"
        elif max_offset >= 100:
            status = "주의"
        else:
            status = "정상"

        statuses.append(
            SensorSyncStatus(
                sensor=sensor,
                status=status,
                mean_offset_ms=mean_offset,
                max_offset_ms=max_offset,
                missing_count=0,
            )
        )

    return statuses


def _timestamps_by_sensor(series_list: list[BagTopicSeries]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {sensor: [] for sensor in EXPECTED_SENSORS}
    for series in series_list:
        if series.sensor in grouped:
            grouped[series.sensor].extend(series.timestamps_ns)

    return {sensor: sorted(timestamps) for sensor, timestamps in grouped.items() if timestamps}


def _select_reference_sensor(timestamps_by_sensor: dict[str, list[int]]) -> str:
    for sensor in ("lidar", "camera", "imu", "gps", "radar"):
        if sensor in timestamps_by_sensor:
            return sensor
    return "unknown"


def _nearest_offsets_ms(timestamps: list[int], reference_timestamps: list[int]) -> list[float]:
    offsets: list[float] = []
    for timestamp in timestamps:
        insert_at = bisect_left(reference_timestamps, timestamp)
        candidates = []
        if insert_at < len(reference_timestamps):
            candidates.append(abs(reference_timestamps[insert_at] - timestamp))
        if insert_at > 0:
            candidates.append(abs(reference_timestamps[insert_at - 1] - timestamp))
        if candidates:
            offsets.append(min(candidates) / 1_000_000)
    return offsets


def _deserialize_message(reader, msgtype: str, rawdata: bytes):
    try:
        return reader.deserialize(rawdata, msgtype)
    except Exception:
        return None


def _detect_imu_acceleration_event(message, timestamp_ns: int) -> DrivingEvent | None:
    try:
        acceleration = message.linear_acceleration
        lateral_accel = math.sqrt(float(acceleration.x) ** 2 + float(acceleration.y) ** 2)
    except Exception:
        return None

    if lateral_accel < 3.0:
        return None

    value = round(lateral_accel, 2)
    return DrivingEvent(
        event_type="bag_imu_acceleration",
        timestamp=_format_ns(_message_timestamp_ns(message, timestamp_ns)),
        severity="주의" if value < 5.0 else "위험",
        description=f"IMU 수평 가속도 {value}m/s^2가 감지되었습니다.",
        value=value,
    )


def _read_gps_point(message, timestamp_ns: int) -> tuple[int, float, float] | None:
    try:
        latitude = float(message.latitude)
        longitude = float(message.longitude)
    except Exception:
        return None

    if math.isnan(latitude) or math.isnan(longitude):
        return None

    return (_message_timestamp_ns(message, timestamp_ns), latitude, longitude)


def _detect_gps_jump_events(points: list[tuple[int, float, float]], threshold_meters: float = 80.0) -> list[DrivingEvent]:
    events: list[DrivingEvent] = []
    points = sorted(points, key=lambda point: point[0])

    for previous, current in zip(points, points[1:]):
        distance = _haversine_meters(previous[1], previous[2], current[1], current[2])
        if distance < threshold_meters:
            continue

        value = round(distance, 2)
        events.append(
            DrivingEvent(
                event_type="bag_gps_jump",
                timestamp=_format_ns(current[0]),
                severity="주의" if value < threshold_meters * 3 else "위험",
                description=f"GPS 위치가 직전 메시지 대비 {value}m 이동했습니다.",
                value=value,
            )
        )
        if len(events) >= MAX_EVENT_COUNT:
            break

    return events


def _message_timestamp_ns(message, fallback_timestamp_ns: int) -> int:
    header = getattr(message, "header", None)
    stamp = getattr(header, "stamp", None)
    if stamp is None:
        return fallback_timestamp_ns

    nanoseconds = getattr(stamp, "nanosec", getattr(stamp, "nsec", 0))
    return int(stamp.sec) * 1_000_000_000 + int(nanoseconds)


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


def _bag_quality_score(
    metrics: list[QualityMetric],
    sync_statuses: list[SensorSyncStatus],
    anomalies: list[AnomalySegment],
) -> float:
    score = 100.0
    for metric in metrics:
        score -= (100 - metric.value) * 0.25

    for status in sync_statuses:
        if status.status == "위험":
            score -= 7
        elif status.status == "주의":
            score -= 3

    score -= min(len(anomalies) * 2, 20)
    return round(max(score, 0.0), 2)


def _high_score_status(value: float) -> str:
    if value >= 90:
        return "정상"
    if value >= 70:
        return "주의"
    return "위험"


def _topic_gap_threshold_ms(median_period_ms: float) -> float:
    if median_period_ms <= 0:
        return 0.0
    return min(max(median_period_ms * 3, 50.0), 3_000.0)


def _intervals_ms(timestamps_ns: list[int]) -> list[float]:
    if len(timestamps_ns) < 2:
        return []
    timestamps = sorted(timestamps_ns)
    return [
        (current - previous) / 1_000_000
        for previous, current in zip(timestamps, timestamps[1:])
        if current >= previous
    ]


def _duration_seconds(start_time_ns: int, end_time_ns: int) -> float:
    if end_time_ns <= start_time_ns:
        return 0.0
    return round((end_time_ns - start_time_ns) / 1_000_000_000, 2)


def _format_ns(timestamp_ns: int) -> str:
    if timestamp_ns <= 0:
        return "unknown"
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc).isoformat(timespec="milliseconds")


def _downsample(timestamps: list[int], limit: int = 2_000) -> list[int]:
    if len(timestamps) <= limit:
        return timestamps
    step = max(len(timestamps) // limit, 1)
    return timestamps[::step][:limit]


def _first_or_zero(values: list[int]) -> int:
    return values[0] if values else 0


def _last_or_zero(values: list[int]) -> int:
    return values[-1] if values else 0
