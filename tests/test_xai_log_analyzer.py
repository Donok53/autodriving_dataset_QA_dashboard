import pytest

from app.services.xai_log_analyzer import InvalidXaiLogError, analyze_xai_log


def test_analyze_xai_log_counts_driving_states():
    summary = analyze_xai_log(
        [
            {
                "model_name": "xai_student_model",
                "model_version": "v2",
                "run_id": "abc123",
                "event_label": "normal_route",
                "driving_mode_ko": "정상 경로",
                "driving_reason_ko": "정상 경로를 따라 주행한다.",
            },
            {
                "event_label": "avoidance",
                "driving_mode_ko": "좌측 회피",
                "driving_reason_ko": "전방 장애물을 피해 좌측 회피 경로로 주행한다.",
            },
            {
                "event_label": "safety_stop",
                "driving_mode_ko": "안전모드 정지",
                "driving_reason_ko": "안전모드가 활성화되어 정지한다.",
            },
            {
                "event_label": "goal_arrival",
                "driving_mode_ko": "목적지 도착",
                "driving_reason_ko": "목적지에 도착해 정지한다.",
            },
        ]
    )

    assert summary["total_explanations"] == 4
    assert summary["normal_count"] == 1
    assert summary["avoidance_count"] == 1
    assert summary["safety_stop_count"] == 1
    assert summary["arrival_count"] == 1
    assert summary["model"]["version"] == "v2"
    assert len(summary["representative_explanations"]) == 4


def test_analyze_xai_log_accepts_ros_string_payloads():
    summary = analyze_xai_log(
        {
            "messages": [
                {
                    "data": (
                        '{"event_label":"avoidance","driving_mode_ko":"우측 회피",'
                        '"driving_reason_ko":"우측 회피 경로로 주행한다."}'
                    )
                }
            ]
        }
    )

    assert summary["total_explanations"] == 1
    assert summary["avoidance_count"] == 1


def test_analyze_xai_log_rejects_empty_payload():
    with pytest.raises(InvalidXaiLogError):
        analyze_xai_log([])
