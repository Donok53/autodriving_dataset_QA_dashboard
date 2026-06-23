from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SAMPLE_PATH = PROJECT_ROOT / "data" / "xai_samples" / "sample_vlm_log.json"


class InvalidXaiLogError(ValueError):
    pass


def analyze_xai_log(source: str | Path | list[Any] | dict[str, Any]) -> dict[str, Any]:
    records = load_xai_records(source)
    counts = Counter(_classify_record(record) for record in records)
    model_info = _extract_model_info(records)
    explanations = [_explanation_text(record) for record in records]
    representative_explanations = [text for text in explanations if text][:5]

    return {
        "source_type": "xai_vlm_log",
        "total_explanations": len(records),
        "normal_count": counts["normal"],
        "safety_stop_count": counts["safety_stop"],
        "avoidance_count": counts["avoidance"],
        "arrival_count": counts["arrival"],
        "blocked_count": counts["blocked"],
        "unknown_count": counts["unknown"],
        "driving_mode_counts": dict(_mode_counts(records)),
        "event_label_counts": dict(_event_counts(records)),
        "model": model_info,
        "representative_explanations": representative_explanations,
        "latest_explanation": representative_explanations[-1] if representative_explanations else "",
    }


def analyze_sample_xai_log() -> dict[str, Any]:
    return analyze_xai_log(DEFAULT_SAMPLE_PATH)


def load_xai_records(source: str | Path | list[Any] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise InvalidXaiLogError(f"XAI log file not found: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidXaiLogError(f"XAI log JSON을 읽을 수 없습니다: {exc}") from exc
    else:
        payload = source

    records = _normalize_records(payload)
    if not records:
        raise InvalidXaiLogError("XAI 설명 로그가 비어 있습니다.")
    return records


def _normalize_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _coerce_records(payload)

    if isinstance(payload, dict):
        for key in ("records", "logs", "messages", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return _coerce_records(value)
        record = _coerce_record(payload)
        return [record] if record else []

    return []


def _coerce_records(items: list[Any]) -> list[dict[str, Any]]:
    records = []
    for item in items:
        record = _coerce_record(item)
        if record:
            records.append(record)
    return records


def _coerce_record(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        if isinstance(item.get("data"), str):
            nested = _loads_or_none(item["data"])
            if isinstance(nested, dict):
                return nested
        return item

    if isinstance(item, str):
        loaded = _loads_or_none(item)
        return loaded if isinstance(loaded, dict) else None

    return None


def _loads_or_none(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _classify_record(record: dict[str, Any]) -> str:
    text = " ".join(
        str(record.get(key) or "")
        for key in (
            "driving_mode_ko",
            "scene_summary_ko",
            "driving_reason_ko",
            "event_label",
            "prediction",
            "primary_object_ko",
        )
    ).lower()

    if any(token in text for token in ("도착", "arrival", "goal")):
        return "arrival"
    if any(token in text for token in ("회피", "avoid")):
        return "avoidance"
    if any(token in text for token in ("차단", "blocked", "block")):
        return "blocked"
    if any(token in text for token in ("안전모드", "긴급", "정지", "emergency", "stop")):
        return "safety_stop"
    if any(token in text for token in ("정상", "normal")):
        return "normal"
    return "unknown"


def _mode_counts(records: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(record.get("driving_mode_ko") or "unknown") for record in records)


def _event_counts(records: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(record.get("event_label") or "unknown") for record in records)


def _extract_model_info(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in reversed(records):
        model_name = record.get("model_name")
        model_version = record.get("model_version") or record.get("version")
        run_id = record.get("run_id")
        if model_name or model_version or run_id:
            return {
                "model_name": model_name or "xai_student_model",
                "version": model_version or "unknown",
                "run_id": run_id or "",
            }
    return {
        "model_name": "xai_student_model",
        "version": "unknown",
        "run_id": "",
    }


def _explanation_text(record: dict[str, Any]) -> str:
    return str(
        record.get("explanation")
        or record.get("driving_reason_ko")
        or record.get("scene_summary_ko")
        or ""
    )
