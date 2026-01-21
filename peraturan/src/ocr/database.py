"""
ILIS OCR Pipeline Database Manager

기능:
- DB 초기화
- peraturan.db에서 문서 마이그레이션
- 상태 관리
"""

import sqlite3
import time
import random
import logging
from pathlib import Path
from typing import Optional
from contextlib import contextmanager
import json
from datetime import datetime

from .schema import SCHEMA_SQL
from .models import (
    Document, Header, Cluster, PatternRule, OCRResult,
    PipelineState, get_priority, DocumentStage,
    PageImage, ImageNoiseRule, QualityMetrics, ProcessingCheckpoint,
)

logger = logging.getLogger(__name__)

# 기본 경로
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"
PERATURAN_DB_PATH = Path(__file__).parent.parent.parent / "data" / "peraturan.db"
BPK_DB_PATH = Path(__file__).parent.parent.parent / "data" / "bpk" / "peraturan_bpk.db"

# SQLite retry 설정
SQLITE_MAX_RETRIES = 5
SQLITE_BASE_DELAY_MS = 50  # 50ms


class OCRPipelineDB:
    """OCR 파이프라인 데이터베이스 관리자"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self, max_retries: int = SQLITE_MAX_RETRIES):
        """
        데이터베이스 연결 컨텍스트 매니저 (retry with exponential backoff)

        다중 스레드에서 동시 쓰기 시 'database is locked' 에러 방지
        """
        conn = None
        last_error = None

        for attempt in range(max_retries):
            try:
                conn = sqlite3.connect(
                    str(self.db_path),
                    timeout=30.0  # 30초 대기
                )
                conn.row_factory = sqlite3.Row
                # WAL 모드 활성화 (동시 읽기 향상)
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as e:
                last_error = e
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    delay = (SQLITE_BASE_DELAY_MS * (2 ** attempt) + random.randint(0, 50)) / 1000
                    logger.warning(f"SQLite locked, retry {attempt+1}/{max_retries} after {delay:.3f}s")
                    time.sleep(delay)
                else:
                    raise

        if conn is None:
            raise last_error or sqlite3.OperationalError("Failed to connect")

        try:
            yield conn
            conn.commit()
        except sqlite3.OperationalError as e:
            conn.rollback()
            # Commit 시 locked 에러는 재시도하지 않음 (이미 연결 시 재시도함)
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        """데이터베이스 스키마 초기화"""
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)
        print(f"[OK] Database initialized: {self.db_path}")

    def get_stats(self) -> dict:
        """DB 통계 조회"""
        with self.connection() as conn:
            stats = {}

            # 문서 수
            row = conn.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()
            stats["total_documents"] = row["cnt"]

            # 단계별 문서 수
            rows = conn.execute(
                "SELECT stage, COUNT(*) as cnt FROM documents GROUP BY stage"
            ).fetchall()
            stats["by_stage"] = {row["stage"]: row["cnt"] for row in rows}

            # 법령 유형별 문서 수
            rows = conn.execute(
                "SELECT jenis, COUNT(*) as cnt FROM documents GROUP BY jenis ORDER BY cnt DESC"
            ).fetchall()
            stats["by_jenis"] = {row["jenis"]: row["cnt"] for row in rows}

            # 클러스터 수
            row = conn.execute("SELECT COUNT(*) as cnt FROM clusters").fetchone()
            stats["total_clusters"] = row["cnt"]

            # 패턴 룰 수
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM pattern_rules WHERE is_active = 1"
            ).fetchone()
            stats["active_pattern_rules"] = row["cnt"]

            return stats

    def get_pipeline_state(self) -> PipelineState:
        """파이프라인 상태 조회"""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM pipeline_state WHERE id = 1").fetchone()
            if row:
                return PipelineState(**dict(row))
            return PipelineState()

    def update_pipeline_state(self) -> None:
        """파이프라인 상태 업데이트 (집계)"""
        with self.connection() as conn:
            # 총 문서 수
            total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

            # 단계별 집계
            stage_counts = {}
            rows = conn.execute(
                "SELECT stage, COUNT(*) as cnt FROM documents GROUP BY stage"
            ).fetchall()
            for row in rows:
                stage_counts[row["stage"]] = row["cnt"]

            # 클러스터 상태별 집계
            cluster_counts = {}
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM clusters GROUP BY status"
            ).fetchall()
            for row in rows:
                cluster_counts[row["status"]] = row["cnt"]

            # 업데이트
            # Low Fix: 통계 집계 수정
            # - stage_05_completed: 패턴 분석 완료 (draft가 아닌 모든 클러스터)
            # - stage_06_pending: 검증 대기 (pending_validation)
            # - stage_06_rejected: 반려됨 (needs_revision)
            # - stage_06_manual: 수동 검토 필요 (manual_review)
            clusters_analyzed = (
                cluster_counts.get("pending_validation", 0) +
                cluster_counts.get("approved", 0) +
                cluster_counts.get("needs_revision", 0) +
                cluster_counts.get("manual_review", 0)
            )
            conn.execute("""
                UPDATE pipeline_state SET
                    total_documents = ?,
                    stage_0_pending = ?,
                    stage_0_completed = ?,
                    stage_05_pending = ?,
                    stage_05_completed = ?,
                    stage_06_pending = ?,
                    stage_06_approved = ?,
                    stage_06_rejected = ?,
                    stage_2_pending = ?,
                    stage_2_completed = ?,
                    stage_3_pending = ?,
                    stage_3_completed = ?,
                    last_updated_at = ?
                WHERE id = 1
            """, (
                total,
                stage_counts.get("pending", 0),
                stage_counts.get("header_extracted", 0) + stage_counts.get("clustered", 0),
                cluster_counts.get("draft", 0),
                clusters_analyzed,  # 분석 완료된 모든 클러스터
                cluster_counts.get("pending_validation", 0),  # 검증 대기
                cluster_counts.get("approved", 0),
                cluster_counts.get("needs_revision", 0) + cluster_counts.get("manual_review", 0),
                stage_counts.get("pattern_assigned", 0),
                stage_counts.get("ocr_completed", 0),
                stage_counts.get("parsed", 0),
                stage_counts.get("akn_generated", 0),
                datetime.now().isoformat()
            ))


class PageImageManager:
    """페이지 이미지 관리자 (Pipeline V3)"""

    def __init__(self, pipeline_db: OCRPipelineDB):
        self.pipeline_db = pipeline_db

    def get_page_images(
        self,
        document_id: str = None,
        status: str = None,
        cluster_id: int = None,
        limit: int = None,
    ) -> list[dict]:
        """페이지 이미지 조회"""
        with self.pipeline_db.connection() as conn:
            query = "SELECT * FROM page_images WHERE 1=1"
            params = []

            if document_id:
                query += " AND document_id = ?"
                params.append(document_id)

            if status:
                query += " AND status = ?"
                params.append(status)

            if cluster_id:
                query += " AND cluster_id = ?"
                params.append(cluster_id)

            query += " ORDER BY document_id, page_number"

            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def save_page_image(self, page_image: PageImage) -> None:
        """페이지 이미지 저장"""
        with self.pipeline_db.connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO page_images
                (document_id, page_number, image_path, cleaned_image_path,
                 cluster_id, status, error_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                page_image.document_id,
                page_image.page_number,
                page_image.image_path,
                page_image.cleaned_image_path,
                page_image.cluster_id,
                page_image.status,
                page_image.error_message,
            ))

    def get_stats(self) -> dict:
        """페이지 이미지 통계"""
        with self.pipeline_db.connection() as conn:
            rows = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM page_images GROUP BY status
            """).fetchall()
            return {row["status"]: row["count"] for row in rows}


class ImageNoiseRuleManager:
    """이미지 노이즈 규칙 관리자 (Pipeline V3)"""

    def __init__(self, pipeline_db: OCRPipelineDB):
        self.pipeline_db = pipeline_db

    def get_rule(self, cluster_id: int) -> dict | None:
        """클러스터별 노이즈 규칙 조회"""
        with self.pipeline_db.connection() as conn:
            row = conn.execute("""
                SELECT * FROM image_noise_rules
                WHERE cluster_id = ? AND is_active = 1
                ORDER BY id DESC LIMIT 1
            """, (cluster_id,)).fetchone()
            return dict(row) if row else None

    def save_rule(self, rule: ImageNoiseRule) -> int:
        """노이즈 규칙 저장"""
        with self.pipeline_db.connection() as conn:
            if rule.id:
                conn.execute("""
                    UPDATE image_noise_rules SET
                        header_crop_ratio = ?, footer_crop_ratio = ?,
                        left_crop_ratio = ?, right_crop_ratio = ?,
                        mask_regions = ?, grayscale = ?, denoise = ?,
                        deskew = ?, binarize = ?, binarize_threshold = ?,
                        description = ?, notes = ?, is_active = ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                """, (
                    rule.header_crop_ratio, rule.footer_crop_ratio,
                    rule.left_crop_ratio, rule.right_crop_ratio,
                    json.dumps(rule.mask_regions) if rule.mask_regions else None,
                    int(rule.grayscale), int(rule.denoise),
                    int(rule.deskew), int(rule.binarize), rule.binarize_threshold,
                    rule.description, rule.notes, int(rule.is_active),
                    rule.id,
                ))
                return rule.id
            else:
                cursor = conn.execute("""
                    INSERT INTO image_noise_rules
                    (cluster_id, header_crop_ratio, footer_crop_ratio,
                     left_crop_ratio, right_crop_ratio, mask_regions,
                     grayscale, denoise, deskew, binarize, binarize_threshold,
                     description, notes, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rule.cluster_id, rule.header_crop_ratio, rule.footer_crop_ratio,
                    rule.left_crop_ratio, rule.right_crop_ratio,
                    json.dumps(rule.mask_regions) if rule.mask_regions else None,
                    int(rule.grayscale), int(rule.denoise),
                    int(rule.deskew), int(rule.binarize), rule.binarize_threshold,
                    rule.description, rule.notes, int(rule.is_active),
                ))
                return cursor.lastrowid

    def list_rules(self) -> list[dict]:
        """모든 활성 규칙 목록"""
        with self.pipeline_db.connection() as conn:
            rows = conn.execute("""
                SELECT r.*, c.description as cluster_desc, c.document_count
                FROM image_noise_rules r
                JOIN clusters c ON r.cluster_id = c.id
                WHERE r.is_active = 1
                ORDER BY c.document_count DESC
            """).fetchall()
            return [dict(row) for row in rows]


class QualityMetricsManager:
    """품질 지표 관리자 (Pipeline V3)"""

    def __init__(self, pipeline_db: OCRPipelineDB):
        self.pipeline_db = pipeline_db

    def get_metrics(self, document_id: str) -> dict | None:
        """문서별 품질 지표 조회"""
        with self.pipeline_db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM quality_metrics WHERE document_id = ?",
                (document_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_metrics(self, metrics: QualityMetrics) -> None:
        """품질 지표 저장"""
        with self.pipeline_db.connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO quality_metrics
                (document_id, avg_ocr_confidence, min_ocr_confidence, max_ocr_confidence,
                 word_recognition_rate, legal_term_rate, broken_char_ratio,
                 has_pasal, has_ayat, structure_score, xml_valid, xml_errors,
                 overall_score, quality_tier, needs_manual_review, manual_review_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metrics.document_id,
                metrics.avg_ocr_confidence, metrics.min_ocr_confidence, metrics.max_ocr_confidence,
                metrics.word_recognition_rate, metrics.legal_term_rate, metrics.broken_char_ratio,
                int(metrics.has_pasal), int(metrics.has_ayat), metrics.structure_score,
                int(metrics.xml_valid), json.dumps(metrics.xml_errors) if metrics.xml_errors else None,
                metrics.overall_score, metrics.quality_tier,
                int(metrics.needs_manual_review), metrics.manual_review_reason,
            ))

    def get_stats(self) -> dict:
        """품질 통계"""
        with self.pipeline_db.connection() as conn:
            # 등급별 통계
            rows = conn.execute("""
                SELECT quality_tier, COUNT(*) as count, AVG(overall_score) as avg_score
                FROM quality_metrics GROUP BY quality_tier
            """).fetchall()

            by_tier = {row["quality_tier"]: {
                "count": row["count"],
                "avg_score": row["avg_score"],
            } for row in rows}

            # 수동 검토 통계
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN needs_manual_review = 1 THEN 1 ELSE 0 END) as needs_review,
                    SUM(CASE WHEN reviewed_at IS NOT NULL THEN 1 ELSE 0 END) as reviewed
                FROM quality_metrics
            """).fetchone()

            return {
                "by_tier": by_tier,
                "total": row["total"],
                "needs_review": row["needs_review"],
                "reviewed": row["reviewed"],
            }

    def get_documents_for_review(self, tier: str = None, limit: int = 50) -> list[dict]:
        """수동 검토 대상 문서 목록"""
        with self.pipeline_db.connection() as conn:
            query = """
                SELECT q.*, d.jenis, d.nomor, d.tahun, d.tentang
                FROM quality_metrics q
                JOIN documents d ON q.document_id = d.id
                WHERE q.needs_manual_review = 1 AND q.reviewed_at IS NULL
            """
            params = []

            if tier:
                query += " AND q.quality_tier = ?"
                params.append(tier)

            query += " ORDER BY q.overall_score ASC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]


class DocumentMigrator:
    """peraturan.db에서 문서 마이그레이션"""

    def __init__(self, pipeline_db: OCRPipelineDB):
        self.pipeline_db = pipeline_db
        self.peraturan_db_path = PERATURAN_DB_PATH
        self.bpk_db_path = BPK_DB_PATH

    def migrate_from_peraturan(
        self,
        jenis_filter: Optional[list] = None,
        limit: Optional[int] = None,
        only_downloaded: bool = True
    ) -> int:
        """
        peraturan.db에서 문서 마이그레이션

        Args:
            jenis_filter: 가져올 법령 유형 리스트 (예: ["UNDANG-UNDANG", "PERATURAN PEMERINTAH"])
            limit: 가져올 최대 문서 수
            only_downloaded: PDF 다운로드된 것만 가져올지 여부

        Returns:
            마이그레이션된 문서 수
        """
        if not self.peraturan_db_path.exists():
            raise FileNotFoundError(f"peraturan.db not found: {self.peraturan_db_path}")

        # 소스 DB 연결
        source_conn = sqlite3.connect(str(self.peraturan_db_path))
        source_conn.row_factory = sqlite3.Row

        # 쿼리 구성
        query = """
            SELECT
                slug as id,
                jenis,
                nomor,
                tahun,
                tentang,
                local_pdf_path as pdf_path,
                pdf_url
            FROM peraturan
            WHERE 1=1
        """
        params = []

        if only_downloaded:
            query += " AND local_pdf_path IS NOT NULL AND local_pdf_path != ''"

        if jenis_filter:
            placeholders = ",".join("?" * len(jenis_filter))
            query += f" AND jenis IN ({placeholders})"
            params.extend(jenis_filter)

        query += " ORDER BY tahun DESC, jenis"

        if limit:
            query += f" LIMIT {limit}"

        rows = source_conn.execute(query, params).fetchall()
        source_conn.close()

        # 대상 DB에 삽입
        count = 0
        with self.pipeline_db.connection() as conn:
            for row in rows:
                doc = dict(row)
                doc["source"] = "peraturan.go.id"
                doc["priority"] = get_priority(doc["jenis"])

                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO documents
                        (id, source, jenis, nomor, tahun, tentang, pdf_path, pdf_url, priority)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        doc["id"],
                        doc["source"],
                        doc["jenis"],
                        doc["nomor"],
                        doc["tahun"],
                        doc["tentang"],
                        doc["pdf_path"],
                        doc["pdf_url"],
                        doc["priority"]
                    ))
                    count += 1
                except sqlite3.IntegrityError:
                    pass  # 중복 무시

        return count

    def migrate_priority_documents(self) -> dict:
        """
        우선순위 문서만 마이그레이션 (UU, PP, Perpres, Perppu)

        Returns:
            유형별 마이그레이션 수
        """
        priority_jenis = [
            "UNDANG-UNDANG",
            "PERATURAN PEMERINTAH",
            "PERATURAN PRESIDEN",
            "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG"
        ]

        results = {}
        for jenis in priority_jenis:
            count = self.migrate_from_peraturan(jenis_filter=[jenis])
            results[jenis] = count
            print(f"  - {jenis}: {count}개")

        return results


def init_db(db_path: Optional[Path] = None) -> OCRPipelineDB:
    """데이터베이스 초기화 및 반환"""
    db = OCRPipelineDB(db_path)
    db.initialize()
    return db


def migrate_documents(
    db_path: Optional[Path] = None,
    priority_only: bool = True,
    jenis_filter: Optional[list] = None,
    limit: Optional[int] = None
) -> dict:
    """
    문서 마이그레이션 실행

    Args:
        db_path: 대상 DB 경로
        priority_only: 우선순위 문서만 마이그레이션
        jenis_filter: 특정 법령 유형만 마이그레이션
        limit: 최대 문서 수

    Returns:
        마이그레이션 결과
    """
    db = OCRPipelineDB(db_path)
    migrator = DocumentMigrator(db)

    if priority_only:
        print("[*] Migrating priority documents (UU, PP, Perpres, Perppu)...")
        results = migrator.migrate_priority_documents()
    else:
        print("[*] Migrating documents...")
        if jenis_filter:
            count = migrator.migrate_from_peraturan(jenis_filter=jenis_filter, limit=limit)
            results = {j: "included" for j in jenis_filter}
            results["total"] = count
        else:
            count = migrator.migrate_from_peraturan(limit=limit)
            results = {"total": count}

    # 상태 업데이트
    db.update_pipeline_state()

    return results


class TestRunManager:
    """테스트 실행 관리자"""

    def __init__(self, pipeline_db: OCRPipelineDB):
        self.pipeline_db = pipeline_db

    def create_test_run(
        self,
        run_id: str,
        phase: str,
        total_samples: int,
        config: dict = None,
    ) -> None:
        """
        테스트 실행 생성

        Args:
            run_id: 테스트 실행 ID
            phase: 테스트 단계 (phase1, phase2, phase3)
            total_samples: 총 샘플 수
            config: 실행 설정 JSON
        """
        with self.pipeline_db.connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO test_runs
                (run_id, phase, total_samples, config_json, started_at)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (
                run_id,
                phase,
                total_samples,
                json.dumps(config) if config else None,
            ))

    def update_test_run(
        self,
        run_id: str,
        processed: int = None,
        failed: int = None,
        avg_quality_score: float = None,
        median_quality_score: float = None,
        min_quality_score: float = None,
        max_quality_score: float = None,
        manual_review_count: int = None,
        manual_review_ratio: float = None,
        total_processing_time_ms: int = None,
        avg_processing_time_ms: float = None,
        summary: dict = None,
        completed: bool = False,
    ) -> None:
        """테스트 실행 상태 업데이트"""
        updates = []
        params = []

        if processed is not None:
            updates.append("processed = ?")
            params.append(processed)

        if failed is not None:
            updates.append("failed = ?")
            params.append(failed)

        if avg_quality_score is not None:
            updates.append("avg_quality_score = ?")
            params.append(avg_quality_score)

        if median_quality_score is not None:
            updates.append("median_quality_score = ?")
            params.append(median_quality_score)

        if min_quality_score is not None:
            updates.append("min_quality_score = ?")
            params.append(min_quality_score)

        if max_quality_score is not None:
            updates.append("max_quality_score = ?")
            params.append(max_quality_score)

        if manual_review_count is not None:
            updates.append("manual_review_count = ?")
            params.append(manual_review_count)

        if manual_review_ratio is not None:
            updates.append("manual_review_ratio = ?")
            params.append(manual_review_ratio)

        if total_processing_time_ms is not None:
            updates.append("total_processing_time_ms = ?")
            params.append(total_processing_time_ms)

        if avg_processing_time_ms is not None:
            updates.append("avg_processing_time_ms = ?")
            params.append(avg_processing_time_ms)

        if summary is not None:
            updates.append("summary_json = ?")
            params.append(json.dumps(summary))

        if completed:
            updates.append("completed_at = datetime('now')")

        if not updates:
            return

        params.append(run_id)

        with self.pipeline_db.connection() as conn:
            conn.execute(f"""
                UPDATE test_runs
                SET {', '.join(updates)}
                WHERE run_id = ?
            """, params)

    def get_test_run(self, run_id: str) -> Optional[dict]:
        """테스트 실행 조회"""
        with self.pipeline_db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM test_runs WHERE run_id = ?",
                (run_id,)
            ).fetchone()

            if row:
                return dict(row)
            return None

    def list_test_runs(self, phase: str = None, limit: int = 20) -> list:
        """테스트 실행 목록 조회"""
        with self.pipeline_db.connection() as conn:
            query = "SELECT * FROM test_runs"
            params = []

            if phase:
                query += " WHERE phase = ?"
                params.append(phase)

            query += " ORDER BY started_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def add_test_sample(
        self,
        run_id: str,
        document_id: str,
        category: str,
        year: int = None,
        avg_quality_score: float = None,
        total_pages: int = None,
        pages_text: int = 0,
        pages_ocr: int = 0,
        pages_hybrid: int = 0,
        pages_skip: int = 0,
        pages_manual: int = 0,
        status: str = "pending",
        processing_time_ms: int = None,
        error_message: str = None,
        result_json: dict = None,
    ) -> None:
        """테스트 샘플 추가"""
        with self.pipeline_db.connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO test_samples
                (run_id, document_id, category, year, avg_quality_score,
                 total_pages, pages_text, pages_ocr, pages_hybrid, pages_skip, pages_manual,
                 status, processing_time_ms, error_message, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, document_id, category, year, avg_quality_score,
                total_pages, pages_text, pages_ocr, pages_hybrid, pages_skip, pages_manual,
                status, processing_time_ms, error_message,
                json.dumps(result_json) if result_json else None,
            ))

    def get_test_samples(
        self,
        run_id: str,
        status: str = None,
        category: str = None,
    ) -> list:
        """테스트 샘플 조회"""
        with self.pipeline_db.connection() as conn:
            query = "SELECT * FROM test_samples WHERE run_id = ?"
            params = [run_id]

            if status:
                query += " AND status = ?"
                params.append(status)

            if category:
                query += " AND category = ?"
                params.append(category)

            query += " ORDER BY avg_quality_score ASC"

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_test_stats(self, run_id: str) -> dict:
        """테스트 실행 통계 조회"""
        with self.pipeline_db.connection() as conn:
            # 기본 통계
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    AVG(avg_quality_score) as avg_quality,
                    SUM(pages_manual) as total_manual
                FROM test_samples
                WHERE run_id = ?
            """, (run_id,)).fetchone()

            stats = dict(row)

            # 카테고리별 통계
            rows = conn.execute("""
                SELECT category, COUNT(*) as count, AVG(avg_quality_score) as avg_quality
                FROM test_samples
                WHERE run_id = ?
                GROUP BY category
            """, (run_id,)).fetchall()

            stats["by_category"] = {row["category"]: {
                "count": row["count"],
                "avg_quality": row["avg_quality"],
            } for row in rows}

            return stats

    def save_thresholds(
        self,
        run_id: str,
        quality_threshold: float,
        manual_review_trigger: float,
        broken_ratio_alert: float,
        word_recognition_min: float,
        ocr_confidence_min: float,
        notes: str = None,
        set_current: bool = False,
    ) -> None:
        """보정된 임계값 저장"""
        with self.pipeline_db.connection() as conn:
            # 새 임계값 저장
            conn.execute("""
                INSERT INTO test_thresholds
                (run_id, quality_threshold, manual_review_trigger, broken_ratio_alert,
                 word_recognition_min, ocr_confidence_min, is_current, calibration_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, quality_threshold, manual_review_trigger, broken_ratio_alert,
                word_recognition_min, ocr_confidence_min, 0, notes,
            ))

            if set_current:
                # 현재 플래그 업데이트
                conn.execute("UPDATE test_thresholds SET is_current = 0 WHERE is_current = 1")
                conn.execute("""
                    UPDATE test_thresholds
                    SET is_current = 1
                    WHERE run_id = ?
                """, (run_id,))

    def get_current_thresholds(self) -> dict:
        """현재 사용 중인 임계값 조회"""
        with self.pipeline_db.connection() as conn:
            row = conn.execute("""
                SELECT * FROM test_thresholds WHERE is_current = 1
            """).fetchone()

            if row:
                return dict(row)

            # 기본값 반환
            return {
                "quality_threshold": 0.92,
                "manual_review_trigger": 0.50,
                "broken_ratio_alert": 0.10,
                "word_recognition_min": 0.40,
                "ocr_confidence_min": 0.60,
            }


# CLI 인터페이스
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m src.ocr.database <command>")
        print("Commands:")
        print("  init          - Initialize database schema")
        print("  migrate       - Migrate priority documents")
        print("  migrate-all   - Migrate all documents")
        print("  stats         - Show database statistics")
        sys.exit(1)

    command = sys.argv[1]

    if command == "init":
        db = init_db()
        print("[OK] Database initialized successfully")

    elif command == "migrate":
        db = init_db()
        results = migrate_documents(priority_only=True)
        print(f"\n[OK] Migration complete: {sum(results.values())} documents")

    elif command == "migrate-all":
        db = init_db()
        results = migrate_documents(priority_only=False)
        print(f"\n[OK] Migration complete: {results.get('total', 0)} documents")

    elif command == "stats":
        db = OCRPipelineDB()
        stats = db.get_stats()
        print("\n=== OCR Pipeline Database Statistics ===")
        print(f"Total documents: {stats['total_documents']}")
        print(f"Total clusters: {stats['total_clusters']}")
        print(f"Active pattern rules: {stats['active_pattern_rules']}")
        print("\nBy stage:")
        for stage, count in stats.get("by_stage", {}).items():
            print(f"  {stage}: {count}")
        print("\nBy jenis:")
        for jenis, count in list(stats.get("by_jenis", {}).items())[:10]:
            print(f"  {jenis}: {count}")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
