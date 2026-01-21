"""
이슈 탐지 및 검증 모듈 (Validator)

문서 품질 이슈 탐지 및 검증 임계치 관리

이슈 탐지 임계값:
- quality_threshold: 0.92 (TEXT vs OCR 결정)
- manual_review_trigger: 0.50 (수동 검토 필요)
- broken_ratio_alert: 0.10 (깨진 문자 경고)
- word_recognition_min: 0.40 (단어 인식 최소)
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Any

from .metrics import DocumentMetrics, PageMetrics, QualityTier


class IssueType(Enum):
    """Issue type classification"""
    LOW_QUALITY = "low_quality"
    BROKEN_CHARACTERS = "broken_characters"
    WORD_RECOGNITION = "word_recognition"
    MANUAL_REVIEW = "manual_review"
    PROCESSING_ERROR = "processing_error"
    EMPTY_PAGE = "empty_page"
    OCR_CONFIDENCE = "ocr_confidence"


class IssueSeverity(Enum):
    """Issue severity level"""
    CRITICAL = "critical"   # Blocks processing
    HIGH = "high"           # Requires attention
    MEDIUM = "medium"       # Should be reviewed
    LOW = "low"             # Informational


@dataclass
class ValidationIssue:
    """Validation issue record"""
    doc_id: str
    issue_type: IssueType
    severity: IssueSeverity
    page_num: Optional[int] = None  # None for document-level issues
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "issue_type": self.issue_type.value,
            "severity": self.severity.value,
            "page_num": self.page_num,
            "message": self.message,
            "details": self.details,
            "created_at": self.created_at,
        }


@dataclass
class ValidationThresholds:
    """Validation thresholds configuration"""
    quality_threshold: float = 0.92       # TEXT vs OCR decision
    manual_review_trigger: float = 0.50   # Manual review needed
    broken_ratio_alert: float = 0.10      # Broken character warning
    word_recognition_min: float = 0.40    # Minimum word recognition
    ocr_confidence_min: float = 0.60      # Minimum OCR confidence
    empty_page_threshold: int = 10        # Characters below = empty

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ValidationThresholds":
        return cls(**data)

    def save(self, path: Path):
        """Save thresholds to JSON"""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "ValidationThresholds":
        """Load thresholds from JSON"""
        with open(path, 'r', encoding='utf-8') as f:
            return cls.from_dict(json.load(f))


@dataclass
class ValidationResult:
    """Validation result for a test run"""
    run_id: str
    total_documents: int = 0
    documents_with_issues: int = 0
    total_issues: int = 0
    issues_by_type: Dict[str, int] = field(default_factory=dict)
    issues_by_severity: Dict[str, int] = field(default_factory=dict)
    issues: List[ValidationIssue] = field(default_factory=list)
    thresholds: ValidationThresholds = field(default_factory=ValidationThresholds)
    validated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "total_documents": self.total_documents,
            "documents_with_issues": self.documents_with_issues,
            "total_issues": self.total_issues,
            "issues_by_type": self.issues_by_type,
            "issues_by_severity": self.issues_by_severity,
            "thresholds": self.thresholds.to_dict(),
            "validated_at": self.validated_at,
            "issues": [i.to_dict() for i in self.issues],
        }

    def save(self, path: Path):
        """Save validation result"""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


class Validator:
    """
    품질 검증기

    문서 메트릭을 분석하여 이슈 탐지

    사용법:
        validator = Validator(thresholds)
        result = validator.validate(run_id, doc_metrics_list)
    """

    def __init__(self, thresholds: Optional[ValidationThresholds] = None):
        """
        Args:
            thresholds: Validation thresholds (default if not provided)
        """
        self.thresholds = thresholds or ValidationThresholds()

    def validate(
        self,
        run_id: str,
        doc_metrics_list: List[DocumentMetrics],
    ) -> ValidationResult:
        """
        Validate all documents and detect issues

        Args:
            run_id: Test run ID
            doc_metrics_list: List of document metrics

        Returns:
            ValidationResult
        """
        result = ValidationResult(
            run_id=run_id,
            total_documents=len(doc_metrics_list),
            thresholds=self.thresholds,
        )

        docs_with_issues = set()

        for dm in doc_metrics_list:
            doc_issues = self._validate_document(dm)
            if doc_issues:
                docs_with_issues.add(dm.doc_id)
                result.issues.extend(doc_issues)

        # Update statistics
        result.documents_with_issues = len(docs_with_issues)
        result.total_issues = len(result.issues)

        # Count by type and severity
        for issue in result.issues:
            type_key = issue.issue_type.value
            result.issues_by_type[type_key] = result.issues_by_type.get(type_key, 0) + 1

            severity_key = issue.severity.value
            result.issues_by_severity[severity_key] = result.issues_by_severity.get(severity_key, 0) + 1

        return result

    def _validate_document(self, dm: DocumentMetrics) -> List[ValidationIssue]:
        """Validate single document and return issues"""
        issues = []

        # Document-level checks
        # 1. Processing error
        if dm.status == "failed":
            issues.append(ValidationIssue(
                doc_id=dm.doc_id,
                issue_type=IssueType.PROCESSING_ERROR,
                severity=IssueSeverity.CRITICAL,
                message=f"Document processing failed: {dm.error_message}",
                details={"error_message": dm.error_message},
            ))

        # 2. Low overall quality
        if dm.avg_quality_score < self.thresholds.manual_review_trigger:
            issues.append(ValidationIssue(
                doc_id=dm.doc_id,
                issue_type=IssueType.LOW_QUALITY,
                severity=IssueSeverity.HIGH,
                message=f"Document quality below threshold: {dm.avg_quality_score:.4f}",
                details={
                    "avg_quality_score": dm.avg_quality_score,
                    "threshold": self.thresholds.manual_review_trigger,
                },
            ))
        elif dm.avg_quality_score < self.thresholds.quality_threshold:
            issues.append(ValidationIssue(
                doc_id=dm.doc_id,
                issue_type=IssueType.LOW_QUALITY,
                severity=IssueSeverity.MEDIUM,
                message=f"Document quality marginal: {dm.avg_quality_score:.4f}",
                details={
                    "avg_quality_score": dm.avg_quality_score,
                    "threshold": self.thresholds.quality_threshold,
                },
            ))

        # 3. High broken character ratio
        if dm.broken_char_ratio > self.thresholds.broken_ratio_alert:
            issues.append(ValidationIssue(
                doc_id=dm.doc_id,
                issue_type=IssueType.BROKEN_CHARACTERS,
                severity=IssueSeverity.HIGH,
                message=f"High broken character ratio: {dm.broken_char_ratio:.4f}",
                details={
                    "broken_char_ratio": dm.broken_char_ratio,
                    "threshold": self.thresholds.broken_ratio_alert,
                },
            ))

        # 4. Manual review pages
        if dm.pages_manual > 0:
            severity = IssueSeverity.HIGH if dm.pages_manual > dm.total_pages * 0.2 else IssueSeverity.MEDIUM
            issues.append(ValidationIssue(
                doc_id=dm.doc_id,
                issue_type=IssueType.MANUAL_REVIEW,
                severity=severity,
                message=f"{dm.pages_manual} pages need manual review",
                details={
                    "pages_manual": dm.pages_manual,
                    "total_pages": dm.total_pages,
                    "ratio": dm.pages_manual / dm.total_pages if dm.total_pages > 0 else 0,
                },
            ))

        # Page-level checks
        for pm in dm.pages:
            page_issues = self._validate_page(dm.doc_id, pm)
            issues.extend(page_issues)

        return issues

    def _validate_page(self, doc_id: str, pm: PageMetrics) -> List[ValidationIssue]:
        """Validate single page and return issues"""
        issues = []

        # 1. Very low quality page
        if pm.quality_score < self.thresholds.manual_review_trigger and pm.quality_score > 0:
            issues.append(ValidationIssue(
                doc_id=doc_id,
                issue_type=IssueType.LOW_QUALITY,
                severity=IssueSeverity.HIGH,
                page_num=pm.page_num,
                message=f"Page quality very low: {pm.quality_score:.4f}",
                details={
                    "quality_score": pm.quality_score,
                    "threshold": self.thresholds.manual_review_trigger,
                },
            ))

        # 2. High broken ratio on page
        if pm.broken_char_ratio > self.thresholds.broken_ratio_alert:
            issues.append(ValidationIssue(
                doc_id=doc_id,
                issue_type=IssueType.BROKEN_CHARACTERS,
                severity=IssueSeverity.MEDIUM,
                page_num=pm.page_num,
                message=f"Page has high broken character ratio: {pm.broken_char_ratio:.4f}",
                details={
                    "broken_char_ratio": pm.broken_char_ratio,
                    "threshold": self.thresholds.broken_ratio_alert,
                },
            ))

        # 3. Low OCR confidence
        if pm.strategy == "ocr" and pm.ocr_confidence < self.thresholds.ocr_confidence_min:
            issues.append(ValidationIssue(
                doc_id=doc_id,
                issue_type=IssueType.OCR_CONFIDENCE,
                severity=IssueSeverity.MEDIUM,
                page_num=pm.page_num,
                message=f"Low OCR confidence: {pm.ocr_confidence:.4f}",
                details={
                    "ocr_confidence": pm.ocr_confidence,
                    "threshold": self.thresholds.ocr_confidence_min,
                },
            ))

        # 4. Empty page (but not skipped)
        if pm.strategy != "skip" and pm.char_count < self.thresholds.empty_page_threshold:
            issues.append(ValidationIssue(
                doc_id=doc_id,
                issue_type=IssueType.EMPTY_PAGE,
                severity=IssueSeverity.LOW,
                page_num=pm.page_num,
                message=f"Page has very few characters: {pm.char_count}",
                details={
                    "char_count": pm.char_count,
                    "threshold": self.thresholds.empty_page_threshold,
                },
            ))

        # 5. Manual review flag
        if pm.needs_manual_review:
            issues.append(ValidationIssue(
                doc_id=doc_id,
                issue_type=IssueType.MANUAL_REVIEW,
                severity=IssueSeverity.MEDIUM,
                page_num=pm.page_num,
                message="Page flagged for manual review",
                details={
                    "quality_score": pm.quality_score,
                    "strategy": pm.strategy,
                },
            ))

        return issues

    def calibrate_thresholds(
        self,
        doc_metrics_list: List[DocumentMetrics],
        target_success_rate: float = 0.95,
        target_manual_review_rate: float = 0.03,
    ) -> ValidationThresholds:
        """
        Calibrate thresholds based on pilot results

        Args:
            doc_metrics_list: Pilot document metrics
            target_success_rate: Target success rate
            target_manual_review_rate: Target manual review rate

        Returns:
            Calibrated ValidationThresholds
        """
        if not doc_metrics_list:
            return self.thresholds

        # Collect all quality scores
        all_scores = []
        for dm in doc_metrics_list:
            if dm.avg_quality_score > 0:
                all_scores.append(dm.avg_quality_score)

        if not all_scores:
            return self.thresholds

        # Sort scores
        sorted_scores = sorted(all_scores)
        n = len(sorted_scores)

        # Calculate new thresholds
        # quality_threshold: Top (target_success_rate) percentile
        quality_idx = int(n * (1 - target_success_rate))
        new_quality_threshold = sorted_scores[quality_idx]

        # manual_review_trigger: Bottom (target_manual_review_rate) percentile
        manual_idx = int(n * target_manual_review_rate)
        new_manual_threshold = sorted_scores[manual_idx]

        # Create new thresholds
        new_thresholds = ValidationThresholds(
            quality_threshold=max(new_quality_threshold, 0.75),  # Floor at 0.75
            manual_review_trigger=min(new_manual_threshold, 0.55),  # Ceiling at 0.55
            broken_ratio_alert=self.thresholds.broken_ratio_alert,
            word_recognition_min=self.thresholds.word_recognition_min,
            ocr_confidence_min=self.thresholds.ocr_confidence_min,
        )

        return new_thresholds

    def generate_calibration_report(
        self,
        original: ValidationThresholds,
        calibrated: ValidationThresholds,
    ) -> Dict[str, Any]:
        """Generate calibration report comparing thresholds"""
        report = {
            "generated_at": datetime.now().isoformat(),
            "original": original.to_dict(),
            "calibrated": calibrated.to_dict(),
            "changes": {},
        }

        for key in original.to_dict():
            orig_val = getattr(original, key)
            cal_val = getattr(calibrated, key)
            if orig_val != cal_val:
                report["changes"][key] = {
                    "original": orig_val,
                    "calibrated": cal_val,
                    "delta": cal_val - orig_val,
                }

        return report


# CLI test
if __name__ == "__main__":
    from .metrics import MetricsCalculator, PageMetrics

    # Create test data
    calc = MetricsCalculator()

    doc_metrics_list = []

    # Good document
    dm1 = calc.calculate_document_metrics(
        doc_id="good_doc",
        category="uu",
        year=2020,
        page_results=[
            {"page_num": 0, "quality_score": 0.95, "strategy": "text", "char_count": 3000},
            {"page_num": 1, "quality_score": 0.92, "strategy": "text", "char_count": 2800},
        ],
    )
    doc_metrics_list.append(dm1)

    # Bad document
    dm2 = calc.calculate_document_metrics(
        doc_id="bad_doc",
        category="pp",
        year=1965,
        page_results=[
            {"page_num": 0, "quality_score": 0.35, "strategy": "ocr", "ocr_confidence": 0.45, "char_count": 500, "broken_ratio": 0.15},
            {"page_num": 1, "quality_score": 0.40, "strategy": "ocr", "ocr_confidence": 0.50, "char_count": 600, "needs_manual_review": True},
        ],
    )
    doc_metrics_list.append(dm2)

    # Validate
    validator = Validator()
    result = validator.validate("test_run", doc_metrics_list)

    print("=== Validation Result ===")
    print(f"Total documents: {result.total_documents}")
    print(f"Documents with issues: {result.documents_with_issues}")
    print(f"Total issues: {result.total_issues}")
    print(f"\nBy type: {result.issues_by_type}")
    print(f"By severity: {result.issues_by_severity}")

    print("\n=== Issues ===")
    for issue in result.issues[:5]:
        print(f"  [{issue.severity.value}] {issue.doc_id} p.{issue.page_num}: {issue.message}")
