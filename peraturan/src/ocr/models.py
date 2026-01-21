"""
ILIS OCR Pipeline Data Models

각 테이블에 대응하는 데이터클래스 정의
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import json


# ============================================
# Enums
# ============================================

class DocumentStage(str, Enum):
    """문서 처리 단계"""
    PENDING = "pending"
    HEADER_EXTRACTED = "header_extracted"
    CLUSTERED = "clustered"
    PATTERN_ASSIGNED = "pattern_assigned"
    OCR_PROCESSING = "ocr_processing"
    OCR_COMPLETED = "ocr_completed"
    PARSED = "parsed"
    AKN_GENERATED = "akn_generated"
    VALIDATED = "validated"
    ERROR = "error"


class ClusterStatus(str, Enum):
    """클러스터 상태"""
    DRAFT = "draft"
    PENDING_VALIDATION = "pending_validation"
    APPROVED = "approved"
    NEEDS_REVISION = "needs_revision"
    MANUAL_REVIEW = "manual_review"


class ValidationResult(str, Enum):
    """검증 결과"""
    PENDING = "pending"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class LineClassification(str, Enum):
    """라인 분류"""
    NOISE = "noise"
    METADATA = "metadata"
    BODY = "body"


# ============================================
# Data Models
# ============================================

@dataclass
class Document:
    """처리할 문서"""
    id: str
    source: str  # 'peraturan.go.id' or 'bpk.go.id'
    jenis: str
    nomor: Optional[str] = None
    tahun: Optional[int] = None
    tentang: Optional[str] = None
    pdf_path: Optional[str] = None
    pdf_url: Optional[str] = None
    page_count: Optional[int] = None
    file_size: Optional[int] = None
    stage: str = DocumentStage.PENDING.value
    priority: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Header:
    """추출된 헤더"""
    id: Optional[int] = None
    document_id: str = ""
    image_path: Optional[str] = None
    header_ratio: float = 0.3
    raw_text: Optional[str] = None
    lines: Optional[list] = None
    ocr_confidence: Optional[float] = None
    cluster_id: Optional[int] = None
    status: str = "pending"
    error_message: Optional[str] = None
    created_at: Optional[str] = None

    @property
    def lines_list(self) -> list:
        """JSON 문자열을 리스트로 변환"""
        if isinstance(self.lines, str):
            return json.loads(self.lines)
        return self.lines or []


@dataclass
class Cluster:
    """헤더 클러스터"""
    id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    document_count: int = 0
    sample_documents: Optional[list] = None
    representative_text: Optional[str] = None
    centroid_vector: Optional[list] = None
    status: str = ClusterStatus.DRAFT.value
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def sample_docs_list(self) -> list:
        if isinstance(self.sample_documents, str):
            return json.loads(self.sample_documents)
        return self.sample_documents or []


@dataclass
class PatternRule:
    """패턴 룰 정의"""
    id: Optional[int] = None
    cluster_id: int = 0

    # 라인별 분류
    noise_lines: Optional[list] = None  # [0, 5, ...]
    metadata_lines: Optional[list] = None  # [1, 2, ...]
    body_start_line: Optional[int] = None

    # 메타데이터 매핑
    metadata_mapping: Optional[dict] = None  # {"1": "law_type", "2": "law_number"}

    # 패턴
    noise_patterns: Optional[list] = None
    header_regex: Optional[str] = None
    structure_config: Optional[dict] = None

    # 검토 정보
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    notes: Optional[str] = None

    version: int = 1
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_json_fields(self) -> dict:
        """JSON 필드들을 문자열로 변환"""
        return {
            "noise_lines": json.dumps(self.noise_lines) if self.noise_lines else None,
            "metadata_lines": json.dumps(self.metadata_lines) if self.metadata_lines else None,
            "metadata_mapping": json.dumps(self.metadata_mapping) if self.metadata_mapping else None,
            "noise_patterns": json.dumps(self.noise_patterns) if self.noise_patterns else None,
            "structure_config": json.dumps(self.structure_config) if self.structure_config else None,
        }


@dataclass
class ValidationHistory:
    """패턴 검증 이력"""
    id: Optional[int] = None
    cluster_id: int = 0
    pattern_rule_id: Optional[int] = None
    round: int = 1
    stage: str = "0.6"  # '0.6' or '0.7'
    sample_documents: Optional[list] = None
    result: Optional[str] = None  # approved, rejected
    rejection_reason: Optional[str] = None
    issues: Optional[list] = None
    changes_made: Optional[dict] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None


@dataclass
class OCRPage:
    """페이지별 OCR 결과"""
    id: Optional[int] = None
    document_id: str = ""
    page_number: int = 0
    image_path: Optional[str] = None
    raw_text: Optional[str] = None
    boxes: Optional[list] = None
    confidence: Optional[float] = None
    noise_removed: Optional[str] = None
    metadata_extracted: Optional[dict] = None
    body_text: Optional[str] = None
    status: str = "pending"
    error_message: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class OCRResult:
    """문서별 OCR 결과"""
    id: Optional[int] = None
    document_id: str = ""
    full_text: Optional[str] = None
    extracted_metadata: Optional[dict] = None
    total_pages: int = 0
    processed_pages: int = 0
    failed_pages: int = 0
    average_confidence: Optional[float] = None
    status: str = "pending"
    error_message: Optional[str] = None
    pattern_rule_id: Optional[int] = None
    cluster_id: Optional[int] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class ParsedStructure:
    """구조 파싱 결과"""
    id: Optional[int] = None
    document_id: str = ""
    structure_json: Optional[dict] = None
    bab_count: int = 0
    pasal_count: int = 0
    ayat_count: int = 0
    huruf_count: int = 0
    angka_count: int = 0
    preamble_text: Optional[str] = None
    body_text: Optional[str] = None
    closing_text: Optional[str] = None
    status: str = "pending"
    error_message: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class AKNOutput:
    """Akoma Ntoso 출력"""
    id: Optional[int] = None
    document_id: str = ""
    xml_content: Optional[str] = None
    xml_path: Optional[str] = None
    akn_uri: Optional[str] = None
    frbr_work: Optional[str] = None
    frbr_expression: Optional[str] = None
    status: str = "pending"
    schema_valid: bool = False
    created_at: Optional[str] = None


@dataclass
class FinalValidation:
    """최종 검증 결과"""
    id: Optional[int] = None
    document_id: str = ""
    status: str = ValidationResult.PENDING.value
    text_match_rate: Optional[float] = None
    missing_text: Optional[list] = None
    structure_complete: bool = False
    structure_issues: Optional[list] = None
    schema_valid: bool = False
    schema_errors: Optional[list] = None
    manual_review_needed: bool = False
    review_notes: Optional[str] = None
    created_at: Optional[str] = None
    reviewed_at: Optional[str] = None


@dataclass
class PipelineState:
    """파이프라인 상태"""
    total_documents: int = 0

    # Stage 0
    stage_0_pending: int = 0
    stage_0_completed: int = 0

    # Stage 0.5
    stage_05_pending: int = 0
    stage_05_completed: int = 0

    # Stage 0.6
    stage_06_pending: int = 0
    stage_06_approved: int = 0
    stage_06_rejected: int = 0

    # Stage 2
    stage_2_pending: int = 0
    stage_2_processing: int = 0
    stage_2_completed: int = 0

    # Stage 3
    stage_3_pending: int = 0
    stage_3_completed: int = 0

    # Stage 4
    stage_4_pending: int = 0
    stage_4_success: int = 0
    stage_4_warning: int = 0
    stage_4_error: int = 0

    started_at: Optional[str] = None
    last_updated_at: Optional[str] = None


@dataclass
class NoisePattern:
    """노이즈 패턴"""
    id: Optional[int] = None
    pattern: str = ""
    pattern_type: str = "regex"  # regex, exact, contains
    description: Optional[str] = None
    scope: str = "global"  # global, cluster_specific
    cluster_id: Optional[int] = None
    is_active: bool = True
    created_at: Optional[str] = None


@dataclass
class ProcessingLog:
    """처리 로그"""
    id: Optional[int] = None
    document_id: Optional[str] = None
    stage: Optional[str] = None
    action: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None
    details: Optional[dict] = None
    duration_ms: Optional[int] = None
    created_at: Optional[str] = None


# ============================================
# 메타데이터 필드 정의
# ============================================

METADATA_FIELDS = {
    "law_type": "법령 유형",
    "law_number": "법령 번호",
    "law_year": "법령 연도",
    "law_title": "법령 제목 (TENTANG 이후)",
    "issuing_authority": "제정 기관",
    "enactment_date": "제정일",
    "promulgation_date": "공포일",
    "signatory": "서명자",
}


# ============================================
# 인도네시아 법령 구조 정의
# ============================================

INDONESIAN_LAW_STRUCTURE = {
    "judul": {"akn": "longTitle", "desc": "법령 제목"},
    "menimbang": {"akn": "preamble/recitals", "desc": "고려사항"},
    "mengingat": {"akn": "preamble/citations", "desc": "법적 근거"},
    "memutuskan": {"akn": "preamble/formula", "desc": "결정"},
    "menetapkan": {"akn": "preamble/formula", "desc": "확정"},
    "bab": {"akn": "chapter", "desc": "장"},
    "bagian": {"akn": "section", "desc": "절"},
    "paragraf": {"akn": "subsection", "desc": "관"},
    "pasal": {"akn": "article", "desc": "조"},
    "ayat": {"akn": "paragraph", "desc": "항"},
    "huruf": {"akn": "point", "desc": "호"},
    "angka": {"akn": "point", "desc": "목"},
    "ketentuan_penutup": {"akn": "wrapUp", "desc": "종결조항"},
    "penjelasan": {"akn": "note", "desc": "설명"},
}


# ============================================
# 법령 유형별 우선순위
# ============================================

JENIS_PRIORITY = {
    "UU": 100,           # 법률 - 최우선
    "UNDANG-UNDANG": 100,
    "PERPPU": 90,        # 긴급법률대체정부령
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": 90,
    "PP": 80,            # 정부령
    "PERATURAN PEMERINTAH": 80,
    "PERPRES": 70,       # 대통령령
    "PERATURAN PRESIDEN": 70,
    "PERMEN": 50,        # 장관령
    "PERATURAN MENTERI": 50,
    "PERATURAN BADAN/LEMBAGA": 40,  # 기관 규정
}


def get_priority(jenis: str) -> int:
    """법령 유형에 따른 우선순위 반환"""
    jenis_upper = jenis.upper()
    for key, priority in JENIS_PRIORITY.items():
        if key in jenis_upper:
            return priority
    return 10  # 기본 우선순위


# ============================================
# Pipeline V3 Models
# ============================================

class PageImageStatus(str, Enum):
    """페이지 이미지 처리 상태"""
    PENDING = "pending"
    GENERATED = "generated"
    CLEANED = "cleaned"
    OCR_DONE = "ocr_done"
    ERROR = "error"


class CheckpointStatus(str, Enum):
    """체크포인트 상태"""
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class QualityTier(str, Enum):
    """품질 등급"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    ERROR = "error"


class TextNoiseRuleType(str, Enum):
    """텍스트 노이즈 규칙 유형"""
    REGEX = "regex"
    EXACT = "exact"
    STARTSWITH = "startswith"
    ENDSWITH = "endswith"
    CONTAINS = "contains"
    LINE_FREQUENCY = "line_frequency"


@dataclass
class PageImage:
    """페이지 이미지"""
    id: Optional[int] = None
    document_id: str = ""
    page_number: int = 0
    image_path: Optional[str] = None
    cleaned_image_path: Optional[str] = None
    cluster_id: Optional[int] = None
    status: str = PageImageStatus.PENDING.value
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ImageNoiseRule:
    """이미지 노이즈 규칙"""
    id: Optional[int] = None
    cluster_id: int = 0

    # 크롭 비율
    header_crop_ratio: float = 0.0
    footer_crop_ratio: float = 0.0
    left_crop_ratio: float = 0.0
    right_crop_ratio: float = 0.0

    # 마스킹 영역 (JSON)
    mask_regions: Optional[list] = None

    # 전처리 옵션
    grayscale: bool = False
    denoise: bool = False
    deskew: bool = False
    binarize: bool = False
    binarize_threshold: int = 127

    # 메타데이터
    description: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool = True

    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def mask_regions_list(self) -> list:
        if isinstance(self.mask_regions, str):
            return json.loads(self.mask_regions)
        return self.mask_regions or []

    def to_json_fields(self) -> dict:
        return {
            "mask_regions": json.dumps(self.mask_regions) if self.mask_regions else None,
        }


@dataclass
class ProcessingCheckpoint:
    """처리 체크포인트"""
    id: Optional[int] = None
    stage: str = ""
    batch_id: Optional[str] = None

    # 진행 상황
    last_document_id: Optional[str] = None
    last_page_number: Optional[int] = None
    processed_count: int = 0
    total_count: Optional[int] = None

    # 상태
    status: str = CheckpointStatus.RUNNING.value
    error_message: Optional[str] = None

    # 시간
    started_at: Optional[str] = None
    last_updated_at: Optional[str] = None
    completed_at: Optional[str] = None

    # 설정
    config_json: Optional[dict] = None

    @property
    def config(self) -> dict:
        if isinstance(self.config_json, str):
            return json.loads(self.config_json)
        return self.config_json or {}

    @property
    def progress_ratio(self) -> float:
        if self.total_count and self.total_count > 0:
            return self.processed_count / self.total_count
        return 0.0


@dataclass
class QualityMetrics:
    """품질 지표"""
    id: Optional[int] = None
    document_id: str = ""

    # OCR 품질
    avg_ocr_confidence: Optional[float] = None
    min_ocr_confidence: Optional[float] = None
    max_ocr_confidence: Optional[float] = None

    # 텍스트 품질
    word_recognition_rate: Optional[float] = None
    legal_term_rate: Optional[float] = None
    broken_char_ratio: Optional[float] = None

    # 구조 품질
    has_pasal: bool = False
    has_ayat: bool = False
    structure_score: Optional[float] = None

    # XML 품질
    xml_valid: bool = False
    xml_errors: Optional[list] = None

    # 종합
    overall_score: Optional[float] = None
    quality_tier: str = QualityTier.MEDIUM.value

    # 수동 검토
    needs_manual_review: bool = False
    manual_review_reason: Optional[str] = None
    reviewed_at: Optional[str] = None
    reviewer_notes: Optional[str] = None

    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def calculate_overall_score(self) -> float:
        """
        가중 평균 점수 계산

        가중치:
        - OCR 신뢰도: 0.25
        - 단어 인식률: 0.30
        - 법률 용어율: 0.20
        - 조문 존재: 0.15
        - XML 유효성: 0.10
        """
        score = 0.0
        weights_used = 0.0

        if self.avg_ocr_confidence is not None:
            score += self.avg_ocr_confidence * 0.25
            weights_used += 0.25

        if self.word_recognition_rate is not None:
            score += self.word_recognition_rate * 0.30
            weights_used += 0.30

        if self.legal_term_rate is not None:
            # 법률 용어율은 0.05 이상이면 만점
            normalized = min(self.legal_term_rate / 0.05, 1.0)
            score += normalized * 0.20
            weights_used += 0.20

        if self.has_pasal:
            score += 0.15
        weights_used += 0.15

        if self.xml_valid:
            score += 0.10
        weights_used += 0.10

        return score / weights_used if weights_used > 0 else 0.0

    def determine_tier(self) -> str:
        """품질 등급 결정"""
        if self.overall_score is None:
            self.overall_score = self.calculate_overall_score()

        if self.overall_score >= 0.80:
            return QualityTier.HIGH.value
        elif self.overall_score >= 0.60:
            return QualityTier.MEDIUM.value
        elif self.overall_score >= 0.40:
            return QualityTier.LOW.value
        else:
            return QualityTier.ERROR.value


@dataclass
class TextNoiseRule:
    """텍스트 노이즈 규칙"""
    id: Optional[int] = None
    scope: str = "global"
    cluster_id: Optional[int] = None
    rule_type: str = TextNoiseRuleType.REGEX.value
    pattern: str = ""
    replacement: str = ""
    min_frequency: Optional[float] = None
    position: Optional[str] = None  # 'header', 'footer', 'any'
    description: Optional[str] = None
    priority: int = 100
    is_active: bool = True
    created_at: Optional[str] = None


# ============================================
# Pipeline V3 Configuration
# ============================================

@dataclass
class PipelineV3Config:
    """파이프라인 V3 설정"""
    # 이미지 생성
    image_dpi: int = 150
    image_format: str = "jpeg"
    image_quality: int = 85

    # 배치 크기
    page_gen_batch_size: int = 100
    ocr_batch_size: int = 50
    workers: int = 8

    # 경로
    page_images_dir: str = "peraturan/data/page_images"
    cleaned_images_dir: str = "peraturan/data/cleaned_images"
    output_dir: str = "peraturan/data/output"

    # 품질 임계값
    quality_pass_threshold: float = 0.80
    quality_warning_threshold: float = 0.60
    manual_review_threshold: float = 0.40

    # OCR 설정
    ocr_lang: str = "en"
    use_gpu: bool = True

    def to_dict(self) -> dict:
        return {
            "image_dpi": self.image_dpi,
            "image_format": self.image_format,
            "image_quality": self.image_quality,
            "page_gen_batch_size": self.page_gen_batch_size,
            "ocr_batch_size": self.ocr_batch_size,
            "workers": self.workers,
            "page_images_dir": self.page_images_dir,
            "cleaned_images_dir": self.cleaned_images_dir,
            "output_dir": self.output_dir,
            "quality_pass_threshold": self.quality_pass_threshold,
            "quality_warning_threshold": self.quality_warning_threshold,
            "manual_review_threshold": self.manual_review_threshold,
            "ocr_lang": self.ocr_lang,
            "use_gpu": self.use_gpu,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineV3Config":
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
