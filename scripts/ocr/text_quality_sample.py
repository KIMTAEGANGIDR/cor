#!/usr/bin/env python3
"""Sample PDF quality scan using TextExtractor.

Usage:
  .venv/bin/python scripts/text_quality_sample.py --count 100 --output docs/reports/text_quality_sample_100.json
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from peraturan.src.services.extractor import TextExtractor
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent / "peraturan"))
    from src.services.extractor import TextExtractor


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample PDF quality scan")
    parser.add_argument("--root", default="peraturan/data/pdfs", help="PDF root directory")
    parser.add_argument("--count", type=int, default=100, help="Sample size")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()

    pdf_root = Path(args.root)
    pdfs = [p for p in pdf_root.rglob("*.pdf")]
    if len(pdfs) < args.count:
        print(f"Not enough PDFs: {len(pdfs)}")
        return 1

    random.seed(args.seed)
    sample = random.sample(pdfs, args.count)

    extractor = TextExtractor()
    rows = []

    for pdf in sample:
        result = extractor.extract(pdf)
        rows.append({
            "path": str(pdf),
            "type": pdf.parent.name,
            "pages": result.total_pages,
            "avg_chars_per_page": round(result.avg_chars_per_page, 1),
            "repeated_line_ratio": round(result.repeated_line_ratio, 3),
            "quality_score": round(result.quality_score, 1),
            "quality_flags": result.quality_flags,
            "needs_ocr": result.needs_ocr,
            "markers": result.markers,
            "error": result.error,
        })

    ok = [r for r in rows if not r["error"]]
    avg_pages = sum(r["pages"] for r in ok) / len(ok) if ok else 0
    avg_chars = sum(r["avg_chars_per_page"] for r in ok) / len(ok) if ok else 0

    low_text = [r for r in ok if r["avg_chars_per_page"] < 100]
    heavy_repeat = [r for r in ok if r["repeated_line_ratio"] >= 0.3]
    needs_ocr = [r for r in ok if r["needs_ocr"]]

    marker_hits = defaultdict(int)
    for r in ok:
        for k, v in r.get("markers", {}).items():
            if v:
                marker_hits[k] += 1

    by_type = defaultdict(list)
    for r in ok:
        by_type[r["type"]].append(r)

    summary = {
        "files_total": len(sample),
        "files_ok": len(ok),
        "avg_pages": round(avg_pages, 1),
        "avg_chars_per_page": round(avg_chars, 1),
        "low_text_pages<100": len(low_text),
        "heavy_repeat_ratio>=0.3": len(heavy_repeat),
        "needs_ocr": len(needs_ocr),
        "marker_hits": dict(marker_hits),
        "type_breakdown": {
            t: {
                "count": len(rows_t),
                "avg_chars_per_page": round(
                    sum(r["avg_chars_per_page"] for r in rows_t) / len(rows_t), 1
                ),
                "low_text": sum(1 for r in rows_t if r["avg_chars_per_page"] < 100),
                "repeat_high": sum(1 for r in rows_t if r["repeated_line_ratio"] >= 0.3),
            }
            for t, rows_t in sorted(by_type.items(), key=lambda x: len(x[1]), reverse=True)
        },
    }

    print("SAMPLE_SUMMARY")
    for k, v in summary.items():
        if k in ("type_breakdown", "marker_hits"):
            continue
        print(f"{k}={v}")

    print("\nMARKER_HITS")
    for k, v in summary["marker_hits"].items():
        print(f"{k}={v}")

    print("\nTYPE_BREAKDOWN")
    for t, stats in summary["type_breakdown"].items():
        print(
            f"{t}\tcount={stats['count']}"
            f"\tavg_chars_page={stats['avg_chars_per_page']}"
            f"\tlow_text={stats['low_text']}"
            f"\trepeat_high={stats['repeat_high']}"
        )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
        print(f"\nWrote: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
