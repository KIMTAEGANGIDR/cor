"""
ILIS OCR Pipeline - Parallel OCR Processor

멀티프로세싱 기반 고속 OCR 처리
- 8 워커 병렬 처리 (RTX 4090 최적화)
- 2.6+ pages/sec 달성
"""

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Optional

os.environ['DISABLE_MODEL_SOURCE_CHECK'] = 'True'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"

# 워커별 OCR 엔진 (전역)
_ocr_engine = None


def init_worker():
    """워커 프로세스 초기화 - PaddleOCR 엔진 생성"""
    global _ocr_engine

    import paddle
    paddle.set_device('gpu:0')

    from paddleocr import PaddleOCR
    _ocr_engine = PaddleOCR(lang='en')


@dataclass
class PageTask:
    """페이지 OCR 작업"""
    document_id: str
    page_number: int
    image_path: str


@dataclass
class PageResult:
    """페이지 OCR 결과"""
    document_id: str
    page_number: int
    success: bool
    text: str = ""
    confidence: float = 0.0
    boxes: Optional[list] = None
    error: Optional[str] = None


def process_single_page(task_dict: dict) -> dict:
    """단일 페이지 OCR 처리 (워커 프로세스에서 실행)"""
    global _ocr_engine

    document_id = task_dict['document_id']
    page_number = task_dict['page_number']
    image_path = task_dict['image_path']

    try:
        if not Path(image_path).exists():
            return {
                'document_id': document_id,
                'page_number': page_number,
                'success': False,
                'error': f'File not found: {image_path}'
            }

        result = _ocr_engine.predict(image_path)

        if not result:
            return {
                'document_id': document_id,
                'page_number': page_number,
                'success': True,
                'text': '',
                'confidence': 0.0,
                'boxes': []
            }

        res = result[0]
        texts = res.get('rec_texts', [])
        scores = res.get('rec_scores', [])
        polys = res.get('rec_polys', [])

        # 박스 정보 구성
        boxes = []
        for i, (text, score) in enumerate(zip(texts, scores)):
            box_info = {"text": text, "confidence": float(score)}
            if i < len(polys):
                box_info["box"] = polys[i].tolist() if hasattr(polys[i], 'tolist') else polys[i]
            boxes.append(box_info)

        full_text = "\n".join(texts)
        avg_conf = sum(scores) / len(scores) if scores else 0.0

        return {
            'document_id': document_id,
            'page_number': page_number,
            'success': True,
            'text': full_text,
            'confidence': float(avg_conf),
            'boxes': boxes
        }

    except Exception as e:
        return {
            'document_id': document_id,
            'page_number': page_number,
            'success': False,
            'error': str(e)
        }


class ParallelOCRProcessor:
    """병렬 OCR 프로세서"""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        num_workers: int = 8,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.num_workers = num_workers

    def get_connection(self):
        """데이터베이스 연결"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get_pages_to_process(self, limit: Optional[int] = None) -> list[dict]:
        """OCR 처리할 페이지 목록 조회"""
        conn = self.get_connection()
        try:
            query = """
                SELECT p.document_id, p.page_number,
                       COALESCE(p.cleaned_image_path, p.image_path) as image_path
                FROM page_images p
                WHERE p.status IN ('generated', 'cleaned')
                  AND (p.cleaned_image_path IS NOT NULL OR p.image_path IS NOT NULL)
                ORDER BY p.document_id, p.page_number
            """
            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def save_results_batch(self, results: list[dict]) -> tuple[int, int]:
        """결과 일괄 저장"""
        conn = self.get_connection()
        processed = 0
        failed = 0

        try:
            for result in results:
                if result['success']:
                    conn.execute("""
                        INSERT OR REPLACE INTO ocr_pages
                        (document_id, page_number, raw_text, boxes, confidence, status)
                        VALUES (?, ?, ?, ?, ?, 'processed')
                    """, (
                        result['document_id'],
                        result['page_number'],
                        result.get('text', ''),
                        json.dumps(result.get('boxes', [])),
                        result.get('confidence', 0.0),
                    ))

                    conn.execute("""
                        UPDATE page_images SET status = 'ocr_done', updated_at = datetime('now')
                        WHERE document_id = ? AND page_number = ?
                    """, (result['document_id'], result['page_number']))

                    processed += 1
                else:
                    conn.execute("""
                        INSERT OR REPLACE INTO ocr_pages
                        (document_id, page_number, raw_text, confidence, status, error_message)
                        VALUES (?, ?, '', 0.0, 'error', ?)
                    """, (
                        result['document_id'],
                        result['page_number'],
                        result.get('error', 'Unknown error'),
                    ))
                    failed += 1

            conn.commit()
        finally:
            conn.close()

        return processed, failed

    def process(
        self,
        limit: Optional[int] = None,
        save_interval: int = 100,
    ) -> dict:
        """
        병렬 OCR 처리

        Args:
            limit: 최대 페이지 수
            save_interval: 저장 간격

        Returns:
            처리 통계
        """
        pages = self.get_pages_to_process(limit=limit)

        if not pages:
            logger.info("처리할 페이지가 없습니다")
            return {"total": 0, "processed": 0, "failed": 0}

        total = len(pages)
        total_processed = 0
        total_failed = 0
        start_time = time.time()

        logger.info(f"병렬 OCR 시작: {total}개 페이지, {self.num_workers}개 워커")

        # 멀티프로세싱 풀 생성
        with Pool(self.num_workers, initializer=init_worker) as pool:
            # imap_unordered로 결과를 스트리밍 처리
            results_buffer = []

            for i, result in enumerate(pool.imap_unordered(process_single_page, pages, chunksize=10)):
                results_buffer.append(result)

                # 일정 간격으로 저장
                if len(results_buffer) >= save_interval:
                    processed, failed = self.save_results_batch(results_buffer)
                    total_processed += processed
                    total_failed += failed
                    results_buffer = []

                    elapsed = time.time() - start_time
                    done = i + 1
                    speed = done / elapsed
                    remaining = (total - done) / speed if speed > 0 else 0

                    logger.info(
                        f"진행: {done}/{total} ({speed:.2f} pages/sec, "
                        f"성공: {total_processed}, 실패: {total_failed}, "
                        f"남은 시간: {remaining / 60:.1f}분)"
                    )

            # 남은 결과 저장
            if results_buffer:
                processed, failed = self.save_results_batch(results_buffer)
                total_processed += processed
                total_failed += failed

        elapsed = time.time() - start_time
        stats = {
            "total": total,
            "processed": total_processed,
            "failed": total_failed,
            "elapsed_sec": elapsed,
            "pages_per_sec": total / elapsed if elapsed > 0 else 0,
        }

        logger.info(
            f"OCR 완료: {total_processed} 성공, {total_failed} 실패, "
            f"{elapsed:.1f}초 ({stats['pages_per_sec']:.2f} pages/sec)"
        )

        return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Parallel OCR Processor")
    parser.add_argument("--limit", "-l", type=int, help="Max pages to process")
    parser.add_argument("--workers", "-w", type=int, default=8, help="Number of workers")
    parser.add_argument("--save-interval", "-s", type=int, default=100, help="Save interval")

    args = parser.parse_args()

    processor = ParallelOCRProcessor(num_workers=args.workers)
    stats = processor.process(limit=args.limit, save_interval=args.save_interval)

    print(f"\n=== 처리 결과 ===")
    print(f"총 페이지: {stats['total']}")
    print(f"성공: {stats['processed']}")
    print(f"실패: {stats['failed']}")
    print(f"처리 시간: {stats['elapsed_sec']:.1f}초")
    print(f"속도: {stats['pages_per_sec']:.2f} pages/sec")


if __name__ == "__main__":
    main()
