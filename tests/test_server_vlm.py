import numpy as np

from app.services import server_vlm
from app.services.server_vlm import ServerVlmUnavailableError, VlmFrameResult, apply_server_vlm_to_summary


def _summary_with_frames():
    return {
        "source_type": "bag",
        "camera_frames": [
            {
                "topic": "/camera/color/image_raw",
                "timestamp": f"2026-06-04T05:26:4{index}.000+00:00",
                "width": 640,
                "height": 480,
                "encoding": "rgb8",
                "image_url": f"/camera-frames/job/frame_{index:06d}.jpg",
                "data_url": "",
                "xai_overlay": {
                    "driving_mode_ko": "장애물 회피" if index == 1 else "정상 주행",
                    "event_label": "avoidance" if index == 1 else "normal_route",
                    "explanation": "회피 관련 이벤트가 감지되었습니다." if index == 1 else "토픽 흐름을 기반으로 정상 주행 상태입니다.",
                },
            }
            for index in range(3)
        ],
        "xai_summary": {
            "total_explanations": 2,
            "normal_count": 1,
            "safety_stop_count": 0,
            "avoidance_count": 1,
            "arrival_count": 0,
            "topics": ["/xai/vlm_log"],
            "source": "bag_driving_state",
            "model": {"model_name": "dashboard_topic_xai", "version": "generated-v1"},
            "representative_explanations": ["토픽 흐름을 기반으로 정상 주행 상태입니다.", "회피 관련 이벤트가 감지되었습니다."],
        },
    }


def test_apply_server_vlm_to_summary_replaces_frames_with_overlay(monkeypatch, tmp_path):
    seen_overlays = []

    class FakeRuntime:
        def predict_frame(self, *, frame_index, timestamp, source_topic, driving_overlay=None, **_kwargs):
            seen_overlays.append(driving_overlay)
            record = {
                "model_name": "xai_student_model",
                "model_version": "test-v1",
                "run_id": "test-run",
                "frame_index": frame_index,
                "timestamp": timestamp,
                "_timestamp": timestamp,
                "_topic": "/xai/vlm_log",
                "_source_topic": source_topic,
                "prediction": "사람",
                "primary_object_ko": "사람",
                "confidence": 0.91,
                "top_candidates": [{"label_ko": "사람", "confidence": 0.91}],
                "driving_mode_ko": "카메라 VLM 분석",
                "event_label": "camera_vlm",
                "explanation": "사람이 보여 주변을 경계하며 천천히 본다고 해석한다.",
            }
            return VlmFrameResult(
                overlay_bgr=np.zeros((12, 34, 3), dtype=np.uint8),
                record=record,
            )

    written = []
    monkeypatch.setattr(server_vlm, "CameraOnlyVlmRuntime", FakeRuntime)
    monkeypatch.setattr(server_vlm, "_load_frame_bgr", lambda _frame, _frame_dir: np.zeros((8, 10, 3), dtype=np.uint8))
    monkeypatch.setattr(server_vlm, "_write_overlay_image", lambda path, _image: written.append(path))

    summary = apply_server_vlm_to_summary(
        _summary_with_frames(),
        frame_dir=tmp_path,
        frame_url_prefix="/camera-frames/job",
    )

    assert summary["server_vlm"]["enabled"] is True
    assert summary["server_vlm"]["frame_count"] == 3
    assert summary["xai_summary"]["topics"] == ["/xai/vlm_log"]
    assert summary["xai_summary"]["source"] == "bag_driving_state"
    assert summary["xai_summary"]["avoidance_count"] == 1
    assert summary["server_vlm"]["xai_summary"]["source"] == "server_vlm"
    assert summary["server_vlm"]["xai_summary"]["model"]["version"] == "test-v1"
    assert seen_overlays[1]["driving_mode_ko"] == "장애물 회피"
    assert len(written) == 3
    assert summary["camera_frames"][0]["raw_image_url"] == "/camera-frames/job/frame_000000.jpg"
    assert summary["camera_frames"][0]["image_url"] == "/camera-frames/job/server_vlm/vlm_frame_000000.jpg"
    assert summary["camera_frames"][0]["vlm_image_url"] == "/camera-frames/job/server_vlm/vlm_frame_000000.jpg"
    assert summary["camera_frames"][0]["width"] == 34
    assert summary["camera_frames"][0]["height"] == 12
    assert summary["camera_frames"][0]["xai_overlay"] is None


def test_apply_server_vlm_to_summary_keeps_frames_when_model_unavailable(monkeypatch, tmp_path):
    class MissingRuntime:
        def __init__(self):
            raise ServerVlmUnavailableError("model missing")

    summary = _summary_with_frames()
    original_url = summary["camera_frames"][0]["image_url"]
    monkeypatch.setattr(server_vlm, "CameraOnlyVlmRuntime", MissingRuntime)

    updated = apply_server_vlm_to_summary(
        summary,
        frame_dir=tmp_path,
        frame_url_prefix="/camera-frames/job",
    )

    assert updated["server_vlm"]["enabled"] is False
    assert "model missing" in updated["server_vlm"]["error"]
    assert updated["camera_frames"][0]["image_url"] == original_url
