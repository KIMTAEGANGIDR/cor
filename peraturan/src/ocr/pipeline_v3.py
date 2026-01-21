"""
ILIS OCR Pipeline V3 - Main Orchestrator

전체 파이프라인 오케스트레이션
- 단계별 실행 및 진행 상황 추적
- 체크포인트 기반 재시작
- CLI 인터페이스

사용법:
    # 전체 파이프라인 실행
    python -m peraturan.src.ocr.pipeline_v3 run --limit 100

    # 특정 단계만 실행
    python -m peraturan.src.ocr.pipeline_v3 run --stage page_gen --limit 100

    # 상태 확인
    python -m peraturan.src.ocr.pipeline_v3 status

    # 품질 리포트
    python -m peraturan.src.ocr.pipeline_v3 report
"""

import argparse
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .batch_ocr import BatchOCRProcessor
from .checkpoint_manager import CheckpointManager
from .models import CheckpointStatus, PipelineV3Config
from .noise_applicator import NoiseApplicator
from .page_generator import PageGenerator
from .quality_controller import QualityController
from .text_cleaner import TextCleaner

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"


@dataclass
class StageResult:
    """단계 실행 결과"""
    stage: str
    success: bool
    processed: int = 0
    failed: int = 0
    elapsed_sec: float = 0.0
    error_message: Optional[str] = None


class PipelineV3:
    """OCR 파이프라인 V3 오케스트레이터"""

    STAGES = ["page_gen", "noise_removal", "ocr", "text_clean", "quality"]

    def __init__(
        self,
        db_path: Optional[Path] = None,
        config: Optional[PipelineV3Config] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.config = config or PipelineV3Config()
        self.checkpoint_manager = CheckpointManager(self.db_path)

        # 각 단계 프로세서
        self._page_generator = None
        self._noise_applicator = None
        self._ocr_processor = None
        self._text_cleaner = None
        self._quality_controller = None

    def get_connection(self):
        """데이터베이스 연결"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @property
    def page_generator(self) -> PageGenerator:
        if not self._page_generator:
            self._page_generator = PageGenerator(self.db_path, config=self.config)
        return self._page_generator

    @property
    def noise_applicator(self) -> NoiseApplicator:
        if not self._noise_applicator:
            self._noise_applicator = NoiseApplicator(self.db_path, config=self.config)
        return self._noise_applicator

    @property
    def ocr_processor(self) -> BatchOCRProcessor:
        if not self._ocr_processor:
            self._ocr_processor = BatchOCRProcessor(self.db_path, config=self.config)
        return self._ocr_processor

    @property
    def text_cleaner(self) -> TextCleaner:
        if not self._text_cleaner:
            self._text_cleaner = TextCleaner(self.db_path, config=self.config)
        return self._text_cleaner

    @property
    def quality_controller(self) -> QualityController:
        if not self._quality_controller:
            self._quality_controller = QualityController(self.db_path, config=self.config)
        return self._quality_controller

    def run_stage(
        self,
        stage: str,
        limit: Optional[int] = None,
        cluster_id: Optional[int] = None,
        workers: Optional[int] = None,
    ) -> StageResult:
        """
        단일 단계 실행

        Args:
            stage: 단계 이름
            limit: 최대 처리 수
            cluster_id: 클러스터 필터
            workers: 워커 수

        Returns:
            실행 결과
        """
        start_time = time.time()

        try:
            if stage == "page_gen":
                stats = self.page_generator.generate_batch(
                    limit=limit,
                    cluster_id=cluster_id,
                    workers=workers or self.config.workers,
                )
                return StageResult(
                    stage=stage,
                    success=True,
                    processed=stats.get("processed", 0),
                    failed=stats.get("failed", 0),
                    elapsed_sec=time.time() - start_time,
                )

            elif stage == "noise_removal":
                stats = self.noise_applicator.apply_batch(
                    cluster_id=cluster_id,
                    limit=limit,
                    workers=workers or self.config.workers,
                )
                return StageResult(
                    stage=stage,
                    success=True,
                    processed=stats.get("processed", 0),
                    failed=stats.get("failed", 0),
                    elapsed_sec=time.time() - start_time,
                )

            elif stage == "ocr":
                stats = self.ocr_processor.process_batch(
                    cluster_id=cluster_id,
                    limit=limit,
                )
                return StageResult(
                    stage=stage,
                    success=True,
                    processed=stats.get("processed", 0),
                    failed=stats.get("failed", 0),
                    elapsed_sec=time.time() - start_time,
                )

            elif stage == "text_clean":
                stats = self.text_cleaner.process_batch(
                    cluster_id=cluster_id,
                    limit=limit,
                )
                return StageResult(
                    stage=stage,
                    success=True,
                    processed=stats.get("processed", 0),
                    failed=stats.get("failed", 0),
                    elapsed_sec=time.time() - start_time,
                )

            elif stage == "quality":
                stats = self.quality_controller.assess_batch(
                    cluster_id=cluster_id,
                    limit=limit,
                )
                return StageResult(
                    stage=stage,
                    success=True,
                    processed=stats.get("total", 0),
                    elapsed_sec=time.time() - start_time,
                )

            else:
                return StageResult(
                    stage=stage,
                    success=False,
                    error_message=f"Unknown stage: {stage}",
                )

        except KeyboardInterrupt:
            return StageResult(
                stage=stage,
                success=False,
                elapsed_sec=time.time() - start_time,
                error_message="Interrupted by user",
            )

        except Exception as e:
            logger.exception(f"Stage {stage} failed")
            return StageResult(
                stage=stage,
                success=False,
                elapsed_sec=time.time() - start_time,
                error_message=str(e),
            )

    def run_pipeline(
        self,
        stages: Optional[list[str]] = None,
        limit: Optional[int] = None,
        cluster_id: Optional[int] = None,
        workers: Optional[int] = None,
        stop_on_error: bool = False,
    ) -> list[StageResult]:
        """
        파이프라인 실행

        Args:
            stages: 실행할 단계 목록 (없으면 전체)
            limit: 최대 처리 수
            cluster_id: 클러스터 필터
            workers: 워커 수
            stop_on_error: 오류 시 중단 여부

        Returns:
            단계별 실행 결과
        """
        stages = stages or self.STAGES
        results = []

        logger.info(f"파이프라인 시작: {', '.join(stages)}")
        pipeline_start = time.time()

        for stage in stages:
            logger.info(f"\n{'='*50}")
            logger.info(f"Stage: {stage}")
            logger.info(f"{'='*50}")

            result = self.run_stage(
                stage=stage,
                limit=limit,
                cluster_id=cluster_id,
                workers=workers,
            )
            results.append(result)

            if result.success:
                logger.info(
                    f"✅ {stage} 완료: {result.processed} 처리, "
                    f"{result.failed} 실패, {result.elapsed_sec:.1f}초"
                )
            else:
                logger.error(f"❌ {stage} 실패: {result.error_message}")
                if stop_on_error:
                    break

        total_elapsed = time.time() - pipeline_start
        logger.info(f"\n{'='*50}")
        logger.info(f"파이프라인 완료: {total_elapsed:.1f}초")

        return results

    def get_status(self) -> dict:
        """파이프라인 상태 조회"""
        conn = self.get_connection()
        try:
            status = {}

            # 문서 통계
            row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
            status["total_documents"] = row[0]

            # 페이지 이미지 상태
            rows = conn.execute("""
                SELECT status, COUNT(*) as count FROM page_images GROUP BY status
            """).fetchall()
            status["page_images"] = {row["status"]: row["count"] for row in rows}

            # OCR 페이지 상태
            rows = conn.execute("""
                SELECT status, COUNT(*) as count FROM ocr_pages GROUP BY status
            """).fetchall()
            status["ocr_pages"] = {row["status"]: row["count"] for row in rows}

            # OCR 결과 상태
            rows = conn.execute("""
                SELECT status, COUNT(*) as count FROM ocr_results GROUP BY status
            """).fetchall()
            status["ocr_results"] = {row["status"]: row["count"] for row in rows}

            # 품질 통계
            rows = conn.execute("""
                SELECT quality_tier, COUNT(*) as count FROM quality_metrics GROUP BY quality_tier
            """).fetchall()
            status["quality_tiers"] = {row["quality_tier"]: row["count"] for row in rows}

            # 수동 검토 필요
            row = conn.execute("""
                SELECT COUNT(*) FROM quality_metrics WHERE needs_manual_review = 1 AND reviewed_at IS NULL
            """).fetchone()
            status["pending_manual_review"] = row[0]

            # 최근 체크포인트
            checkpoints = self.checkpoint_manager.list_checkpoints(limit=5)
            status["recent_checkpoints"] = [
                {
                    "stage": cp.stage,
                    "batch_id": cp.batch_id,
                    "status": cp.status,
                    "progress": f"{cp.processed_count}/{cp.total_count or '?'}",
                }
                for cp in checkpoints
            ]

            return status
        finally:
            conn.close()

    def print_status(self) -> None:
        """상태 출력"""
        status = self.get_status()

        print("\n" + "=" * 60)
        print("OCR Pipeline V3 Status")
        print("=" * 60)

        print(f"\n📊 Total Documents: {status['total_documents']}")

        print("\n📄 Page Images:")
        for s, count in status.get("page_images", {}).items():
            print(f"  {s}: {count}")

        print("\n🔍 OCR Pages:")
        for s, count in status.get("ocr_pages", {}).items():
            print(f"  {s}: {count}")

        print("\n📝 OCR Results:")
        for s, count in status.get("ocr_results", {}).items():
            print(f"  {s}: {count}")

        print("\n⭐ Quality Tiers:")
        total_quality = sum(status.get("quality_tiers", {}).values())
        for tier, count in status.get("quality_tiers", {}).items():
            pct = (count / total_quality * 100) if total_quality > 0 else 0
            print(f"  {tier}: {count} ({pct:.1f}%)")

        print(f"\n⚠️  Pending Manual Review: {status['pending_manual_review']}")

        if status.get("recent_checkpoints"):
            print("\n🔖 Recent Checkpoints:")
            for cp in status["recent_checkpoints"]:
                print(f"  [{cp['status']}] {cp['stage']}/{cp['batch_id']}: {cp['progress']}")

        print("\n" + "=" * 60)

    def generate_report(self) -> str:
        """전체 리포트 생성"""
        return self.quality_controller.generate_report()

    def aggregate_ocr_results(self) -> dict:
        """OCR 결과 집계"""
        return self.ocr_processor.aggregate_all_documents()


def main():
    """CLI 메인 함수"""
    parser = argparse.ArgumentParser(
        description="ILIS OCR Pipeline V3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full pipeline with limit
  python -m peraturan.src.ocr.pipeline_v3 run --limit 100

  # Run specific stages
  python -m peraturan.src.ocr.pipeline_v3 run --stage page_gen --limit 100
  python -m peraturan.src.ocr.pipeline_v3 run --stage ocr --limit 50

  # Check status
  python -m peraturan.src.ocr.pipeline_v3 status

  # Generate report
  python -m peraturan.src.ocr.pipeline_v3 report

  # Aggregate OCR results
  python -m peraturan.src.ocr.pipeline_v3 aggregate
        """,
    )

    parser.add_argument(
        "command",
        choices=["run", "status", "report", "aggregate", "init"],
        help="Command to execute",
    )
    parser.add_argument(
        "--stage", "-s",
        choices=PipelineV3.STAGES,
        help="Run specific stage only",
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=PipelineV3.STAGES,
        help="Run specific stages in order",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        help="Maximum items to process",
    )
    parser.add_argument(
        "--cluster", "-c",
        type=int,
        help="Filter by cluster ID",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=8,
        help="Number of workers (default: 8)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Image DPI (default: 150)",
    )
    parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="Disable GPU for OCR",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop pipeline on first error",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # 로깅 설정
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 설정 생성
    config = PipelineV3Config(
        image_dpi=args.dpi,
        workers=args.workers,
        use_gpu=not args.no_gpu,
    )

    # 파이프라인 생성
    pipeline = PipelineV3(config=config)

    # 명령 실행
    if args.command == "run":
        stages = None
        if args.stage:
            stages = [args.stage]
        elif args.stages:
            stages = args.stages

        results = pipeline.run_pipeline(
            stages=stages,
            limit=args.limit,
            cluster_id=args.cluster,
            workers=args.workers,
            stop_on_error=args.stop_on_error,
        )

        # 결과 요약
        print("\n" + "=" * 60)
        print("Pipeline Results Summary")
        print("=" * 60)
        for result in results:
            status = "✅" if result.success else "❌"
            print(f"{status} {result.stage}: {result.processed} processed, {result.failed} failed ({result.elapsed_sec:.1f}s)")
            if result.error_message:
                print(f"   Error: {result.error_message}")

    elif args.command == "status":
        pipeline.print_status()

    elif args.command == "report":
        report = pipeline.generate_report()
        print(report)

    elif args.command == "aggregate":
        print("Aggregating OCR results...")
        result = pipeline.aggregate_ocr_results()
        print(f"Aggregated {result['aggregated']}/{result['total']} documents")

    elif args.command == "init":
        # 스키마 초기화
        from .database import init_db
        init_db(pipeline.db_path)
        print("Database initialized")


if __name__ == "__main__":
    main()
