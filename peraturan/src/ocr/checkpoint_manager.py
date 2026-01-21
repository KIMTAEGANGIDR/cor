"""
ILIS OCR Pipeline - Checkpoint Manager

재시작 가능한 파이프라인을 위한 체크포인트 관리
- 단계별 진행 상황 추적
- 장애 시 재시작 지원
- 배치 처리 상태 저장
"""

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from .models import CheckpointStatus, ProcessingCheckpoint

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"


class CheckpointManager:
    """체크포인트 관리자"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH

    @contextmanager
    def connection(self):
        """데이터베이스 연결"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_checkpoint(
        self,
        stage: str,
        total_count: int,
        config: Optional[dict] = None,
        batch_id: Optional[str] = None,
    ) -> ProcessingCheckpoint:
        """
        새 체크포인트 생성

        Args:
            stage: 처리 단계 (page_gen, noise_removal, ocr, text_clean, quality)
            total_count: 전체 처리 대상 수
            config: 처리 설정 (재시작 시 동일 설정 사용)
            batch_id: 배치 ID (없으면 자동 생성)

        Returns:
            생성된 체크포인트
        """
        if not batch_id:
            batch_id = f"{stage}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        checkpoint = ProcessingCheckpoint(
            stage=stage,
            batch_id=batch_id,
            total_count=total_count,
            status=CheckpointStatus.RUNNING.value,
            config_json=config,
        )

        with self.connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO processing_checkpoints
                (stage, batch_id, total_count, status, config_json, started_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (
                stage,
                batch_id,
                total_count,
                CheckpointStatus.RUNNING.value,
                json.dumps(config) if config else None,
            ))

            row = conn.execute(
                "SELECT * FROM processing_checkpoints WHERE stage = ? AND batch_id = ?",
                (stage, batch_id)
            ).fetchone()

            return ProcessingCheckpoint(**dict(row)) if row else checkpoint

    def get_checkpoint(self, stage: str, batch_id: Optional[str] = None) -> Optional[ProcessingCheckpoint]:
        """
        체크포인트 조회

        Args:
            stage: 처리 단계
            batch_id: 배치 ID (없으면 가장 최근 체크포인트)
        """
        with self.connection() as conn:
            if batch_id:
                row = conn.execute(
                    "SELECT * FROM processing_checkpoints WHERE stage = ? AND batch_id = ?",
                    (stage, batch_id)
                ).fetchone()
            else:
                row = conn.execute("""
                    SELECT * FROM processing_checkpoints
                    WHERE stage = ?
                    ORDER BY started_at DESC
                    LIMIT 1
                """, (stage,)).fetchone()

            return ProcessingCheckpoint(**dict(row)) if row else None

    def get_active_checkpoint(self, stage: str) -> Optional[ProcessingCheckpoint]:
        """
        실행 중인 체크포인트 조회

        Args:
            stage: 처리 단계
        """
        with self.connection() as conn:
            row = conn.execute("""
                SELECT * FROM processing_checkpoints
                WHERE stage = ? AND status IN ('running', 'paused')
                ORDER BY started_at DESC
                LIMIT 1
            """, (stage,)).fetchone()

            return ProcessingCheckpoint(**dict(row)) if row else None

    def update_progress(
        self,
        stage: str,
        batch_id: str,
        document_id: str,
        page_number: Optional[int] = None,
        increment: int = 1,
    ) -> None:
        """
        진행 상황 업데이트

        Args:
            stage: 처리 단계
            batch_id: 배치 ID
            document_id: 마지막 처리 문서 ID
            page_number: 마지막 처리 페이지 번호
            increment: 처리 완료 증가량
        """
        with self.connection() as conn:
            conn.execute("""
                UPDATE processing_checkpoints
                SET last_document_id = ?,
                    last_page_number = ?,
                    processed_count = processed_count + ?,
                    last_updated_at = datetime('now')
                WHERE stage = ? AND batch_id = ?
            """, (document_id, page_number, increment, stage, batch_id))

    def set_status(
        self,
        stage: str,
        batch_id: str,
        status: CheckpointStatus,
        error_message: Optional[str] = None,
    ) -> None:
        """
        체크포인트 상태 변경

        Args:
            stage: 처리 단계
            batch_id: 배치 ID
            status: 새 상태
            error_message: 오류 메시지 (실패 시)
        """
        with self.connection() as conn:
            if status == CheckpointStatus.COMPLETED:
                conn.execute("""
                    UPDATE processing_checkpoints
                    SET status = ?,
                        error_message = ?,
                        completed_at = datetime('now'),
                        last_updated_at = datetime('now')
                    WHERE stage = ? AND batch_id = ?
                """, (status.value, error_message, stage, batch_id))
            else:
                conn.execute("""
                    UPDATE processing_checkpoints
                    SET status = ?,
                        error_message = ?,
                        last_updated_at = datetime('now')
                    WHERE stage = ? AND batch_id = ?
                """, (status.value, error_message, stage, batch_id))

    def resume_checkpoint(self, stage: str, batch_id: str) -> Optional[ProcessingCheckpoint]:
        """
        일시 중지된 체크포인트 재개

        Args:
            stage: 처리 단계
            batch_id: 배치 ID

        Returns:
            재개된 체크포인트
        """
        checkpoint = self.get_checkpoint(stage, batch_id)
        if not checkpoint:
            logger.warning(f"체크포인트를 찾을 수 없음: {stage}/{batch_id}")
            return None

        if checkpoint.status not in (CheckpointStatus.PAUSED.value, CheckpointStatus.FAILED.value):
            logger.warning(f"재개할 수 없는 상태: {checkpoint.status}")
            return None

        self.set_status(stage, batch_id, CheckpointStatus.RUNNING)
        checkpoint.status = CheckpointStatus.RUNNING.value
        return checkpoint

    def list_checkpoints(
        self,
        stage: Optional[str] = None,
        status: Optional[CheckpointStatus] = None,
        limit: int = 20,
    ) -> list[ProcessingCheckpoint]:
        """
        체크포인트 목록 조회

        Args:
            stage: 처리 단계 필터
            status: 상태 필터
            limit: 최대 조회 수
        """
        with self.connection() as conn:
            query = "SELECT * FROM processing_checkpoints WHERE 1=1"
            params = []

            if stage:
                query += " AND stage = ?"
                params.append(stage)

            if status:
                query += " AND status = ?"
                params.append(status.value)

            query += " ORDER BY started_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [ProcessingCheckpoint(**dict(row)) for row in rows]

    def get_stats(self) -> dict:
        """전체 체크포인트 통계"""
        with self.connection() as conn:
            # 단계별 통계
            rows = conn.execute("""
                SELECT stage, status, COUNT(*) as count
                FROM processing_checkpoints
                GROUP BY stage, status
            """).fetchall()

            stats_by_stage = {}
            for row in rows:
                stage = row["stage"]
                if stage not in stats_by_stage:
                    stats_by_stage[stage] = {}
                stats_by_stage[stage][row["status"]] = row["count"]

            # 최근 체크포인트
            recent = conn.execute("""
                SELECT stage, batch_id, status, processed_count, total_count,
                       last_updated_at
                FROM processing_checkpoints
                ORDER BY last_updated_at DESC
                LIMIT 5
            """).fetchall()

            return {
                "by_stage": stats_by_stage,
                "recent": [dict(r) for r in recent],
            }


class BatchIterator:
    """
    체크포인트 기반 배치 반복자

    체크포인트에서 재시작 가능한 배치 처리 지원
    """

    def __init__(
        self,
        checkpoint_manager: CheckpointManager,
        stage: str,
        batch_id: str,
    ):
        self.checkpoint_manager = checkpoint_manager
        self.stage = stage
        self.batch_id = batch_id
        self._checkpoint = None

    @property
    def checkpoint(self) -> Optional[ProcessingCheckpoint]:
        if not self._checkpoint:
            self._checkpoint = self.checkpoint_manager.get_checkpoint(
                self.stage, self.batch_id
            )
        return self._checkpoint

    def should_skip(self, document_id: str, page_number: Optional[int] = None) -> bool:
        """
        이미 처리된 항목인지 확인

        Args:
            document_id: 문서 ID
            page_number: 페이지 번호 (선택)

        Returns:
            True면 스킵해야 함
        """
        cp = self.checkpoint
        if not cp or not cp.last_document_id:
            return False

        # 문서 ID 비교
        if document_id < cp.last_document_id:
            return True
        if document_id > cp.last_document_id:
            return False

        # 같은 문서면 페이지 번호 비교
        if page_number is not None and cp.last_page_number is not None:
            return page_number <= cp.last_page_number

        return False

    def mark_progress(
        self,
        document_id: str,
        page_number: Optional[int] = None,
        increment: int = 1,
    ) -> None:
        """진행 상황 기록"""
        self.checkpoint_manager.update_progress(
            self.stage, self.batch_id, document_id, page_number, increment
        )

    def complete(self) -> None:
        """처리 완료 표시"""
        self.checkpoint_manager.set_status(
            self.stage, self.batch_id, CheckpointStatus.COMPLETED
        )

    def fail(self, error_message: str) -> None:
        """처리 실패 표시"""
        self.checkpoint_manager.set_status(
            self.stage, self.batch_id, CheckpointStatus.FAILED, error_message
        )

    def pause(self) -> None:
        """처리 일시 중지"""
        self.checkpoint_manager.set_status(
            self.stage, self.batch_id, CheckpointStatus.PAUSED
        )


def iterate_with_checkpoint(
    items: list,
    checkpoint_manager: CheckpointManager,
    stage: str,
    config: Optional[dict] = None,
    batch_id: Optional[str] = None,
    resume: bool = True,
    key_func=lambda x: x,
) -> Generator[tuple, None, None]:
    """
    체크포인트 기반 반복 헬퍼

    Args:
        items: 처리할 항목 목록
        checkpoint_manager: 체크포인트 관리자
        stage: 처리 단계
        config: 처리 설정
        batch_id: 배치 ID (없으면 새로 생성)
        resume: 기존 체크포인트에서 재개할지 여부
        key_func: 항목에서 키를 추출하는 함수

    Yields:
        (item, batch_iterator) 튜플
    """
    # 기존 체크포인트 확인
    existing = checkpoint_manager.get_active_checkpoint(stage) if resume else None

    if existing:
        logger.info(f"기존 체크포인트에서 재개: {existing.batch_id} ({existing.processed_count}/{existing.total_count})")
        batch_id = existing.batch_id
        checkpoint = checkpoint_manager.resume_checkpoint(stage, batch_id)
    else:
        checkpoint = checkpoint_manager.create_checkpoint(
            stage=stage,
            total_count=len(items),
            config=config,
            batch_id=batch_id,
        )
        batch_id = checkpoint.batch_id
        logger.info(f"새 체크포인트 생성: {batch_id}")

    iterator = BatchIterator(checkpoint_manager, stage, batch_id)

    try:
        for item in items:
            key = key_func(item)
            if not iterator.should_skip(key):
                yield item, iterator
        iterator.complete()
    except KeyboardInterrupt:
        logger.info("사용자 중단 - 체크포인트 저장")
        iterator.pause()
        raise
    except Exception as e:
        logger.error(f"처리 실패: {e}")
        iterator.fail(str(e))
        raise


# CLI
if __name__ == "__main__":
    import sys

    cm = CheckpointManager()

    if len(sys.argv) < 2:
        print("Usage: python -m peraturan.src.ocr.checkpoint_manager <command>")
        print("Commands:")
        print("  list           - List all checkpoints")
        print("  stats          - Show checkpoint statistics")
        sys.exit(1)

    command = sys.argv[1]

    if command == "list":
        checkpoints = cm.list_checkpoints()
        for cp in checkpoints:
            progress = f"{cp.processed_count}/{cp.total_count}" if cp.total_count else f"{cp.processed_count}"
            print(f"[{cp.status}] {cp.stage}/{cp.batch_id}: {progress}")

    elif command == "stats":
        stats = cm.get_stats()
        print("\n=== Checkpoint Statistics ===")
        for stage, status_counts in stats["by_stage"].items():
            print(f"\n{stage}:")
            for status, count in status_counts.items():
                print(f"  {status}: {count}")

        print("\n=== Recent Checkpoints ===")
        for cp in stats["recent"]:
            print(f"  {cp['stage']}/{cp['batch_id']}: {cp['status']} ({cp['processed_count']}/{cp['total_count'] or '?'})")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
