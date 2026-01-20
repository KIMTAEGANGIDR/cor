"""
현행성 판단 로직 (Validity Determiner)

법령 간 관계 데이터를 기반으로 현행성(효력 상태)을 자동 판단

⚠️ 중요 원칙:
- 데이터 소스: peraturan.go.id (법제처) 단독 사용
- BPK 데이터 병합 금지
- 조건부 효력 등 해석 영역은 판단하지 않음 (상태만 표시)
- 법률가 검토 요청 불가 (해석은 시스템/법률가 모두 금지)

판단 기준:
1. 직접 폐지 (MENCABUT) → Tidak Berlaku
2. 개정 (MENGUBAH) → Berlaku (개정본 존재 표시)
3. 조건부 효력 (BERLAKU_BERSYARAT) → 상태만 표시 (해석 없음)
4. 경과 규정 (MASA_PERALIHAN) → 상태만 표시 (해석 없음)
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from .effective_date_extractor import EffectiveDateExtractor, EffectiveDateResult, EffectiveDateType


class ValidityStatus(Enum):
    """법령 효력 상태"""
    BERLAKU = "berlaku"                     # 현행 (유효)
    TIDAK_BERLAKU = "tidak_berlaku"         # 비현행 (무효)
    DICABUT = "dicabut"                     # 폐지됨
    DIUBAH = "diubah"                       # 개정됨 (원본은 일부 무효)
    BERLAKU_BERSYARAT = "berlaku_bersyarat" # 조건부 유효
    MASA_PERALIHAN = "masa_peralihan"       # 경과 기간 중
    UNCERTAIN = "uncertain"                  # 판단 불가 (전문가 검토 필요)


class ConfidenceLevel(Enum):
    """판단 신뢰도"""
    HIGH = "high"       # 0.9+ : 명확한 폐지/현행
    MEDIUM = "medium"   # 0.7-0.9 : 개정/조건부
    LOW = "low"         # 0.5-0.7 : 경과규정/복잡한 관계
    UNCERTAIN = "uncertain"  # <0.5 : 데이터 부족


# 법령 유형별 위계 (상위법 우선)
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
class ValidityResult:
    """현행성 판단 결과"""
    slug: str
    status: ValidityStatus
    confidence: float                       # 0.0 ~ 1.0
    confidence_level: ConfidenceLevel
    reason: str                             # 판단 근거 (해석 아님, 데이터 기반 사실만)
    related_laws: list[str] = field(default_factory=list)  # 관련 법령
    effective_date: Optional[str] = None    # 시행일 (YYYY-MM-DD)
    effective_date_type: Optional[str] = None  # 시행일 유형
    determined_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "status": self.status.value,
            "confidence": round(self.confidence, 4),
            "confidence_level": self.confidence_level.value,
            "reason": self.reason,
            "related_laws": self.related_laws,
            "effective_date": self.effective_date,
            "effective_date_type": self.effective_date_type,
            "determined_at": self.determined_at,
        }


@dataclass
class Relation:
    """법령 간 관계"""
    source_slug: str
    target_slug: str
    rel_type: str           # MENCABUT, MENGUBAH, MERUJUK, etc.
    pasal_dasar: Optional[str] = None
    kondisi: Optional[str] = None
    confidence: float = 0.9


class ValidityDeterminer:
    """
    현행성 판단기

    사용법:
        determiner = ValidityDeterminer(db_path)
        result = determiner.determine(slug)
        print(result.status, result.confidence)
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Args:
            db_path: SQLite DB 경로 (관계 데이터 포함)
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

    def determine(
        self,
        slug: str,
        relations: Optional[list[Relation]] = None,
        metadata: Optional[dict] = None,
        text: Optional[str] = None,
    ) -> ValidityResult:
        """
        법령의 현행성 판단

        Args:
            slug: 법령 식별자
            relations: 관계 데이터 (없으면 DB에서 조회)
            metadata: 법령 메타데이터 (jenis, tahun 등)
            text: 법령 본문 (시행일 추출용)

        Returns:
            ValidityResult: 판단 결과
        """
        # 관계 데이터 로드
        if relations is None:
            relations = self._load_relations(slug)

        # 메타데이터 로드
        if metadata is None:
            metadata = self._load_metadata(slug)

        # 시행일 추출
        effective_date_info = self._extract_effective_date(text, metadata)

        # 기본 상태 (메타데이터의 status 필드)
        base_status = metadata.get("status", "") if metadata else ""

        # 1. 직접 폐지 여부 확인
        revocation = self._check_revocation(slug, relations)
        if revocation:
            revocation.effective_date = effective_date_info.effective_date
            revocation.effective_date_type = effective_date_info.date_type.value
            return revocation

        # 2. 개정 여부 확인
        amendment = self._check_amendment(slug, relations, metadata)
        if amendment:
            amendment.effective_date = effective_date_info.effective_date
            amendment.effective_date_type = effective_date_info.date_type.value
            return amendment

        # 3. 조건부 효력 확인
        conditional = self._check_conditional(slug, relations)
        if conditional:
            conditional.effective_date = effective_date_info.effective_date
            conditional.effective_date_type = effective_date_info.date_type.value
            return conditional

        # 4. 경과 규정 확인
        transitional = self._check_transitional(slug, relations)
        if transitional:
            transitional.effective_date = effective_date_info.effective_date
            transitional.effective_date_type = effective_date_info.date_type.value
            return transitional

        # 5. 기본 상태 반환
        result = self._determine_default(slug, base_status, metadata)
        result.effective_date = effective_date_info.effective_date
        result.effective_date_type = effective_date_info.date_type.value
        return result

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

    def _load_relations(self, slug: str) -> list[Relation]:
        """DB에서 관계 데이터 로드"""
        relations = []
        conn = self._get_connection()
        if not conn:
            return relations

        # 이 법령이 대상인 관계 (다른 법령이 이 법령을 폐지/개정)
        cursor = conn.execute("""
            SELECT source_id, target_id, relasi_type
            FROM peraturan_relasi
            WHERE target_id = ? OR source_id = ?
        """, (slug, slug))

        for row in cursor:
            relations.append(Relation(
                source_slug=row["source_id"],
                target_slug=row["target_id"],
                rel_type=row["relasi_type"],
            ))

        return relations

    def _load_metadata(self, slug: str) -> Optional[dict]:
        """DB에서 메타데이터 로드"""
        conn = self._get_connection()
        if not conn:
            return None

        cursor = conn.execute("""
            SELECT slug, jenis, nomor, tahun, tentang, status
            FROM peraturan
            WHERE slug = ?
        """, (slug,))

        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def _check_revocation(
        self,
        slug: str,
        relations: list[Relation]
    ) -> Optional[ValidityResult]:
        """폐지 여부 확인"""
        for rel in relations:
            # 다른 법령이 이 법령을 폐지 (MENCABUT)
            if rel.target_slug == slug and rel.rel_type.upper() in ("MENCABUT", "DICABUT"):
                return ValidityResult(
                    slug=slug,
                    status=ValidityStatus.DICABUT,
                    confidence=0.95,
                    confidence_level=ConfidenceLevel.HIGH,
                    reason=f"{rel.source_slug}에 의해 폐지",
                    related_laws=[rel.source_slug],
                )

        return None

    def _check_amendment(
        self,
        slug: str,
        relations: list[Relation],
        metadata: Optional[dict]
    ) -> Optional[ValidityResult]:
        """개정 여부 확인"""
        amendments = []

        for rel in relations:
            # 다른 법령이 이 법령을 개정 (MENGUBAH, DIUBAH)
            if rel.target_slug == slug and rel.rel_type.upper() in ("MENGUBAH", "DIUBAH"):
                amendments.append(rel.source_slug)

        if amendments:
            # 개정된 경우에도 원본은 여전히 유효 (개정본과 함께 적용)
            return ValidityResult(
                slug=slug,
                status=ValidityStatus.DIUBAH,
                confidence=0.85,
                confidence_level=ConfidenceLevel.MEDIUM,
                reason=f"{len(amendments)}건의 개정 존재",
                related_laws=amendments,
            )

        return None

    def _check_conditional(
        self,
        slug: str,
        relations: list[Relation]
    ) -> Optional[ValidityResult]:
        """조건부 효력 확인 (상태만 표시, 해석 없음)"""
        for rel in relations:
            if rel.target_slug == slug and rel.rel_type.upper() == "BERLAKU_BERSYARAT":
                # 조건부 효력은 해석 영역이므로 상태만 표시
                return ValidityResult(
                    slug=slug,
                    status=ValidityStatus.BERLAKU_BERSYARAT,
                    confidence=0.70,
                    confidence_level=ConfidenceLevel.MEDIUM,
                    reason=f"조건부 효력 관계 존재 ({rel.source_slug})",
                    related_laws=[rel.source_slug],
                )

        return None

    def _check_transitional(
        self,
        slug: str,
        relations: list[Relation]
    ) -> Optional[ValidityResult]:
        """경과 규정 확인 (상태만 표시, 해석 없음)"""
        for rel in relations:
            if rel.target_slug == slug and rel.rel_type.upper() == "MASA_PERALIHAN":
                # 경과 규정은 해석 영역이므로 상태만 표시
                return ValidityResult(
                    slug=slug,
                    status=ValidityStatus.MASA_PERALIHAN,
                    confidence=0.65,
                    confidence_level=ConfidenceLevel.LOW,
                    reason=f"경과 규정 관계 존재 ({rel.source_slug})",
                    related_laws=[rel.source_slug],
                )

        return None

    def _determine_default(
        self,
        slug: str,
        base_status: str,
        metadata: Optional[dict]
    ) -> ValidityResult:
        """기본 상태 결정 (메타데이터 기반)"""
        # 메타데이터의 status 필드 활용
        status_lower = base_status.lower() if base_status else ""

        if "tidak berlaku" in status_lower or "dicabut" in status_lower:
            return ValidityResult(
                slug=slug,
                status=ValidityStatus.TIDAK_BERLAKU,
                confidence=0.90,
                confidence_level=ConfidenceLevel.HIGH,
                reason="메타데이터 status 필드: 비현행",
            )

        if "berlaku" in status_lower and "tidak" not in status_lower:
            return ValidityResult(
                slug=slug,
                status=ValidityStatus.BERLAKU,
                confidence=0.90,
                confidence_level=ConfidenceLevel.HIGH,
                reason="메타데이터 status 필드: 현행",
            )

        # 관계 데이터도 메타데이터도 없으면 불확실 (해석 없음, 상태만 표시)
        return ValidityResult(
            slug=slug,
            status=ValidityStatus.UNCERTAIN,
            confidence=0.40,
            confidence_level=ConfidenceLevel.UNCERTAIN,
            reason="메타데이터 status 필드 없음",
        )

    def determine_batch(
        self,
        slugs: list[str],
    ) -> list[ValidityResult]:
        """여러 법령의 현행성 일괄 판단"""
        return [self.determine(slug) for slug in slugs]

    def get_hierarchy_level(self, jenis: str) -> int:
        """법령 유형의 위계 레벨 반환 (낮을수록 상위법)"""
        jenis_upper = jenis.upper().replace("-", " ").replace("_", " ")

        for key, level in HIERARCHY.items():
            if key in jenis_upper:
                return level

        return 99  # 기타

    def compare_hierarchy(self, jenis1: str, jenis2: str) -> int:
        """
        두 법령의 위계 비교

        Returns:
            -1: jenis1이 상위
             0: 동급
             1: jenis2가 상위
        """
        level1 = self.get_hierarchy_level(jenis1)
        level2 = self.get_hierarchy_level(jenis2)

        if level1 < level2:
            return -1
        elif level1 > level2:
            return 1
        else:
            return 0


def determine_validity(
    slug: str,
    db_path: Optional[Path] = None,
    relations: Optional[list[Relation]] = None,
    metadata: Optional[dict] = None,
) -> ValidityResult:
    """
    편의 함수: 단일 법령 현행성 판단

    Args:
        slug: 법령 식별자
        db_path: DB 경로
        relations: 관계 데이터
        metadata: 메타데이터

    Returns:
        ValidityResult
    """
    determiner = ValidityDeterminer(db_path)
    try:
        return determiner.determine(slug, relations, metadata)
    finally:
        determiner.close()


# CLI 테스트용
if __name__ == "__main__":
    import sys

    print("=== 현행성 판단 테스트 ===\n")

    # 테스트 케이스
    test_cases = [
        {
            "slug": "uu-no-12-tahun-2011",
            "metadata": {"status": "Berlaku", "jenis": "UU"},
            "relations": [],
        },
        {
            "slug": "uu-no-5-tahun-1986",
            "metadata": {"status": "Tidak Berlaku", "jenis": "UU"},
            "relations": [
                Relation("uu-no-51-tahun-2009", "uu-no-5-tahun-1986", "MENCABUT"),
            ],
        },
        {
            "slug": "pp-no-24-tahun-1997",
            "metadata": {"status": "Berlaku", "jenis": "PP"},
            "relations": [
                Relation("pp-no-18-tahun-2021", "pp-no-24-tahun-1997", "MENGUBAH"),
            ],
        },
    ]

    determiner = ValidityDeterminer()

    for tc in test_cases:
        result = determiner.determine(
            tc["slug"],
            relations=tc.get("relations"),
            metadata=tc.get("metadata"),
        )
        print(f"법령: {tc['slug']}")
        print(f"  상태: {result.status.value}")
        print(f"  신뢰도: {result.confidence:.2f} ({result.confidence_level.value})")
        print(f"  근거: {result.reason}")
        if result.related_laws:
            print(f"  관련 법령: {result.related_laws}")
        print()
