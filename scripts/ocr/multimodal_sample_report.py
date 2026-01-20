#!/usr/bin/env python3
"""Sample multimodal (image + text) analysis for PDFs.

Usage:
  .venv/bin/python scripts/multimodal_sample_report.py --count 100
"""

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from peraturan.src.services.extractor import TextExtractor
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent / "peraturan"))
    from src.services.extractor import TextExtractor

import fitz  # PyMuPDF


def moving_average(values: list[float], window: int = 7) -> list[float]:
    if not values:
        return []
    if window <= 1:
        return values[:]
    half = window // 2
    padded = [values[0]] * half + values + [values[-1]] * half
    out = []
    for i in range(len(values)):
        out.append(sum(padded[i:i + window]) / window)
    return out


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(round((len(sorted_vals) - 1) * p))
    return sorted_vals[idx]


def _band_ratio(mask: list[bool], start: int, end: int, min_run: int) -> float:
    run = 0
    max_run = 0
    for i in range(start, end):
        if mask[i]:
            run += 1
            if run >= min_run:
                max_run = max(max_run, run)
        else:
            run = 0
    if not mask:
        return 0.0
    return max_run / len(mask)


def analyze_image_first_page(pdf_path: Path, dpi: int = 96) -> dict:
    doc = fitz.open(pdf_path)
    try:
        if len(doc) == 0:
            return {"error": "empty_pdf"}

        page = doc[0]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)

        width = pix.width
        height = pix.height
        samples = pix.samples

        if width == 0 or height == 0:
            return {"error": "empty_image"}

        row_ink = []
        for y in range(height):
            row = samples[y * width:(y + 1) * width]
            row_avg = sum(row) / width
            row_ink.append(1.0 - (row_avg / 255.0))

        smoothed = moving_average(row_ink, window=7)
        q75 = percentile(smoothed, 0.75)
        ink_threshold = max(0.04, q75 * 0.6)
        text_mask = [v >= ink_threshold for v in smoothed]

        band_limit = 0.25
        top_end = int(height * band_limit)
        bottom_start = int(height * (1 - band_limit))
        min_run = max(3, int(height * 0.01))

        header_band_ratio = _band_ratio(text_mask, 0, top_end, min_run)
        footer_band_ratio = _band_ratio(text_mask, bottom_start, height, min_run)

        top_ratio = 0.15
        bottom_ratio = 0.15
        top_end_2 = int(height * top_ratio)
        bottom_start_2 = int(height * (1 - bottom_ratio))

        def avg_range(start: int, end: int) -> float:
            if end <= start:
                return 0.0
            return sum(row_ink[start:end]) / (end - start)

        ink_top = avg_range(0, top_end_2)
        ink_mid = avg_range(top_end_2, bottom_start_2)
        ink_bottom = avg_range(bottom_start_2, height)

        text_row_ratio = sum(1 for v in text_mask if v) / height

        return {
            "dpi": dpi,
            "width": width,
            "height": height,
            "ink_top": round(ink_top, 4),
            "ink_mid": round(ink_mid, 4),
            "ink_bottom": round(ink_bottom, 4),
            "header_band_ratio": round(header_band_ratio, 4),
            "footer_band_ratio": round(footer_band_ratio, 4),
            "text_row_ratio": round(text_row_ratio, 4),
            "ink_threshold": round(ink_threshold, 4),
        }
    finally:
        doc.close()


def summarize_flags(rows: list[dict]) -> dict:
    counter = Counter()
    for row in rows:
        for flag in row.get("flags", []):
            counter[flag] += 1
    return dict(counter)


def build_markdown_report(summary: dict, rows: list[dict], output_path: Path) -> None:
    lines = []
    lines.append("# Multimodal Sample Report")
    lines.append("")
    lines.append("## Summary")
    for key in [
        "files_total",
        "files_ok_both",
        "files_text_error",
        "files_image_error",
        "avg_pages",
        "avg_chars_per_page",
        "avg_ink_top",
        "avg_ink_mid",
        "avg_ink_bottom",
        "avg_header_band_ratio",
        "avg_footer_band_ratio",
        "avg_text_row_ratio",
    ]:
        if key in summary:
            lines.append(f"- {key}: {summary[key]}")

    lines.append("")
    lines.append("## Flag Counts")
    for k, v in summary.get("flag_counts", {}).items():
        lines.append(f"- {k}: {v}")

    lines.append("")
    lines.append("## Type Breakdown")
    for jenis, stats in summary.get("type_breakdown", {}).items():
        lines.append(
            f"- {jenis}: count={stats['count']}, low_text={stats['low_text']}, "
            f"image_text_mismatch={stats['image_text_mismatch']}"
        )

    lines.append("")
    lines.append("## Sample Cases (image_text_mismatch)")
    for row in summary.get("sample_mismatch", []):
        lines.append(
            f"- {row['path']} | avg_chars_per_page={row['avg_chars_per_page']} | "
            f"ink_mid={row['ink_mid']} | header+footer={row['header_footer_ratio']}"
        )

    lines.append("")
    lines.append("## Sample Cases (header_footer_heavy)")
    for row in summary.get("sample_header_footer", []):
        lines.append(
            f"- {row['path']} | header+footer={row['header_footer_ratio']} | "
            f"repeated_line_ratio={row['repeated_line_ratio']}"
        )

    lines.append("")
    lines.append("## Sample Cases (repeat_noise)")
    for row in summary.get("sample_repeat_noise", []):
        lines.append(
            f"- {row['path']} | repeated_line_ratio={row['repeated_line_ratio']} | "
            f"header+footer={row['header_footer_ratio']}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Multimodal PDF sample report")
    parser.add_argument("--root", default="peraturan/data/pdfs", help="PDF root directory")
    parser.add_argument("--count", type=int, default=100, help="Sample size")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--dpi", type=int, default=96, help="DPI for image analysis")
    parser.add_argument("--output-dir", default="docs/reports", help="Output directory")
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
        row = {
            "path": str(pdf),
            "type": pdf.parent.name,
            "text_error": None,
            "image_error": None,
        }

        text_result = extractor.extract(pdf)
        if text_result.error:
            row["text_error"] = text_result.error
        row.update({
            "pages": text_result.total_pages,
            "avg_chars_per_page": round(text_result.avg_chars_per_page, 1),
            "repeated_line_ratio": round(text_result.repeated_line_ratio, 3),
            "quality_score": round(text_result.quality_score, 1),
            "quality_flags": text_result.quality_flags,
            "needs_ocr": text_result.needs_ocr,
            "markers": text_result.markers,
        })

        image_result = analyze_image_first_page(pdf, dpi=args.dpi)
        if "error" in image_result:
            row["image_error"] = image_result["error"]
        row.update(image_result)

        flags = []
        if row.get("avg_chars_per_page", 0) < 80:
            flags.append("low_text")
        if row.get("needs_ocr"):
            flags.append("needs_ocr")
        if row.get("repeated_line_ratio", 0) >= 0.12:
            flags.append("repeat_noise")
        if row.get("ink_mid") is not None and row.get("avg_chars_per_page", 0) < 80:
            if row["ink_mid"] >= 0.02:
                flags.append("image_text_mismatch")
        header_footer_ratio = 0.0
        if row.get("header_band_ratio") is not None and row.get("footer_band_ratio") is not None:
            header_footer_ratio = row["header_band_ratio"] + row["footer_band_ratio"]
            if header_footer_ratio >= 0.05:
                flags.append("header_footer_heavy")
        row["header_footer_ratio"] = round(header_footer_ratio, 4)
        row["flags"] = flags

        rows.append(row)

    ok_both = [r for r in rows if not r["text_error"] and not r["image_error"]]
    text_errors = [r for r in rows if r["text_error"]]
    image_errors = [r for r in rows if r["image_error"]]

    summary = {
        "files_total": len(rows),
        "files_ok_both": len(ok_both),
        "files_text_error": len(text_errors),
        "files_image_error": len(image_errors),
    }

    if ok_both:
        summary.update({
            "avg_pages": round(mean(r["pages"] for r in ok_both), 1),
            "avg_chars_per_page": round(mean(r["avg_chars_per_page"] for r in ok_both), 1),
            "avg_ink_top": round(mean(r["ink_top"] for r in ok_both), 4),
            "avg_ink_mid": round(mean(r["ink_mid"] for r in ok_both), 4),
            "avg_ink_bottom": round(mean(r["ink_bottom"] for r in ok_both), 4),
            "avg_header_band_ratio": round(mean(r["header_band_ratio"] for r in ok_both), 4),
            "avg_footer_band_ratio": round(mean(r["footer_band_ratio"] for r in ok_both), 4),
            "avg_text_row_ratio": round(mean(r["text_row_ratio"] for r in ok_both), 4),
        })

    summary["flag_counts"] = summarize_flags(rows)

    by_type = defaultdict(list)
    for r in rows:
        by_type[r["type"]].append(r)

    summary["type_breakdown"] = {
        jenis: {
            "count": len(items),
            "low_text": sum(1 for r in items if "low_text" in r.get("flags", [])),
            "image_text_mismatch": sum(1 for r in items if "image_text_mismatch" in r.get("flags", [])),
        }
        for jenis, items in sorted(by_type.items(), key=lambda x: len(x[1]), reverse=True)
    }

    mismatch_samples = [
        r for r in rows if "image_text_mismatch" in r.get("flags", [])
    ]
    mismatch_samples.sort(key=lambda r: r.get("ink_mid", 0), reverse=True)
    summary["sample_mismatch"] = [
        {
            "path": r["path"],
            "avg_chars_per_page": r["avg_chars_per_page"],
            "ink_mid": r.get("ink_mid"),
            "header_footer_ratio": r.get("header_footer_ratio"),
        }
        for r in mismatch_samples[:10]
    ]

    header_footer_samples = [
        r for r in rows if "header_footer_heavy" in r.get("flags", [])
    ]
    header_footer_samples.sort(key=lambda r: r.get("header_footer_ratio", 0), reverse=True)
    summary["sample_header_footer"] = [
        {
            "path": r["path"],
            "header_footer_ratio": r.get("header_footer_ratio"),
            "repeated_line_ratio": r.get("repeated_line_ratio"),
        }
        for r in header_footer_samples[:10]
    ]

    repeat_noise_samples = [
        r for r in rows if "repeat_noise" in r.get("flags", [])
    ]
    repeat_noise_samples.sort(key=lambda r: r.get("repeated_line_ratio", 0), reverse=True)
    summary["sample_repeat_noise"] = [
        {
            "path": r["path"],
            "repeated_line_ratio": r.get("repeated_line_ratio"),
            "header_footer_ratio": r.get("header_footer_ratio"),
        }
        for r in repeat_noise_samples[:10]
    ]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"multimodal_sample_{args.count}.json"
    md_path = output_dir / f"multimodal_sample_{args.count}.md"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)

    build_markdown_report(summary, rows, md_path)

    print(f"Wrote: {json_path}")
    print(f"Wrote: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
