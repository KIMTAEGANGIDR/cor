#!/usr/bin/env python3
"""Sample documents, render images, OCR them, and compare to extracted_text."""

from __future__ import annotations

import argparse
import functools
import json
import multiprocessing as mp
import os
import random
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "peraturan"))

from src.services.extractor import TextExtractor
from src.ocr.batch_ocr import PaddleOCREngine


CER_THRESHOLD = 0.001  # 0.1%
ANCHOR_LEN = 120
SEARCH_WINDOW_MULT = 2
SEARCH_WINDOW_PAD = 200
DEFAULT_REPORTS_ROOT = Path("/workspace/cor_reports") if Path("/workspace").exists() else (PROJECT_ROOT / "docs" / "reports")

_OCR_ENGINE = None
_EXTRACTOR = None
_NOISE_REGEXES = None


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


def deskew_image(image_path: Path) -> bool:
    if not CV2_AVAILABLE:
        return False
    image = cv2.imread(str(image_path))
    if image is None:
        return False
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thresh = cv2.bitwise_not(thresh)
    coords = cv2.findNonZero(thresh)
    if coords is None:
        return False
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    angle = -angle
    if abs(angle) < 0.1:
        return True
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return cv2.imwrite(str(image_path), rotated)


def iter_random_documents(conn: sqlite3.Connection, sample_size: int, seed: int) -> Iterable[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT slug, local_pdf_path, extracted_text
        FROM peraturan
        WHERE local_pdf_path IS NOT NULL
          AND extracted_text IS NOT NULL
          AND extracted_text != ''
        """,
    ).fetchall()
    random.Random(seed).shuffle(rows)
    for row in rows[:sample_size]:
        yield row


def iter_documents(conn: sqlite3.Connection, limit: int, offset: int, order_by: str) -> Iterable[sqlite3.Row]:
    query = """
        SELECT slug, local_pdf_path, extracted_text
        FROM peraturan
        WHERE local_pdf_path IS NOT NULL
          AND extracted_text IS NOT NULL
          AND extracted_text != ''
    """
    if order_by:
        query += f" ORDER BY {order_by}"
    if limit > 0:
        query += f" LIMIT {limit} OFFSET {offset}"
    conn.row_factory = sqlite3.Row
    return conn.execute(query)


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


def _init_worker(skip_ocr: bool) -> None:
    global _OCR_ENGINE, _EXTRACTOR, _NOISE_REGEXES
    _EXTRACTOR = TextExtractor()
    _NOISE_REGEXES = [
        __import__("re").compile(p, __import__("re").MULTILINE)
        for p in _EXTRACTOR.NOISE_PATTERNS
    ]
    _OCR_ENGINE = None
    if not skip_ocr:
        _OCR_ENGINE = PaddleOCREngine(lang="en", use_gpu=True)


def _process_document(doc_row: dict, args_dict: dict) -> dict:
    global _OCR_ENGINE, _EXTRACTOR, _NOISE_REGEXES
    slug = doc_row["slug"]
    local_pdf_path = doc_row["local_pdf_path"]
    extracted_text = doc_row.get("extracted_text") or ""

    output_dir = Path(args_dict["output_dir"])
    images_dir = output_dir / "images"
    pdf_path = resolve_pdf_path(Path(args_dict["pdf_base"]), local_pdf_path)

    if not pdf_path.exists():
        return {
            "slug": slug,
            "status": "error",
            "reason": "pdf_missing",
            "page_checks": [],
            "failures": [],
            "pages_failed": 0,
            "max_cer": 0.0,
            "avg_cer": 0.0,
            "total_pages": 0,
            "pages_checked": 0,
        }

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return {
            "slug": slug,
            "status": "error",
            "reason": "pdf_open_failed",
            "page_checks": [],
            "failures": [],
            "pages_failed": 0,
            "max_cer": 0.0,
            "avg_cer": 0.0,
            "total_pages": 0,
            "pages_checked": 0,
        }

    total_pages = len(doc)
    page_limit = args_dict["max_pages"] if args_dict["max_pages"] > 0 else total_pages
    page_texts: list[str] = []
    ocr_page_texts: list[str] = []

    for idx in range(min(total_pages, page_limit)):
        text = doc.load_page(idx).get_text()
        page_texts.append(text)

        image_path = images_dir / slug / f"page_{idx + 1:04d}.png"
        render_page_image(doc, idx, args_dict["image_dpi"], image_path)
        if args_dict["deskew"]:
            deskew_image(image_path)

        if _OCR_ENGINE:
            ocr_result = _OCR_ENGINE.process_image(str(image_path))
            ocr_page_texts.append(ocr_result.text or "")
        else:
            ocr_page_texts.append("")

    doc.close()

    if args_dict["skip_ocr"]:
        return {
            "slug": slug,
            "status": "skipped",
            "reason": "skip_ocr",
            "page_checks": [],
            "failures": [],
            "pages_failed": 0,
            "max_cer": 0.0,
            "avg_cer": 0.0,
            "total_pages": total_pages,
            "pages_checked": 0,
        }

    page_lines = [_EXTRACTOR._extract_page_lines(text) for text in page_texts]
    repeated_lines, _ = _EXTRACTOR._find_repeated_lines(page_lines, len(page_texts))
    _ = compute_body_slices(page_texts, _EXTRACTOR)

    ref_pages = []
    for text in ocr_page_texts:
        cleaned = clean_lines(text, _NOISE_REGEXES, repeated_lines, _EXTRACTOR)
        ref_pages.append(normalize_text(cleaned))

    cand_cleaned = clean_lines(extracted_text, _NOISE_REGEXES, repeated_lines, _EXTRACTOR)
    cand_text = normalize_text(cand_cleaned)

    cand_pos = 0
    page_checks: list[dict] = []
    failures: list[dict] = []

    for page_index, ref in enumerate(ref_pages, start=1):
        if not ref:
            page_checks.append({
                "slug": slug,
                "page_index": page_index,
                "ref_len": 0,
                "cand_len": 0,
                "distance": 0,
                "cer": 0.0,
                "status": "skip_empty",
            })
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

        page_checks.append({
            "slug": slug,
            "page_index": page_index,
            "ref_len": len(ref),
            "cand_len": len(cand_segment),
            "distance": distance,
            "cer": cer,
            "status": status,
        })
        if status == "fail":
            failures.append({
                "slug": slug,
                "page_index": page_index,
                "cer": cer,
                "ref_text": ref,
                "cand_text": cand_segment,
                "ocr_raw": ocr_page_texts[page_index - 1] if page_index - 1 < len(ocr_page_texts) else "",
            })
        cand_pos += len(cand_segment)

    pages_checked = sum(1 for p in page_checks if p["status"] in ("pass", "fail"))
    pages_failed = sum(1 for p in page_checks if p["status"] == "fail")
    cer_values = [p["cer"] for p in page_checks if p["status"] in ("pass", "fail")]
    max_cer = max(cer_values) if cer_values else 0.0
    avg_cer = (sum(cer_values) / len(cer_values)) if cer_values else 0.0
    status = "pass" if pages_failed == 0 else "fail"

    return {
        "slug": slug,
        "status": status,
        "page_checks": page_checks,
        "failures": failures,
        "pages_failed": pages_failed,
        "max_cer": max_cer,
        "avg_cer": avg_cer,
        "total_pages": len(ref_pages),
        "pages_checked": pages_checked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample OCR compare pipeline")
    parser.add_argument("--db", default=str(PROJECT_ROOT / "peraturan" / "data" / "peraturan.db"))
    parser.add_argument("--pdf-base", default=str(PROJECT_ROOT / "peraturan" / "data"))
    parser.add_argument("--output-dir", default=str(DEFAULT_REPORTS_ROOT / "ocr_compare_sample"))
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--order-by", default="tahun DESC, slug")
    parser.add_argument("--image-dpi", type=int, default=150)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--num-workers", type=int, default=mp.cpu_count())
    parser.add_argument("--deskew", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--run-log", default=str(DEFAULT_REPORTS_ROOT / "ocr_compare_sample" / "ocr_compare_runlog.jsonl"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--fail-fast-after", type=int, default=0)
    args = parser.parse_args()

    if not PYMUPDF_AVAILABLE:
        print("PyMuPDF not installed. Install pymupdf to run OCR sample.")
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    pages_path = output_dir / "ocr_compare_pages.jsonl"
    summary_path = output_dir / "ocr_compare_summary.json"
    failures_path = output_dir / "ocr_compare_failures.jsonl"
    run_log_path = Path(args.run_log)

    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    append_run_log(run_log_path, {"event": "run_start", "run_id": run_id, "args": vars(args)})

    completed_slugs: set[str] = set()
    doc_results = []
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
                    doc_results.append(doc)

    pages_mode = "a" if args.resume and pages_path.exists() else "w"
    failures_mode = "a" if args.resume and failures_path.exists() else "w"

    def finalize_run(summary: dict, reason: str | None = None) -> None:
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        payload = {
            "event": "run_end",
            "run_id": run_id,
            "total_docs": summary["total_docs"],
            "passed": summary["passed"],
            "failed": summary["failed"],
            "skipped": summary["skipped"],
            "errors": summary["errors"],
        }
        if reason:
            payload["reason"] = reason
        append_run_log(run_log_path, payload)

    conn = sqlite3.connect(args.db)
    processed_docs = 0
    failed_docs = 0
    doc_rows: list[dict] = []
    if args.limit > 0:
        rows = iter_documents(conn, args.limit, args.offset, args.order_by)
    else:
        rows = iter_random_documents(conn, args.sample_size, args.seed)

    for row in rows:
        slug = row["slug"]
        if args.resume and slug in completed_slugs:
            continue
        doc_rows.append({
            "slug": slug,
            "local_pdf_path": row["local_pdf_path"],
            "extracted_text": row["extracted_text"],
        })

    args_dict = {
        "pdf_base": args.pdf_base,
        "output_dir": str(output_dir),
        "image_dpi": args.image_dpi,
        "max_pages": args.max_pages,
        "deskew": args.deskew,
        "skip_ocr": args.skip_ocr,
    }

    def handle_result(result: dict, page_out, fail_out) -> None:
        nonlocal processed_docs, failed_docs
        slug = result["slug"]
        status = result.get("status", "error")
        reason = result.get("reason")

        for page_check in result.get("page_checks", []):
            page_out.write(json.dumps(page_check, ensure_ascii=False) + "\n")
        for failure in result.get("failures", []):
            fail_out.write(json.dumps(failure, ensure_ascii=False) + "\n")

        doc_entry = {
            "slug": slug,
            "total_pages": result.get("total_pages", 0),
            "pages_checked": result.get("pages_checked", 0),
            "pages_failed": result.get("pages_failed", 0),
            "max_cer": result.get("max_cer", 0.0),
            "avg_cer": result.get("avg_cer", 0.0),
            "status": status,
        }
        if reason:
            doc_entry["reason"] = reason
        doc_results.append(doc_entry)

        if status == "fail":
            failed_docs += 1

        log_payload = {
            "event": "doc_done",
            "run_id": run_id,
            "slug": slug,
            "status": status,
            "pages_failed": result.get("pages_failed", 0),
            "max_cer": result.get("max_cer", 0.0),
            "avg_cer": result.get("avg_cer", 0.0),
        }
        if reason:
            log_payload["reason"] = reason
        append_run_log(run_log_path, log_payload)

        processed_docs += 1
        if args.progress_every > 0 and processed_docs % args.progress_every == 0:
            append_run_log(run_log_path, {
                "event": "progress",
                "run_id": run_id,
                "processed_docs": processed_docs,
                "failed_docs": failed_docs,
            })

    with pages_path.open(pages_mode, encoding="utf-8") as page_out, failures_path.open(failures_mode, encoding="utf-8") as fail_out:
        if args.num_workers > 1:
            ctx = mp.get_context("spawn")
            with ctx.Pool(processes=args.num_workers, initializer=_init_worker, initargs=(args.skip_ocr,)) as pool:
                for result in pool.imap_unordered(functools.partial(_process_document, args_dict=args_dict), doc_rows, chunksize=1):
                    handle_result(result, page_out, fail_out)
                    if args.fail_fast_after > 0 and processed_docs >= args.fail_fast_after and failed_docs > 0:
                        pool.terminate()
                        break
                pool.join()
        else:
            _init_worker(args.skip_ocr)
            for doc_row in doc_rows:
                result = _process_document(doc_row, args_dict)
                handle_result(result, page_out, fail_out)
                if args.fail_fast_after > 0 and processed_docs >= args.fail_fast_after and failed_docs > 0:
                    break

    if args.fail_fast_after > 0 and processed_docs >= args.fail_fast_after and failed_docs > 0:
        summary = {
            "total_docs": len(doc_results),
            "passed": sum(1 for d in doc_results if d.get("status") == "pass"),
            "failed": sum(1 for d in doc_results if d.get("status") == "fail"),
            "skipped": sum(1 for d in doc_results if d.get("status") == "skipped"),
            "errors": sum(1 for d in doc_results if d.get("status") == "error"),
            "cer_threshold": CER_THRESHOLD,
            "documents": doc_results,
        }
        finalize_run(summary, reason="fail_fast")
        print("Fail-fast triggered: failed docs detected.")
        return 2

    conn.close()

    summary = {
        "total_docs": len(doc_results),
        "passed": sum(1 for d in doc_results if d.get("status") == "pass"),
        "failed": sum(1 for d in doc_results if d.get("status") == "fail"),
        "skipped": sum(1 for d in doc_results if d.get("status") == "skipped"),
        "errors": sum(1 for d in doc_results if d.get("status") == "error"),
        "cer_threshold": CER_THRESHOLD,
        "documents": doc_results,
    }

    finalize_run(summary)
    print(f"Wrote summary: {summary_path}")
    print(f"Wrote pages: {pages_path}")
    print(f"Images in: {images_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
