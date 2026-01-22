#!/usr/bin/env python3
"""Verify extracted_text against PDF text with page-level CER.

Policy:
- PDF text is the reference for digital PDFs.
- Noise lines (headers/footers/watermarks) are removed.
- Text is normalized (whitespace, hyphenation, unicode cleanup).
- Page-level CER must be <= 0.1% (99.9% match).

Outputs:
- Summary JSON with per-document stats
- Detailed JSONL with per-page stats
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import unicodedata
from datetime import datetime
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


@dataclass
class DocCheck:
    slug: str
    pdf_path: str
    total_pages: int
    pages_checked: int
    pages_failed: int
    max_cer: float
    avg_cer: float
    status: str
    reason: str | None = None


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


def iter_documents(conn: sqlite3.Connection, limit: int, offset: int) -> Iterable[sqlite3.Row]:
    query = """
        SELECT slug, local_pdf_path, extracted_text, needs_ocr
        FROM peraturan
        WHERE local_pdf_path IS NOT NULL
          AND extracted_text IS NOT NULL
          AND extracted_text != ''
        ORDER BY tahun DESC, slug
    """
    if limit > 0:
        query += f" LIMIT {limit} OFFSET {offset}"
    conn.row_factory = sqlite3.Row
    return conn.execute(query)


def resolve_pdf_path(pdf_base_dir: Path, local_pdf_path: str) -> Path:
    # local_pdf_path stored like 'data/pdfs/uu/...'
    if local_pdf_path.startswith("data/"):
        local_pdf_path = local_pdf_path[5:]
    return pdf_base_dir / local_pdf_path


def append_run_log(run_log_path: Path, event: dict) -> None:
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    event["ts"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with run_log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, ensure_ascii=True) + "\n")


def load_completed_slugs(run_log_path: Path) -> set[str]:
    completed = set()
    if not run_log_path.exists():
        return completed
    with run_log_path.open("r", encoding="utf-8") as log_file:
        for line in log_file:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "doc_done" and event.get("slug"):
                completed.add(event["slug"])
    return completed


def doccheck_from_dict(data: dict) -> DocCheck:
    return DocCheck(
        slug=data.get("slug", ""),
        pdf_path=data.get("pdf_path", ""),
        total_pages=int(data.get("total_pages", 0)),
        pages_checked=int(data.get("pages_checked", 0)),
        pages_failed=int(data.get("pages_failed", 0)),
        max_cer=float(data.get("max_cer", 0.0)),
        avg_cer=float(data.get("avg_cer", 0.0)),
        status=data.get("status", ""),
        reason=data.get("reason"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify extracted_text vs PDF text")
    parser.add_argument("--db", default=str(PROJECT_ROOT / "peraturan" / "data" / "peraturan.db"))
    parser.add_argument("--pdf-base", default=str(PROJECT_ROOT / "peraturan" / "data"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--summary", default=str(PROJECT_ROOT / "docs" / "reports" / "verification_summary.json"))
    parser.add_argument("--pages", default=str(PROJECT_ROOT / "docs" / "reports" / "verification_pages.jsonl"))
    parser.add_argument("--run-log", default=str(PROJECT_ROOT / "docs" / "reports" / "verification_runlog.jsonl"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--progress-every", type=int, default=50)
    args = parser.parse_args()

    if not PYMUPDF_AVAILABLE:
        print("PyMuPDF not installed. Install pymupdf to run verification.")
        return 1

    db_path = Path(args.db)
    pdf_base_dir = Path(args.pdf_base)
    summary_path = Path(args.summary)
    pages_path = Path(args.pages)
    run_log_path = Path(args.run_log)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    append_run_log(run_log_path, {"event": "run_start", "run_id": run_id, "args": vars(args)})

    extractor = TextExtractor()
    noise_regexes = [
        __import__("re").compile(p, __import__("re").MULTILINE)
        for p in extractor.NOISE_PATTERNS
    ]

    conn = sqlite3.connect(db_path)

    doc_results: list[DocCheck] = []
    completed_slugs: set[str] = set()
    if args.resume:
        completed_slugs = load_completed_slugs(run_log_path)
        if summary_path.exists():
            try:
                existing_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing_summary = {}
            for doc in existing_summary.get("documents", []):
                slug = doc.get("slug")
                if slug:
                    completed_slugs.add(slug)
                    doc_results.append(doccheck_from_dict(doc))

    pages_mode = "a" if args.resume and pages_path.exists() else "w"
    processed_docs = 0
    with pages_path.open(pages_mode, encoding="utf-8") as page_out:
        for row in iter_documents(conn, args.limit, args.offset):
            slug = row["slug"]
            if args.resume and slug in completed_slugs:
                continue

            local_pdf_path = row["local_pdf_path"]
            extracted_text = row["extracted_text"] or ""
            needs_ocr = bool(row["needs_ocr"])

            append_run_log(run_log_path, {"event": "doc_start", "run_id": run_id, "slug": slug})

            try:
                pdf_path = resolve_pdf_path(pdf_base_dir, local_pdf_path)
                if not pdf_path.exists():
                    doc_results.append(DocCheck(
                        slug=slug,
                        pdf_path=str(pdf_path),
                        total_pages=0,
                        pages_checked=0,
                        pages_failed=0,
                        max_cer=0.0,
                        avg_cer=0.0,
                        status="error",
                        reason="pdf_missing",
                    ))
                    append_run_log(run_log_path, {
                        "event": "doc_done",
                        "run_id": run_id,
                        "slug": slug,
                        "status": "error",
                        "reason": "pdf_missing",
                    })
                    continue

                try:
                    doc = fitz.open(pdf_path)
                except Exception as exc:
                    doc_results.append(DocCheck(
                        slug=slug,
                        pdf_path=str(pdf_path),
                        total_pages=0,
                        pages_checked=0,
                        pages_failed=0,
                        max_cer=0.0,
                        avg_cer=0.0,
                        status="error",
                        reason="pdf_open_failed",
                    ))
                    append_run_log(run_log_path, {
                        "event": "doc_done",
                        "run_id": run_id,
                        "slug": slug,
                        "status": "error",
                        "reason": "pdf_open_failed",
                        "error": str(exc),
                    })
                    continue

                total_pages = len(doc)
                page_texts = [page.get_text() for page in doc]
                doc.close()

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

                if needs_ocr:
                    doc_results.append(DocCheck(
                        slug=slug,
                        pdf_path=str(pdf_path),
                        total_pages=total_pages,
                        pages_checked=0,
                        pages_failed=0,
                        max_cer=0.0,
                        avg_cer=0.0,
                        status="skipped",
                        reason="needs_ocr",
                    ))
                    append_run_log(run_log_path, {
                        "event": "doc_done",
                        "run_id": run_id,
                        "slug": slug,
                        "status": "skipped",
                        "reason": "needs_ocr",
                    })
                    continue

                cand_pos = 0
                page_checks: list[PageCheck] = []

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

                    cand_pos += len(cand_segment)

                pages_checked = sum(1 for p in page_checks if p.status in ("pass", "fail"))
                pages_failed = sum(1 for p in page_checks if p.status == "fail")
                cer_values = [p.cer for p in page_checks if p.status in ("pass", "fail")]
                max_cer = max(cer_values) if cer_values else 0.0
                avg_cer = (sum(cer_values) / len(cer_values)) if cer_values else 0.0
                status = "pass" if pages_failed == 0 else "fail"

                for page_check in page_checks:
                    page_out.write(json.dumps(asdict(page_check), ensure_ascii=False) + "\n")

                doc_results.append(DocCheck(
                    slug=slug,
                    pdf_path=str(pdf_path),
                    total_pages=total_pages,
                    pages_checked=pages_checked,
                    pages_failed=pages_failed,
                    max_cer=max_cer,
                    avg_cer=avg_cer,
                    status=status,
                ))

                append_run_log(run_log_path, {
                    "event": "doc_done",
                    "run_id": run_id,
                    "slug": slug,
                    "status": status,
                    "pages_failed": pages_failed,
                    "max_cer": max_cer,
                    "avg_cer": avg_cer,
                })
            except Exception as exc:
                doc_results.append(DocCheck(
                    slug=slug,
                    pdf_path=local_pdf_path or "",
                    total_pages=0,
                    pages_checked=0,
                    pages_failed=0,
                    max_cer=0.0,
                    avg_cer=0.0,
                    status="error",
                    reason="exception",
                ))
                append_run_log(run_log_path, {
                    "event": "doc_error",
                    "run_id": run_id,
                    "slug": slug,
                    "error": str(exc),
                })
                continue

            processed_docs += 1
            if args.progress_every > 0 and processed_docs % args.progress_every == 0:
                append_run_log(run_log_path, {
                    "event": "progress",
                    "run_id": run_id,
                    "processed_docs": processed_docs,
                })

    conn.close()

    summary = {
        "total_docs": len(doc_results),
        "passed": sum(1 for d in doc_results if d.status == "pass"),
        "failed": sum(1 for d in doc_results if d.status == "fail"),
        "skipped": sum(1 for d in doc_results if d.status == "skipped"),
        "errors": sum(1 for d in doc_results if d.status == "error"),
        "cer_threshold": CER_THRESHOLD,
        "documents": [asdict(d) for d in doc_results],
    }

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote summary: {summary_path}")
    print(f"Wrote pages: {pages_path}")

    append_run_log(run_log_path, {
        "event": "run_end",
        "run_id": run_id,
        "total_docs": summary["total_docs"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "skipped": summary["skipped"],
        "errors": summary["errors"],
    })

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
