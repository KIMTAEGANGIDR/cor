"""
ILIS OCR Quality Validator

자동화된 OCR 품질 검증 시스템 (KOICA zero-defect 기준)
- 자동 품질 점수 계산
- 오류 감지 및 분류
- 품질 등급에 따른 검토 요구사항 결정

품질 등급 및 인간 개입 요구:
- PASS (≥95%): 자동 승인 - 인간 개입 불필요
- WARNING (90-95%): 샘플 검토 - 무작위 10% 확인 권장
- FAIL (<90%): 수동 검토 - 전수 확인 필수

법률 문서 특성상 95% 미만은 100단어당 5개 이상 오류를 의미하므로
KOICA 품질 기준을 충족하지 못함.
"""

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pathlib import Path

from .indonesian_dict import IndonesianDictionary
from .ocr_corrector import OCRCorrector

logger = logging.getLogger(__name__)


class QualityGrade(Enum):
    """품질 등급 (KOICA zero-defect 기준)"""
    PASS = "pass"           # ≥95% - 자동 승인, 인간 개입 불필요
    WARNING = "warning"     # 90-95% - 샘플 검토 권장 (무작위 10% 확인)
    FAIL = "fail"           # <90% - 수동 검토 필수 (전수 확인)
    ERROR = "error"         # 처리 오류


@dataclass
class QualityReport:
    """품질 검증 리포트"""
    grade: QualityGrade
    overall_score: float  # 0-100

    # 세부 점수
    confidence_score: float = 0.0      # OCR 신뢰도
    dictionary_score: float = 0.0      # 사전 검증 점수
    legal_term_score: float = 0.0      # 법률 용어 점수
    structure_score: float = 0.0       # 구조 패턴 점수

    # 상세 정보
    total_words: int = 0
    valid_words: int = 0
    legal_terms_found: int = 0
    structure_patterns_found: int = 0

    # 문제점
    issues: list = field(default_factory=list)

    # 권장 조치
    recommendation: str = ""


class QualityValidator:
    """OCR 품질 검증기 - IndonesianDictionary 기반"""

    # 구조 패턴 (정규식)
    STRUCTURE_PATTERNS = [
        r"(?i)^pasal\s+\d+",           # Pasal 1, Pasal 2, ...
        r"(?i)^\(\d+\)",               # (1), (2), ...
        r"(?i)^[a-z]\.",               # a., b., c., ...
        r"(?i)^bab\s+[ivxlcdm]+",      # BAB I, BAB II, ...
        r"(?i)^bagian\s+\w+",          # Bagian Kesatu, ...
        r"(?i)^ayat\s+\(\d+\)",        # Ayat (1), ...
    ]

    # 품질 임계값 (KOICA zero-defect 기준) - 기본값
    THRESHOLDS = {
        "pass": 95.0,       # 95% 이상: 자동 승인 (KOICA 기준)
        "warning": 90.0,    # 90-95%: 재처리 시도 (최대 5회)
        # <90%: FAIL → LLM 교정 필요
    }

    # 연도별 동적 임계치 (오래된 문서 관용)
    # 스캔 품질과 옛 철자법 변환 한계를 고려
    YEAR_BASED_THRESHOLDS = {
        # (연도 상한, pass 임계치, warning 임계치)
        1970: (88.0, 83.0),   # 1970년 미만: 옛 철자법 + 저품질 스캔
        1990: (91.0, 86.0),   # 1970-1989: 초기 EYD 시대
        2010: (93.0, 88.0),   # 1990-2009: 현대 문서
        9999: (95.0, 90.0),   # 2010 이후: 최신 문서 (기본값)
    }

    # 가중치 (OCR 품질 중심, 구조 패턴 의존도 낮춤)
    # - 오래된/짧은 문서는 구조가 단순할 수 있음
    # - OCR 신뢰도와 사전 검증이 더 중요
    WEIGHTS = {
        "confidence": 0.45,      # OCR 신뢰도 45%
        "dictionary": 0.35,      # 사전 검증 35%
        "legal_terms": 0.15,     # 법률 용어 15%
        "structure": 0.05,       # 구조 패턴 5% (짧은 문서 고려)
    }

    def __init__(self, use_dynamic_threshold: bool = True):
        """
        Args:
            use_dynamic_threshold: 연도별 동적 임계치 사용 여부
        """
        self._compiled_patterns = [
            re.compile(p, re.MULTILINE) for p in self.STRUCTURE_PATTERNS
        ]
        # IndonesianDictionary 사용 (Sastrawi 스테머 포함)
        self._indonesian_dict = IndonesianDictionary()
        self.use_dynamic_threshold = use_dynamic_threshold

    def get_quality_threshold(self, document_year: Optional[int] = None) -> tuple[float, float]:
        """
        연도별 동적 품질 임계치 반환

        Args:
            document_year: 문서 연도 (None이면 기본값 사용)

        Returns:
            (pass 임계치, warning 임계치) 튜플
        """
        if not self.use_dynamic_threshold or document_year is None:
            return (self.THRESHOLDS["pass"], self.THRESHOLDS["warning"])

        for year_limit, (pass_thresh, warning_thresh) in sorted(self.YEAR_BASED_THRESHOLDS.items()):
            if document_year < year_limit:
                return (pass_thresh, warning_thresh)

        # 기본값
        return (self.THRESHOLDS["pass"], self.THRESHOLDS["warning"])

    def validate(
        self,
        text: str,
        confidence_scores: list[float],
        min_words: int = 50,
        document_year: Optional[int] = None,
    ) -> QualityReport:
        """
        OCR 결과 품질 검증

        Args:
            text: OCR 추출 텍스트
            confidence_scores: 각 라인의 신뢰도 점수 리스트
            min_words: 최소 단어 수 (이하면 FAIL)
            document_year: 문서 연도 (동적 임계치 적용, None이면 기본값)

        Returns:
            QualityReport
        """
        issues = []

        # 텍스트 전처리
        words = self._extract_words(text)
        total_words = len(words)

        # 최소 단어 수 확인
        if total_words < min_words:
            return QualityReport(
                grade=QualityGrade.FAIL,
                overall_score=0.0,
                total_words=total_words,
                issues=[f"텍스트가 너무 짧음 ({total_words} 단어 < {min_words})"],
                recommendation="PDF가 스캔 품질이 낮거나 빈 페이지일 수 있음",
            )

        # 1. OCR 신뢰도 점수
        confidence_score = self._calculate_confidence_score(confidence_scores)
        if confidence_score < 90:
            issues.append(f"낮은 OCR 신뢰도: {confidence_score:.1f}%")

        # 2. 사전 검증 점수
        dictionary_score, valid_words = self._calculate_dictionary_score(words)
        if dictionary_score < 80:
            issues.append(f"사전 검증 실패율 높음: {100-dictionary_score:.1f}%")

        # 3. 법률 용어 점수
        legal_term_score, legal_terms_found = self._calculate_legal_term_score(words)
        if legal_terms_found < 5:
            issues.append(f"법률 용어 부족: {legal_terms_found}개")

        # 4. 구조 패턴 점수
        structure_score, patterns_found = self._calculate_structure_score(text)
        if patterns_found < 3:
            issues.append(f"법률 구조 패턴 부족: {patterns_found}개")

        # 종합 점수 계산
        overall_score = (
            confidence_score * self.WEIGHTS["confidence"] +
            dictionary_score * self.WEIGHTS["dictionary"] +
            legal_term_score * self.WEIGHTS["legal_terms"] +
            structure_score * self.WEIGHTS["structure"]
        )

        # 연도별 동적 임계치 적용
        pass_threshold, warning_threshold = self.get_quality_threshold(document_year)

        # 등급 결정
        if overall_score >= pass_threshold:
            grade = QualityGrade.PASS
            if document_year and document_year < 1990:
                recommendation = f"자동 승인 (옛 문서 기준 {pass_threshold:.0f}% 적용)"
            else:
                recommendation = "자동 승인"
        elif overall_score >= warning_threshold:
            grade = QualityGrade.WARNING
            recommendation = f"샘플 검토 필요 - {pass_threshold:.0f}% 미만으로 품질 확인 권장"
        else:
            grade = QualityGrade.FAIL
            recommendation = "수동 검토 필요"

        return QualityReport(
            grade=grade,
            overall_score=overall_score,
            confidence_score=confidence_score,
            dictionary_score=dictionary_score,
            legal_term_score=legal_term_score,
            structure_score=structure_score,
            total_words=total_words,
            valid_words=valid_words,
            legal_terms_found=legal_terms_found,
            structure_patterns_found=patterns_found,
            issues=issues,
            recommendation=recommendation,
        )

    def _extract_words(self, text: str) -> list[str]:
        """텍스트에서 단어 추출"""
        # 소문자로, 특수문자 제거
        text = text.lower()
        text = re.sub(r'[^\w\s-]', ' ', text)
        words = text.split()
        # 숫자만 있는 단어 제거, 1글자 제거
        words = [w for w in words if len(w) > 1 and not w.isdigit()]
        return words

    def _calculate_confidence_score(self, scores: list[float]) -> float:
        """OCR 신뢰도 점수 계산"""
        if not scores:
            return 0.0

        # 평균 신뢰도 (0-1 → 0-100)
        avg = sum(scores) / len(scores) * 100

        # 낮은 신뢰도 비율 페널티
        low_conf_count = sum(1 for s in scores if s < 0.8)
        low_conf_ratio = low_conf_count / len(scores)
        penalty = low_conf_ratio * 10  # 최대 10점 감점

        return max(0, min(100, avg - penalty))

    def _calculate_dictionary_score(self, words: list[str]) -> tuple[float, int]:
        """사전 검증 점수 계산 - IndonesianDictionary + Sastrawi 스테머 사용"""
        if not words:
            return 0.0, 0

        valid_count = 0
        for word in words:
            # IndonesianDictionary로 검증 (스테머 포함)
            if self._indonesian_dict.is_valid_word(word):
                valid_count += 1
            # 숫자 포함 (법조문 번호 등)
            elif any(c.isdigit() for c in word):
                valid_count += 1
            # 충분히 긴 단어 (4자 이상, 알 수 없는 고유명사 등)
            elif len(word) >= 4:
                valid_count += 0.7  # 부분 점수

        score = (valid_count / len(words)) * 100
        return score, int(valid_count)

    def _calculate_legal_term_score(self, words: list[str]) -> tuple[float, int]:
        """법률 용어 점수 계산 - IndonesianDictionary 사용"""
        if not words:
            return 0.0, 0

        found_terms = set()
        for word in words:
            if self._indonesian_dict.is_legal_term(word):
                found_terms.add(word)

        # 최소 10개 법률 용어 기대
        expected_min = 10
        found_count = len(found_terms)

        if found_count >= expected_min:
            score = 100.0
        else:
            score = (found_count / expected_min) * 100

        return score, found_count

    def _calculate_structure_score(self, text: str) -> tuple[float, int]:
        """구조 패턴 점수 계산"""
        found_patterns = 0

        for pattern in self._compiled_patterns:
            matches = pattern.findall(text)
            if matches:
                found_patterns += min(len(matches), 5)  # 패턴당 최대 5점

        # 최소 15개 패턴 기대 (Pasal, ayat 등)
        expected_min = 15

        if found_patterns >= expected_min:
            score = 100.0
        else:
            score = (found_patterns / expected_min) * 100

        return score, found_patterns


class ProcessingStage(Enum):
    """처리 단계"""
    OCR = "ocr"                    # 초기 OCR
    REPROCESS = "reprocess"        # 재처리 중
    LLM_CORRECTION = "llm"         # LLM 교정 중
    HUMAN_REVIEW = "human"         # 인간 검토 대기
    COMPLETED = "completed"        # 완료


@dataclass
class AutomatedPipelineResult:
    """자동화 파이프라인 결과"""
    pdf_path: str
    quality_report: QualityReport

    # 처리 상태
    stage: ProcessingStage = ProcessingStage.OCR
    auto_approved: bool = False
    needs_llm: bool = False
    needs_human_review: bool = False

    # 재처리 정보
    reprocess_count: int = 0
    reprocess_scores: list = field(default_factory=list)  # 각 시도별 점수

    # LLM 처리 정보
    llm_processed: bool = False
    llm_confident: bool = False
    llm_corrections: list = field(default_factory=list)

    # 최종 텍스트
    final_text: str = ""

    def __str__(self) -> str:
        if self.auto_approved:
            status = "✅ 자동승인"
        elif self.llm_processed and self.llm_confident:
            status = "🤖 LLM승인"
        elif self.needs_human_review:
            status = "👤 인간검토"
        elif self.needs_llm:
            status = "🤖 LLM대기"
        else:
            status = f"🔄 재처리({self.reprocess_count}/5)"

        return (
            f"{Path(self.pdf_path).name}: {status} "
            f"(점수: {self.quality_report.overall_score:.1f}%)"
        )


class AutomatedPipeline:
    """
    인간 개입 최소화 자동화 파이프라인 (KOICA zero-defect)

    처리 흐름:
    ┌─────────────────────────────────────────────────────────────┐
    │ PDF → Surya OCR → OCR교정 → 품질검증                        │
    │                                  ↓                          │
    │                    ┌─────────────┴─────────────┐            │
    │                    │                           │            │
    │              ≥95% PASS                   <95% (재처리)      │
    │              (자동승인)                        ↓            │
    │                              ┌─────────────────┴──────┐     │
    │                              │                        │     │
    │                       90-95% WARNING            <90% FAIL   │
    │                              ↓                        │     │
    │                       최대 5회 재처리                 │     │
    │                              ↓                        │     │
    │                       개선 안됨 ─────────────────────→│     │
    │                                                       ↓     │
    │                                                  LLM 교정   │
    │                                                       ↓     │
    │                                          ┌───────────┴───┐  │
    │                                          │               │  │
    │                                    LLM 확신 O      LLM 애매함│
    │                                    (자동승인)          ↓    │
    │                                                   인간 검토 │
    └─────────────────────────────────────────────────────────────┘

    임계값:
    - PASS: ≥95% (KOICA zero-defect 기준)
    - WARNING: 90-95% (최대 5회 재처리)
    - FAIL: <90% (LLM 교정 필요)
    """

    # 재처리 설정
    MAX_REPROCESS_ATTEMPTS = 5     # WARNING 최대 재처리 횟수
    REPROCESS_DPI = 300            # 고해상도 재처리 DPI
    LOW_CONFIDENCE_THRESHOLD = 0.85

    # LLM 설정
    LLM_CONFIDENCE_THRESHOLD = 0.9  # LLM이 90% 이상 확신하면 자동 승인

    def __init__(
        self,
        surya_pipeline,  # SuryaPipeline 인스턴스
        output_dir: Optional[Path] = None,
        llm_corrector=None,  # LLM 교정기 (없으면 인간 검토로)
    ):
        self.surya = surya_pipeline
        self.validator = QualityValidator()
        self.output_dir = output_dir or Path("/tmp/ocr_output")
        self.llm_corrector = llm_corrector

        # 출력 디렉토리 구조
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "approved").mkdir(exist_ok=True)
        (self.output_dir / "llm_queue").mkdir(exist_ok=True)
        (self.output_dir / "human_queue").mkdir(exist_ok=True)

        # 통계
        self.stats = {
            "total": 0,
            "auto_approved": 0,       # ≥95% 자동 승인
            "reprocess_attempts": 0,  # 재처리 시도 횟수
            "reprocess_success": 0,   # 재처리로 95% 달성
            "llm_processed": 0,       # LLM 처리 수
            "llm_approved": 0,        # LLM이 확신하여 승인
            "human_review": 0,        # 인간 검토 필요
            "errors": 0,
        }

    def _find_low_confidence_pages(
        self,
        ocr_result,
        threshold: float = None,
    ) -> list[int]:
        """
        신뢰도 낮은 페이지 찾기

        Returns:
            페이지 번호 리스트 (0-indexed)
        """
        threshold = threshold or self.LOW_CONFIDENCE_THRESHOLD
        low_pages = set()

        # 각 라인의 페이지 정보가 있으면 사용
        for i, line in enumerate(ocr_result.text_lines):
            if line.get("confidence", 1.0) < threshold:
                # 페이지 번호 추정 (polygon 좌표나 인덱스 기반)
                page_num = line.get("page", i // 50)  # 대략 페이지당 50라인 가정
                low_pages.add(page_num)

        return sorted(low_pages)

    def _reprocess_pages_high_dpi(
        self,
        pdf_path: Path,
        page_nums: list[int],
    ) -> tuple[list[dict], list[float]]:
        """
        특정 페이지를 고해상도 이미지로 변환 후 재처리

        Args:
            pdf_path: PDF 파일 경로
            page_nums: 재처리할 페이지 번호 (0-indexed)

        Returns:
            (text_lines, confidences) 튜플
        """
        import subprocess
        import tempfile
        import json

        try:
            import fitz
        except ImportError:
            logger.warning("PyMuPDF not available for high-DPI reprocess")
            return [], []

        if not page_nums:
            return [], []

        all_lines = []
        all_confidences = []

        with tempfile.TemporaryDirectory(prefix="reprocess_") as tmpdir:
            tmpdir = Path(tmpdir)

            # PDF 페이지를 고해상도 이미지로 변환
            doc = fitz.open(pdf_path)
            try:
                for page_num in page_nums:
                    if page_num >= len(doc):
                        continue

                    page = doc[page_num]
                    # 300 DPI로 렌더링 (기본 72 DPI 대비 약 4배)
                    mat = fitz.Matrix(self.REPROCESS_DPI / 72, self.REPROCESS_DPI / 72)
                    pix = page.get_pixmap(matrix=mat)

                    img_path = tmpdir / f"page_{page_num:04d}.png"
                    pix.save(str(img_path))

                    # Surya OCR로 이미지 처리
                    output_dir = tmpdir / f"output_{page_num}"
                    cmd = ["surya_ocr", str(img_path), "--output_dir", str(output_dir)]

                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )

                    if result.returncode == 0:
                        # 결과 파싱
                        for json_file in output_dir.rglob("results.json"):
                            with open(json_file) as f:
                                data = json.load(f)

                            for key, pages in data.items():
                                for page_result in pages:
                                    for line in page_result.get("text_lines", []):
                                        all_lines.append({
                                            "text": line["text"],
                                            "confidence": line["confidence"],
                                            "page": page_num,
                                        })
                                        all_confidences.append(line["confidence"])
            finally:
                doc.close()

        return all_lines, all_confidences

    def process(self, pdf_path: Path) -> AutomatedPipelineResult:
        """
        단일 PDF 처리 (새 흐름)

        1. OCR + 교정 → 품질 검증
        2. ≥95%: 자동 승인
        3. 90-95%: 최대 5회 재처리
        4. <90% 또는 재처리 실패: LLM 교정
        5. LLM 애매함: 인간 검토
        """
        self.stats["total"] += 1
        pdf_path = Path(pdf_path)

        # 1. 초기 OCR + 교정 + 검증
        text, confidences, report, ocr_result = self._initial_ocr(pdf_path)

        if report is None:  # OCR 에러
            self.stats["errors"] += 1
            return AutomatedPipelineResult(
                pdf_path=str(pdf_path),
                quality_report=QualityReport(
                    grade=QualityGrade.ERROR,
                    overall_score=0.0,
                    issues=["OCR 처리 오류"],
                    recommendation="PDF 파일 확인 필요",
                ),
                stage=ProcessingStage.HUMAN_REVIEW,
                needs_human_review=True,
            )

        result = AutomatedPipelineResult(
            pdf_path=str(pdf_path),
            quality_report=report,
            final_text=text,
            reprocess_scores=[report.overall_score],
        )

        # 2. ≥95%: 자동 승인
        if report.grade == QualityGrade.PASS:
            result.auto_approved = True
            result.stage = ProcessingStage.COMPLETED
            self.stats["auto_approved"] += 1
            self._save_approved(pdf_path, text, report)
            return result

        # 3. 90-95% (WARNING): 최대 5회 재처리
        if report.grade == QualityGrade.WARNING:
            result.stage = ProcessingStage.REPROCESS

            for attempt in range(self.MAX_REPROCESS_ATTEMPTS):
                result.reprocess_count = attempt + 1
                self.stats["reprocess_attempts"] += 1

                # 품질 낮은 페이지 재처리
                low_pages = self._find_low_confidence_pages(ocr_result)
                if not low_pages:
                    break

                new_text, new_conf, new_report = self._reprocess_attempt(
                    pdf_path, ocr_result, low_pages
                )

                if new_report is None:
                    continue

                result.reprocess_scores.append(new_report.overall_score)

                # 개선 확인
                if new_report.overall_score > report.overall_score:
                    text = new_text
                    confidences = new_conf
                    report = new_report
                    result.quality_report = report
                    result.final_text = text

                    logger.info(
                        f"재처리 {attempt+1}회: {result.reprocess_scores[-2]:.1f}% → "
                        f"{report.overall_score:.1f}%"
                    )

                    # 95% 달성 시 자동 승인
                    if report.grade == QualityGrade.PASS:
                        result.auto_approved = True
                        result.stage = ProcessingStage.COMPLETED
                        self.stats["auto_approved"] += 1
                        self.stats["reprocess_success"] += 1
                        self._save_approved(pdf_path, text, report)
                        return result
                else:
                    # 개선 없으면 중단
                    logger.info(f"재처리 {attempt+1}회: 개선 없음, 중단")
                    break

        # 4. <90% (FAIL) 또는 재처리로 95% 미달성 → LLM 교정
        result.needs_llm = True
        result.stage = ProcessingStage.LLM_CORRECTION

        if self.llm_corrector:
            llm_result = self._process_with_llm(text, report)

            if llm_result:
                result.llm_processed = True
                result.llm_corrections = llm_result.get("corrections", [])

                llm_text = llm_result.get("text", text)
                llm_confidence = llm_result.get("confidence", 0.0)

                # LLM 결과 재검증
                llm_report = self.validator.validate(llm_text, confidences)
                self.stats["llm_processed"] += 1

                # LLM이 90% 이상 확신하고 품질도 95% 이상이면 승인
                if (llm_confidence >= self.LLM_CONFIDENCE_THRESHOLD and
                    llm_report.grade == QualityGrade.PASS):
                    result.llm_confident = True
                    result.auto_approved = True
                    result.stage = ProcessingStage.COMPLETED
                    result.quality_report = llm_report
                    result.final_text = llm_text
                    self.stats["llm_approved"] += 1
                    self._save_approved(pdf_path, llm_text, llm_report)
                    return result

        # 5. LLM 실패 또는 애매함 → 인간 검토
        result.needs_llm = False
        result.needs_human_review = True
        result.stage = ProcessingStage.HUMAN_REVIEW
        self.stats["human_review"] += 1
        self._queue_for_human_review(pdf_path, text, report, result)

        return result

    def _initial_ocr(self, pdf_path: Path):
        """초기 OCR + 교정 + 검증"""
        ocr_result = self.surya.process_pdf(pdf_path, skip_lampiran=True)

        if ocr_result.error:
            return None, None, None, None

        text = self.surya.get_full_text(ocr_result)
        confidences = [line["confidence"] for line in ocr_result.text_lines]

        # OCR 교정
        corrector = OCRCorrector()
        correction_result = corrector.correct(text)
        text = correction_result.corrected

        if correction_result.corrections_made > 0:
            logger.debug(f"OCR 교정: {correction_result.corrections_made}개")

        # 품질 검증
        report = self.validator.validate(text, confidences)

        return text, confidences, report, ocr_result

    def _reprocess_attempt(self, pdf_path: Path, ocr_result, low_pages: list[int]):
        """재처리 시도"""
        new_lines, new_confidences = self._reprocess_pages_high_dpi(pdf_path, low_pages)

        if not new_lines:
            return None, None, None

        # 원본과 병합
        merged_lines = []
        merged_confidences = []
        reprocessed_pages = set(low_pages)

        for line in ocr_result.text_lines:
            page = line.get("page", 0)
            if page not in reprocessed_pages:
                merged_lines.append(line)
                merged_confidences.append(line["confidence"])

        merged_lines.extend(new_lines)
        merged_confidences.extend(new_confidences)

        # 교정 + 검증
        merged_text = "\n".join(l["text"] for l in merged_lines)
        corrector = OCRCorrector()
        correction_result = corrector.correct(merged_text)
        merged_text = correction_result.corrected

        new_report = self.validator.validate(merged_text, merged_confidences)

        return merged_text, merged_confidences, new_report

    def _process_with_llm(self, text: str, report: QualityReport) -> Optional[dict]:
        """LLM 교정 처리"""
        if not self.llm_corrector:
            return None

        try:
            # LLM 교정기 인터페이스:
            # llm_corrector.correct(text, issues) -> {text, confidence, corrections}
            return self.llm_corrector.correct(text, report.issues)
        except Exception as e:
            logger.error(f"LLM 교정 오류: {e}")
            return None

    def _save_approved(self, pdf_path: Path, text: str, report: QualityReport):
        """승인된 결과 저장"""
        output_path = self.output_dir / "approved" / f"{pdf_path.stem}.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)

        # 메타데이터 저장
        meta_path = output_path.with_suffix(".json")
        import json
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "pdf_path": str(pdf_path),
                "quality_score": report.overall_score,
                "grade": report.grade.value,
            }, f, ensure_ascii=False, indent=2)

        logger.info(f"✅ 승인: {pdf_path.name} ({report.overall_score:.1f}%)")

    def _queue_for_human_review(
        self,
        pdf_path: Path,
        text: str,
        report: QualityReport,
        result: AutomatedPipelineResult,
    ):
        """인간 검토 대기열에 추가"""
        import json

        # 텍스트 저장
        text_path = self.output_dir / "human_queue" / f"{pdf_path.stem}.txt"
        text_path.parent.mkdir(parents=True, exist_ok=True)
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(text)

        # 메타데이터 저장
        meta_path = text_path.with_suffix(".json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "pdf_path": str(pdf_path),
                "quality_score": report.overall_score,
                "grade": report.grade.value,
                "issues": report.issues,
                "reprocess_count": result.reprocess_count,
                "reprocess_scores": result.reprocess_scores,
                "llm_processed": result.llm_processed,
                "llm_confident": result.llm_confident,
            }, f, ensure_ascii=False, indent=2)

        logger.warning(f"👤 인간 검토 필요: {pdf_path.name} ({report.overall_score:.1f}%)")

    def get_stats_summary(self) -> str:
        """통계 요약"""
        total = self.stats["total"]
        if total == 0:
            return "처리된 PDF 없음"

        auto_rate = self.stats["auto_approved"] / total * 100
        reprocess_rate = self.stats["reprocess_attempts"] / total * 100
        reprocess_success_rate = (
            self.stats["reprocess_success"] / self.stats["reprocess_attempts"] * 100
            if self.stats["reprocess_attempts"] > 0 else 0
        )
        llm_rate = self.stats["llm_processed"] / total * 100
        llm_success_rate = (
            self.stats["llm_approved"] / self.stats["llm_processed"] * 100
            if self.stats["llm_processed"] > 0 else 0
        )
        human_rate = self.stats["human_review"] / total * 100

        # 자동화율 = (자동승인 + LLM승인) / 전체
        automation_count = self.stats["auto_approved"] + self.stats["llm_approved"]
        automation_rate = automation_count / total * 100 if total > 0 else 0

        return f"""
{'=' * 65}
처리 통계 (KOICA zero-defect 파이프라인)
{'=' * 65}
총 처리: {total}

[1단계: OCR + 교정]
  ✅ 자동 승인 (≥95%): {self.stats['auto_approved']} ({auto_rate:.1f}%)

[2단계: 재처리 (90-95%)]
  🔄 재처리 시도: {self.stats['reprocess_attempts']}회
     └─ 95% 달성: {self.stats['reprocess_success']} ({reprocess_success_rate:.1f}%)

[3단계: LLM 교정 (<90% 또는 재처리 실패)]
  🤖 LLM 처리: {self.stats['llm_processed']} ({llm_rate:.1f}%)
     └─ LLM 승인: {self.stats['llm_approved']} ({llm_success_rate:.1f}%)

[4단계: 인간 검토]
  👤 인간 검토 필요: {self.stats['human_review']} ({human_rate:.1f}%)

[오류]
  🔴 처리 오류: {self.stats['errors']}

{'─' * 65}
[최종 결과]
  → 자동화 처리: {automation_count}/{total} ({automation_rate:.1f}%)
  → 인간 개입 필요: {self.stats['human_review']}/{total} ({human_rate:.1f}%)
{'=' * 65}
"""


def main():
    """테스트"""
    import argparse

    parser = argparse.ArgumentParser(description="OCR Quality Validator")
    parser.add_argument("--test", action="store_true", help="Run test")
    args = parser.parse_args()

    if args.test:
        # 테스트 텍스트
        test_text = """
        PRESIDEN REPUBLIK INDONESIA
        UNDANG-UNDANG REPUBLIK INDONESIA
        NOMOR 1 TAHUN 2024
        TENTANG
        PERUBAHAN ATAS UNDANG-UNDANG

        Menimbang: bahwa untuk mewujudkan tujuan negara...
        Mengingat: Pasal 5 ayat (1) Undang-Undang Dasar...

        MEMUTUSKAN:
        Menetapkan: UNDANG-UNDANG TENTANG PERUBAHAN

        BAB I
        KETENTUAN UMUM

        Pasal 1
        Dalam Undang-Undang ini yang dimaksud dengan:
        (1) Pemerintah adalah...
        (2) Menteri adalah...

        Pasal 2
        (1) Ketentuan sebagaimana dimaksud dalam Pasal 1...
        """

        # 가상 신뢰도 점수
        confidences = [0.95] * 30

        validator = QualityValidator()
        report = validator.validate(test_text, confidences)

        print("=" * 60)
        print("품질 검증 결과")
        print("=" * 60)
        print(f"등급: {report.grade.value.upper()}")
        print(f"종합 점수: {report.overall_score:.1f}%")
        print(f"\n세부 점수:")
        print(f"  신뢰도: {report.confidence_score:.1f}%")
        print(f"  사전 검증: {report.dictionary_score:.1f}%")
        print(f"  법률 용어: {report.legal_term_score:.1f}%")
        print(f"  구조 패턴: {report.structure_score:.1f}%")
        print(f"\n통계:")
        print(f"  총 단어: {report.total_words}")
        print(f"  유효 단어: {report.valid_words}")
        print(f"  법률 용어: {report.legal_terms_found}")
        print(f"  구조 패턴: {report.structure_patterns_found}")
        print(f"\n문제점: {report.issues}")
        print(f"권장 조치: {report.recommendation}")


if __name__ == "__main__":
    main()
