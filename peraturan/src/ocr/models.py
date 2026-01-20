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
