from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import warnings

import numpy as np
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from app.services.model_service import ModelUnavailableError, get_model_info, load_student_model
from app.services.xai_log_analyzer import analyze_xai_log


def _read_nonnegative_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(0, value)


SERVER_VLM_ENABLED = os.getenv("SERVER_VLM_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
SERVER_VLM_MAX_FRAMES = _read_nonnegative_int_env("SERVER_VLM_MAX_FRAMES", 0)
SERVER_VLM_FLOW_IMAGE_SIDE_PX = _read_nonnegative_int_env("SERVER_VLM_FLOW_IMAGE_SIDE_PX", 320)
SERVER_VLM_JPEG_QUALITY = max(1, min(100, _read_nonnegative_int_env("SERVER_VLM_JPEG_QUALITY", 88)))
SERVER_VLM_TOPIC = "/xai/vlm_log"


class ServerVlmUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class VlmFrameResult:
    overlay_bgr: np.ndarray
    record: dict[str, Any]


ProgressCallback = Any


def apply_server_vlm_to_summary(
    summary: dict[str, Any],
    *,
    frame_dir: Path,
    frame_url_prefix: str,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not SERVER_VLM_ENABLED:
        return summary

    frames = list(summary.get("camera_frames") or [])
    if not frames:
        return summary

    try:
        runtime = CameraOnlyVlmRuntime()
        updated_frames, records = _analyze_frames(
            frames=frames,
            frame_dir=frame_dir,
            frame_url_prefix=frame_url_prefix,
            runtime=runtime,
            progress_callback=progress_callback,
        )
    except (ServerVlmUnavailableError, ModelUnavailableError, OSError, ValueError) as exc:
        summary["server_vlm"] = {
            "enabled": False,
            "status": "unavailable",
            "error": str(exc),
        }
        return summary

    if not records:
        return summary

    xai_summary = analyze_xai_log(records)
    xai_summary["topics"] = [SERVER_VLM_TOPIC]
    xai_summary["source_topics"] = sorted({str(frame.get("topic") or "") for frame in frames if frame.get("topic")})
    xai_summary["source"] = "server_vlm"

    summary["camera_frames"] = updated_frames
    summary["xai_summary"] = xai_summary
    summary["server_vlm"] = {
        "enabled": True,
        "status": "completed",
        "frame_count": len(records),
        "topic": SERVER_VLM_TOPIC,
        "model": xai_summary.get("model") or {},
    }
    return summary


def _analyze_frames(
    *,
    frames: list[dict[str, Any]],
    frame_dir: Path,
    frame_url_prefix: str,
    runtime: "CameraOnlyVlmRuntime",
    progress_callback: ProgressCallback | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frame_count = len(frames)
    limit = SERVER_VLM_MAX_FRAMES or frame_count
    analyze_count = min(frame_count, limit)
    if analyze_count <= 0:
        return frames, []

    loaded_frames = [_load_frame_bgr(frame, frame_dir) for frame in frames[:analyze_count]]
    output_dir = frame_dir / "server_vlm"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_url_prefix = f"{frame_url_prefix.rstrip('/')}/server_vlm"

    updated_frames = [dict(frame) for frame in frames]
    records: list[dict[str, Any]] = []
    started_at = time.perf_counter()

    for index, curr_bgr in enumerate(loaded_frames):
        prev_bgr = loaded_frames[index - 1] if index > 0 else curr_bgr
        next_bgr = loaded_frames[index + 1] if index + 1 < len(loaded_frames) else curr_bgr
        source_frame = frames[index]
        result = runtime.predict_frame(
            prev_bgr=prev_bgr,
            curr_bgr=curr_bgr,
            next_bgr=next_bgr,
            frame_index=index,
            timestamp=str(source_frame.get("timestamp") or ""),
            source_topic=str(source_frame.get("topic") or ""),
        )

        overlay_name = f"vlm_frame_{index:06d}.jpg"
        overlay_path = output_dir / overlay_name
        _write_overlay_image(overlay_path, result.overlay_bgr)
        overlay_url = f"{output_url_prefix}/{overlay_name}"

        frame = updated_frames[index]
        frame["raw_image_url"] = frame.get("image_url") or frame.get("data_url") or ""
        frame["vlm_image_url"] = overlay_url
        frame["image_url"] = overlay_url
        frame["width"] = int(result.overlay_bgr.shape[1])
        frame["height"] = int(result.overlay_bgr.shape[0])
        frame["encoding"] = "server_vlm_overlay"
        frame["xai_overlay"] = None
        records.append(result.record)

        if progress_callback is not None and (index == 0 or (index + 1) % 25 == 0 or index + 1 == analyze_count):
            elapsed = max(0.001, time.perf_counter() - started_at)
            progress_callback(
                90 + int(((index + 1) / max(analyze_count, 1)) * 8),
                f"VLM 영상 생성 중 ({index + 1:,}/{analyze_count:,}, {elapsed:.1f}s)",
            )

    return updated_frames, records


class CameraOnlyVlmRuntime:
    def __init__(self) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise ServerVlmUnavailableError("opencv-python-headless가 설치되어 있지 않습니다.") from exc

        self.cv2 = cv2
        self.model_info = get_model_info()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.bundle = load_student_model()
        self.model = self.bundle.get("model")
        self.vectorizer = self.bundle.get("vectorizer")
        self.label_encoder = self.bundle.get("label_encoder")
        self.image_size = int(self.bundle.get("image_size") or 0)
        if self.model is None or self.vectorizer is None or self.label_encoder is None or self.image_size <= 0:
            raise ServerVlmUnavailableError("student model bundle 형식이 올바르지 않습니다.")

    def predict_frame(
        self,
        *,
        prev_bgr: np.ndarray,
        curr_bgr: np.ndarray,
        next_bgr: np.ndarray,
        frame_index: int,
        timestamp: str,
        source_topic: str,
    ) -> VlmFrameResult:
        t0 = time.perf_counter()
        motion_summary = summarize_flow(
            self.cv2,
            prev_bgr,
            curr_bgr,
            next_bgr,
            SERVER_VLM_FLOW_IMAGE_SIDE_PX,
            1.5,
        )
        row = {
            "motion_summary": motion_summary,
            "event_label": None,
            "planner_reason": None,
            "motion_state": None,
            "path_blocked": False,
            "obstacle_summary": {},
            "source_bag_stem": "dashboard_upload",
        }
        image_feature = load_image_feature_from_bgr(self.cv2, curr_bgr, self.image_size)
        context_feature = build_context_feature(row)
        context_matrix = self.vectorizer.transform([context_feature]).astype(np.float32)
        x = np.concatenate([image_feature.reshape(1, -1), context_matrix], axis=1)
        probabilities = self.model.predict_proba(x)[0]
        top_indices = np.argsort(probabilities)[::-1][: min(3, len(probabilities))]
        pred_index = int(top_indices[0])
        pred_label = str(self.label_encoder.inverse_transform([pred_index])[0])
        confidence = float(probabilities[pred_index])
        top_candidates = [
            {
                "label_ko": str(self.label_encoder.inverse_transform([int(idx)])[0]),
                "confidence": float(probabilities[int(idx)]),
            }
            for idx in top_indices
        ]
        infer_ms = (time.perf_counter() - t0) * 1000.0

        scene_summary, camera_reason = build_camera_thought(pred_label, row)
        raw_motion, ego_motion, scene_state = describe_motion(row)
        record = {
            "model_name": str(self.model_info.get("model_name") or "xai_student_model"),
            "model_version": str(self.model_info.get("version") or "unknown"),
            "run_id": str(self.model_info.get("run_id") or ""),
            "model_status": str(self.model_info.get("status") or ""),
            "frame_index": int(frame_index),
            "stamp": timestamp,
            "timestamp": timestamp,
            "_timestamp": timestamp,
            "_topic": SERVER_VLM_TOPIC,
            "_source_topic": source_topic,
            "prediction": pred_label,
            "primary_object_ko": pred_label,
            "confidence": confidence,
            "top_candidates": top_candidates,
            "raw_screen_motion_ko": raw_motion,
            "ego_motion_ko": ego_motion,
            "scene_state_ko": scene_state,
            "motion_summary": motion_summary,
            "scene_summary_ko": scene_summary,
            "driving_mode_ko": "카메라 VLM 분석",
            "driving_reason_ko": camera_reason,
            "explanation": camera_reason,
            "event_label": "camera_vlm",
            "infer_ms": infer_ms,
        }
        overlay_bgr = render_panel(
            self.cv2,
            curr_bgr=curr_bgr,
            pred_label=pred_label,
            confidence=confidence,
            motion_summary=motion_summary,
            infer_ms=infer_ms,
            frame_index=int(frame_index),
            top_candidates=top_candidates,
        )
        return VlmFrameResult(overlay_bgr=overlay_bgr, record=record)


def _load_frame_bgr(frame: dict[str, Any], frame_dir: Path) -> np.ndarray:
    try:
        import cv2
    except ImportError as exc:
        raise ServerVlmUnavailableError("opencv-python-headless가 설치되어 있지 않습니다.") from exc

    image_url = str(frame.get("image_url") or "")
    if image_url:
        image_path = frame_dir / Path(urlparse(image_url).path).name
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is not None:
            return image

    data_url = str(frame.get("data_url") or "")
    if data_url.startswith("data:") and "," in data_url:
        raw = base64.b64decode(data_url.split(",", 1)[1])
        encoded = np.frombuffer(raw, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is not None:
            return image

    raise ServerVlmUnavailableError(f"카메라 프레임을 읽을 수 없습니다: {image_url or 'data_url'}")


def _write_overlay_image(path: Path, image_bgr: np.ndarray) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), SERVER_VLM_JPEG_QUALITY])
    if not ok:
        raise ServerVlmUnavailableError(f"VLM overlay 이미지를 저장하지 못했습니다: {path}")


def load_image_feature_from_bgr(cv2_module: Any, image_bgr: np.ndarray, image_size: int) -> np.ndarray:
    image = cv2_module.cvtColor(image_bgr, cv2_module.COLOR_BGR2GRAY)
    image = cv2_module.resize(image, (image_size, image_size), interpolation=cv2_module.INTER_AREA)
    return image.astype(np.float32).reshape(-1) / 255.0


def resize_for_flow(cv2_module: Any, image_bgr: np.ndarray, max_side: int) -> np.ndarray:
    max_side = max(32, int(max_side))
    h, w = image_bgr.shape[:2]
    scale = float(max_side) / float(max(h, w))
    if scale >= 1.0:
        resized = image_bgr
    else:
        resized = cv2_module.resize(
            image_bgr,
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            interpolation=cv2_module.INTER_AREA,
        )
    return cv2_module.cvtColor(resized, cv2_module.COLOR_BGR2GRAY)


def summarize_flow(
    cv2_module: Any,
    prev_bgr: np.ndarray,
    curr_bgr: np.ndarray,
    next_bgr: np.ndarray,
    max_side: int,
    motion_threshold: float,
) -> dict[str, Any]:
    prev_gray = resize_for_flow(cv2_module, prev_bgr, max_side)
    curr_gray = resize_for_flow(cv2_module, curr_bgr, max_side)
    next_gray = resize_for_flow(cv2_module, next_bgr, max_side)

    flow_prev = cv2_module.calcOpticalFlowFarneback(prev_gray, curr_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    flow_next = cv2_module.calcOpticalFlowFarneback(curr_gray, next_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)

    def _stats(flow: np.ndarray) -> dict[str, float]:
        dx = flow[..., 0]
        dy = flow[..., 1]
        mag = np.sqrt(dx * dx + dy * dy)
        moving = mag > float(motion_threshold)
        center = mag[
            mag.shape[0] // 4 : (mag.shape[0] * 3) // 4,
            mag.shape[1] // 4 : (mag.shape[1] * 3) // 4,
        ]
        if np.any(moving):
            mean_dx = float(np.mean(dx[moving]))
            mean_dy = float(np.mean(dy[moving]))
        else:
            mean_dx = 0.0
            mean_dy = 0.0
        return {
            "mean_magnitude": float(np.mean(mag)),
            "moving_ratio": float(np.mean(moving)),
            "center_moving_ratio": float(np.mean(center > float(motion_threshold))),
            "mean_dx": mean_dx,
            "mean_dy": mean_dy,
        }

    prev_stats = _stats(flow_prev)
    next_stats = _stats(flow_next)
    raw_motion_ko = infer_raw_screen_motion_ko(prev_stats, next_stats)
    ego_motion_ko = infer_ego_motion_ko(prev_stats, next_stats)
    scene_state_ko = infer_scene_state_ko(prev_stats, next_stats, ego_motion_ko)
    return {
        "prev_to_curr": prev_stats,
        "curr_to_next": next_stats,
        "dominant_motion_ko": raw_motion_ko,
        "raw_screen_motion_ko": raw_motion_ko,
        "ego_motion_ko": ego_motion_ko,
        "scene_state_ko": scene_state_ko,
    }


def infer_raw_screen_motion_ko(prev_stats: dict[str, Any], next_stats: dict[str, Any]) -> str:
    direction_ko = "정지 또는 미미한 움직임"
    dx = 0.5 * (float(prev_stats.get("mean_dx") or 0.0) + float(next_stats.get("mean_dx") or 0.0))
    dy = 0.5 * (float(prev_stats.get("mean_dy") or 0.0) + float(next_stats.get("mean_dy") or 0.0))
    center_ratio = max(
        float(prev_stats.get("center_moving_ratio") or 0.0),
        float(next_stats.get("center_moving_ratio") or 0.0),
    )
    if center_ratio > 0.02:
        if abs(dx) >= abs(dy):
            direction_ko = "화면 기준 우측으로 이동하는 움직임" if dx > 0.0 else "화면 기준 좌측으로 이동하는 움직임"
        else:
            direction_ko = "화면 기준 아래쪽으로 이동하는 움직임" if dy > 0.0 else "화면 기준 위쪽으로 이동하는 움직임"
    return direction_ko


def infer_ego_motion_ko(prev_stats: dict[str, Any], next_stats: dict[str, Any]) -> str:
    mean_dx = 0.5 * (float(prev_stats.get("mean_dx") or 0.0) + float(next_stats.get("mean_dx") or 0.0))
    mean_dy = 0.5 * (float(prev_stats.get("mean_dy") or 0.0) + float(next_stats.get("mean_dy") or 0.0))
    mean_mag = 0.5 * (
        float(prev_stats.get("mean_magnitude") or 0.0) + float(next_stats.get("mean_magnitude") or 0.0)
    )
    center_ratio = max(
        float(prev_stats.get("center_moving_ratio") or 0.0),
        float(next_stats.get("center_moving_ratio") or 0.0),
    )
    moving_ratio = max(float(prev_stats.get("moving_ratio") or 0.0), float(next_stats.get("moving_ratio") or 0.0))

    if center_ratio < 0.03 and moving_ratio < 0.10 and mean_mag < 1.0:
        return "정지"
    if mean_dy >= 0.35:
        if mean_dx >= 0.35:
            return "전진 좌회전"
        if mean_dx <= -0.35:
            return "전진 우회전"
        return "전진"
    if mean_dy <= -0.35:
        if mean_dx >= 0.35:
            return "후진 좌회전"
        if mean_dx <= -0.35:
            return "후진 우회전"
        return "후진"
    if mean_dx >= 0.35:
        return "좌회전"
    if mean_dx <= -0.35:
        return "우회전"
    return "정지"


def infer_scene_state_ko(prev_stats: dict[str, Any], next_stats: dict[str, Any], ego_motion_ko: str) -> str:
    center_ratio = max(
        float(prev_stats.get("center_moving_ratio") or 0.0),
        float(next_stats.get("center_moving_ratio") or 0.0),
    )
    moving_ratio = max(float(prev_stats.get("moving_ratio") or 0.0), float(next_stats.get("moving_ratio") or 0.0))
    if ego_motion_ko == "정지":
        if center_ratio >= 0.20 or moving_ratio >= 0.25:
            return "동적 객체 영향 큼"
        return "정적 구조 우세"
    if center_ratio >= 0.12 or moving_ratio >= 0.20:
        return "자차 이동 영향 큼"
    return "정적/동적 혼합"


def describe_motion(row: dict[str, Any]) -> tuple[str, str, str]:
    motion = row.get("motion_summary") or {}
    prev = motion.get("prev_to_curr") or {}
    nxt = motion.get("curr_to_next") or {}
    raw_motion = motion.get("raw_screen_motion_ko") or motion.get("dominant_motion_ko") or "정지 또는 미미한 움직임"
    ego_motion = motion.get("ego_motion_ko") or infer_ego_motion_ko(prev, nxt)
    scene_state = motion.get("scene_state_ko") or infer_scene_state_ko(prev, nxt, ego_motion)
    return raw_motion, ego_motion, scene_state


def build_camera_thought(pred_label: str, row: dict[str, Any]) -> tuple[str, str]:
    _, ego_motion, scene_state = describe_motion(row)
    if pred_label == "사람":
        if scene_state == "동적 객체 영향 큼":
            reason = "사람 움직임이 보여 감속이나 회피를 준비한다고 본다."
        elif ego_motion != "정지":
            reason = f"{ego_motion} 중 사람과의 간격을 확인하며 지나가려 한다고 본다."
        else:
            reason = "사람이 보여 주변을 경계하며 천천히 본다고 해석한다."
    elif pred_label in {"자동차", "차량"}:
        if scene_state == "동적 객체 영향 큼":
            reason = "차량 움직임이 보여 간격을 두고 지나가려 한다고 본다."
        elif ego_motion != "정지":
            reason = f"{ego_motion} 중 차량과 차선 가장자리를 함께 확인한다고 본다."
        else:
            reason = "차량이 보여 통과 가능 공간을 살핀다고 해석한다."
    elif pred_label == "벽":
        reason = f"{ego_motion} 중 통로 구조와 진행 공간을 확인한다고 본다." if ego_motion != "정지" else "정적인 통로 구조가 보여 즉시 회피할 대상은 약하다고 본다."
    else:
        reason = f"{pred_label}이 보여 보수적으로 진행한다고 해석한다."
    return f"대표 객체 {pred_label} / 로봇 {ego_motion}", reason


def format_top_candidates(top_candidates: list[dict[str, Any]]) -> str:
    if not top_candidates:
        return "후보 없음"
    return ", ".join(f"{item['label_ko']} {float(item['confidence']):.2f}" for item in top_candidates)


def render_panel(
    cv2_module: Any,
    *,
    curr_bgr: np.ndarray,
    pred_label: str,
    confidence: float,
    motion_summary: dict[str, Any],
    infer_ms: float,
    frame_index: int,
    top_candidates: list[dict[str, Any]],
) -> np.ndarray:
    h, w = curr_bgr.shape[:2]
    panel_w = 460
    canvas = np.zeros((h, w + panel_w, 3), dtype=np.uint8)
    canvas[:, :w] = curr_bgr
    canvas[:, w:] = (18, 18, 18)

    row = {"motion_summary": motion_summary}
    scene_summary, camera_reason = build_camera_thought(pred_label, row)
    raw_motion, ego_motion, scene_state = describe_motion(row)

    pil = PILImage.fromarray(cv2_module.cvtColor(canvas, cv2_module.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    title_font = find_font(28)
    body_font = find_font(22)
    small_font = find_font(18)

    x0 = w + 20
    y = 24
    draw.text((x0, y), "student camera thought", fill=(255, 255, 255), font=title_font)
    y += 46
    lines = [
        f"frame: {frame_index}",
        f"대표 객체: {pred_label} ({confidence:.2f})",
        f"robot motion: {ego_motion}",
        f"scene state: {scene_state}",
        f"raw flow: {raw_motion}",
        f"후보: {format_top_candidates(top_candidates)}",
        f"scene: {scene_summary}",
        f"reason: {camera_reason}",
        f"infer: {infer_ms:.1f} ms",
    ]
    for idx, line in enumerate(lines):
        font = body_font if idx < 5 else small_font
        for subline in wrap_text(line, width=22):
            draw.text((x0, y), subline, fill=(240, 240, 240), font=font)
            y += 28 if font == body_font else 24
        y += 4
    return cv2_module.cvtColor(np.array(pil), cv2_module.COLOR_RGB2BGR)


def build_context_feature(row: dict[str, Any]) -> dict[str, float]:
    obstacle = row.get("obstacle_summary") or {}
    motion = row.get("motion_summary") or {}
    prev_to_curr = motion.get("prev_to_curr") or {}
    curr_to_next = motion.get("curr_to_next") or {}
    return {
        f"event_label={row.get('event_label') or 'unknown'}": 1.0,
        f"motion_state={row.get('motion_state') or 'unknown'}": 1.0,
        f"planner_reason={row.get('planner_reason') or 'unknown'}": 1.0,
        "path_blocked": float(bool(row.get("path_blocked"))),
        "near_raw_points": float(obstacle.get("near_raw_points") or 0.0),
        "near_raw_min_range_m": float(obstacle.get("near_raw_min_range_m") or 0.0),
        f"source_bag={row.get('source_bag_stem') or row.get('source_bag') or 'unknown'}": 1.0,
        f"dominant_motion={motion.get('dominant_motion_ko') or 'unknown'}": 1.0,
        f"ego_motion={motion.get('ego_motion_ko') or 'unknown'}": 1.0,
        f"scene_state={motion.get('scene_state_ko') or 'unknown'}": 1.0,
        "prev_to_curr_mean_magnitude": float(prev_to_curr.get("mean_magnitude") or 0.0),
        "prev_to_curr_moving_ratio": float(prev_to_curr.get("moving_ratio") or 0.0),
        "prev_to_curr_center_moving_ratio": float(prev_to_curr.get("center_moving_ratio") or 0.0),
        "prev_to_curr_mean_dx": float(prev_to_curr.get("mean_dx") or 0.0),
        "prev_to_curr_mean_dy": float(prev_to_curr.get("mean_dy") or 0.0),
        "curr_to_next_mean_magnitude": float(curr_to_next.get("mean_magnitude") or 0.0),
        "curr_to_next_moving_ratio": float(curr_to_next.get("moving_ratio") or 0.0),
        "curr_to_next_center_moving_ratio": float(curr_to_next.get("center_moving_ratio") or 0.0),
        "curr_to_next_mean_dx": float(curr_to_next.get("mean_dx") or 0.0),
        "curr_to_next_mean_dy": float(curr_to_next.get("mean_dy") or 0.0),
    }


def find_font(size: int) -> Any:
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def wrap_text(text: str, width: int = 18) -> list[str]:
    text = str(text or "")
    if len(text) <= width:
        return [text]
    return [text[start : start + width] for start in range(0, len(text), width)]
