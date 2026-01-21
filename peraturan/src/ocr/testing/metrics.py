"""
품질 메트릭 계산 모듈 (Metrics Calculator)

문서 및 집계 수준의 품질 메트릭 계산

메트릭:
- 문서 수준: avg_quality_score, word_recognition_ratio, legal_term_density, broken_char_ratio
- 페이지 수준: quality_score, strategy, ocr_confidence, needs_manual_review
- 집계: 카테고리별/연대별 평균, 품질 분포 (excellent/good/fair/poor)
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Any
import json


class QualityTier(Enum):
    """Quality tier classification"""
    EXCELLENT = "excellent"  # >= 0.90
    GOOD = "good"            # >= 0.75
    FAIR = "fair"            # >= 0.50
    POOR = "poor"            # < 0.50


# Quality tier thresholds
QUALITY_THRESHOLDS = {
    QualityTier.EXCELLENT: 0.90,
    QualityTier.GOOD: 0.75,
    QualityTier.FAIR: 0.50,
    QualityTier.POOR: 0.0,
}


@dataclass
class PageMetrics:
    """Per-page metrics"""
    page_num: int
    quality_score: float
    strategy: str  # text, ocr, hybrid, skip
    ocr_confidence: float = 0.0
    word_count: int = 0
    char_count: int = 0
    broken_char_ratio: float = 0.0
    needs_manual_review: bool = False
    processing_time_ms: int = 0
    error_message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocumentMetrics:
    """Document-level metrics"""
    doc_id: str
    category: str
    year: Optional[int]

    # Overall scores
    avg_quality_score: float = 0.0
    word_recognition_ratio: float = 0.0
    legal_term_density: float = 0.0
    broken_char_ratio: float = 0.0

    # Page statistics
    total_pages: int = 0
    pages_text: int = 0       # Pages using embedded text
    pages_ocr: int = 0        # Pages using OCR
    pages_hybrid: int = 0     # Pages using hybrid strategy
    pages_skip: int = 0       # Skipped pages (empty)
    pages_manual: int = 0     # Pages needing manual review

    # Processing info
    status: str = "pending"   # pending, completed, failed
    processing_time_ms: int = 0
    error_message: str = ""

    # Page details
    pages: List[PageMetrics] = field(default_factory=list)

    def quality_tier(self) -> QualityTier:
        """Get quality tier classification"""
        if self.avg_quality_score >= QUALITY_THRESHOLDS[QualityTier.EXCELLENT]:
            return QualityTier.EXCELLENT
        elif self.avg_quality_score >= QUALITY_THRESHOLDS[QualityTier.GOOD]:
            return QualityTier.GOOD
        elif self.avg_quality_score >= QUALITY_THRESHOLDS[QualityTier.FAIR]:
            return QualityTier.FAIR
        else:
            return QualityTier.POOR

    def to_dict(self) -> dict:
        result = asdict(self)
        result["quality_tier"] = self.quality_tier().value
        result["pages"] = [p.to_dict() for p in self.pages]
        return result

    def to_summary(self) -> dict:
        """Get summary without page details"""
        return {
            "doc_id": self.doc_id,
            "category": self.category,
            "year": self.year,
            "avg_quality_score": round(self.avg_quality_score, 4),
            "quality_tier": self.quality_tier().value,
            "total_pages": self.total_pages,
            "pages_text": self.pages_text,
            "pages_ocr": self.pages_ocr,
            "pages_manual": self.pages_manual,
            "status": self.status,
            "processing_time_ms": self.processing_time_ms,
        }


@dataclass
class AggregateMetrics:
    """Aggregate metrics for a test run"""
    run_id: str
    phase: str

    # Overall statistics
    total_samples: int = 0
    processed: int = 0
    failed: int = 0

    # Quality scores
    avg_quality_score: float = 0.0
    median_quality_score: float = 0.0
    min_quality_score: float = 0.0
    max_quality_score: float = 0.0
    std_quality_score: float = 0.0

    # Quality distribution
    quality_distribution: Dict[str, int] = field(default_factory=lambda: {
        "excellent": 0, "good": 0, "fair": 0, "poor": 0
    })

    # Strategy distribution
    strategy_distribution: Dict[str, int] = field(default_factory=lambda: {
        "text": 0, "ocr": 0, "hybrid": 0, "skip": 0
    })

    # By category
    by_category: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # By era
    by_era: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Manual review stats
    manual_review_count: int = 0
    manual_review_ratio: float = 0.0

    # Processing stats
    total_processing_time_ms: int = 0
    avg_processing_time_ms: float = 0.0

    # Timestamps
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class MetricsCalculator:
    """
    품질 메트릭 계산기

    개별 문서 결과를 분석하여 메트릭 계산

    사용법:
        calc = MetricsCalculator()
        doc_metrics = calc.calculate_document_metrics(doc_result, page_results)
        agg_metrics = calc.aggregate(run_id, doc_metrics_list)
    """

    def __init__(
        self,
        quality_threshold: float = 0.92,
        manual_review_threshold: float = 0.50,
        broken_ratio_alert: float = 0.10,
    ):
        """
        Args:
            quality_threshold: TEXT vs OCR decision threshold
            manual_review_threshold: Manual review trigger threshold
            broken_ratio_alert: Broken character ratio alert threshold
        """
        self.quality_threshold = quality_threshold
        self.manual_review_threshold = manual_review_threshold
        self.broken_ratio_alert = broken_ratio_alert

    def calculate_document_metrics(
        self,
        doc_id: str,
        category: str,
        year: Optional[int],
        page_results: List[dict],
        processing_time_ms: int = 0,
    ) -> DocumentMetrics:
        """
        Calculate document-level metrics from page results

        Args:
            doc_id: Document ID
            category: Document category (uu, pp, etc.)
            year: Document year
            page_results: List of page result dicts with quality info
            processing_time_ms: Total processing time

        Returns:
            DocumentMetrics
        """
        metrics = DocumentMetrics(
            doc_id=doc_id,
            category=category,
            year=year,
            total_pages=len(page_results),
            processing_time_ms=processing_time_ms,
        )

        if not page_results:
            metrics.status = "failed"
            metrics.error_message = "No page results"
            return metrics

        quality_scores = []
        word_counts = []
        total_chars = 0
        total_broken_chars = 0

        for pr in page_results:
            # Extract page metrics
            page_metrics = PageMetrics(
                page_num=pr.get("page_num", 0),
                quality_score=pr.get("quality_score", 0.0),
                strategy=pr.get("strategy", "unknown"),
                ocr_confidence=pr.get("ocr_confidence", 0.0),
                word_count=pr.get("word_count", 0),
                char_count=pr.get("char_count", 0),
                broken_char_ratio=pr.get("broken_ratio", 0.0),
                needs_manual_review=pr.get("needs_manual_review", False),
                processing_time_ms=pr.get("processing_time_ms", 0),
                error_message=pr.get("error_message", ""),
            )
            metrics.pages.append(page_metrics)

            # Count by strategy
            strategy = page_metrics.strategy.lower()
            if strategy == "text":
                metrics.pages_text += 1
            elif strategy == "ocr":
                metrics.pages_ocr += 1
            elif strategy == "hybrid":
                metrics.pages_hybrid += 1
            elif strategy == "skip":
                metrics.pages_skip += 1

            # Track manual review
            if page_metrics.needs_manual_review or page_metrics.quality_score < self.manual_review_threshold:
                metrics.pages_manual += 1

            # Collect for aggregation
            if page_metrics.quality_score > 0:
                quality_scores.append(page_metrics.quality_score)

            if page_metrics.word_count > 0:
                word_counts.append(page_metrics.word_count)

            total_chars += page_metrics.char_count
            if page_metrics.char_count > 0:
                total_broken_chars += page_metrics.char_count * page_metrics.broken_char_ratio

        # Calculate averages
        if quality_scores:
            metrics.avg_quality_score = sum(quality_scores) / len(quality_scores)

        # Broken character ratio
        if total_chars > 0:
            metrics.broken_char_ratio = total_broken_chars / total_chars

        # Status determination
        if metrics.pages_manual > 0 and metrics.pages_manual == metrics.total_pages:
            metrics.status = "failed"
        elif metrics.pages_manual > 0:
            metrics.status = "partial"
        else:
            metrics.status = "completed"

        return metrics

    def aggregate(
        self,
        run_id: str,
        phase: str,
        doc_metrics_list: List[DocumentMetrics],
    ) -> AggregateMetrics:
        """
        Aggregate document metrics into run-level statistics

        Args:
            run_id: Test run ID
            phase: Test phase
            doc_metrics_list: List of DocumentMetrics

        Returns:
            AggregateMetrics
        """
        agg = AggregateMetrics(
            run_id=run_id,
            phase=phase,
            total_samples=len(doc_metrics_list),
            started_at=datetime.now().isoformat(),
        )

        if not doc_metrics_list:
            return agg

        quality_scores = []
        by_category = {}
        by_era = {}

        for dm in doc_metrics_list:
            # Count status
            if dm.status == "completed":
                agg.processed += 1
            elif dm.status == "failed":
                agg.failed += 1
            else:
                agg.processed += 1  # partial counts as processed

            # Quality scores
            if dm.avg_quality_score > 0:
                quality_scores.append(dm.avg_quality_score)

            # Quality tier distribution
            tier = dm.quality_tier().value
            agg.quality_distribution[tier] = agg.quality_distribution.get(tier, 0) + 1

            # Strategy distribution (page-level)
            agg.strategy_distribution["text"] += dm.pages_text
            agg.strategy_distribution["ocr"] += dm.pages_ocr
            agg.strategy_distribution["hybrid"] += dm.pages_hybrid
            agg.strategy_distribution["skip"] += dm.pages_skip

            # Manual review
            agg.manual_review_count += dm.pages_manual

            # Processing time
            agg.total_processing_time_ms += dm.processing_time_ms

            # By category
            cat = dm.category
            if cat not in by_category:
                by_category[cat] = {"count": 0, "scores": [], "manual": 0}
            by_category[cat]["count"] += 1
            if dm.avg_quality_score > 0:
                by_category[cat]["scores"].append(dm.avg_quality_score)
            by_category[cat]["manual"] += dm.pages_manual

            # By era
            era = self._classify_era(dm.year)
            if era not in by_era:
                by_era[era] = {"count": 0, "scores": [], "manual": 0}
            by_era[era]["count"] += 1
            if dm.avg_quality_score > 0:
                by_era[era]["scores"].append(dm.avg_quality_score)
            by_era[era]["manual"] += dm.pages_manual

        # Calculate aggregate stats
        if quality_scores:
            agg.avg_quality_score = sum(quality_scores) / len(quality_scores)
            sorted_scores = sorted(quality_scores)
            n = len(sorted_scores)
            agg.median_quality_score = sorted_scores[n // 2] if n % 2 else (sorted_scores[n//2 - 1] + sorted_scores[n//2]) / 2
            agg.min_quality_score = min(quality_scores)
            agg.max_quality_score = max(quality_scores)

            # Standard deviation
            mean = agg.avg_quality_score
            variance = sum((x - mean) ** 2 for x in quality_scores) / len(quality_scores)
            agg.std_quality_score = variance ** 0.5

        # Manual review ratio
        total_pages = sum(dm.total_pages for dm in doc_metrics_list)
        if total_pages > 0:
            agg.manual_review_ratio = agg.manual_review_count / total_pages

        # Average processing time
        if agg.processed > 0:
            agg.avg_processing_time_ms = agg.total_processing_time_ms / agg.processed

        # Finalize by_category
        for cat, data in by_category.items():
            scores = data["scores"]
            agg.by_category[cat] = {
                "count": data["count"],
                "avg_quality": sum(scores) / len(scores) if scores else 0,
                "manual_review": data["manual"],
            }

        # Finalize by_era
        for era, data in by_era.items():
            scores = data["scores"]
            agg.by_era[era] = {
                "count": data["count"],
                "avg_quality": sum(scores) / len(scores) if scores else 0,
                "manual_review": data["manual"],
            }

        agg.completed_at = datetime.now().isoformat()
        return agg

    def _classify_era(self, year: Optional[int]) -> str:
        """Classify year to era"""
        if year is None:
            return "unknown"
        if year < 1970:
            return "pre-1970"
        elif year < 2000:
            return "1970-1999"
        else:
            return "2000+"


# CLI test
if __name__ == "__main__":
    # Test with mock data
    calc = MetricsCalculator()

    # Mock page results
    page_results = [
        {"page_num": 0, "quality_score": 0.95, "strategy": "text", "word_count": 500, "char_count": 3000},
        {"page_num": 1, "quality_score": 0.85, "strategy": "text", "word_count": 450, "char_count": 2800},
        {"page_num": 2, "quality_score": 0.45, "strategy": "ocr", "ocr_confidence": 0.75, "word_count": 400, "char_count": 2500},
    ]

    doc_metrics = calc.calculate_document_metrics(
        doc_id="test_doc_1",
        category="uu",
        year=2020,
        page_results=page_results,
        processing_time_ms=5000,
    )

    print("=== Document Metrics ===")
    print(json.dumps(doc_metrics.to_summary(), indent=2))

    # Aggregate
    agg = calc.aggregate("test_run", "phase1", [doc_metrics])
    print("\n=== Aggregate Metrics ===")
    print(json.dumps(agg.to_dict(), indent=2, default=str))
