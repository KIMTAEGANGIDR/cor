"""
OCR Testing and Validation Module

계층화 샘플링 기반 OCR 파이프라인 검증

모듈:
- sampler: 유형별 계층화 샘플링
- metrics: 품질 메트릭 계산
- reporter: JSON/MD/HTML 리포트 생성
- validator: 이슈 탐지 및 검증
- cli: 테스트 CLI 명령어
"""

__version__ = "0.1.0"

from .sampler import StratifiedSampler, SampleConfig, SampleResult
from .metrics import MetricsCalculator, DocumentMetrics, AggregateMetrics
from .reporter import Reporter, ReportFormat
from .validator import Validator, ValidationIssue, IssueType

__all__ = [
    # Sampler
    "StratifiedSampler",
    "SampleConfig",
    "SampleResult",
    # Metrics
    "MetricsCalculator",
    "DocumentMetrics",
    "AggregateMetrics",
    # Reporter
    "Reporter",
    "ReportFormat",
    # Validator
    "Validator",
    "ValidationIssue",
    "IssueType",
]
