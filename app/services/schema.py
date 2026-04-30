from dataclasses import dataclass

import pandas as pd

from app.services.loader import REQUIRED_COLUMNS


@dataclass(frozen=True)
class SchemaReport:
    is_valid: bool
    missing_columns: list[str]
    extra_columns: list[str]


def validate_sensor_schema(frame: pd.DataFrame) -> SchemaReport:
    columns = set(frame.columns)
    required = set(REQUIRED_COLUMNS)

    missing_columns = sorted(required - columns)
    extra_columns = sorted(columns - required)

    return SchemaReport(
        is_valid=not missing_columns,
        missing_columns=missing_columns,
        extra_columns=extra_columns,
    )
