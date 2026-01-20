"""
ILIS OCR Pipeline - Header Extractor (STAGE 0)

PDF 1페이지 상단 영역을 이미지로 추출하여 헤더 분석에 사용
"""

import fitz  # PyMuPDF
import logging
from pathlib import Path
from typing import Optional, Generator
from dataclasses import dataclass
from datetime import datetime
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from .database import OCRPipelineDB, DEFAULT_DB_PATH
from .models import Header, DocumentStage

logger = logging.getLogger(__name__)


# 기본 설정
DEFAULT_HEADER_RATIO = 0.35  # 상단 35%
DEFAULT_DPI = 200  # 200 DPI (품질/용량 균형)
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "headers"


@dataclass
class ExtractionResult:
    """추출 결과"""
    document_id: str
    success: bool
    image_path: Optional[str] = None
    page_count: Optional[int] = None
    file_size: Optional[int] = None
    error: Optional[str] = None
    duration_ms: int = 0


class HeaderExtractor:
    """PDF 헤더 이미지 추출기"""

    def __init__(
        self,
        db: Optional[OCRPipelineDB] = None,
        output_dir: Optional[Path] = None,
        header_ratio: float = DEFAULT_HEADER_RATIO,
        dpi: int = DEFAULT_DPI
    ):
        self.db = db or OCRPipelineDB()
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.header_ratio = header_ratio
        self.dpi = dpi

        # 출력 디렉토리 생성
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract_header_image(
        self,
        pdf_path: str,
        output_path: Optional[str] = None,
        header_ratio: Optional[float] = None
    ) -> tuple[bool, Optional[str], Optional[int], Optional[int]]:
        """
        PDF 1페이지 상단 영역을 이미지로 추출

        Args:
            pdf_path: PDF 파일 경로
            output_path: 출력 이미지 경로 (None이면 자동 생성)
            header_ratio: 추출할 상단 비율 (0.0 ~ 1.0)

        Returns:
            (성공여부, 이미지경로, 페이지수, 파일크기)
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            return False, None, None, None

        ratio = header_ratio or self.header_ratio

        doc = None
        try:
            doc = fitz.open(str(pdf_path))
            page_count = len(doc)

            if page_count == 0:
                return False, None, 0, None

            # 첫 페이지
            page = doc[0]
            rect = page.rect

            # 상단 영역만 클리핑
            clip = fitz.Rect(
                rect.x0,
                rect.y0,
                rect.x1,
                rect.y0 + (rect.height * ratio)
            )

            # DPI 설정으로 렌더링
            mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
            pix = page.get_pixmap(matrix=mat, clip=clip)

            # 출력 경로 결정
            if output_path is None:
                output_path = self.output_dir / f"{pdf_path.stem}_header.jpg"
            else:
                output_path = Path(output_path)

            output_path.parent.mkdir(parents=True, exist_ok=True)

            # JPEG로 저장 (용량 최적화)
            pix.pil_save(str(output_path), format="JPEG", quality=85)

            file_size = pdf_path.stat().st_size

            return True, str(output_path), page_count, file_size

        except Exception as e:
            # Low Fix: 예외 로깅 추가 (손상 PDF 진단 용이)
            logger.warning(f"헤더 추출 실패: {pdf_path} - {type(e).__name__}: {e}")
            return False, None, None, None
        finally:
            # Low Fix: 예외 발생해도 doc.close() 보장 (FD 누수 방지)
            if doc is not None:
                doc.close()

    def extract_single(self, document_id: str) -> ExtractionResult:
        """
        단일 문서의 헤더 추출

        Args:
            document_id: 문서 ID

        Returns:
            ExtractionResult
        """
        start_time = time.time()

        with self.db.connection() as conn:
            # 문서 조회
            row = conn.execute(
                "SELECT id, pdf_path, jenis FROM documents WHERE id = ?",
                (document_id,)
            ).fetchone()

            if not row:
                return ExtractionResult(
                    document_id=document_id,
                    success=False,
                    error="Document not found"
                )

            pdf_path = row["pdf_path"]
            jenis = row["jenis"]

            if not pdf_path:
                return ExtractionResult(
                    document_id=document_id,
                    success=False,
                    error="PDF path is empty"
                )

            # 법령 유형별 하위 디렉토리
            jenis_dir = self._get_jenis_dir(jenis)
            output_dir = self.output_dir / jenis_dir
            output_path = output_dir / f"{document_id}_header.jpg"

            # 헤더 추출
            success, image_path, page_count, file_size = self.extract_header_image(
                pdf_path, str(output_path)
            )

            duration_ms = int((time.time() - start_time) * 1000)

            if success:
                # documents 테이블 업데이트
                conn.execute("""
                    UPDATE documents SET
                        page_count = ?,
                        file_size = ?,
                        stage = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    page_count,
                    file_size,
                    DocumentStage.HEADER_EXTRACTED.value,
                    datetime.now().isoformat(),
                    document_id
                ))

                # headers 테이블에 삽입
                conn.execute("""
                    INSERT OR REPLACE INTO headers
                    (document_id, image_path, header_ratio, status, created_at)
                    VALUES (?, ?, ?, 'extracted', ?)
                """, (
                    document_id,
                    image_path,
                    self.header_ratio,
                    datetime.now().isoformat()
                ))

                # 로그 기록
                conn.execute("""
                    INSERT INTO processing_logs
                    (document_id, stage, action, status, message, duration_ms)
                    VALUES (?, 'stage_0', 'extract_header', 'success', ?, ?)
                """, (
                    document_id,
                    f"Extracted header: {page_count} pages",
                    duration_ms
                ))

                return ExtractionResult(
                    document_id=document_id,
                    success=True,
                    image_path=image_path,
                    page_count=page_count,
                    file_size=file_size,
                    duration_ms=duration_ms
                )
            else:
                # 에러 기록
                error_msg = f"Failed to extract header from {pdf_path}"
                conn.execute("""
                    INSERT INTO processing_logs
                    (document_id, stage, action, status, message, duration_ms)
                    VALUES (?, 'stage_0', 'extract_header', 'error', ?, ?)
                """, (document_id, error_msg, duration_ms))

                return ExtractionResult(
                    document_id=document_id,
                    success=False,
                    error=error_msg,
                    duration_ms=duration_ms
                )

    def extract_batch(
        self,
        jenis: Optional[str] = None,
        limit: Optional[int] = None,
        workers: int = 4
    ) -> Generator[ExtractionResult, None, None]:
        """
        배치로 헤더 추출

        Args:
            jenis: 특정 법령 유형만 처리
            limit: 최대 처리 수
            workers: 병렬 처리 워커 수

        Yields:
            ExtractionResult
        """
        # 처리할 문서 조회
        with self.db.connection() as conn:
            query = """
                SELECT id FROM documents
                WHERE stage = 'pending'
                AND pdf_path IS NOT NULL
            """
            params = []

            if jenis:
                query += " AND jenis = ?"
                params.append(jenis)

            query += " ORDER BY priority DESC, tahun DESC"

            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query, params).fetchall()
            document_ids = [row["id"] for row in rows]

        if not document_ids:
            return

        # 병렬 처리
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.extract_single, doc_id): doc_id
                for doc_id in document_ids
            }

            for future in as_completed(futures):
                result = future.result()
                yield result

    def get_pending_count(self, jenis: Optional[str] = None) -> int:
        """대기 중인 문서 수 조회"""
        with self.db.connection() as conn:
            query = """
                SELECT COUNT(*) FROM documents
                WHERE stage = 'pending'
                AND pdf_path IS NOT NULL
            """
            params = []

            if jenis:
                query += " AND jenis = ?"
                params.append(jenis)

            return conn.execute(query, params).fetchone()[0]

    def get_extracted_count(self, jenis: Optional[str] = None) -> int:
        """추출 완료된 문서 수 조회"""
        with self.db.connection() as conn:
            query = """
                SELECT COUNT(*) FROM documents
                WHERE stage = 'header_extracted'
            """
            params = []

            if jenis:
                query += " AND jenis = ?"
                params.append(jenis)

            return conn.execute(query, params).fetchone()[0]

    def _get_jenis_dir(self, jenis: str) -> str:
        """법령 유형에 따른 디렉토리명 반환"""
        jenis_upper = jenis.upper()
        if "UNDANG-UNDANG" in jenis_upper:
            return "uu"
        elif "PERATURAN PEMERINTAH PENGGANTI" in jenis_upper or "PERPPU" in jenis_upper:
            return "perppu"
        elif "PERATURAN PEMERINTAH" in jenis_upper:
            return "pp"
        elif "PERATURAN PRESIDEN" in jenis_upper or "PERPRES" in jenis_upper:
            return "perpres"
        elif "PERATURAN MENTERI" in jenis_upper or "PERMEN" in jenis_upper:
            return "permen"
        else:
            return "other"


def extract_headers(
    jenis: Optional[str] = None,
    limit: Optional[int] = None,
    workers: int = 4,
    verbose: bool = True
) -> dict:
    """
    헤더 추출 실행

    Args:
        jenis: 특정 법령 유형만 처리
        limit: 최대 처리 수
        workers: 병렬 처리 워커 수
        verbose: 진행 상황 출력

    Returns:
        처리 결과 통계
    """
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    from rich.console import Console

    console = Console()
    extractor = HeaderExtractor()

    pending = extractor.get_pending_count(jenis)
    if limit:
        pending = min(pending, limit)

    if pending == 0:
        console.print("[yellow]No pending documents to process[/yellow]")
        return {"total": 0, "success": 0, "failed": 0}

    console.print(f"[cyan]Processing {pending} documents with {workers} workers...[/cyan]")

    stats = {"total": 0, "success": 0, "failed": 0, "total_pages": 0}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Extracting headers...", total=pending)

        for result in extractor.extract_batch(jenis=jenis, limit=limit, workers=workers):
            stats["total"] += 1

            if result.success:
                stats["success"] += 1
                stats["total_pages"] += result.page_count or 0
                if verbose:
                    progress.console.print(
                        f"  [green]✓[/green] {result.document_id} "
                        f"({result.page_count} pages, {result.duration_ms}ms)"
                    )
            else:
                stats["failed"] += 1
                if verbose:
                    progress.console.print(
                        f"  [red]✗[/red] {result.document_id}: {result.error}"
                    )

            progress.update(task, advance=1)

    # 파이프라인 상태 업데이트
    extractor.db.update_pipeline_state()

    console.print(f"\n[green]Done![/green] Success: {stats['success']}, Failed: {stats['failed']}")
    console.print(f"Total pages: {stats['total_pages']}")

    return stats


# CLI 인터페이스
if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Extract headers from PDFs")
    parser.add_argument("command", choices=["extract", "status", "test"],
                       help="Command to run")
    parser.add_argument("--jenis", "-j", help="Filter by jenis (law type)")
    parser.add_argument("--limit", "-l", type=int, help="Maximum documents to process")
    parser.add_argument("--workers", "-w", type=int, default=4,
                       help="Number of parallel workers")
    parser.add_argument("--quiet", "-q", action="store_true",
                       help="Suppress verbose output")

    args = parser.parse_args()

    if args.command == "extract":
        extract_headers(
            jenis=args.jenis,
            limit=args.limit,
            workers=args.workers,
            verbose=not args.quiet
        )

    elif args.command == "status":
        extractor = HeaderExtractor()
        pending = extractor.get_pending_count(args.jenis)
        extracted = extractor.get_extracted_count(args.jenis)

        print(f"\n=== Header Extraction Status ===")
        if args.jenis:
            print(f"Jenis: {args.jenis}")
        print(f"Pending: {pending}")
        print(f"Extracted: {extracted}")
        print(f"Total: {pending + extracted}")

    elif args.command == "test":
        # 단일 문서 테스트
        extractor = HeaderExtractor()

        with extractor.db.connection() as conn:
            row = conn.execute("""
                SELECT id, pdf_path FROM documents
                WHERE stage = 'pending' AND pdf_path IS NOT NULL
                ORDER BY priority DESC
                LIMIT 1
            """).fetchone()

        if row:
            print(f"Testing with: {row['id']}")
            print(f"PDF: {row['pdf_path']}")

            result = extractor.extract_single(row["id"])

            if result.success:
                print(f"[OK] Extracted to: {result.image_path}")
                print(f"     Pages: {result.page_count}")
                print(f"     Duration: {result.duration_ms}ms")
            else:
                print(f"[ERROR] {result.error}")
        else:
            print("No pending documents found")
