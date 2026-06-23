from __future__ import annotations

import base64
from io import BytesIO
import math
import os
import shutil
import subprocess
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from rosbags.highlevel import AnyReader

from app.models import (
    AnalysisSummary,
    AnomalySegment,
    BagTopicProfile,
    CameraFramePreview,
    DrivingEvent,
    QualityMetric,
    SensorSyncStatus,
)


def _read_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(1, value)


EXPECTED_SENSORS = ("lidar", "imu", "camera", "gps", "vehicle_motion")
SENSOR_SORT_ORDER = {sensor: index for index, sensor in enumerate((*EXPECTED_SENSORS, "other"))}
MAX_BAG_MESSAGES = 500_000
MAX_EVENT_COUNT = 30
MAX_CAMERA_PREVIEW_FRAMES = _read_positive_int_env("MAX_CAMERA_PREVIEW_FRAMES", 60)
XAI_OVERLAY_TOPIC_HINTS = ("student_xai", "rich_overlay", "overlay", "vlm", "/xai")


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
    camera_frames: list[CameraFramePreview] = field(default_factory=list)


@dataclass(frozen=True)
class EncodedCameraImage:
    payload: bytes
    content_type: str
    width: int
    height: int
    encoding: str


ProgressCallback = Callable[[int, str], None]


def analyze_bag(
    path: Path,
    max_messages: int | None = MAX_BAG_MESSAGES,
    progress_callback: ProgressCallback | None = None,
    allow_reindex: bool = False,
    camera_frame_dir: Path | None = None,
    camera_frame_url_prefix: str | None = None,
    max_camera_frames: int | None = MAX_CAMERA_PREVIEW_FRAMES,
) -> AnalysisSummary:
    if not path.exists() or path.stat().st_size == 0:
        raise InvalidBagFileError("비어 있거나 존재하지 않는 bag 파일입니다.")

    try:
        _notify_progress(progress_callback, 15, "bag 메타데이터 읽는 중")
        try:
            read_result = read_bag(
                path,
                max_messages=max_messages,
                progress_callback=progress_callback,
                camera_frame_dir=camera_frame_dir,
                camera_frame_url_prefix=camera_frame_url_prefix,
                max_camera_frames=max_camera_frames,
            )
        except Exception as exc:
            if not allow_reindex or not _is_reindexable_bag_error(exc):
                raise
            _notify_progress(progress_callback, 20, "bag index 복구 중")
            _reindex_bag(path)
            _notify_progress(progress_callback, 30, "복구된 bag 메타데이터 읽는 중")
            read_result = read_bag(
                path,
                max_messages=max_messages,
                progress_callback=progress_callback,
                camera_frame_dir=camera_frame_dir,
                camera_frame_url_prefix=camera_frame_url_prefix,
                max_camera_frames=max_camera_frames,
            )
        _notify_progress(progress_callback, 90, "분석 결과 정리 중")
    except Exception as exc:
        raise InvalidBagFileError(f"bag 파일을 읽을 수 없습니다: {exc}") from exc

    return build_bag_summary(read_result)


def read_bag(
    path: Path,
    max_messages: int | None = MAX_BAG_MESSAGES,
    progress_callback: ProgressCallback | None = None,
    camera_frame_dir: Path | None = None,
    camera_frame_url_prefix: str | None = None,
    max_camera_frames: int | None = MAX_CAMERA_PREVIEW_FRAMES,
) -> BagReadResult:
    with AnyReader([path]) as reader:
        total_to_process = (
            min(int(reader.message_count), max_messages)
            if max_messages is not None
            else int(reader.message_count)
        )
        camera_preview_topic = _select_camera_preview_topic(reader.topics)
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
        camera_frames: list[CameraFramePreview] = []
        processed_count = 0

        for connection, timestamp, rawdata in reader.messages():
            message = None
            effective_timestamp = int(timestamp)
            should_decode_message = (
                connection.msgtype.endswith("/Imu")
                or "NavSatFix" in connection.msgtype
                or (
                    connection.topic == camera_preview_topic
                    and _can_collect_camera_frame(len(camera_frames), max_camera_frames)
                )
            )
            if should_decode_message:
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

            if (
                connection.topic == camera_preview_topic
                and message is not None
                and _can_collect_camera_frame(len(camera_frames), max_camera_frames)
            ):
                frame = _build_camera_frame_preview(
                    topic=connection.topic,
                    msgtype=connection.msgtype,
                    message=message,
                    timestamp_ns=effective_timestamp,
                    frame_index=len(camera_frames),
                    camera_frame_dir=camera_frame_dir,
                    camera_frame_url_prefix=camera_frame_url_prefix,
                )
                if frame is not None:
                    camera_frames.append(frame)

            processed_count += 1
            if processed_count == 1 or processed_count % 5000 == 0 or processed_count == total_to_process:
                percent = 15 + int((processed_count / max(total_to_process, 1)) * 70)
                _notify_progress(
                    progress_callback,
                    percent,
                    f"bag 메시지 분석 중 ({processed_count:,}/{total_to_process:,})",
                )
            if max_messages is not None and processed_count >= max_messages:
                break

        return BagReadResult(
            topic_series=sorted(topic_by_name.values(), key=_topic_series_sort_key),
            total_message_count=int(reader.message_count),
            processed_message_count=processed_count,
            start_time_ns=int(reader.start_time),
            end_time_ns=int(reader.end_time),
            imu_events=imu_events,
            gps_events=_detect_gps_jump_events(gps_points),
            camera_frames=camera_frames,
        )


def _is_reindexable_bag_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "bag index looks damaged" in message
        or "bag is not indexed" in message
        or "run rosbag reindex" in message
    )


def _reindex_bag(path: Path) -> None:
    rosbag_command = shutil.which("rosbag")
    if rosbag_command is None:
        raise InvalidBagFileError(
            "bag index가 손상되어 자동 복구가 필요하지만, 현재 실행 환경에서 rosbag 명령을 찾을 수 없습니다. "
            "로컬 ROS 환경에서 rosbag reindex를 실행한 뒤 다시 업로드해주세요."
        )

    backup_path = _reindex_backup_path(path)
    try:
        result = subprocess.run(
            [rosbag_command, "reindex", "--force", "--quiet", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=None,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise InvalidBagFileError(f"rosbag reindex에 실패했습니다: {detail}")
    finally:
        backup_path.unlink(missing_ok=True)


def _reindex_backup_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.orig{path.suffix}")


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
        camera_frames=read_result.camera_frames,
    )


def infer_sensor_category(topic: str, msgtype: str) -> str:
    text = f"{topic} {msgtype}".lower()
    if any(keyword in text for keyword in ("camera", "image", "compressed", "cam")):
        return "camera"
    if any(keyword in text for keyword in ("lidar", "velodyne", "ouster", "pointcloud", "pointcloud2", "points")):
        return "lidar"
    if "imu" in text:
        return "imu"
    if any(keyword in text for keyword in ("gps", "gnss", "navsat", "ublox", "fix")):
        return "gps"
    if _is_vehicle_motion_topic(topic, msgtype):
        return "vehicle_motion"
    return "other"


def _select_camera_preview_topic(topics) -> str | None:
    candidates = []
    for topic, info in topics.items():
        msgtype = str(info.msgtype)
        if msgtype not in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}:
            continue
        score = _camera_preview_topic_score(topic, msgtype)
        if score <= 0:
            continue
        candidates.append((score, int(info.msgcount), topic))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return candidates[0][2]


def _camera_preview_topic_score(topic: str, msgtype: str) -> int:
    text = f"{topic} {msgtype}".lower()
    if any(token in text for token in ("depth", "nearir", "range_image", "reflec", "signal", "theora")):
        return 0
    if "/camera" not in text and "camera" not in text and "image" not in msgtype.lower():
        return 0

    score = 10
    if any(token in text for token in XAI_OVERLAY_TOPIC_HINTS):
        score += 100
    if "rich_overlay" in text:
        score += 30
    if "student_xai" in text:
        score += 20
    if "color" in text or "rgb" in text:
        score += 30
    if "image_raw" in text:
        score += 10
    if "compressed" in msgtype.lower() or topic.endswith("/compressed"):
        score += 5
    return score


def _can_collect_camera_frame(current_count: int, max_camera_frames: int | None) -> bool:
    return max_camera_frames is None or current_count < max_camera_frames


def _build_camera_frame_preview(
    *,
    topic: str,
    msgtype: str,
    message,
    timestamp_ns: int,
    frame_index: int,
    camera_frame_dir: Path | None = None,
    camera_frame_url_prefix: str | None = None,
) -> CameraFramePreview | None:
    try:
        image = _encode_camera_image(message, msgtype)
    except Exception:
        return None

    image_url = None
    data_url = ""
    if camera_frame_dir is not None and camera_frame_url_prefix is not None:
        image_path = _write_camera_frame_file(camera_frame_dir, frame_index, image)
        image_url = f"{camera_frame_url_prefix.rstrip('/')}/{image_path.name}"
    else:
        data_url = _camera_image_data_url(image)

    return CameraFramePreview(
        topic=topic,
        timestamp=_format_ns(timestamp_ns),
        width=image.width,
        height=image.height,
        encoding=image.encoding,
        data_url=data_url,
        image_url=image_url,
    )


def _encode_camera_image(message, msgtype: str) -> EncodedCameraImage:
    if msgtype == "sensor_msgs/msg/CompressedImage":
        return _compressed_image_payload(message)
    return _raw_image_payload(message)


def _write_camera_frame_file(
    camera_frame_dir: Path,
    frame_index: int,
    image: EncodedCameraImage,
) -> Path:
    camera_frame_dir.mkdir(parents=True, exist_ok=True)
    extension = _camera_image_extension(image.content_type)
    image_path = camera_frame_dir / f"frame_{frame_index:06d}.{extension}"
    image_path.write_bytes(image.payload)
    return image_path


def _camera_image_extension(content_type: str) -> str:
    if content_type == "image/png":
        return "png"
    return "jpg"


def _camera_image_data_url(image: EncodedCameraImage) -> str:
    return "data:{};base64,{}".format(image.content_type, base64.b64encode(image.payload).decode("ascii"))


def _compressed_image_payload(message) -> EncodedCameraImage:
    raw = _message_data_bytes(message)
    content_type = _compressed_image_content_type(raw, str(getattr(message, "format", "")))
    width, height = _image_size_from_bytes(raw)
    return EncodedCameraImage(
        payload=raw,
        content_type=content_type,
        width=width,
        height=height,
        encoding=str(getattr(message, "format", "compressed")),
    )


def _raw_image_payload(message) -> EncodedCameraImage:
    from PIL import Image as PILImage
    import numpy as np

    width = int(getattr(message, "width", 0) or 0)
    height = int(getattr(message, "height", 0) or 0)
    step = int(getattr(message, "step", 0) or 0)
    encoding = str(getattr(message, "encoding", "") or "").lower()
    raw_array = np.asarray(getattr(message, "data"), dtype=np.uint8)

    if width <= 0 or height <= 0 or raw_array.size == 0:
        raise ValueError("empty image")

    channels = _encoding_channels(encoding)
    if channels <= 0:
        raise ValueError(f"unsupported image encoding: {encoding}")

    row_size = step if step > 0 else width * channels
    image_rows = raw_array.reshape(height, row_size)
    image_data = image_rows[:, : width * channels]

    if channels == 1:
        image = image_data.reshape(height, width)
        pil_image = PILImage.fromarray(image, mode="L")
    else:
        image = image_data.reshape(height, width, channels)
        if encoding in {"bgr8", "bgra8"}:
            image = image[:, :, [2, 1, 0, *([] if channels == 3 else [3])]]
        mode = "RGB" if channels == 3 else "RGBA"
        pil_image = PILImage.fromarray(image, mode=mode)

    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")

    buffer = BytesIO()
    pil_image.save(buffer, format="JPEG", quality=78)
    return EncodedCameraImage(
        payload=buffer.getvalue(),
        content_type="image/jpeg",
        width=width,
        height=height,
        encoding=encoding,
    )


def _encoding_channels(encoding: str) -> int:
    normalized = encoding.lower()
    if normalized in {"rgb8", "bgr8"}:
        return 3
    if normalized in {"rgba8", "bgra8"}:
        return 4
    if normalized in {"mono8", "8uc1"}:
        return 1
    return 0


def _message_data_bytes(message) -> bytes:
    data = getattr(message, "data")
    if isinstance(data, bytes):
        return data
    return bytes(data.tolist() if hasattr(data, "tolist") else data)


def _compressed_image_content_type(raw: bytes, image_format: str) -> str:
    lowered = image_format.lower()
    if "png" in lowered or raw.startswith(b"\x89PNG"):
        return "image/png"
    return "image/jpeg"


def _image_size_from_bytes(raw: bytes) -> tuple[int, int]:
    try:
        from PIL import Image as PILImage

        with PILImage.open(BytesIO(raw)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _topic_series_sort_key(series: BagTopicSeries) -> tuple[int, str]:
    return (SENSOR_SORT_ORDER.get(series.sensor, SENSOR_SORT_ORDER["other"]), series.topic)


def _notify_progress(
    progress_callback: ProgressCallback | None,
    progress: int,
    stage: str,
) -> None:
    if progress_callback is not None:
        progress_callback(progress, stage)


def _is_vehicle_motion_topic(topic: str, msgtype: str) -> bool:
    normalized_topic = topic.lower().replace("_", "/")
    text = f"{normalized_topic} {msgtype.lower()}"
    return any(
        keyword in text
        for keyword in (
            "cmd/vel",
            "vehicle/cmd",
            "vehicle/command",
            "control/cmd",
            "ackermann",
            "geometry_msgs/msg/twist",
        )
    )


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
            name="핵심 데이터 스트림 커버리지",
            status=_high_score_status(sensor_coverage),
            value=sensor_coverage,
            detail=f"{len(EXPECTED_SENSORS)}개 핵심 데이터 스트림 중 {len(present_sensors)}개 감지",
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
            description=f"{sensor} 계열 데이터가 bag 파일에서 감지되지 않았습니다.",
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
    for sensor in ("lidar", "camera", "imu", "gps", "vehicle_motion"):
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
        if current > previous
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
