import pandas as pd

from app.models import AnomalySegment, QualityMetric
from app.services.loader import REQUIRED_COLUMNS


def status_from_ratio(ratio: float, warning: float, critical: float) -> str:
    if ratio >= critical:
        return "위험"
    if ratio >= warning:
        return "주의"
    return "정상"


def check_missing_values(frame: pd.DataFrame) -> QualityMetric:
    available_columns = [column for column in REQUIRED_COLUMNS if column in frame.columns]
    if not available_columns or frame.empty:
        return QualityMetric(
            name="결측치 비율",
            status="위험",
            value=100.0,
            detail="분석 가능한 컬럼 또는 행이 없습니다.",
        )

    inspected = frame[available_columns]
    missing_count = int(inspected.isna().sum().sum())
    total_cells = int(inspected.shape[0] * inspected.shape[1])
    missing_ratio = round((missing_count / total_cells) * 100, 2) if total_cells else 100.0

    return QualityMetric(
        name="결측치 비율",
        status=status_from_ratio(missing_ratio, warning=1.0, critical=5.0),
        value=missing_ratio,
        detail=f"{total_cells}개 셀 중 {missing_count}개 결측",
    )


def check_duplicate_timestamps(frame: pd.DataFrame) -> QualityMetric:
    if "timestamp" not in frame.columns or frame.empty:
        return QualityMetric(
            name="timestamp 중복",
            status="위험",
            value=100.0,
            detail="timestamp 컬럼 또는 행이 없습니다.",
        )

    duplicated_count = int(frame["timestamp"].duplicated(keep=False).sum())
    duplicated_ratio = round((duplicated_count / len(frame)) * 100, 2)

    return QualityMetric(
        name="timestamp 중복",
        status=status_from_ratio(duplicated_ratio, warning=1.0, critical=5.0),
        value=duplicated_ratio,
        detail=f"{len(frame)}개 행 중 {duplicated_count}개 timestamp 중복",
    )


def check_sampling_gaps(frame: pd.DataFrame, threshold_ms: int = 150) -> QualityMetric:
    gaps = _timestamp_gaps(frame, threshold_ms)
    interval_count = max(len(_sorted_timestamps(frame)) - 1, 0)
    gap_ratio = round((len(gaps) / interval_count) * 100, 2) if interval_count else 100.0
    max_gap_ms = round(float(gaps["gap_ms"].max()), 2) if not gaps.empty else 0.0

    return QualityMetric(
        name="sampling gap",
        status=status_from_ratio(gap_ratio, warning=1.0, critical=5.0),
        value=gap_ratio,
        detail=f"{interval_count}개 간격 중 {len(gaps)}개 gap, 최대 {max_gap_ms}ms",
    )


def detect_sampling_gap_segments(frame: pd.DataFrame, threshold_ms: int = 150) -> list[AnomalySegment]:
    gaps = _timestamp_gaps(frame, threshold_ms)
    segments: list[AnomalySegment] = []

    for _, row in gaps.iterrows():
        gap_ms = round(float(row["gap_ms"]), 2)
        segments.append(
            AnomalySegment(
                category="sampling_gap",
                start=_format_timestamp(row["previous_timestamp"]),
                end=_format_timestamp(row["timestamp"]),
                severity="주의" if gap_ms < threshold_ms * 3 else "위험",
                description=f"예상 주기보다 긴 {gap_ms}ms 간격이 감지되었습니다.",
            )
        )

    return segments


def _timestamp_gaps(frame: pd.DataFrame, threshold_ms: int) -> pd.DataFrame:
    timestamps = _sorted_timestamps(frame)
    if len(timestamps) < 2:
        return pd.DataFrame(columns=["previous_timestamp", "timestamp", "gap_ms"])

    gap_frame = pd.DataFrame({"timestamp": timestamps})
    gap_frame["previous_timestamp"] = gap_frame["timestamp"].shift(1)
    gap_frame["gap_ms"] = (
        gap_frame["timestamp"] - gap_frame["previous_timestamp"]
    ).dt.total_seconds() * 1000
    return gap_frame[gap_frame["gap_ms"] > threshold_ms].dropna()


def _sorted_timestamps(frame: pd.DataFrame) -> pd.Series:
    if "timestamp" not in frame.columns:
        return pd.Series(dtype="datetime64[ns]")
    return frame["timestamp"].dropna().drop_duplicates().sort_values().reset_index(drop=True)


def _format_timestamp(value: pd.Timestamp) -> str:
    return value.isoformat(timespec="milliseconds")
