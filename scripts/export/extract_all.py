#!/usr/bin/env python3
"""Batch extract text from all downloaded PDFs."""

import json
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn, MofNCompleteColumn

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.extractor import TextExtractor, ExtractionResult


def get_pdfs_to_extract(db_path: str = "data/peraturan.db") -> list[tuple[str, str, str]]:
    """Get list of PDFs to extract from database.

    Returns:
        List of (slug, local_pdf_path, jenis) tuples
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT slug, local_pdf_path, jenis
        FROM peraturan
        WHERE local_pdf_path IS NOT NULL
        ORDER BY tahun DESC, slug
    """)

    results = cursor.fetchall()
    conn.close()

    return results


def save_extraction_result(
    conn: sqlite3.Connection,
    slug: str,
    result: ExtractionResult
) -> None:
    """Save extraction result to database."""
    cursor = conn.cursor()

    # Update peraturan table with extraction results
    cursor.execute("""
        UPDATE peraturan SET
            extracted_text = ?,
            extraction_success = ?,
            extraction_error = ?,
            needs_ocr = ?,
            pasal_count = ?,
            updated_at = ?
        WHERE slug = ?
    """, (
        result.body_text if result.success else None,
        1 if result.success else 0,
        result.error,
        1 if result.needs_ocr else 0,
        result.pasal_count,
        datetime.now().isoformat(),
        slug
    ))


def ensure_extraction_columns(db_path: str = "data/peraturan.db") -> None:
    """Ensure extraction columns exist in database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check existing columns
    cursor.execute("PRAGMA table_info(peraturan)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # Add missing columns
    new_cols = [
        ("extracted_text", "TEXT"),
        ("extraction_success", "INTEGER DEFAULT 0"),
        ("extraction_error", "TEXT"),
        ("needs_ocr", "INTEGER DEFAULT 0"),
        ("pasal_count", "INTEGER DEFAULT 0"),
    ]

    for col_name, col_type in new_cols:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE peraturan ADD COLUMN {col_name} {col_type}")
            print(f"Added column: {col_name}")

    conn.commit()
    conn.close()


def run_batch_extraction(
    db_path: str = "data/peraturan.db",
    batch_size: int = 100,
    limit: int = 0,
) -> dict:
    """Run batch extraction on all PDFs.

    Args:
        db_path: Path to database
        batch_size: Commit frequency
        limit: Max PDFs to process (0 = all)

    Returns:
        Statistics dictionary
    """
    # Ensure columns exist
    ensure_extraction_columns(db_path)

    # Get PDFs to extract
    pdfs = get_pdfs_to_extract(db_path)

    if limit > 0:
        pdfs = pdfs[:limit]

    total = len(pdfs)
    print(f"\n총 {total:,}개 PDF 추출 시작...\n")

    # Initialize extractor
    extractor = TextExtractor()

    # Statistics
    stats = {
        "total": total,
        "success": 0,
        "failed": 0,
        "needs_ocr": 0,
        "by_category": defaultdict(lambda: {"total": 0, "success": 0, "failed": 0, "ocr": 0}),
        "total_chars": 0,
        "total_pasals": 0,
        "errors": [],
        "start_time": datetime.now().isoformat(),
    }

    conn = sqlite3.connect(db_path)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("성공:{task.fields[success]} 실패:{task.fields[failed]} OCR:{task.fields[ocr]}"),
        TimeRemainingColumn(),
    ) as progress:

        task = progress.add_task(
            "텍스트 추출 중",
            total=total,
            success=0,
            failed=0,
            ocr=0,
        )

        for i, (slug, pdf_path, jenis) in enumerate(pdfs):
            # Get category from path
            path = Path(pdf_path)
            category = path.parent.name if path.parent.name != "pdfs" else "other"

            stats["by_category"][category]["total"] += 1

            try:
                # Extract
                result = extractor.extract(path, slug)

                # Save to database
                save_extraction_result(conn, slug, result)

                if result.success:
                    stats["success"] += 1
                    stats["by_category"][category]["success"] += 1
                    stats["total_chars"] += len(result.body_text)
                    stats["total_pasals"] += result.pasal_count
                elif result.needs_ocr:
                    stats["needs_ocr"] += 1
                    stats["by_category"][category]["ocr"] += 1
                else:
                    stats["failed"] += 1
                    stats["by_category"][category]["failed"] += 1
                    if len(stats["errors"]) < 100:  # Limit error log
                        stats["errors"].append({
                            "slug": slug,
                            "error": result.error,
                        })

            except Exception as e:
                stats["failed"] += 1
                stats["by_category"][category]["failed"] += 1
                if len(stats["errors"]) < 100:
                    stats["errors"].append({
                        "slug": slug,
                        "error": str(e),
                    })

            # Update progress
            progress.update(
                task,
                advance=1,
                success=stats["success"],
                failed=stats["failed"],
                ocr=stats["needs_ocr"],
            )

            # Commit periodically
            if (i + 1) % batch_size == 0:
                conn.commit()

        # Final commit
        conn.commit()

    conn.close()

    stats["end_time"] = datetime.now().isoformat()
    stats["by_category"] = dict(stats["by_category"])

    return stats


def generate_report(stats: dict, output_path: str = "docs/EXTRACTION_REPORT.md") -> None:
    """Generate extraction report."""

    success_rate = (stats["success"] / stats["total"] * 100) if stats["total"] > 0 else 0
    avg_chars = stats["total_chars"] / stats["success"] if stats["success"] > 0 else 0
    avg_pasals = stats["total_pasals"] / stats["success"] if stats["success"] > 0 else 0

    report = f"""# 텍스트 추출 결과 보고서

> 추출일: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## 1. 요약

| 항목 | 값 |
|------|-----|
| **총 PDF** | {stats['total']:,}개 |
| **추출 성공** | {stats['success']:,}개 ({success_rate:.1f}%) |
| **추출 실패** | {stats['failed']:,}개 |
| **OCR 필요** | {stats['needs_ocr']:,}개 |

## 2. 추출 통계

| 항목 | 값 |
|------|-----|
| **총 추출 문자** | {stats['total_chars']:,}자 |
| **평균 문서 길이** | {avg_chars:,.0f}자 |
| **총 Pasal 수** | {stats['total_pasals']:,}개 |
| **평균 Pasal 수** | {avg_pasals:.1f}개 |

## 3. 카테고리별 결과

| 카테고리 | 총 | 성공 | 실패 | OCR 필요 | 성공률 |
|----------|-----|------|------|----------|--------|
"""

    for cat, data in sorted(stats["by_category"].items()):
        cat_rate = (data["success"] / data["total"] * 100) if data["total"] > 0 else 0
        report += f"| {cat.upper()} | {data['total']:,} | {data['success']:,} | {data['failed']} | {data['ocr']} | {cat_rate:.1f}% |\n"

    report += f"""
## 4. 시간 정보

- 시작: {stats['start_time']}
- 종료: {stats['end_time']}

## 5. 오류 샘플

"""

    if stats["errors"]:
        report += "| Slug | 오류 |\n|------|------|\n"
        for err in stats["errors"][:20]:
            error_msg = (err["error"][:50] + "...") if len(err["error"]) > 50 else err["error"]
            report += f"| {err['slug'][:40]} | {error_msg} |\n"

        if len(stats["errors"]) > 20:
            report += f"\n... 외 {len(stats['errors']) - 20}개 오류\n"
    else:
        report += "오류 없음\n"

    report += """
## 6. 다음 단계

1. **OCR 처리**: OCR 필요한 문서 처리 (Tesseract/EasyOCR)
2. **구조화 저장**: XML/JSON 형식으로 구조화 저장
3. **품질 검증**: 샘플 추출 결과 수동 검증
4. **검색 인덱스**: 전문 검색 인덱스 구축
"""

    # Save report
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n보고서 저장: {output_path}")


def main():
    """Run batch extraction."""
    import argparse

    parser = argparse.ArgumentParser(description="Batch extract text from PDFs")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of PDFs (0=all)")
    parser.add_argument("--batch-size", type=int, default=100, help="Commit batch size")
    args = parser.parse_args()

    # Run extraction
    stats = run_batch_extraction(
        limit=args.limit,
        batch_size=args.batch_size,
    )

    # Save stats as JSON
    stats_path = "data/extraction_stats.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"통계 저장: {stats_path}")

    # Generate report
    generate_report(stats)

    # Print summary
    print("\n" + "=" * 50)
    print("추출 완료!")
    print("=" * 50)
    print(f"성공: {stats['success']:,}개")
    print(f"실패: {stats['failed']:,}개")
    print(f"OCR 필요: {stats['needs_ocr']:,}개")


if __name__ == "__main__":
    main()
