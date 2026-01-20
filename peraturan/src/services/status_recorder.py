"""
메타데이터 상태 기록 (Status Recorder)

원본 메타데이터의 status를 기록하고, 본문에서 검출된 관계 표현을 태깅

⚠️ 중요 원칙:
- 데이터 소스: peraturan.go.id (법제처) 단독 사용
- BPK 데이터 병합 금지
- **판단/해석 금지** - 상태 기록과 표현 태깅만 수행
- 법률가 검토 요청 불가

기록 방식:
1. status_meta: 원본 메타데이터의 status 필드 그대로 전달
2. relation_tags: 본문에서 검출된 관계 표현 태깅 (해석 없음)
3. has_conditional_expr: 조건부 표현 검출 플래그
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from .effective_date_extractor import EffectiveDateExtractor, EffectiveDateResult, EffectiveDateType


class StatusMeta(Enum):
    """원본 메타데이터 status 값 (그대로 전달)"""
    BERLAKU = "berlaku"                     # 원본: "Berlaku"
    TIDAK_BERLAKU = "tidak_berlaku"         # 원본: "Tidak Berlaku"
    UNKNOWN = "unknown"                     # 원본 status 없음


class RelationTag(Enum):
    """관계 표현 태그 (검출된 표현, 해석 아님)"""
    MENCABUT_DETECTED = "mencabut_detected"           # 폐지 표현 검출됨
    MENGUBAH_DETECTED = "mengubah_detected"           # 개정 표현 검출됨
    CONDITIONAL_EXPR_DETECTED = "conditional_expr_detected"  # 조건부 표현 검출됨
    TRANSITIONAL_DETECTED = "transitional_detected"   # 경과규정 표현 검출됨
    MERUJUK_DETECTED = "merujuk_detected"             # 참조 표현 검출됨


# 법령 유형별 위계 (참고용, 해석에 사용하지 않음)
HIERARCHY = {
    "UUD": 1,           # 헌법
    "TAP MPR": 2,       # MPR 결정
    "UU": 3,            # 법률
    "PERPPU": 3,        # 긴급법률 (UU와 동급)
    "PP": 4,            # 정부령
    "PERPRES": 5,       # 대통령령
    "PERMEN": 6,        # 장관령
    "PERBAN": 6,        # 기관규정
    "PERDA": 7,         # 지방조례
}


@dataclass
class DetectedExpression:
    """검출된 관계 표현"""
    expression_type: RelationTag
    target_ref: str                 # 검출된 참조 텍스트 (예: "UU No. 5 Tahun 2020")
    target_slug: Optional[str]      # 매핑 가능한 경우 slug
    raw_text: str                   # 원문 발췌
    pasal_context: Optional[str]    # 검출 위치 (Pasal X)

    def to_dict(self) -> dict:
        return {
            "expression_type": self.expression_type.value,
            "target_ref": self.target_ref,
            "target_slug": self.target_slug,
            "raw_text": self.raw_text,
            "pasal_context": self.pasal_context,
        }


@dataclass
class StatusRecord:
    """메타데이터 상태 기록 (판단 아님)"""
    slug: str

    # 메타데이터 기반 (원본 그대로)
    status_meta: StatusMeta                 # 원본 status 필드 값

    # 본문 관계 표현 태깅 (해석 없음)
    relation_tags: list[str] = field(default_factory=list)  # 검출된 관계 표현 태그들
    has_conditional_expr: bool = False      # 조건부 표현 검출 플래그

    # 검출된 표현 상세
    detected_expressions: list[DetectedExpression] = field(default_factory=list)

    # 시행일 (본문 추출)
    effective_date: Optional[str] = None    # YYYY-MM-DD (계산 가능한 경우)
    effective_date_type: Optional[str] = None
    effective_date_raw: str = ""            # 원문 보존

    # 관련 법령 참조 (slug 목록)
    referenced_laws: list[str] = field(default_factory=list)

    # 메타
    recorded_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "status_meta": self.status_meta.value,
            "relation_tags": self.relation_tags,
            "has_conditional_expr": self.has_conditional_expr,
            "detected_expressions": [e.to_dict() for e in self.detected_expressions],
            "effective_date": self.effective_date,
            "effective_date_type": self.effective_date_type,
            "effective_date_raw": self.effective_date_raw,
            "referenced_laws": self.referenced_laws,
            "recorded_at": self.recorded_at,
        }


class StatusRecorder:
    """
    메타데이터 상태 기록기

    사용법:
        recorder = StatusRecorder(db_path)
        record = recorder.record(slug, metadata, detected_relations)
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Args:
            db_path: SQLite DB 경로
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._effective_date_extractor = EffectiveDateExtractor()

    def _get_connection(self) -> Optional[sqlite3.Connection]:
        """DB 연결 획득"""
        if self.db_path and self.db_path.exists():
            if not self._conn:
                self._conn = sqlite3.connect(self.db_path)
                self._conn.row_factory = sqlite3.Row
            return self._conn
        return None

    def close(self):
        """DB 연결 종료"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def record(
        self,
        slug: str,
        metadata: Optional[dict] = None,
        detected_expressions: Optional[list[DetectedExpression]] = None,
        text: Optional[str] = None,
    ) -> StatusRecord:
        """
        메타데이터 상태 기록

        Args:
            slug: 법령 식별자
            metadata: 법령 메타데이터 (status 필드 포함)
            detected_expressions: 검출된 관계 표현들
            text: 법령 본문 (시행일 추출용)

        Returns:
            StatusRecord: 상태 기록 (판단 아님)
        """
        # 메타데이터 로드
        if metadata is None:
            metadata = self._load_metadata(slug)

        # 원본 status 필드 → status_meta
        status_meta = self._extract_status_meta(metadata)

        # 시행일 추출
        effective_date_info = self._extract_effective_date(text, metadata)

        # 관계 표현 태깅
        relation_tags = []
        has_conditional_expr = False
        referenced_laws = []

        if detected_expressions:
            for expr in detected_expressions:
                relation_tags.append(expr.expression_type.value)
                if expr.expression_type == RelationTag.CONDITIONAL_EXPR_DETECTED:
                    has_conditional_expr = True
                if expr.target_slug:
                    referenced_laws.append(expr.target_slug)

        # 중복 제거
        relation_tags = list(set(relation_tags))
        referenced_laws = list(set(referenced_laws))

        return StatusRecord(
            slug=slug,
            status_meta=status_meta,
            relation_tags=relation_tags,
            has_conditional_expr=has_conditional_expr,
            detected_expressions=detected_expressions or [],
            effective_date=effective_date_info.effective_date,
            effective_date_type=effective_date_info.date_type.value if effective_date_info.date_type else None,
            effective_date_raw=effective_date_info.raw_text,
            referenced_laws=referenced_laws,
        )

    def _extract_status_meta(self, metadata: Optional[dict]) -> StatusMeta:
        """원본 메타데이터 status 추출 (그대로 전달)"""
        if not metadata:
            return StatusMeta.UNKNOWN

        status = metadata.get("status", "")
        if not status:
            return StatusMeta.UNKNOWN

        status_lower = status.lower()

        if "tidak berlaku" in status_lower or "dicabut" in status_lower:
            return StatusMeta.TIDAK_BERLAKU

        if "berlaku" in status_lower:
            return StatusMeta.BERLAKU

        return StatusMeta.UNKNOWN

    def _extract_effective_date(
        self,
        text: Optional[str],
        metadata: Optional[dict]
    ) -> EffectiveDateResult:
        """시행일 추출"""
        tanggal_pengundangan = metadata.get("tanggal_pengundangan") if metadata else None

        if text:
            return self._effective_date_extractor.extract(text, tanggal_pengundangan)

        # 텍스트 없으면 UNKNOWN 반환
        return EffectiveDateResult(
            date_type=EffectiveDateType.UNKNOWN,
            effective_date=None,
            raw_text="",
            confidence=0.0,
        )

    def _load_metadata(self, slug: str) -> Optional[dict]:
        """DB에서 메타데이터 로드"""
        conn = self._get_connection()
        if not conn:
            return None

        cursor = conn.execute("""
            SELECT slug, jenis, nomor, tahun, tentang, status,
                   tanggal_penetapan, tanggal_pengundangan
            FROM peraturan
            WHERE slug = ?
        """, (slug,))

        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def record_batch(
        self,
        items: list[dict],
    ) -> list[StatusRecord]:
        """
        여러 법령의 상태 일괄 기록

        Args:
            items: [{"slug": ..., "metadata": ..., "detected_expressions": ..., "text": ...}, ...]

        Returns:
            list[StatusRecord]
        """
        results = []
        for item in items:
            record = self.record(
                slug=item["slug"],
                metadata=item.get("metadata"),
                detected_expressions=item.get("detected_expressions"),
                text=item.get("text"),
            )
            results.append(record)
        return results

    def get_hierarchy_level(self, jenis: str) -> int:
        """법령 유형의 위계 레벨 반환 (참고용, 해석에 사용하지 않음)"""
        jenis_upper = jenis.upper().replace("-", " ").replace("_", " ")

        for key, level in HIERARCHY.items():
            if key in jenis_upper:
                return level

        return 99  # 기타


def record_status(
    slug: str,
    db_path: Optional[Path] = None,
    metadata: Optional[dict] = None,
    detected_expressions: Optional[list[DetectedExpression]] = None,
    text: Optional[str] = None,
) -> StatusRecord:
    """
    편의 함수: 단일 법령 상태 기록

    Args:
        slug: 법령 식별자
        db_path: DB 경로
        metadata: 메타데이터
        detected_expressions: 검출된 관계 표현
        text: 법령 본문

    Returns:
        StatusRecord
    """
    recorder = StatusRecorder(db_path)
    try:
        return recorder.record(slug, metadata, detected_expressions, text)
    finally:
        recorder.close()


# CLI 테스트용
if __name__ == "__main__":
    print("=== 메타데이터 상태 기록 테스트 ===\n")

    # 테스트 케이스
    test_cases = [
        {
            "slug": "uu-no-12-tahun-2011",
            "metadata": {"status": "Berlaku", "jenis": "UU"},
            "detected_expressions": [],
        },
        {
            "slug": "uu-no-5-tahun-1986",
            "metadata": {"status": "Tidak Berlaku", "jenis": "UU"},
            "detected_expressions": [
                DetectedExpression(
                    expression_type=RelationTag.MENCABUT_DETECTED,
                    target_ref="UU No. 5 Tahun 1986",
                    target_slug="uu-no-5-tahun-1986",
                    raw_text="mencabut UU No. 5 Tahun 1986",
                    pasal_context="Pasal 100",
                ),
            ],
        },
        {
            "slug": "pp-no-24-tahun-1997",
            "metadata": {"status": "Berlaku", "jenis": "PP"},
            "detected_expressions": [
                DetectedExpression(
                    expression_type=RelationTag.CONDITIONAL_EXPR_DETECTED,
                    target_ref="PP No. 24 Tahun 1997",
                    target_slug=None,
                    raw_text="sepanjang tidak bertentangan dengan Peraturan ini",
                    pasal_context="Pasal 50",
                ),
            ],
        },
    ]

    recorder = StatusRecorder()

    for tc in test_cases:
        record = recorder.record(
            tc["slug"],
            metadata=tc.get("metadata"),
            detected_expressions=tc.get("detected_expressions"),
        )
        print(f"법령: {tc['slug']}")
        print(f"  status_meta: {record.status_meta.value}")
        print(f"  relation_tags: {record.relation_tags}")
        print(f"  has_conditional_expr: {record.has_conditional_expr}")
        if record.referenced_laws:
            print(f"  referenced_laws: {record.referenced_laws}")
        print()
