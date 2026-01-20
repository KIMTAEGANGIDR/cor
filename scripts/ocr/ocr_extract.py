#!/usr/bin/env python3
"""OCR extraction for scanned PDFs."""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from pdf2image import convert_from_path
import pytesseract
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeRemainingColumn

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))


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


def ocr_pdf(pdf_path: Path, lang: str = "ind+eng") -> tuple[str, int]:
    """Extract text from scanned PDF using OCR.

    Args:
        pdf_path: Path to PDF file
        lang: Tesseract language codes

    Returns:
        Tuple of (extracted_text, page_count)
    """
    # Convert PDF to images
    images = convert_from_path(pdf_path, dpi=200)

    texts = []
    for i, image in enumerate(images):
        # OCR each page
        text = pytesseract.image_to_string(image, lang=lang)
        texts.append(text)

    return "\n\n".join(texts), len(images)


def extract_body(text: str) -> str:
    """Extract main body from OCR text."""
    import re

    # Find body start
    start_patterns = [
        r'MEMUTUSKAN\s*:',
        r'MENETAPKAN\s*:',
    ]

    body_start = 0
    for pattern in start_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            body_start = match.end()
            break

    # Find body end
    end_patterns = [
        r'Ditetapkan\s+di\s+\w+',
        r'DITETAPKAN\s+DI\s+\w+',
    ]

    body_end = len(text)
    for pattern in end_patterns:
        match = re.search(pattern, text[body_start:], re.IGNORECASE)
        if match:
            body_end = body_start + match.start()
            break

    return text[body_start:body_end].strip()


def count_pasal(text: str) -> int:
    """Count Pasal in text."""
    import re
    pattern = re.compile(r'\bPasal\s+\d+', re.IGNORECASE)
    return len(pattern.findall(text))


def save_ocr_result(conn: sqlite3.Connection, slug: str, text: str, body: str, pasal_count: int, error: str = None):
    """Save OCR result to database."""
    cursor = conn.cursor()

    if error:
        cursor.execute("""
            UPDATE peraturan SET
                extraction_error = ?,
                updated_at = ?
            WHERE slug = ?
        """, (error, datetime.now().isoformat(), slug))
    else:
        cursor.execute("""
            UPDATE peraturan SET
                extracted_text = ?,
                extraction_success = 1,
                needs_ocr = 0,
                pasal_count = ?,
                extraction_error = NULL,
                updated_at = ?
            WHERE slug = ?
        """, (body, pasal_count, datetime.now().isoformat(), slug))


def run_ocr_extraction(db_path: str = "data/peraturan.db", limit: int = 0):
    """Run OCR extraction on all scanned PDFs."""

    pdfs = get_ocr_needed_pdfs(db_path)

    if limit > 0:
        pdfs = pdfs[:limit]

    total = len(pdfs)
    print(f"\nOCR 추출 대상: {total}개 PDF\n")

    if total == 0:
        print("처리할 PDF가 없습니다.")
        return

    stats = {
        "total": total,
        "success": 0,
        "failed": 0,
        "total_chars": 0,
        "total_pasals": 0,
    }

    conn = sqlite3.connect(db_path)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("성공:{task.fields[success]} 실패:{task.fields[failed]}"),
        TimeRemainingColumn(),
    ) as progress:

        task = progress.add_task(
            "OCR 추출 중",
            total=total,
            success=0,
            failed=0,
        )

        for slug, pdf_path in pdfs:
            try:
                path = Path(pdf_path)

                if not path.exists():
                    raise FileNotFoundError(f"File not found: {pdf_path}")

                # OCR extraction
                full_text, page_count = ocr_pdf(path)

                # Extract body
                body = extract_body(full_text)

                # Count pasal
                pasal_count = count_pasal(body)

                # Save result
                save_ocr_result(conn, slug, full_text, body, pasal_count)

                stats["success"] += 1
                stats["total_chars"] += len(body)
                stats["total_pasals"] += pasal_count

            except Exception as e:
                stats["failed"] += 1
                save_ocr_result(conn, slug, "", "", 0, str(e))

            progress.update(
                task,
                advance=1,
                success=stats["success"],
                failed=stats["failed"],
            )

            # Commit every 10
            if (stats["success"] + stats["failed"]) % 10 == 0:
                conn.commit()

    conn.commit()
    conn.close()

    # Summary
    print("\n" + "=" * 50)
    print("OCR 추출 완료!")
    print("=" * 50)
    print(f"성공: {stats['success']}개")
    print(f"실패: {stats['failed']}개")
    print(f"총 문자: {stats['total_chars']:,}자")
    print(f"총 Pasal: {stats['total_pasals']}개")

    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OCR extraction for scanned PDFs")
    parser.add_argument("--limit", type=int, default=0, help="Limit PDFs to process")
    args = parser.parse_args()

    run_ocr_extraction(limit=args.limit)
