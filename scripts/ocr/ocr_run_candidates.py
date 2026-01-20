#!/usr/bin/env python3
"""Run OCR for candidate PDFs and optionally update the database.

Usage:
  .venv/bin/python scripts/ocr_run_candidates.py \
    --candidates docs/reports/ocr_candidates_full.json \
    --output-dir peraturan/data/ocr_text \
    --db peraturan/data/peraturan.db \
    --limit 10 --max-pages 5
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

import fitz
from peraturan.src.services.ocr_cleaner import clean_ocr_pages


def load_candidates(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data.get("candidates", [])


def iter_candidates(
    items: list[dict],
    limit: int,
    offset: int,
) -> Iterable[dict]:
    if offset < 0:
        offset = 0
    if limit > 0:
        return items[offset:offset + limit]
    if offset > 0:
        return items[offset:]
    return items


def run_tesseract(img_path: Path, lang: str, psm: int) -> str:
    cmd = [
        "tesseract",
        str(img_path),
        "stdout",
        "-l",
        lang,
        "--psm",
        str(psm),
    ]
    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    return out.decode("utf-8", errors="ignore")


def ocr_pdf(
    pdf_path: Path,
    output_dir: Path,
    lang: str,
    psm: int,
    dpi: int,
    max_pages: int,
    clean: bool,
    repeat_threshold: float,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = pdf_path.stem
    txt_path = output_dir / f"{slug}.txt"

    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    with fitz.open(pdf_path) as doc, tempfile.TemporaryDirectory() as tmp_dir:
        temp_dir = Path(tmp_dir)
        pages = min(len(doc), max_pages) if max_pages > 0 else len(doc)
        parts = []
        page_texts = []

        for page_idx in range(pages):
            page = doc.load_page(page_idx)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_path = temp_dir / f"page_{page_idx + 1:04d}.png"
            pix.save(img_path)
            text = run_tesseract(img_path, lang, psm)
            page_texts.append(text)
            parts.append(f"\n\n===== PAGE {page_idx + 1} =====\n\n{text}")

    if clean:
        cleaned = clean_ocr_pages(page_texts, repeat_threshold=repeat_threshold)
        cleaned_parts = []
        for page_idx, page_text in enumerate(cleaned.cleaned_pages):
            cleaned_parts.append(f"\n\n===== PAGE {page_idx + 1} =====\n\n{page_text}")
        txt_path.write_text("".join(cleaned_parts), encoding="utf-8")
    else:
        txt_path.write_text("".join(parts), encoding="utf-8")
    return txt_path


def update_db(conn: sqlite3.Connection, slug: str, text: str | None, error: str | None) -> None:
    if error:
        conn.execute(
            "UPDATE peraturan SET extraction_success = 0, extraction_error = ?, needs_ocr = 1 WHERE slug = ?",
            (error, slug),
        )
        conn.commit()
        return

    if text is not None:
        conn.execute(
            "UPDATE peraturan SET extracted_text = ?, extraction_success = 1, extraction_error = NULL, needs_ocr = 0 WHERE slug = ?",
            (text, slug),
        )
    else:
        conn.execute(
            "UPDATE peraturan SET extraction_success = 1, extraction_error = NULL, needs_ocr = 0 WHERE slug = ?",
            (slug,),
        )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR candidate runner (tesseract)")
    parser.add_argument("--candidates", required=True, help="Path to OCR candidates JSON")
    parser.add_argument("--output-dir", default="peraturan/data/ocr_text", help="OCR text output dir")
    parser.add_argument("--db", default="peraturan/data/peraturan.db", help="SQLite DB path")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of docs")
    parser.add_argument("--offset", type=int, default=0, help="Start offset for docs")
    parser.add_argument("--lang", default="ind+eng", help="Tesseract language set")
    parser.add_argument("--psm", type=int, default=6, help="Tesseract page segmentation mode")
    parser.add_argument("--dpi", type=int, default=200, help="Render DPI for OCR")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages per document (0 = all)")
    parser.add_argument("--update-db", action="store_true", help="Update peraturan DB with OCR results")
    parser.add_argument("--store-text", action="store_true", help="Store OCR text in DB (with --update-db)")
    parser.add_argument("--clean", action="store_true", help="Clean OCR text (remove noise/repeated lines)")
    parser.add_argument(
        "--repeat-threshold",
        type=float,
        default=0.6,
        help="Repeated-line threshold for cleaning",
    )
    args = parser.parse_args()

    candidates_path = Path(args.candidates)
    output_dir = Path(args.output_dir)
    db_path = Path(args.db)

    items = load_candidates(candidates_path)
    targets = list(iter_candidates(items, args.limit, args.offset))
    if not targets:
        print("No candidates to process.")
        return 1

    conn = None
    if args.update_db:
        conn = sqlite3.connect(str(db_path))

    processed = 0
    failed = 0

    for item in targets:
        pdf_path = Path(item["path"])
        slug = pdf_path.stem
        try:
            txt_path = ocr_pdf(
                pdf_path,
                output_dir,
                args.lang,
                args.psm,
                args.dpi,
                args.max_pages,
                args.clean,
                args.repeat_threshold,
            )
            processed += 1
            if conn:
                text = txt_path.read_text(encoding="utf-8") if args.store_text else None
                update_db(conn, slug, text, None)
        except Exception as e:
            failed += 1
            if conn:
                update_db(conn, slug, None, str(e))
            continue

    if conn:
        conn.close()

    print(f"Processed: {processed}")
    print(f"Failed: {failed}")
    print(f"Output dir: {output_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
