#!/usr/bin/env python3
"""Scan PDFs and list OCR candidates based on quality scoring.

Usage:
  .venv/bin/python scripts/ocr_candidate_scan.py --limit 500 --output docs/reports/ocr_candidates.json
"""

import argparse
import json
import random
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from peraturan.src.services.extractor import TextExtractor
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent / "peraturan"))
    from src.services.extractor import TextExtractor


def iter_pdfs(
    pdf_root: Path,
    limit: int,
    sample: int,
    seed: int,
    offset: int,
) -> list[Path]:
    pdfs = sorted(pdf_root.rglob("*.pdf"))
    if sample > 0:
        random.seed(seed)
        if len(pdfs) < sample:
            return pdfs
        return random.sample(pdfs, sample)
    if offset < 0:
        offset = 0
    if limit > 0:
        return pdfs[offset:offset + limit]
    if offset > 0:
        return pdfs[offset:]
    return pdfs


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR candidate scan")
    parser.add_argument("--root", default="peraturan/data/pdfs", help="PDF root directory")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of PDFs")
    parser.add_argument("--sample", type=int, default=0, help="Random sample size")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--offset", type=int, default=0, help="Start offset for deterministic scans")
    parser.add_argument("--min-score", type=float, default=50.0, help="Quality score threshold")
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()

    pdf_root = Path(args.root)
    pdfs = iter_pdfs(pdf_root, args.limit, args.sample, args.seed, args.offset)
    if not pdfs:
        print("No PDFs found.")
        return 1

    extractor = TextExtractor()
    candidates = []

    for pdf in pdfs:
        result = extractor.extract(pdf)
        if result.error:
            candidates.append({
                "path": str(pdf),
                "type": pdf.parent.name,
                "reason": "extract_error",
                "error": result.error,
            })
            continue

        is_candidate = result.needs_ocr or result.quality_score < args.min_score
        if is_candidate:
            candidates.append({
                "path": str(pdf),
                "type": pdf.parent.name,
                "pages": result.total_pages,
                "avg_chars_per_page": round(result.avg_chars_per_page, 1),
                "repeated_line_ratio": round(result.repeated_line_ratio, 3),
                "quality_score": round(result.quality_score, 1),
                "quality_flags": result.quality_flags,
                "needs_ocr": result.needs_ocr,
            })

    print(f"Total scanned: {len(pdfs)}")
    if args.sample == 0:
        print(f"Offset: {args.offset}")
    print(f"OCR candidates: {len(candidates)}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump({"total": len(pdfs), "candidates": candidates}, f, ensure_ascii=False, indent=2)
        print(f"Wrote: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
