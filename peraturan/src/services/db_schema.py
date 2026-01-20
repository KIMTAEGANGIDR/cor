"""
통합 DB 스키마 및 마이그레이션 (v3)

peraturan.go.id 데이터 전용 (BPK 병합 금지)

테이블:
- peraturan: 메타데이터 + 텍스트 + 상태 기록
- relation_expressions: 검출된 관계 표현 (해석 아님)
- processing_log: 처리 로그

v3 변경사항:
- validity_status → status_meta (원본 메타데이터 그대로)
- relation_tags 컬럼 추가 (JSON)
- has_conditional_expr 플래그 추가
- relation_expressions 테이블 (peraturan_relations 대체)
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


# 현재 스키마 버전
SCHEMA_VERSION = 3


def get_schema_version(conn: sqlite3.Connection) -> int:
    """현재 DB 스키마 버전 조회"""
    try:
        cursor = conn.execute("SELECT version FROM schema_version")
        row = cursor.fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """스키마 버전 설정"""
    conn.execute("""
        INSERT OR REPLACE INTO schema_version (id, version, updated_at)
        VALUES (1, ?, datetime('now'))
    """, (version,))
    conn.commit()


def migrate(db_path: Path, target_version: int = SCHEMA_VERSION) -> bool:
    """
    DB 마이그레이션 실행

    Args:
        db_path: DB 파일 경로
        target_version: 목표 버전

    Returns:
        성공 여부
    """
    conn = sqlite3.connect(db_path)
    current_version = get_schema_version(conn)

    print(f"현재 스키마 버전: {current_version}")
    print(f"목표 스키마 버전: {target_version}")

    if current_version >= target_version:
        print("마이그레이션 필요 없음")
        conn.close()
        return True

    try:
        # 버전별 마이그레이션
        if current_version < 1:
            _migrate_v1(conn)
            set_schema_version(conn, 1)
            print("v1 마이그레이션 완료")

        if current_version < 2 and target_version >= 2:
            _migrate_v2(conn)
            set_schema_version(conn, 2)
            print("v2 마이그레이션 완료")

        if current_version < 3 and target_version >= 3:
            _migrate_v3(conn)
            set_schema_version(conn, 3)
            print("v3 마이그레이션 완료")

        conn.close()
        return True

    except Exception as e:
        print(f"마이그레이션 실패: {e}")
        conn.rollback()
        conn.close()
        return False


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """v1: 스키마 버전 테이블 생성"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """v2: 현행성 판단 + 관계 테이블 + 처리 로그 (레거시)"""

    # 1. peraturan 테이블에 현행성 컬럼 추가
    _add_column_if_not_exists(conn, "peraturan", "validity_status", "TEXT")
    _add_column_if_not_exists(conn, "peraturan", "validity_confidence", "REAL")
    _add_column_if_not_exists(conn, "peraturan", "validity_reason", "TEXT")
    _add_column_if_not_exists(conn, "peraturan", "akn_xml_path", "TEXT")
    _add_column_if_not_exists(conn, "peraturan", "processed_at", "TEXT")

    # 2. 법령 간 관계 테이블 (레거시, v3에서 대체됨)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peraturan_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_slug TEXT NOT NULL,
            target_slug TEXT,
            target_jenis TEXT,
            target_nomor TEXT,
            target_tahun INTEGER,
            rel_type TEXT NOT NULL,
            pasal_dasar TEXT,
            kondisi TEXT,
            confidence REAL DEFAULT 0.9,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_slug) REFERENCES peraturan(slug) ON DELETE CASCADE,
            UNIQUE(source_slug, target_slug, rel_type)
        )
    """)

    # 3. 관계 테이블 인덱스
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relations_source
        ON peraturan_relations(source_slug)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relations_target
        ON peraturan_relations(target_slug)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relations_type
        ON peraturan_relations(rel_type)
    """)

    # 4. 처리 로그 테이블
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL,
            stage TEXT NOT NULL,
            success INTEGER NOT NULL,
            error_message TEXT,
            processing_time_ms INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (slug) REFERENCES peraturan(slug) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_processing_log_slug
        ON processing_log(slug)
    """)

    # 5. 현행성 인덱스
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_peraturan_validity
        ON peraturan(validity_status)
    """)

    conn.commit()


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """v3: 메타데이터 상태 기록 (판단 아님) + 관계 표현 테이블"""

    # 1. peraturan 테이블에 새 컬럼 추가
    # status_meta: 원본 메타데이터 status 그대로
    _add_column_if_not_exists(conn, "peraturan", "status_meta", "TEXT")
    # relation_tags: JSON 형식으로 검출된 관계 태그 저장
    _add_column_if_not_exists(conn, "peraturan", "relation_tags", "TEXT")
    # has_conditional_expr: 조건부 표현 검출 플래그
    _add_column_if_not_exists(conn, "peraturan", "has_conditional_expr", "INTEGER DEFAULT 0")
    # effective_date: 시행일
    _add_column_if_not_exists(conn, "peraturan", "effective_date", "TEXT")
    # effective_date_type: 시행일 유형
    _add_column_if_not_exists(conn, "peraturan", "effective_date_type", "TEXT")
    # effective_date_raw: 시행일 원문
    _add_column_if_not_exists(conn, "peraturan", "effective_date_raw", "TEXT")
    # recorded_at: 기록 시점
    _add_column_if_not_exists(conn, "peraturan", "recorded_at", "TEXT")

    # 2. 관계 표현 테이블 (검출된 표현 기록, 해석 아님)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relation_expressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_slug TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            target_slug TEXT,
            target_jenis TEXT,
            target_nomor TEXT,
            target_tahun INTEGER,
            expression_type TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            pasal_context TEXT,
            kondisi_text TEXT,
            confidence REAL DEFAULT 0.9,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_slug) REFERENCES peraturan(slug) ON DELETE CASCADE
        )
    """)

    # 3. 인덱스 생성
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relation_expr_source
        ON relation_expressions(source_slug)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relation_expr_type
        ON relation_expressions(expression_type)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relation_expr_target
        ON relation_expressions(target_slug)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_peraturan_status_meta
        ON peraturan(status_meta)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_peraturan_conditional
        ON peraturan(has_conditional_expr)
    """)

    conn.commit()


def _add_column_if_not_exists(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str
) -> None:
    """컬럼이 없으면 추가"""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor]

    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


class DatabaseManager:
    """
    DB 관리자 (v3)

    사용법:
        db = DatabaseManager(Path("peraturan.db"))
        db.migrate()
        db.save_status_record(record)
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _get_connection(self) -> sqlite3.Connection:
        if not self._conn:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def migrate(self) -> bool:
        """마이그레이션 실행"""
        return migrate(self.db_path)

    def save_status_record(
        self,
        slug: str,
        status_meta: str,
        relation_tags: list[str],
        has_conditional_expr: bool,
        effective_date: Optional[str] = None,
        effective_date_type: Optional[str] = None,
        effective_date_raw: str = "",
    ) -> None:
        """메타데이터 상태 기록 저장"""
        conn = self._get_connection()
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
            status_meta,
            json.dumps(relation_tags),
            1 if has_conditional_expr else 0,
            effective_date,
            effective_date_type,
            effective_date_raw,
            slug
        ))
        conn.commit()

    def save_tagged_expression(
        self,
        source_slug: str,
        target_ref: str,
        expression_type: str,
        raw_text: str,
        target_slug: Optional[str] = None,
        target_jenis: Optional[str] = None,
        target_nomor: Optional[str] = None,
        target_tahun: Optional[int] = None,
        pasal_context: Optional[str] = None,
        kondisi_text: Optional[str] = None,
        confidence: float = 0.9,
    ) -> None:
        """태깅된 관계 표현 저장"""
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO relation_expressions
            (source_slug, target_ref, target_slug, target_jenis, target_nomor,
             target_tahun, expression_type, raw_text, pasal_context, kondisi_text, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            source_slug, target_ref, target_slug, target_jenis, target_nomor,
            target_tahun, expression_type, raw_text, pasal_context, kondisi_text, confidence
        ))
        conn.commit()

    def save_processing_log(
        self,
        slug: str,
        stage: str,
        success: bool,
        error_message: Optional[str] = None,
        processing_time_ms: int = 0,
    ) -> None:
        """처리 로그 저장"""
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO processing_log
            (slug, stage, success, error_message, processing_time_ms)
            VALUES (?, ?, ?, ?, ?)
        """, (slug, stage, 1 if success else 0, error_message, processing_time_ms))
        conn.commit()

    def get_tagged_expressions(self, slug: str) -> list[dict]:
        """법령의 태깅된 표현 조회"""
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT * FROM relation_expressions
            WHERE source_slug = ?
        """, (slug,))
        return [dict(row) for row in cursor]

    def get_expressions_targeting(self, slug: str) -> list[dict]:
        """특정 법령을 참조하는 표현들 조회"""
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT * FROM relation_expressions
            WHERE target_slug = ?
        """, (slug,))
        return [dict(row) for row in cursor]

    def get_statistics(self) -> dict:
        """통계 조회"""
        conn = self._get_connection()
        stats = {}

        # 전체
        cursor = conn.execute("SELECT COUNT(*) FROM peraturan")
        stats["total"] = cursor.fetchone()[0]

        # status_meta별
        cursor = conn.execute("""
            SELECT status_meta, COUNT(*)
            FROM peraturan
            WHERE status_meta IS NOT NULL
            GROUP BY status_meta
        """)
        stats["by_status_meta"] = {row[0]: row[1] for row in cursor}

        # 조건부 표현 검출
        cursor = conn.execute("""
            SELECT COUNT(*) FROM peraturan
            WHERE has_conditional_expr = 1
        """)
        stats["has_conditional_expr"] = cursor.fetchone()[0]

        # 관계 표현 수
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM relation_expressions")
            stats["total_expressions"] = cursor.fetchone()[0]

            # 표현 유형별
            cursor = conn.execute("""
                SELECT expression_type, COUNT(*)
                FROM relation_expressions
                GROUP BY expression_type
            """)
            stats["expressions_by_type"] = {row[0]: row[1] for row in cursor}
        except sqlite3.OperationalError:
            stats["total_expressions"] = 0
            stats["expressions_by_type"] = {}

        return stats

    # 레거시 호환 (v2)
    def save_validity(
        self,
        slug: str,
        status: str,
        confidence: float,
        reason: str,
    ) -> None:
        """현행성 판단 결과 저장 (레거시 v2 호환)"""
        conn = self._get_connection()
        conn.execute("""
            UPDATE peraturan SET
                validity_status = ?,
                validity_confidence = ?,
                validity_reason = ?,
                processed_at = datetime('now')
            WHERE slug = ?
        """, (status, confidence, reason, slug))
        conn.commit()

    def save_relation(
        self,
        source_slug: str,
        target_slug: Optional[str],
        rel_type: str,
        target_jenis: Optional[str] = None,
        target_nomor: Optional[str] = None,
        target_tahun: Optional[int] = None,
        pasal_dasar: Optional[str] = None,
        kondisi: Optional[str] = None,
        confidence: float = 0.9,
    ) -> None:
        """관계 저장 (레거시 v2 호환)"""
        conn = self._get_connection()
        conn.execute("""
            INSERT OR REPLACE INTO peraturan_relations
            (source_slug, target_slug, target_jenis, target_nomor, target_tahun,
             rel_type, pasal_dasar, kondisi, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            source_slug, target_slug, target_jenis, target_nomor, target_tahun,
            rel_type, pasal_dasar, kondisi, confidence
        ))
        conn.commit()

    def get_relations(self, slug: str) -> list[dict]:
        """법령의 관계 조회 (레거시 v2 호환)"""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT * FROM peraturan_relations
                WHERE source_slug = ? OR target_slug = ?
            """, (slug, slug))
            return [dict(row) for row in cursor]
        except sqlite3.OperationalError:
            return []


# CLI
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python db_schema.py <db_path> [migrate|stats]")
        sys.exit(1)

    db_path = Path(sys.argv[1])
    action = sys.argv[2] if len(sys.argv) > 2 else "stats"

    if not db_path.exists():
        print(f"DB 파일 없음: {db_path}")
        sys.exit(1)

    db = DatabaseManager(db_path)

    if action == "migrate":
        print("마이그레이션 실행 중...")
        success = db.migrate()
        print("완료" if success else "실패")

    elif action == "stats":
        stats = db.get_statistics()
        print("=== DB 통계 (v3) ===")
        print(f"총 법령: {stats['total']:,}")
        print(f"status_meta별: {stats.get('by_status_meta', {})}")
        print(f"조건부 표현 검출: {stats.get('has_conditional_expr', 0):,}건")
        print(f"총 관계 표현: {stats.get('total_expressions', 0):,}")
        print(f"표현 유형별: {stats.get('expressions_by_type', {})}")

    db.close()
