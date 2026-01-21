"""
ILIS OCR Pipeline - Batch OCR Processor

정제된 이미지에 대해 OCR 수행
- PaddleOCR 기반 (플러그인 방식으로 확장 가능)
- GPU 가속 지원
- 배치 처리 및 체크포인트 지원
"""

import json
import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .checkpoint_manager import CheckpointManager
from .models import (
    CheckpointStatus,
    OCRPage,
    PageImageStatus,
    PipelineV3Config,
)

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"


@dataclass
class OCRResult:
    """OCR 결과"""
    text: str
    confidence: float
    boxes: Optional[list] = None  # 바운딩 박스 정보


@dataclass
class PageOCRTask:
    """페이지 OCR 작업"""
    document_id: str
    page_number: int
    image_path: str
    cluster_id: Optional[int] = None


@dataclass
class PageOCRResult:
    """페이지 OCR 결과"""
    document_id: str
    page_number: int
    success: bool
    text: Optional[str] = None
    confidence: Optional[float] = None
    boxes: Optional[list] = None
    error_message: Optional[str] = None
    duration_ms: int = 0


class OCREngine(ABC):
    """OCR 엔진 추상 클래스"""

    @abstractmethod
    def initialize(self) -> None:
        """엔진 초기화"""
        pass

    @abstractmethod
    def process_image(self, image_path: str) -> OCRResult:
        """이미지 OCR 수행"""
        pass

    @abstractmethod
    def process_batch(self, image_paths: list[str]) -> list[OCRResult]:
        """배치 이미지 OCR 수행"""
        pass


class PaddleOCREngine(OCREngine):
    """PaddleOCR 엔진 (PP-OCRv5 기반)"""

    def __init__(
        self,
        lang: str = "en",
        use_gpu: bool = True,
        show_log: bool = False,
    ):
        self.lang = lang
        self.use_gpu = use_gpu
        self.show_log = show_log
        self.ocr = None

    def initialize(self) -> None:
        """PaddleOCR 초기화 (PP-OCRv5)"""
        if self.ocr is not None:
            return

        import os
        os.environ['DISABLE_MODEL_SOURCE_CHECK'] = 'True'

        try:
            # GPU 설정
            if self.use_gpu:
                import paddle
                paddle.set_device('gpu:0')

            from paddleocr import PaddleOCR
            # PaddleOCR 3.x API (PP-OCRv5)
            self.ocr = PaddleOCR(lang=self.lang)
            logger.info(f"PaddleOCR PP-OCRv5 initialized (lang={self.lang}, gpu={self.use_gpu})")
        except ImportError:
            raise RuntimeError("PaddleOCR not installed. Run: pip install paddleocr")

    def process_image(self, image_path: str) -> OCRResult:
        """단일 이미지 OCR (PP-OCRv5)"""
        if self.ocr is None:
            self.initialize()

        try:
            # PaddleOCR 3.x API: predict() 메서드 사용
            result = self.ocr.predict(image_path)

            if not result:
                return OCRResult(text="", confidence=0.0, boxes=[])

            # 결과 파싱 (PaddleOCR 3.x 형식)
            res = result[0]
            texts = res.get('rec_texts', [])
            scores = res.get('rec_scores', [])
            polys = res.get('rec_polys', [])

            if not texts:
                return OCRResult(text="", confidence=0.0, boxes=[])

            # 박스 정보 구성
            boxes = []
            for i, (text, score) in enumerate(zip(texts, scores)):
                box_info = {
                    "text": text,
                    "confidence": float(score),
                }
                if i < len(polys):
                    box_info["box"] = polys[i].tolist() if hasattr(polys[i], 'tolist') else polys[i]
                boxes.append(box_info)

            full_text = "\n".join(texts)
            avg_conf = sum(scores) / len(scores) if scores else 0.0

            return OCRResult(
                text=full_text,
                confidence=float(avg_conf),
                boxes=boxes,
            )

        except Exception as e:
            logger.error(f"OCR failed for {image_path}: {e}")
            return OCRResult(text="", confidence=0.0, boxes=[])

    def process_batch(self, image_paths: list[str]) -> list[OCRResult]:
        """배치 이미지 OCR (순차 처리)"""
        return [self.process_image(path) for path in image_paths]


class BatchOCRProcessor:
    """배치 OCR 프로세서"""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        config: Optional[PipelineV3Config] = None,
        engine: Optional[OCREngine] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.config = config or PipelineV3Config()
        self.checkpoint_manager = CheckpointManager(self.db_path)

        # OCR 엔진 설정
        if engine:
            self.engine = engine
        else:
            self.engine = PaddleOCREngine(
                lang=self.config.ocr_lang,
                use_gpu=self.config.use_gpu,
            )

    def get_connection(self):
        """데이터베이스 연결"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get_pages_to_process(
        self,
        cluster_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        OCR 처리할 페이지 목록 조회

        Args:
            cluster_id: 특정 클러스터만
            limit: 최대 페이지 수
        """
        conn = self.get_connection()
        try:
            query = """
                SELECT p.document_id, p.page_number,
                       COALESCE(p.cleaned_image_path, p.image_path) as image_path,
                       p.cluster_id
                FROM page_images p
                WHERE p.status IN ('generated', 'cleaned')
                  AND (p.cleaned_image_path IS NOT NULL OR p.image_path IS NOT NULL)
            """
            params = []

            if cluster_id:
                query += " AND p.cluster_id = ?"
                params.append(cluster_id)

            query += " ORDER BY p.document_id, p.page_number"

            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def save_ocr_result(
        self,
        document_id: str,
        page_number: int,
        text: str,
        confidence: float,
        boxes: Optional[list] = None,
        status: str = "processed",
        error_message: Optional[str] = None,
    ) -> None:
        """OCR 결과 저장"""
        conn = self.get_connection()
        try:
            # ocr_pages 테이블에 저장
            conn.execute("""
                INSERT OR REPLACE INTO ocr_pages
                (document_id, page_number, raw_text, boxes, confidence, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                document_id,
                page_number,
                text,
                json.dumps(boxes) if boxes else None,
                confidence,
                status,
                error_message,
            ))

            # page_images 상태 업데이트
            conn.execute("""
                UPDATE page_images SET
                    status = 'ocr_done',
                    updated_at = datetime('now')
                WHERE document_id = ? AND page_number = ?
            """, (document_id, page_number))

            conn.commit()
        finally:
            conn.close()

    def process_page(self, task: PageOCRTask) -> PageOCRResult:
        """
        단일 페이지 OCR 처리

        Args:
            task: OCR 작업

        Returns:
            처리 결과
        """
        start_time = time.time()

        # 이미지 파일 확인
        if not Path(task.image_path).exists():
            return PageOCRResult(
                document_id=task.document_id,
                page_number=task.page_number,
                success=False,
                error_message=f"Image not found: {task.image_path}",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        try:
            # OCR 수행
            result = self.engine.process_image(task.image_path)

            # 결과 저장
            self.save_ocr_result(
                document_id=task.document_id,
                page_number=task.page_number,
                text=result.text,
                confidence=result.confidence,
                boxes=result.boxes,
                status="processed",
            )

            return PageOCRResult(
                document_id=task.document_id,
                page_number=task.page_number,
                success=True,
                text=result.text,
                confidence=result.confidence,
                boxes=result.boxes,
                duration_ms=int((time.time() - start_time) * 1000),
            )

        except Exception as e:
            error_msg = str(e)

            # 오류 저장
            self.save_ocr_result(
                document_id=task.document_id,
                page_number=task.page_number,
                text="",
                confidence=0.0,
                status="error",
                error_message=error_msg,
            )

            return PageOCRResult(
                document_id=task.document_id,
                page_number=task.page_number,
                success=False,
                error_message=error_msg,
                duration_ms=int((time.time() - start_time) * 1000),
            )

    def process_batch(
        self,
        pages: Optional[list[dict]] = None,
        cluster_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """
        배치 OCR 처리

        Args:
            pages: 처리할 페이지 목록 (없으면 DB에서 조회)
            cluster_id: 특정 클러스터만
            limit: 최대 페이지 수

        Returns:
            처리 통계
        """
        if pages is None:
            pages = self.get_pages_to_process(cluster_id=cluster_id, limit=limit)

        if not pages:
            logger.info("처리할 페이지가 없습니다")
            return {"total": 0, "processed": 0, "failed": 0}

        total = len(pages)
        processed = 0
        failed = 0
        start_time = time.time()

        logger.info(f"OCR 배치 처리 시작: {total}개 페이지")

        # OCR 엔진 초기화
        self.engine.initialize()

        # 체크포인트 생성
        checkpoint = self.checkpoint_manager.create_checkpoint(
            stage="ocr",
            total_count=total,
            config=self.config.to_dict(),
        )
        batch_id = checkpoint.batch_id

        try:
            for i, page in enumerate(pages):
                task = PageOCRTask(
                    document_id=page["document_id"],
                    page_number=page["page_number"],
                    image_path=page["image_path"],
                    cluster_id=page.get("cluster_id"),
                )

                result = self.process_page(task)

                if result.success:
                    processed += 1
                else:
                    failed += 1
                    logger.warning(
                        f"OCR 실패: {result.document_id} p{result.page_number}: {result.error_message}"
                    )

                # 체크포인트 업데이트
                self.checkpoint_manager.update_progress(
                    "ocr",
                    batch_id,
                    result.document_id,
                    result.page_number,
                )

                # 진행 상황 로그
                if (i + 1) % 50 == 0:
                    elapsed = time.time() - start_time
                    speed = (i + 1) / elapsed
                    remaining = (total - i - 1) / speed if speed > 0 else 0
                    logger.info(
                        f"진행: {i + 1}/{total} ({speed:.2f} pages/sec, "
                        f"남은 시간: {remaining / 60:.1f}분)"
                    )

            # 완료
            self.checkpoint_manager.set_status("ocr", batch_id, CheckpointStatus.COMPLETED)

        except KeyboardInterrupt:
            logger.info("사용자 중단 - 체크포인트 저장")
            self.checkpoint_manager.set_status("ocr", batch_id, CheckpointStatus.PAUSED)
            raise

        except Exception as e:
            logger.error(f"배치 처리 실패: {e}")
            self.checkpoint_manager.set_status(
                "ocr", batch_id, CheckpointStatus.FAILED, str(e)
            )
            raise

        elapsed = time.time() - start_time
        stats = {
            "total": total,
            "processed": processed,
            "failed": failed,
            "elapsed_sec": elapsed,
            "pages_per_sec": (processed + failed) / elapsed if elapsed > 0 else 0,
        }

        logger.info(f"OCR 배치 완료: {processed} 성공, {failed} 실패, {elapsed:.1f}초")
        return stats

    def aggregate_document_results(self, document_id: str) -> Optional[dict]:
        """
        문서의 모든 페이지 OCR 결과 집계

        Args:
            document_id: 문서 ID

        Returns:
            집계된 결과
        """
        conn = self.get_connection()
        try:
            rows = conn.execute("""
                SELECT page_number, raw_text, confidence, status
                FROM ocr_pages
                WHERE document_id = ?
                ORDER BY page_number
            """, (document_id,)).fetchall()

            if not rows:
                return None

            # 전체 텍스트 합치기
            full_text_parts = []
            confidences = []
            processed = 0
            failed = 0

            for row in rows:
                if row["status"] == "processed" and row["raw_text"]:
                    full_text_parts.append(f"--- Page {row['page_number'] + 1} ---\n{row['raw_text']}")
                    if row["confidence"]:
                        confidences.append(row["confidence"])
                    processed += 1
                else:
                    failed += 1

            full_text = "\n\n".join(full_text_parts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            # ocr_results 테이블에 저장
            conn.execute("""
                INSERT OR REPLACE INTO ocr_results
                (document_id, full_text, total_pages, processed_pages, failed_pages,
                 average_confidence, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                document_id,
                full_text,
                len(rows),
                processed,
                failed,
                avg_confidence,
                "completed" if failed == 0 else "partial",
            ))
            conn.commit()

            return {
                "document_id": document_id,
                "full_text": full_text,
                "total_pages": len(rows),
                "processed_pages": processed,
                "failed_pages": failed,
                "average_confidence": avg_confidence,
            }
        finally:
            conn.close()

    def aggregate_all_documents(self) -> dict:
        """모든 문서의 OCR 결과 집계"""
        conn = self.get_connection()
        try:
            # OCR 완료된 문서 목록
            rows = conn.execute("""
                SELECT DISTINCT document_id
                FROM ocr_pages
                WHERE status = 'processed'
            """).fetchall()

            document_ids = [row["document_id"] for row in rows]
        finally:
            conn.close()

        logger.info(f"{len(document_ids)}개 문서 결과 집계 시작")

        aggregated = 0
        for doc_id in document_ids:
            result = self.aggregate_document_results(doc_id)
            if result:
                aggregated += 1

        logger.info(f"{aggregated}개 문서 결과 집계 완료")
        return {"aggregated": aggregated, "total": len(document_ids)}

    def get_stats(self) -> dict:
        """OCR 통계"""
        conn = self.get_connection()
        try:
            # 페이지별 통계
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) as processed,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failed,
                    AVG(CASE WHEN status = 'processed' THEN confidence ELSE NULL END) as avg_confidence
                FROM ocr_pages
            """).fetchone()

            page_stats = {
                "total": row["total"],
                "processed": row["processed"],
                "failed": row["failed"],
                "avg_confidence": row["avg_confidence"],
            }

            # 문서별 통계
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) as partial,
                    AVG(average_confidence) as avg_confidence
                FROM ocr_results
            """).fetchone()

            doc_stats = {
                "total": row["total"],
                "completed": row["completed"],
                "partial": row["partial"],
                "avg_confidence": row["avg_confidence"],
            }

            # 대기 중인 페이지
            row = conn.execute("""
                SELECT COUNT(*) FROM page_images WHERE status IN ('generated', 'cleaned')
            """).fetchone()
            pending = row[0]

            return {
                "pages": page_stats,
                "documents": doc_stats,
                "pending_pages": pending,
            }
        finally:
            conn.close()


# CLI
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Batch OCR Processor")
    parser.add_argument("command", choices=["process", "aggregate", "stats"], help="Command")
    parser.add_argument("--limit", "-l", type=int, help="Max pages to process")
    parser.add_argument("--cluster", "-c", type=int, help="Cluster ID filter")
    parser.add_argument("--no-gpu", action="store_true", help="Disable GPU")
    parser.add_argument("--lang", default="en", help="OCR language")

    args = parser.parse_args()

    config = PipelineV3Config(
        ocr_lang=args.lang,
        use_gpu=not args.no_gpu,
    )
    processor = BatchOCRProcessor(config=config)

    if args.command == "process":
        stats = processor.process_batch(
            cluster_id=args.cluster,
            limit=args.limit,
        )
        print(f"\n=== OCR 처리 결과 ===")
        print(f"총 페이지: {stats['total']}")
        print(f"성공: {stats['processed']}")
        print(f"실패: {stats['failed']}")
        print(f"처리 시간: {stats['elapsed_sec']:.1f}초")
        print(f"속도: {stats['pages_per_sec']:.2f} pages/sec")

    elif args.command == "aggregate":
        result = processor.aggregate_all_documents()
        print(f"\n=== 문서 집계 결과 ===")
        print(f"집계 완료: {result['aggregated']}/{result['total']}")

    elif args.command == "stats":
        stats = processor.get_stats()
        print("\n=== OCR Statistics ===")
        print(f"\nPages:")
        print(f"  Total: {stats['pages']['total']}")
        print(f"  Processed: {stats['pages']['processed']}")
        print(f"  Failed: {stats['pages']['failed']}")
        if stats['pages']['avg_confidence']:
            print(f"  Avg confidence: {stats['pages']['avg_confidence']:.3f}")
        print(f"\nDocuments:")
        print(f"  Total: {stats['documents']['total']}")
        print(f"  Completed: {stats['documents']['completed']}")
        print(f"  Partial: {stats['documents']['partial']}")
        print(f"\nPending pages: {stats['pending_pages']}")
