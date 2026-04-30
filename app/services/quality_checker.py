import pandas as pd

from app.models import QualityMetric
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
