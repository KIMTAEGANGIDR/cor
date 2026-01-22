#!/usr/bin/env python3
"""Build a review bundle for failed pages with images and text."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "peraturan"))

from src.services.extractor import TextExtractor


CER_THRESHOLD = 0.001  # 0.1%
ANCHOR_LEN = 120
SEARCH_WINDOW_MULT = 2
SEARCH_WINDOW_PAD = 200


@dataclass
class PageCheck:
    slug: str
    page_index: int
    ref_len: int
    cand_len: int
    distance: int
    cer: float
    status: str


def normalize_text(text: str) -> str:
    if not text:
        return ""
    # Unicode normalization
    text = unicodedata.normalize("NFKC", text)
    # Remove zero-width and soft hyphen
    text = text.replace("\u00ad", "")
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(ch, "")
    # Normalize quotes/dashes
    replacements = {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u00a0": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    # Remove hyphenation across line breaks
    text = text.replace("-\n", "")
    text = text.replace("-\r\n", "")
    # Normalize whitespace
    text = " ".join(text.split())
    return text.strip()


def clean_lines(text: str, noise_regexes: list, repeated_lines: set[str], extractor: TextExtractor) -> str:
    lines = []
    for raw in text.splitlines():
        is_noise = False
        for pattern in noise_regexes:
            if pattern.match(raw):
                is_noise = True
                break
        if not is_noise:
            normalized = extractor._normalize_line(raw)
            if normalized and normalized in repeated_lines:
                is_noise = True
        if not is_noise:
            lines.append(raw)
    return "\n".join(lines)


def compute_body_slices(page_texts: list[str], extractor: TextExtractor) -> list[str]:
    full_text = "\n".join(page_texts)
    body_start = 0
    body_end = len(full_text)

    for pattern in extractor.BODY_START_PATTERNS:
        match = __import__("re").search(pattern, full_text, __import__("re").IGNORECASE)
        if match:
            body_start = match.end()
            break

    for pattern in extractor.BODY_END_PATTERNS:
        match = __import__("re").search(pattern, full_text[body_start:], __import__("re").IGNORECASE)
        if match:
            body_end = body_start + match.start()
            break

    slices = []
    running = 0
    for text in page_texts:
        page_start = running
        page_end = running + len(text)
        running = page_end + 1

        slice_start = max(body_start, page_start) - page_start
        slice_end = min(body_end, page_end) - page_start
        if slice_start < slice_end:
            slices.append(text[slice_start:slice_end])
        else:
            slices.append("")

    return slices


def bounded_levenshtein(a: str, b: str, max_dist: int) -> int:
    """Compute Levenshtein distance with early exit (banded)."""
    if a == b:
        return 0
    if not a:
        return min(len(b), max_dist + 1)
    if not b:
        return min(len(a), max_dist + 1)

    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1

    # Ensure a is the shorter string
    if len(a) > len(b):
        a, b = b, a

    prev = list(range(len(a) + 1))
    for i, cb in enumerate(b, start=1):
        start = max(1, i - max_dist)
        end = min(len(a), i + max_dist)
        cur = [i] + [0] * len(a)
        for j in range(start, end + 1):
            cost = 0 if a[j - 1] == cb else 1
            cur[j] = min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + cost,
            )
        prev = cur
        if min(prev[start:end + 1]) > max_dist:
            return max_dist + 1

    return prev[len(a)]


def iter_documents(conn: sqlite3.Connection, slugs: list[str]) -> Iterable[sqlite3.Row]:
    query = """
        SELECT slug, local_pdf_path, extracted_text
        FROM peraturan
        WHERE slug = ?
    """
    conn.row_factory = sqlite3.Row
    for slug in slugs:
        row = conn.execute(query, (slug,)).fetchone()
        if row:
            yield row


def resolve_pdf_path(pdf_base_dir: Path, local_pdf_path: str) -> Path:
    # local_pdf_path stored like 'data/pdfs/uu/...'
    if local_pdf_path.startswith("data/"):
        local_pdf_path = local_pdf_path[5:]
    return pdf_base_dir / local_pdf_path


def render_page_image(doc: fitz.Document, page_index: int, dpi: int, out_path: Path) -> None:
    page = doc.load_page(page_index)
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(out_path.as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description="Build review bundle for failed pages")
    parser.add_argument("--db", default=str(PROJECT_ROOT / "peraturan" / "data" / "peraturan.db"))
    parser.add_argument("--pdf-base", default=str(PROJECT_ROOT / "peraturan" / "data"))
    parser.add_argument("--failed-pages", default=str(PROJECT_ROOT / "docs" / "reports" / "verification_failed_pages_aggregate.jsonl"))
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "docs" / "reports" / "verification_review"))
    parser.add_argument("--image-dpi", type=int, default=150)
    args = parser.parse_args()

    if not PYMUPDF_AVAILABLE:
        print("PyMuPDF not installed. Install pymupdf to run review bundle.")
        return 1

    failed_pages_path = Path(args.failed_pages)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    review_pages_path = out_dir / "review_pages.jsonl"
    review_errors_path = out_dir / "review_errors.jsonl"
    images_dir = out_dir / "images"

    if not failed_pages_path.exists():
        print(f"Failed pages file not found: {failed_pages_path}")
        return 1

    failed_by_slug: dict[str, set[int]] = {}
    with failed_pages_path.open("r", encoding="utf-8") as src:
        for line in src:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = obj.get("slug")
            page_index = obj.get("page_index")
            if not slug or not page_index:
                continue
            failed_by_slug.setdefault(slug, set()).add(int(page_index))

    if not failed_by_slug:
        print("No failed pages found.")
        return 0

    extractor = TextExtractor()
    noise_regexes = [
        __import__("re").compile(p, __import__("re").MULTILINE)
        for p in extractor.NOISE_PATTERNS
    ]

    conn = sqlite3.connect(args.db)
    total_written = 0
    with review_pages_path.open("w", encoding="utf-8") as page_out, review_errors_path.open("w", encoding="utf-8") as err_out:
        for row in iter_documents(conn, sorted(failed_by_slug.keys())):
            slug = row["slug"]
            local_pdf_path = row["local_pdf_path"]
            extracted_text = row["extracted_text"] or ""

            pdf_path = resolve_pdf_path(Path(args.pdf_base), local_pdf_path)
            if not pdf_path.exists():
                err_out.write(json.dumps({"slug": slug, "error": "pdf_missing", "pdf_path": str(pdf_path)}) + "\n")
                continue

            try:
                doc = fitz.open(pdf_path)
            except Exception as exc:
                err_out.write(json.dumps({"slug": slug, "error": "pdf_open_failed", "pdf_path": str(pdf_path), "detail": str(exc)}) + "\n")
                continue

            total_pages = len(doc)
            page_texts = [page.get_text() for page in doc]

            page_lines = [extractor._extract_page_lines(text) for text in page_texts]
            repeated_lines, _ = extractor._find_repeated_lines(page_lines, total_pages)
            body_page_texts = compute_body_slices(page_texts, extractor)

            # Clean reference page texts
            ref_pages = []
            for text in body_page_texts:
                cleaned = clean_lines(text, noise_regexes, repeated_lines, extractor)
                ref_pages.append(normalize_text(cleaned))

            # Clean candidate text (extracted_text)
            cand_cleaned = clean_lines(extracted_text, noise_regexes, repeated_lines, extractor)
            cand_text = normalize_text(cand_cleaned)

            cand_pos = 0
            page_checks: list[PageCheck] = []
            cand_segments: list[str] = []

            for idx, ref in enumerate(ref_pages, start=1):
                if not ref:
                    page_checks.append(PageCheck(
                        slug=slug,
                        page_index=idx,
                        ref_len=0,
                        cand_len=0,
                        distance=0,
                        cer=0.0,
                        status="skip_empty",
                    ))
                    cand_segments.append("")
                    continue

                anchor = ref[:min(ANCHOR_LEN, len(ref))]
                search_window = cand_pos + (len(ref) * SEARCH_WINDOW_MULT) + SEARCH_WINDOW_PAD
                found = cand_text.find(anchor, cand_pos, min(search_window, len(cand_text)))
                if found != -1:
                    cand_pos = found

                cand_segment = cand_text[cand_pos:cand_pos + len(ref)]
                max_dist = max(1, int(len(ref) * CER_THRESHOLD))
                distance = bounded_levenshtein(ref, cand_segment, max_dist)
                cer = distance / len(ref)
                status = "pass" if cer <= CER_THRESHOLD else "fail"

                page_checks.append(PageCheck(
                    slug=slug,
                    page_index=idx,
                    ref_len=len(ref),
                    cand_len=len(cand_segment),
                    distance=distance,
                    cer=cer,
                    status=status,
                ))
                cand_segments.append(cand_segment)
                cand_pos += len(cand_segment)

            target_pages = failed_by_slug.get(slug, set())
            for page_check, ref_text, cand_text_segment in zip(page_checks, ref_pages, cand_segments):
                if page_check.page_index not in target_pages:
                    continue
                image_path = images_dir / slug / f"page_{page_check.page_index:04d}.png"
                try:
                    render_page_image(doc, page_check.page_index - 1, args.image_dpi, image_path)
                except Exception as exc:
                    err_out.write(json.dumps({
                        "slug": slug,
                        "page_index": page_check.page_index,
                        "error": "image_render_failed",
                        "detail": str(exc),
                    }) + "\n")
                    continue

                payload = {
                    "slug": slug,
                    "page_index": page_check.page_index,
                    "pdf_path": str(pdf_path),
                    "image_path": str(image_path),
                    "ref_text": ref_text,
                    "cand_text": cand_text_segment,
                }
                payload.update(asdict(page_check))
                page_out.write(json.dumps(payload, ensure_ascii=False) + "\n")
                total_written += 1

            doc.close()

    conn.close()
    print(f"Wrote review pages: {review_pages_path}")
    print(f"Wrote review errors: {review_errors_path}")
    print(f"Images in: {images_dir}")
    print(f"Total review pages: {total_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
