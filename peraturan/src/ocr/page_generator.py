"""
ILIS OCR Pipeline - Page Image Generator

PDF 문서를 페이지별 이미지로 변환
- multiprocessing을 사용한 병렬 처리
- 체크포인트 기반 재시작 지원
- 진행 상황 추적
"""

import json
import logging
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from .checkpoint_manager import CheckpointManager, iterate_with_checkpoint
from .models import PageImage, PageImageStatus, PipelineV3Config

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "page_images"


@dataclass
class PageGenTask:
    """페이지 생성 작업"""
    document_id: str
    pdf_path: str
    page_number: int
    output_path: str
    dpi: int = 150
    image_format: str = "jpeg"
    image_quality: int = 85


@dataclass
class PageGenResult:
    """페이지 생성 결과"""
    document_id: str
    page_number: int
    success: bool
    image_path: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: int = 0


def generate_page_image(task: PageGenTask) -> PageGenResult:
    """
    단일 페이지를 이미지로 변환 (워커 함수)

    Args:
        task: 페이지 생성 작업

    Returns:
        생성 결과
    """
    start_time = time.time()
    try:
        output_path = Path(task.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # PDF 열기
        doc = fitz.open(task.pdf_path)
        if task.page_number >= len(doc):
            return PageGenResult(
                document_id=task.document_id,
                page_number=task.page_number,
                success=False,
                error_message=f"Page {task.page_number} out of range (total: {len(doc)})",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        page = doc[task.page_number]

        # 페이지를 이미지로 렌더링
        mat = fitz.Matrix(task.dpi / 72, task.dpi / 72)
        pix = page.get_pixmap(matrix=mat)

        # 저장
        if task.image_format.lower() == "jpeg":
            pix.save(str(output_path), "jpeg", jpg_quality=task.image_quality)
        elif task.image_format.lower() == "png":
            pix.save(str(output_path), "png")
        else:
            pix.save(str(output_path))

        doc.close()

        return PageGenResult(
            document_id=task.document_id,
            page_number=task.page_number,
            success=True,
            image_path=str(output_path),
            duration_ms=int((time.time() - start_time) * 1000),
        )

    except Exception as e:
        return PageGenResult(
            document_id=task.document_id,
            page_number=task.page_number,
            success=False,
            error_message=str(e),
            duration_ms=int((time.time() - start_time) * 1000),
        )


class PageGenerator:
    """페이지 이미지 생성기"""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        config: Optional[PipelineV3Config] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.output_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
        self.config = config or PipelineV3Config()
        self.checkpoint_manager = CheckpointManager(self.db_path)

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def get_connection(self):
        """데이터베이스 연결"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get_documents_to_process(
        self,
        limit: Optional[int] = None,
        cluster_id: Optional[int] = None,
        jenis: Optional[str] = None,
    ) -> list[dict]:
        """
        처리할 문서 목록 조회

        Args:
            limit: 최대 문서 수
            cluster_id: 특정 클러스터만
            jenis: 특정 법령 유형만
        """
        conn = self.get_connection()
        try:
            query = """
                SELECT d.id, d.pdf_path, d.page_count, h.cluster_id
                FROM documents d
                LEFT JOIN headers h ON d.id = h.document_id
                WHERE d.pdf_path IS NOT NULL
                  AND d.pdf_path != ''
            """
            params = []

            if cluster_id:
                query += " AND h.cluster_id = ?"
                params.append(cluster_id)

            if jenis:
                query += " AND d.jenis = ?"
                params.append(jenis)

            query += " ORDER BY d.priority DESC, d.tahun DESC"

            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_pending_pages(self, document_id: str) -> list[int]:
        """처리되지 않은 페이지 번호 목록"""
        conn = self.get_connection()
        try:
            # 이미 처리된 페이지 조회
            rows = conn.execute("""
                SELECT page_number FROM page_images
                WHERE document_id = ? AND status IN ('generated', 'cleaned', 'ocr_done')
            """, (document_id,)).fetchall()

            processed = {row["page_number"] for row in rows}

            # 문서의 전체 페이지 수 조회
            row = conn.execute(
                "SELECT page_count FROM documents WHERE id = ?",
                (document_id,)
            ).fetchone()

            if not row or not row["page_count"]:
                return []

            total_pages = row["page_count"]
            return [i for i in range(total_pages) if i not in processed]
        finally:
            conn.close()

    def save_page_image(self, page_image: PageImage) -> None:
        """페이지 이미지 정보 저장"""
        conn = self.get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO page_images
                (document_id, page_number, image_path, cluster_id, status, error_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                page_image.document_id,
                page_image.page_number,
                page_image.image_path,
                page_image.cluster_id,
                page_image.status,
                page_image.error_message,
            ))
            conn.commit()
        finally:
            conn.close()

    def get_image_path(self, document_id: str, page_number: int) -> Path:
        """이미지 저장 경로 생성"""
        # 문서 ID의 처음 2글자로 서브디렉토리 생성 (파일 분산)
        subdir = document_id[:2] if len(document_id) >= 2 else "00"
        return self.output_dir / subdir / f"{document_id}_p{page_number:04d}.{self.config.image_format}"

    def generate_for_document(
        self,
        document_id: str,
        pdf_path: str,
        cluster_id: Optional[int] = None,
        page_numbers: Optional[list[int]] = None,
    ) -> list[PageGenResult]:
        """
        단일 문서의 페이지 이미지 생성

        Args:
            document_id: 문서 ID
            pdf_path: PDF 파일 경로
            cluster_id: 클러스터 ID
            page_numbers: 처리할 페이지 번호 목록 (없으면 전체)

        Returns:
            생성 결과 목록
        """
        # PDF 파일 확인
        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            logger.warning(f"PDF not found: {pdf_path}")
            return []

        # 페이지 수 확인
        try:
            doc = fitz.open(str(pdf_file))
            total_pages = len(doc)
            doc.close()
        except Exception as e:
            logger.error(f"Failed to open PDF {pdf_path}: {e}")
            return []

        # 처리할 페이지 결정
        if page_numbers is None:
            page_numbers = list(range(total_pages))

        # 작업 목록 생성
        tasks = []
        for page_num in page_numbers:
            output_path = self.get_image_path(document_id, page_num)
            tasks.append(PageGenTask(
                document_id=document_id,
                pdf_path=str(pdf_file),
                page_number=page_num,
                output_path=str(output_path),
                dpi=self.config.image_dpi,
                image_format=self.config.image_format,
                image_quality=self.config.image_quality,
            ))

        # 순차 처리 (단일 문서)
        results = []
        for task in tasks:
            result = generate_page_image(task)
            results.append(result)

            # DB 저장
            page_image = PageImage(
                document_id=result.document_id,
                page_number=result.page_number,
                image_path=result.image_path if result.success else None,
                cluster_id=cluster_id,
                status=PageImageStatus.GENERATED.value if result.success else PageImageStatus.ERROR.value,
                error_message=result.error_message,
            )
            self.save_page_image(page_image)

        return results

    def generate_batch(
        self,
        documents: Optional[list[dict]] = None,
        limit: Optional[int] = None,
        cluster_id: Optional[int] = None,
        workers: Optional[int] = None,
        resume: bool = True,
    ) -> dict:
        """
        배치 페이지 이미지 생성

        Args:
            documents: 처리할 문서 목록 (없으면 DB에서 조회)
            limit: 최대 문서 수
            cluster_id: 특정 클러스터만
            workers: 워커 수 (기본: config 값)
            resume: 체크포인트에서 재개

        Returns:
            처리 통계
        """
        if documents is None:
            documents = self.get_documents_to_process(limit=limit, cluster_id=cluster_id)

        if not documents:
            logger.info("처리할 문서가 없습니다")
            return {"total": 0, "processed": 0, "failed": 0}

        workers = workers or self.config.workers
        total_pages = 0
        processed = 0
        failed = 0
        start_time = time.time()

        logger.info(f"배치 처리 시작: {len(documents)}개 문서, {workers}개 워커")

        # 모든 작업 수집
        all_tasks = []
        for doc in documents:
            document_id = doc["id"]
            pdf_path = doc["pdf_path"]
            doc_cluster_id = doc.get("cluster_id")

            if not pdf_path:
                continue

            # 처리되지 않은 페이지만
            pending_pages = self.get_pending_pages(document_id)
            if not pending_pages:
                continue

            for page_num in pending_pages:
                output_path = self.get_image_path(document_id, page_num)
                all_tasks.append((
                    PageGenTask(
                        document_id=document_id,
                        pdf_path=pdf_path,
                        page_number=page_num,
                        output_path=str(output_path),
                        dpi=self.config.image_dpi,
                        image_format=self.config.image_format,
                        image_quality=self.config.image_quality,
                    ),
                    doc_cluster_id,
                ))

        total_pages = len(all_tasks)
        logger.info(f"총 {total_pages}개 페이지 처리 예정")

        if not all_tasks:
            return {"total": 0, "processed": 0, "failed": 0}

        # 체크포인트 생성
        checkpoint = self.checkpoint_manager.create_checkpoint(
            stage="page_gen",
            total_count=total_pages,
            config=self.config.to_dict(),
        )
        batch_id = checkpoint.batch_id

        # 병렬 처리
        try:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                # 작업 제출
                future_to_task = {}
                for task, cluster_id in all_tasks:
                    future = executor.submit(generate_page_image, task)
                    future_to_task[future] = (task, cluster_id)

                # 결과 수집
                for future in as_completed(future_to_task):
                    task, cluster_id = future_to_task[future]
                    try:
                        result = future.result()

                        # DB 저장
                        page_image = PageImage(
                            document_id=result.document_id,
                            page_number=result.page_number,
                            image_path=result.image_path if result.success else None,
                            cluster_id=cluster_id,
                            status=PageImageStatus.GENERATED.value if result.success else PageImageStatus.ERROR.value,
                            error_message=result.error_message,
                        )
                        self.save_page_image(page_image)

                        if result.success:
                            processed += 1
                        else:
                            failed += 1
                            logger.warning(f"페이지 생성 실패: {result.document_id} p{result.page_number}: {result.error_message}")

                        # 체크포인트 업데이트
                        self.checkpoint_manager.update_progress(
                            "page_gen",
                            batch_id,
                            result.document_id,
                            result.page_number,
                        )

                        # 진행 상황 로그
                        if (processed + failed) % 100 == 0:
                            elapsed = time.time() - start_time
                            speed = (processed + failed) / elapsed
                            logger.info(f"진행: {processed + failed}/{total_pages} ({speed:.1f} pages/sec)")

                    except Exception as e:
                        failed += 1
                        logger.error(f"작업 실패: {task.document_id} p{task.page_number}: {e}")

            # 완료
            self.checkpoint_manager.set_status(
                "page_gen", batch_id,
                status=__import__("peraturan.src.ocr.models", fromlist=["CheckpointStatus"]).CheckpointStatus.COMPLETED,
            )

        except KeyboardInterrupt:
            logger.info("사용자 중단 - 체크포인트 저장")
            self.checkpoint_manager.set_status(
                "page_gen", batch_id,
                status=__import__("peraturan.src.ocr.models", fromlist=["CheckpointStatus"]).CheckpointStatus.PAUSED,
            )
            raise

        except Exception as e:
            logger.error(f"배치 처리 실패: {e}")
            self.checkpoint_manager.set_status(
                "page_gen", batch_id,
                status=__import__("peraturan.src.ocr.models", fromlist=["CheckpointStatus"]).CheckpointStatus.FAILED,
                error_message=str(e),
            )
            raise

        elapsed = time.time() - start_time
        stats = {
            "total": total_pages,
            "processed": processed,
            "failed": failed,
            "elapsed_sec": elapsed,
            "pages_per_sec": (processed + failed) / elapsed if elapsed > 0 else 0,
        }

        logger.info(f"배치 처리 완료: {processed} 성공, {failed} 실패, {elapsed:.1f}초")
        return stats

    def get_stats(self) -> dict:
        """페이지 이미지 통계"""
        conn = self.get_connection()
        try:
            # 상태별 통계
            rows = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM page_images
                GROUP BY status
            """).fetchall()
            by_status = {row["status"]: row["count"] for row in rows}

            # 클러스터별 통계
            rows = conn.execute("""
                SELECT cluster_id, status, COUNT(*) as count
                FROM page_images
                WHERE cluster_id IS NOT NULL
                GROUP BY cluster_id, status
            """).fetchall()

            by_cluster = {}
            for row in rows:
                cid = row["cluster_id"]
                if cid not in by_cluster:
                    by_cluster[cid] = {}
                by_cluster[cid][row["status"]] = row["count"]

            # 전체 통계
            total = conn.execute("SELECT COUNT(*) FROM page_images").fetchone()[0]
            total_docs = conn.execute(
                "SELECT COUNT(DISTINCT document_id) FROM page_images"
            ).fetchone()[0]

            return {
                "total_pages": total,
                "total_documents": total_docs,
                "by_status": by_status,
                "by_cluster": by_cluster,
            }
        finally:
            conn.close()


# CLI
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Page Image Generator")
    parser.add_argument("command", choices=["generate", "stats"], help="Command")
    parser.add_argument("--limit", "-l", type=int, help="Max documents to process")
    parser.add_argument("--cluster", "-c", type=int, help="Cluster ID filter")
    parser.add_argument("--workers", "-w", type=int, default=8, help="Number of workers")
    parser.add_argument("--dpi", type=int, default=150, help="Image DPI")
    parser.add_argument("--document", "-d", type=str, help="Single document ID")

    args = parser.parse_args()

    config = PipelineV3Config(
        image_dpi=args.dpi,
        workers=args.workers,
    )
    generator = PageGenerator(config=config)

    if args.command == "generate":
        if args.document:
            # 단일 문서 처리
            conn = generator.get_connection()
            row = conn.execute(
                "SELECT id, pdf_path FROM documents WHERE id = ?",
                (args.document,)
            ).fetchone()
            conn.close()

            if row:
                results = generator.generate_for_document(row["id"], row["pdf_path"])
                print(f"처리 완료: {len([r for r in results if r.success])} 성공, {len([r for r in results if not r.success])} 실패")
            else:
                print(f"문서를 찾을 수 없음: {args.document}")
        else:
            # 배치 처리
            stats = generator.generate_batch(
                limit=args.limit,
                cluster_id=args.cluster,
                workers=args.workers,
            )
            print(f"\n=== 처리 결과 ===")
            print(f"총 페이지: {stats['total']}")
            print(f"성공: {stats['processed']}")
            print(f"실패: {stats['failed']}")
            print(f"처리 시간: {stats['elapsed_sec']:.1f}초")
            print(f"속도: {stats['pages_per_sec']:.1f} pages/sec")

    elif args.command == "stats":
        stats = generator.get_stats()
        print("\n=== Page Image Statistics ===")
        print(f"Total pages: {stats['total_pages']}")
        print(f"Total documents: {stats['total_documents']}")
        print("\nBy status:")
        for status, count in stats["by_status"].items():
            print(f"  {status}: {count}")
