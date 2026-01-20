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
    PipelineState, get_priority, DocumentStage
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
