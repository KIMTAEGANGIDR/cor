#!/usr/bin/env python3
"""Analyze failed OCR pages to suggest noise line candidates."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORTS_ROOT = Path("/workspace/cor_reports") if Path("/workspace").exists() else (PROJECT_ROOT / "docs" / "reports")


def normalize_line(line: str) -> str:
    line = " ".join(line.strip().split())
    return line


def should_keep_line(line: str) -> bool:
    if len(line) < 4:
        return False
    alnum = sum(ch.isalnum() for ch in line)
    return alnum >= 2


def iter_failure_files(root: Path) -> list[Path]:
    return sorted(root.glob("seed_*/ocr_compare_failures.jsonl"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze OCR failures for noise candidates.")
    parser.add_argument("--root", default=str(DEFAULT_REPORTS_ROOT / "ocr_compare_full_100_deskew"))
    parser.add_argument("--top", type=int, default=50)
    args = parser.parse_args()

    root = Path(args.root)
    failure_files = iter_failure_files(root)
    if not failure_files:
        print("No failure files found.")
        return 1

    line_counts: dict[str, int] = defaultdict(int)
    line_doc_counts: dict[str, int] = defaultdict(int)
    last_seen_doc: dict[str, str] = {}
    total_failures = 0

    for path in failure_files:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                total_failures += 1
                slug = record.get("slug") or ""
                ocr_raw = record.get("ocr_raw") or ""
                for line in ocr_raw.splitlines():
                    cleaned = normalize_line(line)
                    if not should_keep_line(cleaned):
                        continue
                    line_counts[cleaned] += 1
                    if last_seen_doc.get(cleaned) != slug:
                        line_doc_counts[cleaned] += 1
                        last_seen_doc[cleaned] = slug

    top_lines = sorted(
        line_counts.items(),
        key=lambda item: (line_doc_counts.get(item[0], 0), item[1]),
        reverse=True,
    )[: args.top]

    report_path = root / "noise_candidates.md"
    lines = []
    lines.append("# OCR Noise Candidates (Partial)")
    lines.append("")
    lines.append(f"Generated: {datetime.utcnow().isoformat(timespec='seconds')}Z")
    lines.append(f"Root: {root}")
    lines.append(f"Failures analyzed: {total_failures}")
    lines.append("")
    lines.append("## Suggested Lines (by document coverage)")
    for text, count in top_lines:
        doc_count = line_doc_counts.get(text, 0)
        lines.append(f"- {doc_count} docs, {count} hits: {text}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
