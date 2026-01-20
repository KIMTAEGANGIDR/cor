"""
통합 파이프라인 (Unified Pipeline) v3

PDF → 텍스트 추출 → 구조 파싱 → 관계 표현 태깅 → 메타데이터 상태 기록 → 출력

데이터 소스: peraturan.go.id 단독 (BPK 병합 금지)

v3 변경사항:
- 관계 추출 → 관계 표현 태깅 (해석 없음)
- 현행성 판단 → 메타데이터 상태 기록 (판단 없음)
- ValidityResult → StatusRecord

출력:
- SQLite DB (메타데이터 + 파싱 결과 + 상태 기록)
- Akoma Ntoso XML - 선택
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

# PDF 텍스트 추출
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

# 내부 모듈 (v3)
from .structure_parser import StructureParser, ParsedDocument
from .relation_tagger import RelationTagger, TaggedExpression
from .status_recorder import StatusRecorder, StatusRecord, DetectedExpression, RelationTag
from .effective_date_extractor import EffectiveDateExtractor
from .akoma_ntoso import AkomaNtosoGenerator, AknMetadata


@dataclass
class PipelineConfig:
    """파이프라인 설정"""
    # 입출력 경로
    db_path: Path                           # SQLite DB
    pdf_dir: Optional[Path] = None          # PDF 폴더
    output_dir: Optional[Path] = None       # 출력 폴더 (XML 등)

    # 기능 활성화
    enable_ocr: bool = False                # OCR 활성화 (GPU 필요)
    enable_akn_xml: bool = True             # Akoma Ntoso XML 생성

    # OCR 설정
    ocr_confidence_threshold: float = 0.70  # OCR 필요 판단 임계치

    # 배치 설정
    batch_size: int = 100                   # 배치 크기
    checkpoint_interval: int = 50           # 체크포인트 간격


@dataclass
class ProcessingResult:
    """처리 결과"""
    slug: str
    success: bool
    stage: str                              # 실패 시 단계
    error: Optional[str] = None

    # 각 단계 결과
    text_length: int = 0
    needs_ocr: bool = False
    parsed: Optional[ParsedDocument] = None
    tagged_expressions: list[TaggedExpression] = field(default_factory=list)
    status_record: Optional[StatusRecord] = None
    akn_path: Optional[Path] = None

    processing_time_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "success": self.success,
            "stage": self.stage,
            "error": self.error,
            "text_length": self.text_length,
            "needs_ocr": self.needs_ocr,
            "pasal_count": self.parsed.total_pasal if self.parsed else 0,
            "expression_count": len(self.tagged_expressions),
            "status_meta": self.status_record.status_meta.value if self.status_record else None,
            "has_conditional_expr": self.status_record.has_conditional_expr if self.status_record else False,
            "akn_path": str(self.akn_path) if self.akn_path else None,
            "processing_time_ms": self.processing_time_ms,
        }


class UnifiedPipeline:
    """
    통합 처리 파이프라인 (v3)

    사용법:
        config = PipelineConfig(db_path=Path("peraturan.db"))
        pipeline = UnifiedPipeline(config)
        result = pipeline.process_single("uu-no-12-tahun-2011")
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._conn: Optional[sqlite3.Connection] = None

        # 파서/태거 초기화 (v3)
        self._structure_parser = StructureParser()
        self._relation_tagger = RelationTagger()
        self._status_recorder = StatusRecorder(config.db_path)
        self._effective_date_extractor = EffectiveDateExtractor()
        self._akn_generator = AkomaNtosoGenerator()

        # OCR 엔진 (지연 로딩)
        self._ocr_engine = None

    def _get_connection(self) -> sqlite3.Connection:
        """DB 연결 획득"""
        if not self._conn:
            self._conn = sqlite3.connect(self.config.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        """리소스 정리"""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._status_recorder.close()

    def process_single(self, slug: str) -> ProcessingResult:
        """
        단일 법령 처리

        Args:
            slug: 법령 식별자

        Returns:
            ProcessingResult
        """
        start_time = datetime.now()
        result = ProcessingResult(slug=slug, success=False, stage="init")

        try:
            # 1. 메타데이터 로드
            metadata = self._load_metadata(slug)
            if not metadata:
                result.stage = "metadata"
                result.error = "메타데이터 없음"
                return result

            # 2. 텍스트 추출
            text = self._get_text(slug, metadata)
            if not text:
                result.stage = "extraction"
                result.error = "텍스트 추출 실패"
                return result

            result.text_length = len(text)
            result.needs_ocr = metadata.get("needs_ocr", False)

            # 3. 구조 파싱
            parsed = self._structure_parser.parse(text, slug)
            result.parsed = parsed
            result.stage = "parsing"

            if parsed.total_pasal == 0:
                result.error = "파싱 결과 없음 (Pasal 0개)"
                # 계속 진행 (일부 문서는 Pasal이 없을 수 있음)

            # 4. 관계 표현 태깅 (v3, 해석 없음)
            tagged_exprs = self._relation_tagger.tag_from_text(text, slug)
            tagged_exprs.extend(
                self._relation_tagger.tag_from_title(metadata.get("tentang", ""))
            )
            result.tagged_expressions = tagged_exprs
            result.stage = "tagging"

            # 5. DetectedExpression으로 변환 (StatusRecorder 형식)
            detected_expressions = self._convert_to_detected_expressions(tagged_exprs)

            # 6. 메타데이터 상태 기록 (v3, 판단 없음)
            status_record = self._status_recorder.record(
                slug=slug,
                metadata=metadata,
                detected_expressions=detected_expressions,
                text=text,
            )
            result.status_record = status_record
            result.stage = "recording"

            # 7. Akoma Ntoso XML 생성
            if self.config.enable_akn_xml and self.config.output_dir:
                akn_path = self._generate_akn(parsed, metadata)
                result.akn_path = akn_path
                result.stage = "akn"

            # 8. DB 업데이트
            self._update_db(slug, result)
            result.stage = "complete"

            result.success = True

        except Exception as e:
            result.error = str(e)

        finally:
            elapsed = (datetime.now() - start_time).total_seconds() * 1000
            result.processing_time_ms = int(elapsed)

        return result

    def _convert_to_detected_expressions(
        self, tagged_exprs: list[TaggedExpression]
    ) -> list[DetectedExpression]:
        """TaggedExpression → DetectedExpression 변환"""
        detected = []
        for tag in tagged_exprs:
            # ExpressionType → RelationTag 매핑
            relation_tag = self._map_expression_to_relation_tag(tag.expression_type.value)
            if relation_tag:
                detected.append(DetectedExpression(
                    expression_type=relation_tag,
                    target_ref=tag.target_ref,
                    target_slug=tag.target_slug,
                    raw_text=tag.raw_text,
                    pasal_context=tag.pasal_context,
                ))
        return detected

    def _map_expression_to_relation_tag(self, expr_type: str) -> Optional[RelationTag]:
        """ExpressionType 값 → RelationTag 매핑"""
        mapping = {
            "mencabut_detected": RelationTag.MENCABUT_DETECTED,
            "mengubah_detected": RelationTag.MENGUBAH_DETECTED,
            "merujuk_detected": RelationTag.MERUJUK_DETECTED,
            "conditional_expr_detected": RelationTag.CONDITIONAL_EXPR_DETECTED,
            "transitional_detected": RelationTag.TRANSITIONAL_DETECTED,
        }
        return mapping.get(expr_type)

    def process_batch(
        self,
        slugs: list[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> list[ProcessingResult]:
        """
        배치 처리

        Args:
            slugs: 법령 식별자 목록
            progress_callback: 진행 콜백 (current, total, slug)

        Returns:
            처리 결과 목록
        """
        results = []
        total = len(slugs)

        for i, slug in enumerate(slugs):
            if progress_callback:
                progress_callback(i + 1, total, slug)

            result = self.process_single(slug)
            results.append(result)

            # 체크포인트
            if (i + 1) % self.config.checkpoint_interval == 0:
                self._conn.commit() if self._conn else None

        # 최종 커밋
        if self._conn:
            self._conn.commit()

        return results

    def process_all_pending(
        self,
        limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> list[ProcessingResult]:
        """
        미처리 법령 전체 처리

        Args:
            limit: 처리 건수 제한
            progress_callback: 진행 콜백

        Returns:
            처리 결과 목록
        """
        slugs = self._get_pending_slugs(limit)
        return self.process_batch(slugs, progress_callback)

    def _load_metadata(self, slug: str) -> Optional[dict]:
        """메타데이터 로드"""
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT slug, jenis, nomor, tahun, tentang, status,
                   pemrakarsa, tanggal_penetapan, tanggal_pengundangan,
                   local_pdf_path, extracted_text, needs_ocr
            FROM peraturan
            WHERE slug = ?
        """, (slug,))

        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def _get_text(self, slug: str, metadata: dict) -> Optional[str]:
        """텍스트 획득 (DB 우선, 없으면 PDF 추출)"""
        # DB에 이미 추출된 텍스트가 있으면 사용
        text = metadata.get("extracted_text")
        if text and len(text) > 100:
            return text

        # PDF에서 추출
        pdf_path = metadata.get("local_pdf_path")
        if pdf_path and PYMUPDF_AVAILABLE:
            full_path = self.config.pdf_dir / pdf_path if self.config.pdf_dir else Path(pdf_path)
            if full_path.exists():
                return self._extract_text_from_pdf(full_path)

        return None

    def _extract_text_from_pdf(self, pdf_path: Path) -> Optional[str]:
        """PDF에서 텍스트 추출"""
        if not PYMUPDF_AVAILABLE:
            return None

        try:
            doc = fitz.open(pdf_path)
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()
            return "\n".join(text_parts)
        except Exception:
            return None

    def _generate_akn(self, parsed: ParsedDocument, metadata: dict) -> Optional[Path]:
        """Akoma Ntoso XML 생성"""
        if not self.config.output_dir:
            return None

        akn_metadata = AknMetadata(
            slug=metadata.get("slug", parsed.slug),
            jenis=metadata.get("jenis", ""),
            nomor=str(metadata.get("nomor", "")),
            tahun=metadata.get("tahun", 0),
            tentang=metadata.get("tentang", ""),
            tanggal_penetapan=metadata.get("tanggal_penetapan"),
            tanggal_pengundangan=metadata.get("tanggal_pengundangan"),
            pemrakarsa=metadata.get("pemrakarsa"),
            status=metadata.get("status"),
        )

        xml_content = self._akn_generator.generate(parsed, akn_metadata)

        # 저장
        jenis_dir = metadata.get("jenis", "other").lower().replace(" ", "_").replace("/", "_")
        output_path = self.config.output_dir / "akn" / jenis_dir / f"{parsed.slug}.xml"
        self._akn_generator.save(xml_content, output_path)

        return output_path

    def _update_db(self, slug: str, result: ProcessingResult) -> None:
        """DB 업데이트 (v3)"""
        conn = self._get_connection()

        # 파싱 결과 저장
        if result.parsed:
            conn.execute("""
                UPDATE peraturan SET
                    parsed_bab_count = ?,
                    parsed_pasal_count = ?,
                    parsed_ayat_count = ?,
                    parsed_huruf_count = ?,
                    parse_success = ?,
                    parsed_json = ?
                WHERE slug = ?
            """, (
                len(result.parsed.babs),
                result.parsed.total_pasal,
                result.parsed.total_ayat,
                result.parsed.total_huruf,
                1 if result.parsed.total_pasal > 0 else 0,
                json.dumps(result.parsed.to_dict(), ensure_ascii=False),
                slug,
            ))

        # 메타데이터 상태 기록 저장 (v3)
        if result.status_record:
            sr = result.status_record
            conn.execute("""
                UPDATE peraturan SET
                    status_meta = ?,
                    relation_tags = ?,
                    has_conditional_expr = ?,
                    effective_date = ?,
                    effective_date_type = ?,
                    effective_date_raw = ?,
                    recorded_at = datetime('now')
                WHERE slug = ?
            """, (
                sr.status_meta.value,
                json.dumps(sr.relation_tags),
                1 if sr.has_conditional_expr else 0,
                sr.effective_date,
                sr.effective_date_type,
                sr.effective_date_raw,
                slug,
            ))

        # 태깅된 표현 저장
        for tag in result.tagged_expressions:
            try:
                conn.execute("""
                    INSERT INTO relation_expressions
                    (source_slug, target_ref, target_slug, target_jenis, target_nomor,
                     target_tahun, expression_type, raw_text, pasal_context, kondisi_text, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    slug, tag.target_ref, tag.target_slug, tag.target_jenis, tag.target_nomor,
                    tag.target_tahun, tag.expression_type.value, tag.raw_text,
                    tag.pasal_context, tag.kondisi_text, tag.confidence,
                ))
            except sqlite3.OperationalError:
                # relation_expressions 테이블이 없으면 스킵 (마이그레이션 필요)
                pass

    def _get_pending_slugs(self, limit: Optional[int] = None) -> list[str]:
        """미처리 법령 목록"""
        conn = self._get_connection()
        query = """
            SELECT slug FROM peraturan
            WHERE extracted_text IS NOT NULL
              AND (parse_success IS NULL OR parse_success = 0)
            ORDER BY tahun DESC
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor = conn.execute(query)
        return [row["slug"] for row in cursor]

    def get_statistics(self) -> dict:
        """처리 통계 (v3)"""
        conn = self._get_connection()

        stats = {}

        # 전체 건수
        cursor = conn.execute("SELECT COUNT(*) FROM peraturan")
        stats["total"] = cursor.fetchone()[0]

        # 텍스트 추출 완료
        cursor = conn.execute("SELECT COUNT(*) FROM peraturan WHERE extracted_text IS NOT NULL")
        stats["text_extracted"] = cursor.fetchone()[0]

        # OCR 필요
        cursor = conn.execute("SELECT COUNT(*) FROM peraturan WHERE needs_ocr = 1")
        stats["needs_ocr"] = cursor.fetchone()[0]

        # 파싱 완료
        cursor = conn.execute("SELECT COUNT(*) FROM peraturan WHERE parse_success = 1")
        stats["parsed"] = cursor.fetchone()[0]

        # status_meta별 (v3)
        try:
            cursor = conn.execute("""
                SELECT status_meta, COUNT(*)
                FROM peraturan
                WHERE status_meta IS NOT NULL
                GROUP BY status_meta
            """)
            stats["by_status_meta"] = {row[0]: row[1] for row in cursor}
        except sqlite3.OperationalError:
            stats["by_status_meta"] = {}

        # 조건부 표현 검출 (v3)
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM peraturan WHERE has_conditional_expr = 1")
            stats["has_conditional_expr"] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            stats["has_conditional_expr"] = 0

        # 유형별
        cursor = conn.execute("""
            SELECT jenis, COUNT(*), SUM(CASE WHEN parse_success = 1 THEN 1 ELSE 0 END)
            FROM peraturan
            GROUP BY jenis
        """)
        stats["by_jenis"] = {
            row[0]: {"total": row[1], "parsed": row[2]}
            for row in cursor
        }

        return stats


# CLI
if __name__ == "__main__":
    import sys
    from pathlib import Path

    print("=== 통합 파이프라인 (v3) 테스트 ===\n")

    # 설정
    config = PipelineConfig(
        db_path=Path("peraturan/data/peraturan.db"),
        pdf_dir=Path("peraturan/data/pdfs"),
        output_dir=Path("peraturan/data/output"),
        enable_akn_xml=True,
    )

    if not config.db_path.exists():
        print(f"DB 파일 없음: {config.db_path}")
        sys.exit(1)

    pipeline = UnifiedPipeline(config)

    # 통계
    stats = pipeline.get_statistics()
    print("현재 상태:")
    print(f"  총 법령: {stats['total']:,}")
    print(f"  텍스트 추출: {stats['text_extracted']:,}")
    print(f"  파싱 완료: {stats['parsed']:,}")
    print(f"  OCR 필요: {stats['needs_ocr']:,}")
    print(f"  status_meta별: {stats.get('by_status_meta', {})}")
    print(f"  조건부 표현 검출: {stats.get('has_conditional_expr', 0):,}건")

    # 단일 처리 테스트
    if len(sys.argv) > 1:
        slug = sys.argv[1]
        print(f"\n처리 중: {slug}")
        result = pipeline.process_single(slug)
        print(f"  결과: {'성공' if result.success else '실패'}")
        print(f"  단계: {result.stage}")
        if result.error:
            print(f"  오류: {result.error}")
        if result.parsed:
            print(f"  Pasal: {result.parsed.total_pasal}")
        if result.status_record:
            print(f"  status_meta: {result.status_record.status_meta.value}")
            print(f"  has_conditional_expr: {result.status_record.has_conditional_expr}")
            print(f"  relation_tags: {result.status_record.relation_tags}")

    pipeline.close()
