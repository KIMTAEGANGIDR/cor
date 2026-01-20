#!/usr/bin/env python3
"""Comprehensive PDF Analysis for KOICA RFP Report.

Analyzes all PDFs for:
1. PENJELASAN presence (해설 포함 여부)
2. Text extractability (텍스트 추출 가능성 / OCR 필요 여부)
3. Header type classification (LEMBARAN vs BERITA NEGARA)
4. Document structure markers (MEMUTUSKAN, Ditetapkan di 등)

Usage:
    python scripts/analyze_pdf_comprehensive.py
    python scripts/analyze_pdf_comprehensive.py --sample 1000  # 샘플 분석
    python scripts/analyze_pdf_comprehensive.py --output docs/data/pdf_analysis.json
"""

import json
import os
import re
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    print("Warning: PyMuPDF not installed. Run: pip install pymupdf")


@dataclass
class PDFAnalysisResult:
    """PDF 분석 결과."""
    document_id: str
    pdf_path: str

    # Basic info
    page_count: int = 0
    file_size: int = 0

    # Text extraction
    total_text_length: int = 0
    avg_text_per_page: float = 0.0
    needs_ocr: bool = False

    # Header type
    header_type: str = "unknown"  # LEMBARAN_NEGARA, BERITA_NEGARA, NONE, UNKNOWN

    # PENJELASAN
    has_penjelasan: bool = False
    has_penjelasan_umum: bool = False
    has_penjelasan_pasal: bool = False
    penjelasan_start_page: Optional[int] = None

    # Structure markers
    has_memutuskan: bool = False
    has_menetapkan: bool = False
    has_ditetapkan: bool = False

    # Pasal count
    pasal_count: int = 0

    # Error
    error: Optional[str] = None


def analyze_pdf(pdf_path: str, document_id: str) -> PDFAnalysisResult:
    """Analyze a single PDF file."""
    result = PDFAnalysisResult(document_id=document_id, pdf_path=pdf_path)

    if not PYMUPDF_AVAILABLE:
        result.error = "PyMuPDF not available"
        return result

    try:
        if not os.path.exists(pdf_path):
            result.error = f"File not found: {pdf_path}"
            return result

        result.file_size = os.path.getsize(pdf_path)

        doc = fitz.open(pdf_path)
        result.page_count = len(doc)

        # Extract text from all pages
        full_text = ""
        page_texts = []

        for page_num, page in enumerate(doc):
            try:
                text = page.get_text()
                page_texts.append(text)
                full_text += text + "\n"
            except Exception:
                page_texts.append("")

        doc.close()

        result.total_text_length = len(full_text)
        result.avg_text_per_page = len(full_text) / result.page_count if result.page_count > 0 else 0

        # Check if OCR is needed (very low text per page)
        result.needs_ocr = result.avg_text_per_page < 100 and result.page_count > 1

        # Convert to uppercase for pattern matching
        full_text_upper = full_text.upper()

        # Header type detection (check first 3 pages)
        first_pages = "\n".join(page_texts[:3]).upper() if page_texts else ""

        if "LEMBARAN NEGARA" in first_pages:
            result.header_type = "LEMBARAN_NEGARA"
        elif "BERITA NEGARA" in first_pages:
            result.header_type = "BERITA_NEGARA"
        elif result.total_text_length < 200:
            result.header_type = "NONE"  # Likely scanned without text
        else:
            result.header_type = "OTHER"

        # PENJELASAN detection
        penjelasan_patterns = [
            r'PENJELASAN\s+ATAS',
            r'PENJELASAN\s+UNDANG',
            r'PENJELASAN\s+PERATURAN',
            r'\bPENJELASAN\b',
        ]

        for pattern in penjelasan_patterns:
            match = re.search(pattern, full_text_upper)
            if match:
                result.has_penjelasan = True
                # Find page number
                char_pos = match.start()
                running_length = 0
                for i, page_text in enumerate(page_texts):
                    running_length += len(page_text) + 1
                    if running_length > char_pos:
                        result.penjelasan_start_page = i + 1
                        break
                break

        # PENJELASAN UMUM
        if re.search(r'I\.?\s*UMUM|UMUM\s*$', full_text_upper, re.MULTILINE):
            result.has_penjelasan_umum = True

        # PENJELASAN PASAL DEMI PASAL
        if re.search(r'II\.?\s*PASAL\s+DEMI\s+PASAL|PASAL\s+DEMI\s+PASAL', full_text_upper):
            result.has_penjelasan_pasal = True

        # Structure markers
        result.has_memutuskan = bool(re.search(r'MEMUTUSKAN\s*:', full_text_upper))
        result.has_menetapkan = bool(re.search(r'MENETAPKAN\s*:', full_text_upper))
        result.has_ditetapkan = bool(re.search(r'DITETAPKAN\s+DI', full_text_upper))

        # Count Pasal
        pasal_matches = re.findall(r'\bPASAL\s+\d+', full_text_upper)
        result.pasal_count = len(set(pasal_matches))

    except Exception as e:
        result.error = str(e)

    return result


def analyze_pdf_wrapper(args):
    """Wrapper for multiprocessing."""
    pdf_path, document_id = args
    return analyze_pdf(pdf_path, document_id)


def run_analysis(
    peraturan_db: Path,
    pdf_base_dir: Path,
    sample_size: Optional[int] = None,
    workers: int = 8,
) -> dict:
    """Run comprehensive PDF analysis.

    Args:
        peraturan_db: Path to peraturan database
        pdf_base_dir: Base directory for PDF files
        sample_size: If set, only analyze this many files
        workers: Number of parallel workers

    Returns:
        Analysis results dictionary
    """
    print(f"Loading documents from {peraturan_db}...")

    conn = sqlite3.connect(peraturan_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
    SELECT slug, local_pdf_path, jenis, tahun
    FROM peraturan
    WHERE local_pdf_path IS NOT NULL
    """
    if sample_size:
        query += f" ORDER BY RANDOM() LIMIT {sample_size}"

    cursor.execute(query)
    documents = [dict(row) for row in cursor.fetchall()]
    conn.close()

    print(f"Found {len(documents)} documents with PDFs")

    # Prepare tasks
    tasks = []
    for doc in documents:
        # local_pdf_path is like 'data/pdfs/uu/...', pdf_base_dir is 'peraturan/data'
        # So we need to skip the 'data/' prefix from local_pdf_path
        local_path = doc['local_pdf_path']
        if local_path.startswith('data/'):
            local_path = local_path[5:]  # Remove 'data/' prefix
        pdf_path = pdf_base_dir / local_path
        tasks.append((str(pdf_path), doc['slug']))

    # Run analysis in parallel
    results = []
    errors = []

    print(f"Analyzing PDFs with {workers} workers...")

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(analyze_pdf_wrapper, task): task for task in tasks}

        completed = 0
        total = len(futures)

        for future in as_completed(futures):
            completed += 1
            if completed % 1000 == 0 or completed == total:
                print(f"  Progress: {completed}/{total} ({completed/total*100:.1f}%)")

            try:
                result = future.result()
                results.append(result)
                if result.error:
                    errors.append(result)
            except Exception as e:
                task = futures[future]
                errors.append(PDFAnalysisResult(
                    document_id=task[1],
                    pdf_path=task[0],
                    error=str(e)
                ))

    # Aggregate statistics
    print("\nAggregating statistics...")

    valid_results = [r for r in results if not r.error]

    stats = {
        "meta": {
            "analysis_date": datetime.now().isoformat(),
            "total_analyzed": len(results),
            "successful": len(valid_results),
            "errors": len(errors),
            "sample_size": sample_size,
        },
        "page_count": {
            "total_pages": sum(r.page_count for r in valid_results),
            "avg_pages": sum(r.page_count for r in valid_results) / len(valid_results) if valid_results else 0,
            "max_pages": max((r.page_count for r in valid_results), default=0),
            "min_pages": min((r.page_count for r in valid_results), default=0),
        },
        "text_extraction": {
            "total_docs": len(valid_results),
            "needs_ocr_count": sum(1 for r in valid_results if r.needs_ocr),
            "needs_ocr_pct": sum(1 for r in valid_results if r.needs_ocr) / len(valid_results) * 100 if valid_results else 0,
            "direct_extract_count": sum(1 for r in valid_results if not r.needs_ocr),
            "direct_extract_pct": sum(1 for r in valid_results if not r.needs_ocr) / len(valid_results) * 100 if valid_results else 0,
            "avg_text_length": sum(r.total_text_length for r in valid_results) / len(valid_results) if valid_results else 0,
        },
        "header_type": {
            "LEMBARAN_NEGARA": sum(1 for r in valid_results if r.header_type == "LEMBARAN_NEGARA"),
            "BERITA_NEGARA": sum(1 for r in valid_results if r.header_type == "BERITA_NEGARA"),
            "OTHER": sum(1 for r in valid_results if r.header_type == "OTHER"),
            "NONE": sum(1 for r in valid_results if r.header_type == "NONE"),
            "distribution_pct": {},
        },
        "penjelasan": {
            "has_penjelasan_count": sum(1 for r in valid_results if r.has_penjelasan),
            "has_penjelasan_pct": sum(1 for r in valid_results if r.has_penjelasan) / len(valid_results) * 100 if valid_results else 0,
            "has_umum_count": sum(1 for r in valid_results if r.has_penjelasan_umum),
            "has_umum_pct": sum(1 for r in valid_results if r.has_penjelasan_umum) / len(valid_results) * 100 if valid_results else 0,
            "has_pasal_demi_pasal_count": sum(1 for r in valid_results if r.has_penjelasan_pasal),
            "has_pasal_demi_pasal_pct": sum(1 for r in valid_results if r.has_penjelasan_pasal) / len(valid_results) * 100 if valid_results else 0,
        },
        "structure_markers": {
            "has_memutuskan_count": sum(1 for r in valid_results if r.has_memutuskan),
            "has_memutuskan_pct": sum(1 for r in valid_results if r.has_memutuskan) / len(valid_results) * 100 if valid_results else 0,
            "has_menetapkan_count": sum(1 for r in valid_results if r.has_menetapkan),
            "has_menetapkan_pct": sum(1 for r in valid_results if r.has_menetapkan) / len(valid_results) * 100 if valid_results else 0,
            "has_ditetapkan_count": sum(1 for r in valid_results if r.has_ditetapkan),
            "has_ditetapkan_pct": sum(1 for r in valid_results if r.has_ditetapkan) / len(valid_results) * 100 if valid_results else 0,
        },
        "pasal_count": {
            "total_pasal": sum(r.pasal_count for r in valid_results),
            "avg_pasal": sum(r.pasal_count for r in valid_results) / len(valid_results) if valid_results else 0,
            "max_pasal": max((r.pasal_count for r in valid_results), default=0),
        },
    }

    # Calculate header type percentages
    total_valid = len(valid_results)
    if total_valid > 0:
        for header_type in ["LEMBARAN_NEGARA", "BERITA_NEGARA", "OTHER", "NONE"]:
            count = stats["header_type"][header_type]
            stats["header_type"]["distribution_pct"][header_type] = round(count / total_valid * 100, 1)

    # Add error details
    if errors:
        stats["errors_detail"] = [
            {"document_id": e.document_id, "error": e.error}
            for e in errors[:50]  # First 50 errors
        ]

    return stats


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Comprehensive PDF Analysis")
    parser.add_argument(
        "--peraturan-db",
        type=Path,
        default=Path("peraturan/data/peraturan.db"),
        help="Path to peraturan database",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=Path("peraturan/data"),
        help="Base directory for PDF files",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Sample size (None = all)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel workers",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("docs/data/pdf_comprehensive_analysis.json"),
        help="Output JSON file",
    )

    args = parser.parse_args()

    if not PYMUPDF_AVAILABLE:
        print("Error: PyMuPDF is required. Install with: pip install pymupdf")
        sys.exit(1)

    print("=" * 60)
    print("Comprehensive PDF Analysis for KOICA RFP Report")
    print("=" * 60)
    print(f"Database: {args.peraturan_db}")
    print(f"PDF Base: {args.pdf_dir}")
    print(f"Sample: {args.sample or 'All'}")
    print(f"Workers: {args.workers}")
    print()

    stats = run_analysis(
        peraturan_db=args.peraturan_db,
        pdf_base_dir=args.pdf_dir,
        sample_size=args.sample,
        workers=args.workers,
    )

    # Save results
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print("Analysis Complete")
    print("=" * 60)
    print(f"Output saved to: {args.output}")
    print()
    print("Key Findings:")
    print(f"  Total analyzed: {stats['meta']['successful']:,} PDFs")
    print(f"  Total pages: {stats['page_count']['total_pages']:,}")
    print(f"  Avg pages/doc: {stats['page_count']['avg_pages']:.1f}")
    print()
    print(f"  Text extractable: {stats['text_extraction']['direct_extract_pct']:.1f}%")
    print(f"  OCR needed: {stats['text_extraction']['needs_ocr_pct']:.1f}%")
    print()
    print(f"  Header - LEMBARAN NEGARA: {stats['header_type']['distribution_pct'].get('LEMBARAN_NEGARA', 0)}%")
    print(f"  Header - BERITA NEGARA: {stats['header_type']['distribution_pct'].get('BERITA_NEGARA', 0)}%")
    print(f"  Header - OTHER/NONE: {stats['header_type']['distribution_pct'].get('OTHER', 0) + stats['header_type']['distribution_pct'].get('NONE', 0)}%")
    print()
    print(f"  PENJELASAN 포함: {stats['penjelasan']['has_penjelasan_pct']:.1f}% ({stats['penjelasan']['has_penjelasan_count']:,}건)")
    print(f"  PENJELASAN UMUM: {stats['penjelasan']['has_umum_pct']:.1f}% ({stats['penjelasan']['has_umum_count']:,}건)")
    print(f"  PASAL DEMI PASAL: {stats['penjelasan']['has_pasal_demi_pasal_pct']:.1f}% ({stats['penjelasan']['has_pasal_demi_pasal_count']:,}건)")
    print()
    print(f"  MEMUTUSKAN 마커: {stats['structure_markers']['has_memutuskan_pct']:.1f}%")
    print(f"  Ditetapkan 마커: {stats['structure_markers']['has_ditetapkan_pct']:.1f}%")


if __name__ == "__main__":
    main()
