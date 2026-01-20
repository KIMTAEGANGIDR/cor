#!/usr/bin/env python3
"""Fast OCR extraction with parallel processing."""

import sqlite3
import sys
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from multiprocessing import cpu_count

from pdf2image import convert_from_path
import pytesseract
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeRemainingColumn
import fitz  # PyMuPDF for page count

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Config
MAX_PAGES = 100  # Skip PDFs with more pages
DPI = 150  # Lower DPI for faster processing
MAX_WORKERS = max(1, cpu_count() - 1)  # Leave 1 CPU free


def get_page_count(pdf_path: str) -> int:
    """Get PDF page count."""
    try:
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except:
        return 0


def ocr_single_pdf(args: tuple) -> dict:
    """OCR a single PDF file.

    Args:
        args: Tuple of (slug, pdf_path)

    Returns:
        Result dict with extracted text and metadata
    """
    slug, pdf_path = args

    result = {
        "slug": slug,
        "success": False,
        "text": "",
        "body": "",
        "pasal_count": 0,
        "error": None,
        "skipped": False,
    }

    try:
        path = Path(pdf_path)

        if not path.exists():
            result["error"] = f"File not found: {pdf_path}"
            return result

        # Check page count
        page_count = get_page_count(pdf_path)
        if page_count > MAX_PAGES:
            result["skipped"] = True
            result["error"] = f"Too many pages: {page_count} > {MAX_PAGES}"
            return result

        # Convert to images (lower DPI for speed)
        images = convert_from_path(pdf_path, dpi=DPI)

        # OCR each page
        texts = []
        for image in images:
            text = pytesseract.image_to_string(image, lang="ind+eng")
            texts.append(text)

        full_text = "\n\n".join(texts)
        result["text"] = full_text

        # Extract body
        body_start = 0
        for pattern in [r'MEMUTUSKAN\s*:', r'MENETAPKAN\s*:']:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                body_start = match.end()
                break

        body_end = len(full_text)
        for pattern in [r'Ditetapkan\s+di\s+\w+', r'DITETAPKAN\s+DI\s+\w+']:
            match = re.search(pattern, full_text[body_start:], re.IGNORECASE)
            if match:
                body_end = body_start + match.start()
                break

        result["body"] = full_text[body_start:body_end].strip()

        # Count pasal
        result["pasal_count"] = len(re.findall(r'\bPasal\s+\d+', full_text, re.IGNORECASE))

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


def get_ocr_needed_pdfs(db_path: str = "data/peraturan.db") -> list[tuple[str, str]]:
    """Get PDFs that need OCR."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT slug, local_pdf_path
        FROM peraturan
        WHERE needs_ocr = 1 AND (extracted_text IS NULL OR extracted_text = '')
        ORDER BY tahun DESC
    """)

    results = cursor.fetchall()
    conn.close()
    return results


def save_results_batch(db_path: str, results: list[dict]) -> None:
    """Save batch of results to database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for r in results:
        if r["skipped"]:
            # Mark as skipped but keep needs_ocr = 1
            cursor.execute("""
                UPDATE peraturan SET
                    extraction_error = ?,
                    updated_at = ?
                WHERE slug = ?
            """, (r["error"], datetime.now().isoformat(), r["slug"]))
        elif r["success"]:
            cursor.execute("""
                UPDATE peraturan SET
                    extracted_text = ?,
                    extraction_success = 1,
                    needs_ocr = 0,
                    pasal_count = ?,
                    extraction_error = NULL,
                    updated_at = ?
                WHERE slug = ?
            """, (r["body"], r["pasal_count"], datetime.now().isoformat(), r["slug"]))
        else:
            cursor.execute("""
                UPDATE peraturan SET
                    extraction_error = ?,
                    updated_at = ?
                WHERE slug = ?
            """, (r["error"], datetime.now().isoformat(), r["slug"]))

    conn.commit()
    conn.close()


def run_fast_ocr(db_path: str = "data/peraturan.db", limit: int = 0):
    """Run fast parallel OCR extraction."""

    pdfs = get_ocr_needed_pdfs(db_path)

    if limit > 0:
        pdfs = pdfs[:limit]

    total = len(pdfs)
    print(f"\n{'='*50}")
    print(f"OCR 최적화 추출")
    print(f"{'='*50}")
    print(f"대상: {total}개 PDF")
    print(f"병렬 처리: {MAX_WORKERS} workers")
    print(f"DPI: {DPI}")
    print(f"최대 페이지: {MAX_PAGES} (초과 시 건너뜀)")
    print(f"{'='*50}\n")

    if total == 0:
        print("처리할 PDF가 없습니다.")
        return

    stats = {
        "total": total,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "total_chars": 0,
        "total_pasals": 0,
    }

    batch_size = 10  # Save every 10 results
    pending_results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("✓:{task.fields[success]} ✗:{task.fields[failed]} ⊘:{task.fields[skipped]}"),
        TimeRemainingColumn(),
    ) as progress:

        task = progress.add_task(
            "OCR 추출 중",
            total=total,
            success=0,
            failed=0,
            skipped=0,
        )

        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(ocr_single_pdf, pdf): pdf for pdf in pdfs}

            for future in as_completed(futures):
                try:
                    result = future.result()

                    if result["skipped"]:
                        stats["skipped"] += 1
                    elif result["success"]:
                        stats["success"] += 1
                        stats["total_chars"] += len(result["body"])
                        stats["total_pasals"] += result["pasal_count"]
                    else:
                        stats["failed"] += 1

                    pending_results.append(result)

                    # Save batch
                    if len(pending_results) >= batch_size:
                        save_results_batch(db_path, pending_results)
                        pending_results = []

                except Exception as e:
                    stats["failed"] += 1

                progress.update(
                    task,
                    advance=1,
                    success=stats["success"],
                    failed=stats["failed"],
                    skipped=stats["skipped"],
                )

    # Save remaining results
    if pending_results:
        save_results_batch(db_path, pending_results)

    # Summary
    print("\n" + "=" * 50)
    print("OCR 추출 완료!")
    print("=" * 50)
    print(f"성공: {stats['success']}개")
    print(f"실패: {stats['failed']}개")
    print(f"건너뜀 (대용량): {stats['skipped']}개")
    print(f"총 문자: {stats['total_chars']:,}자")
    print(f"총 Pasal: {stats['total_pasals']}개")

    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fast OCR extraction")
    parser.add_argument("--limit", type=int, default=0, help="Limit PDFs")
    parser.add_argument("--workers", type=int, default=0, help="Number of workers")
    args = parser.parse_args()

    if args.workers > 0:
        MAX_WORKERS = args.workers

    run_fast_ocr(limit=args.limit)
