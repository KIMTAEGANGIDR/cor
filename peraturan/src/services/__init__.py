"""Services for Peraturan Crawler.

데이터 소스: peraturan.go.id 단독 (BPK 병합 금지)

v3 변경사항:
- 판단 → 기록 (ValidityDeterminer → StatusRecorder)
- 추출 → 태깅 (RelationExtractor → RelationTagger)
- 조건부 효력 → 조건부 표현 검출
"""

from src.services.database import Database
from src.services.parser import Parser, ParseError
from src.services.downloader import Downloader, DownloaderError, get_pdf_folder

# 구조 파싱
from src.services.structure_parser import StructureParser, ParsedDocument

# 관계 표현 태깅 (v3, 해석 없음)
from src.services.relation_tagger import RelationTagger, TaggedExpression, ExpressionType, tag_relations

# 메타데이터 상태 기록 (v3, 판단 아님)
from src.services.status_recorder import StatusRecorder, StatusRecord, StatusMeta, RelationTag, DetectedExpression, record_status

# 시행일 추출
from src.services.effective_date_extractor import EffectiveDateExtractor, EffectiveDateResult, EffectiveDateType

# Akoma Ntoso XML
from src.services.akoma_ntoso import AkomaNtosoGenerator, AknMetadata

# 통합 파이프라인
from src.services.unified_pipeline import UnifiedPipeline, PipelineConfig, ProcessingResult

# DB 스키마 (v3)
from src.services.db_schema import DatabaseManager, migrate, SCHEMA_VERSION

# 레거시 호환 (v2) - deprecated
from src.services.relation_extractor import RelationExtractor, ExtractedRelation, RelationType, extract_relations
from src.services.validity_determiner import ValidityDeterminer, ValidityResult, ValidityStatus, determine_validity

__all__ = [
    # 기존
    "Database", "Parser", "ParseError", "Downloader", "DownloaderError", "get_pdf_folder",
    # 구조 파싱
    "StructureParser", "ParsedDocument",
    # 관계 표현 태깅 (v3)
    "RelationTagger", "TaggedExpression", "ExpressionType", "tag_relations",
    # 메타데이터 상태 기록 (v3)
    "StatusRecorder", "StatusRecord", "StatusMeta", "RelationTag", "DetectedExpression", "record_status",
    # 시행일 추출
    "EffectiveDateExtractor", "EffectiveDateResult", "EffectiveDateType",
    # Akoma Ntoso
    "AkomaNtosoGenerator", "AknMetadata",
    # 통합 파이프라인
    "UnifiedPipeline", "PipelineConfig", "ProcessingResult",
    # DB (v3)
    "DatabaseManager", "migrate", "SCHEMA_VERSION",
    # 레거시 호환 (v2) - deprecated
    "RelationExtractor", "ExtractedRelation", "RelationType", "extract_relations",
    "ValidityDeterminer", "ValidityResult", "ValidityStatus", "determine_validity",
]
