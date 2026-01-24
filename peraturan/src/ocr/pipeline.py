"""
ILIS OCR Pipeline

멀티프로세스 OCR 파이프라인
- 4워커 GPU 병렬 처리
- 내장 텍스트 우선 (OCR 스킵으로 속도 향상)
- VM 분산 처리 지원

Usage:
    python -m peraturan.src.ocr.pipeline run --workers 4
    python -m peraturan.src.ocr.pipeline run --workers 4 --shard 0 --total-shards 4
"""

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Optional, List

os.environ['DISABLE_MODEL_SOURCE_CHECK'] = 'True'
os.environ['FLAGS_allocator_strategy'] = 'auto_growth'

import fitz

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

DPI = 150
MIN_TEXT_LENGTH = 50
MIN_TEXT_QUALITY = 0.7

# 워커별 전역 OCR 엔진
_ocr_engine = None


def init_worker():
    """워커 프로세스 초기화"""
    global _ocr_engine

    import paddle
    paddle.set_device('gpu:0')

    from paddleocr import PaddleOCR
    _ocr_engine = PaddleOCR(lang='en')

    logger.info(f"워커 초기화 완료 (PID: {os.getpid()})")


def calculate_text_quality(text: str) -> float:
    """간단한 텍스트 품질 점수 (0.0 ~ 1.0)"""
    if not text or len(text) < 10:
        return 0.0

    # 알파벳/숫자 비율
    alnum_count = sum(1 for c in text if c.isalnum())
    alnum_ratio = alnum_count / len(text) if text else 0

    # 공백 비율 (너무 많으면 품질 낮음)
    space_ratio = text.count(' ') / len(text) if text else 0
    space_penalty = max(0, space_ratio - 0.3) * 2

    # 특수문자 비율 (너무 많으면 품질 낮음)
    special_count = sum(1 for c in text if not c.isalnum() and not c.isspace())
    special_ratio = special_count / len(text) if text else 0
    special_penalty = max(0, special_ratio - 0.1) * 3

    quality = alnum_ratio - space_penalty - special_penalty
    return max(0.0, min(1.0, quality))


@dataclass
class PageTask:
    """페이지 처리 작업"""
    pdf_path: str
    page_num: int
    doc_id: str


@dataclass
class PageResult:
    """페이지 처리 결과"""
    doc_id: str
    page_num: int
    success: bool
    text: str = ""
    source: str = ""  # "embedded" | "ocr" | "skip"
    quality: float = 0.0
    confidence: float = 0.0
    error: str = ""
    processing_time: float = 0.0


def process_page(task_dict: dict) -> dict:
    """단일 페이지 처리 (워커에서 실행)"""
    global _ocr_engine

    start_time = time.time()
    pdf_path = task_dict['pdf_path']
    page_num = task_dict['page_num']
    doc_id = task_dict['doc_id']

    try:
        # PDF 열기
        doc = fitz.open(pdf_path)
        page = doc[page_num]

        # 1. 내장 텍스트 추출
        embedded_text = page.get_text("text").strip()
        embedded_quality = calculate_text_quality(embedded_text)

        # 2. 내장 텍스트 품질 충분하면 OCR 스킵
        if len(embedded_text) >= MIN_TEXT_LENGTH and embedded_quality >= MIN_TEXT_QUALITY:
            doc.close()
            return {
                'doc_id': doc_id,
                'page_num': page_num,
                'success': True,
                'text': embedded_text,
                'source': 'embedded',
                'quality': embedded_quality,
                'confidence': 1.0,
                'processing_time': time.time() - start_time,
            }

        # 3. 빈 페이지 체크
        if len(embedded_text) < 10:
            # 이미지로 변환해서 OCR
            pix = page.get_pixmap(dpi=DPI)
            img_path = f"/tmp/ocr_{os.getpid()}_{page_num}.png"
            pix.save(img_path)

            result = _ocr_engine.predict(img_path)
            os.remove(img_path)

            if not result or not result[0].get('rec_texts'):
                doc.close()
                return {
                    'doc_id': doc_id,
                    'page_num': page_num,
                    'success': True,
                    'text': '',
                    'source': 'skip',
                    'quality': 0.0,
                    'confidence': 0.0,
                    'processing_time': time.time() - start_time,
                }

            # OCR 결과 사용
            res = result[0]
            ocr_text = "\n".join(res.get('rec_texts', []))
            ocr_scores = res.get('rec_scores', [])
            avg_conf = sum(ocr_scores) / len(ocr_scores) if ocr_scores else 0.0

            doc.close()
            return {
                'doc_id': doc_id,
                'page_num': page_num,
                'success': True,
                'text': ocr_text,
                'source': 'ocr',
                'quality': calculate_text_quality(ocr_text),
                'confidence': avg_conf,
                'processing_time': time.time() - start_time,
            }

        # 4. 내장 텍스트 품질 낮음 → OCR 시도
        pix = page.get_pixmap(dpi=DPI)
        img_path = f"/tmp/ocr_{os.getpid()}_{page_num}.png"
        pix.save(img_path)

        result = _ocr_engine.predict(img_path)
        os.remove(img_path)

        if not result or not result[0].get('rec_texts'):
            # OCR 실패 → 내장 텍스트 사용
            doc.close()
            return {
                'doc_id': doc_id,
                'page_num': page_num,
                'success': True,
                'text': embedded_text,
                'source': 'embedded',
                'quality': embedded_quality,
                'confidence': 1.0,
                'processing_time': time.time() - start_time,
            }

        res = result[0]
        ocr_text = "\n".join(res.get('rec_texts', []))
        ocr_scores = res.get('rec_scores', [])
        avg_conf = sum(ocr_scores) / len(ocr_scores) if ocr_scores else 0.0
        ocr_quality = calculate_text_quality(ocr_text)

        # 5. 더 나은 결과 선택
        if ocr_quality > embedded_quality:
            final_text = ocr_text
            source = 'ocr'
            quality = ocr_quality
            confidence = avg_conf
        else:
            final_text = embedded_text
            source = 'embedded'
            quality = embedded_quality
            confidence = 1.0

        doc.close()
        return {
            'doc_id': doc_id,
            'page_num': page_num,
            'success': True,
            'text': final_text,
            'source': source,
            'quality': quality,
            'confidence': confidence,
            'processing_time': time.time() - start_time,
        }

    except Exception as e:
        return {
            'doc_id': doc_id,
            'page_num': page_num,
            'success': False,
            'error': str(e),
            'processing_time': time.time() - start_time,
        }


class OCRPipeline:
    """고속 OCR 파이프라인"""

    def __init__(
        self,
        pdf_dir: str | Path,
        output_dir: str | Path,
        num_workers: int = 4,
        shard: int = 0,
        total_shards: int = 1,
    ):
        self.pdf_dir = Path(pdf_dir)
        self.output_dir = Path(output_dir)
        self.num_workers = num_workers
        self.shard = shard
        self.total_shards = total_shards

        # 디렉토리 생성
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "text").mkdir(exist_ok=True)
        (self.output_dir / "checkpoints").mkdir(exist_ok=True)

        # DB 초기화
        self.db_path = self.output_dir / "checkpoints" / f"pipeline_shard{shard}.db"
        self._init_db()

        logger.info(f"OCRPipeline 초기화")
        logger.info(f"  PDF 디렉토리: {self.pdf_dir}")
        logger.info(f"  출력 디렉토리: {self.output_dir}")
        logger.info(f"  워커 수: {self.num_workers}")
        logger.info(f"  샤드: {self.shard}/{self.total_shards}")

    def _init_db(self):
        """SQLite DB 초기화"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                pdf_path TEXT,
                total_pages INTEGER,
                completed_pages INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                started_at TEXT,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                doc_id TEXT,
                page_num INTEGER,
                text TEXT,
                source TEXT,
                quality REAL,
                confidence REAL,
                status TEXT DEFAULT 'pending',
                error TEXT,
                processing_time REAL,
                processed_at TEXT,
                PRIMARY KEY (doc_id, page_num)
            )
        """)
        conn.commit()
        conn.close()

    def scan_pdfs(self) -> List[Path]:
        """PDF 파일 스캔 (other 폴더 제외)"""
        pdfs = []
        for pdf_path in sorted(self.pdf_dir.rglob("*.pdf")):
            # other 폴더 제외
            if '/other/' in str(pdf_path) or '\\other\\' in str(pdf_path):
                continue
            pdfs.append(pdf_path)

        # 샤딩
        if self.total_shards > 1:
            pdfs = [p for i, p in enumerate(pdfs) if i % self.total_shards == self.shard]

        return pdfs

    def generate_doc_id(self, pdf_path: Path) -> str:
        """문서 ID 생성"""
        try:
            rel_path = pdf_path.relative_to(self.pdf_dir)
            return str(rel_path.with_suffix('')).replace('/', '__').replace('\\', '__')
        except ValueError:
            return pdf_path.stem

    def get_pending_documents(self) -> List[dict]:
        """미처리 문서 목록"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT doc_id, pdf_path, total_pages
            FROM documents
            WHERE status != 'completed'
        """).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def register_documents(self, pdfs: List[Path]):
        """문서 등록"""
        conn = sqlite3.connect(self.db_path)

        for pdf_path in pdfs:
            doc_id = self.generate_doc_id(pdf_path)

            # 이미 등록된 문서 스킵
            row = conn.execute(
                "SELECT doc_id FROM documents WHERE doc_id = ?",
                (doc_id,)
            ).fetchone()
            if row:
                continue

            # 페이지 수 확인
            try:
                doc = fitz.open(pdf_path)
                total_pages = len(doc)
                doc.close()
            except:
                total_pages = 0

            conn.execute("""
                INSERT INTO documents (doc_id, pdf_path, total_pages, status, started_at)
                VALUES (?, ?, ?, 'pending', ?)
            """, (doc_id, str(pdf_path), total_pages, datetime.now().isoformat()))

        conn.commit()
        conn.close()

    def get_tasks(self, limit: Optional[int] = None) -> List[dict]:
        """처리할 작업 목록 생성"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # 미완료 문서의 미처리 페이지
        query = """
            SELECT d.doc_id, d.pdf_path, d.total_pages
            FROM documents d
            WHERE d.status != 'completed'
            ORDER BY d.doc_id
        """
        docs = conn.execute(query).fetchall()

        tasks = []
        for doc in docs:
            doc_id = doc['doc_id']
            pdf_path = doc['pdf_path']
            total_pages = doc['total_pages']

            # 완료된 페이지 조회
            completed = conn.execute("""
                SELECT page_num FROM pages
                WHERE doc_id = ? AND status = 'completed'
            """, (doc_id,)).fetchall()
            completed_pages = {row['page_num'] for row in completed}

            # 미처리 페이지 추가
            for page_num in range(total_pages):
                if page_num not in completed_pages:
                    tasks.append({
                        'doc_id': doc_id,
                        'pdf_path': pdf_path,
                        'page_num': page_num,
                    })

            if limit and len(tasks) >= limit:
                break

        conn.close()

        if limit:
            tasks = tasks[:limit]

        return tasks

    def save_results(self, results: List[dict]):
        """결과 저장"""
        conn = sqlite3.connect(self.db_path)

        for result in results:
            doc_id = result['doc_id']
            page_num = result['page_num']

            if result.get('success'):
                conn.execute("""
                    INSERT OR REPLACE INTO pages
                    (doc_id, page_num, text, source, quality, confidence, status, processing_time, processed_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?)
                """, (
                    doc_id, page_num,
                    result.get('text', ''),
                    result.get('source', ''),
                    result.get('quality', 0.0),
                    result.get('confidence', 0.0),
                    result.get('processing_time', 0.0),
                    datetime.now().isoformat(),
                ))
            else:
                conn.execute("""
                    INSERT OR REPLACE INTO pages
                    (doc_id, page_num, status, error, processing_time, processed_at)
                    VALUES (?, ?, 'failed', ?, ?, ?)
                """, (
                    doc_id, page_num,
                    result.get('error', ''),
                    result.get('processing_time', 0.0),
                    datetime.now().isoformat(),
                ))

        conn.commit()
        conn.close()

    def update_document_status(self):
        """문서 상태 업데이트"""
        conn = sqlite3.connect(self.db_path)

        # 모든 페이지 완료된 문서 찾기
        conn.execute("""
            UPDATE documents SET
                status = 'completed',
                completed_pages = (
                    SELECT COUNT(*) FROM pages
                    WHERE pages.doc_id = documents.doc_id AND pages.status = 'completed'
                ),
                completed_at = ?
            WHERE doc_id IN (
                SELECT d.doc_id FROM documents d
                WHERE d.status != 'completed'
                AND d.total_pages = (
                    SELECT COUNT(*) FROM pages p
                    WHERE p.doc_id = d.doc_id AND p.status = 'completed'
                )
            )
        """, (datetime.now().isoformat(),))

        # 진행 중 문서 업데이트
        conn.execute("""
            UPDATE documents SET
                status = 'processing',
                completed_pages = (
                    SELECT COUNT(*) FROM pages
                    WHERE pages.doc_id = documents.doc_id AND pages.status = 'completed'
                )
            WHERE status = 'pending'
            AND doc_id IN (
                SELECT DISTINCT doc_id FROM pages WHERE status = 'completed'
            )
        """)

        conn.commit()
        conn.close()

    def export_text(self):
        """완료된 문서 텍스트 내보내기"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # 완료된 문서
        docs = conn.execute("""
            SELECT doc_id FROM documents WHERE status = 'completed'
        """).fetchall()

        for doc in docs:
            doc_id = doc['doc_id']
            output_path = self.output_dir / "text" / f"{doc_id}.txt"

            if output_path.exists():
                continue

            # 페이지 텍스트 조회
            pages = conn.execute("""
                SELECT text FROM pages
                WHERE doc_id = ? AND status = 'completed'
                ORDER BY page_num
            """, (doc_id,)).fetchall()

            texts = [p['text'] for p in pages if p['text']]

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("\n\n--- PAGE BREAK ---\n\n".join(texts))

        conn.close()

    def get_stats(self) -> dict:
        """통계 조회"""
        conn = sqlite3.connect(self.db_path)

        doc_stats = dict(conn.execute("""
            SELECT status, COUNT(*) FROM documents GROUP BY status
        """).fetchall())

        page_stats = dict(conn.execute("""
            SELECT status, COUNT(*) FROM pages GROUP BY status
        """).fetchall())

        source_stats = dict(conn.execute("""
            SELECT source, COUNT(*) FROM pages WHERE status = 'completed' GROUP BY source
        """).fetchall())

        total_pages = conn.execute("SELECT SUM(total_pages) FROM documents").fetchone()[0] or 0
        completed_pages = page_stats.get('completed', 0)

        conn.close()

        return {
            'documents': doc_stats,
            'pages': page_stats,
            'sources': source_stats,
            'total_pages': total_pages,
            'completed_pages': completed_pages,
            'progress': completed_pages / total_pages if total_pages > 0 else 0,
        }

    def run(self, limit: Optional[int] = None, save_interval: int = 50):
        """파이프라인 실행"""
        logger.info("=== OCRPipeline 시작 ===")

        # 1. PDF 스캔 및 등록
        pdfs = self.scan_pdfs()
        logger.info(f"PDF 스캔 완료: {len(pdfs)}개")

        self.register_documents(pdfs)

        # 2. 작업 목록 생성
        tasks = self.get_tasks(limit=limit)
        if not tasks:
            logger.info("처리할 페이지가 없습니다")
            return self.get_stats()

        total_tasks = len(tasks)
        logger.info(f"처리할 페이지: {total_tasks}개")

        # 3. 멀티프로세스 처리
        start_time = time.time()
        processed = 0
        failed = 0

        with Pool(self.num_workers, initializer=init_worker) as pool:
            results_buffer = []

            for i, result in enumerate(pool.imap_unordered(process_page, tasks, chunksize=5)):
                results_buffer.append(result)

                if result.get('success'):
                    processed += 1
                else:
                    failed += 1

                # 주기적 저장
                if len(results_buffer) >= save_interval:
                    self.save_results(results_buffer)
                    results_buffer = []

                    elapsed = time.time() - start_time
                    done = i + 1
                    speed = done / elapsed
                    remaining = (total_tasks - done) / speed if speed > 0 else 0

                    logger.info(
                        f"진행: {done}/{total_tasks} ({done/total_tasks*100:.1f}%) | "
                        f"{speed:.2f} pages/sec | "
                        f"성공: {processed} | 실패: {failed} | "
                        f"남은 시간: {remaining/3600:.1f}시간"
                    )

            # 남은 결과 저장
            if results_buffer:
                self.save_results(results_buffer)

        # 4. 문서 상태 업데이트 및 텍스트 내보내기
        self.update_document_status()
        self.export_text()

        # 5. 최종 통계
        elapsed = time.time() - start_time
        stats = self.get_stats()
        stats['elapsed_sec'] = elapsed
        stats['pages_per_sec'] = total_tasks / elapsed if elapsed > 0 else 0

        logger.info(f"\n=== 처리 완료 ===")
        logger.info(f"총 페이지: {total_tasks}")
        logger.info(f"성공: {processed}, 실패: {failed}")
        logger.info(f"처리 시간: {elapsed/3600:.2f}시간")
        logger.info(f"속도: {stats['pages_per_sec']:.2f} pages/sec")
        logger.info(f"소스 분포: {stats['sources']}")

        return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fast OCR Pipeline")
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # run 명령
    run_parser = subparsers.add_parser('run', help='Run pipeline')
    run_parser.add_argument('--pdf-dir', '-p', default=os.path.expanduser('~/cor/peraturan/data/pdfs'),
                           help='PDF directory')
    run_parser.add_argument('--output-dir', '-o', default=os.path.expanduser('~/cor/ocr_output'),
                           help='Output directory')
    run_parser.add_argument('--workers', '-w', type=int, default=4, help='Number of workers')
    run_parser.add_argument('--limit', '-l', type=int, help='Max pages to process')
    run_parser.add_argument('--shard', type=int, default=0, help='Shard index (for distributed processing)')
    run_parser.add_argument('--total-shards', type=int, default=1, help='Total shards')

    # stats 명령
    stats_parser = subparsers.add_parser('stats', help='Show statistics')
    stats_parser.add_argument('--output-dir', '-o', default=os.path.expanduser('~/cor/ocr_output'),
                             help='Output directory')
    stats_parser.add_argument('--shard', type=int, default=0, help='Shard index')

    args = parser.parse_args()

    if args.command == 'run':
        pipeline = OCRPipeline(
            pdf_dir=args.pdf_dir,
            output_dir=args.output_dir,
            num_workers=args.workers,
            shard=args.shard,
            total_shards=args.total_shards,
        )
        pipeline.run(limit=args.limit)

    elif args.command == 'stats':
        pipeline = OCRPipeline(
            pdf_dir='.',  # dummy
            output_dir=args.output_dir,
            shard=args.shard,
        )
        stats = pipeline.get_stats()
        print(json.dumps(stats, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
