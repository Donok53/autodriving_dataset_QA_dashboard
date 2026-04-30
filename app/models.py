from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class QualityMetric:
    name: str
    status: str
    value: float
    detail: str

    def to_dict(self) -> dict[str, str | float]:
        return asdict(self)


@dataclass(frozen=True)
class SensorSyncStatus:
    sensor: str
    status: str
    mean_offset_ms: float
    max_offset_ms: float
    missing_count: int

    def to_dict(self) -> dict[str, str | float | int]:
        return asdict(self)


@dataclass(frozen=True)
class AnomalySegment:
    category: str
    start: str
    end: str
    severity: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class DrivingEvent:
    event_type: str
    timestamp: str
    severity: str
    description: str
    value: float

    def to_dict(self) -> dict[str, str | float]:
        return asdict(self)


@dataclass(frozen=True)
class BagTopicProfile:
    topic: str
    sensor: str
    msgtype: str
    message_count: int
    frequency_hz: float
    median_period_ms: float
    max_gap_ms: float

    def to_dict(self) -> dict[str, str | float | int]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisSummary:
    total_rows: int
    duration_seconds: float
    quality_score: float
    metrics: list[QualityMetric]
    sync_statuses: list[SensorSyncStatus]
    anomalies: list[AnomalySegment]
    events: list[DrivingEvent]
    source_type: str = "csv"
    topic_profiles: list[BagTopicProfile] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "total_rows": self.total_rows,
            "duration_seconds": self.duration_seconds,
            "quality_score": self.quality_score,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "sync_statuses": [status.to_dict() for status in self.sync_statuses],
            "anomalies": [anomaly.to_dict() for anomaly in self.anomalies],
            "events": [event.to_dict() for event in self.events],
            "topic_profiles": [
                profile.to_dict() for profile in self.topic_profiles or []
            ],
        }
