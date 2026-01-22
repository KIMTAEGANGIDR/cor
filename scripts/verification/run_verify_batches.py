#!/usr/bin/env python3
"""Run verification in batches and write per-batch reports."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = PROJECT_ROOT / "scripts" / "verification" / "verify_extracted_text.py"


def write_failed_pages(pages_path: Path, failed_path: Path) -> int:
    count = 0
    with pages_path.open("r", encoding="utf-8") as src, failed_path.open("w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("status") == "fail":
                dst.write(json.dumps(obj, ensure_ascii=False) + "\n")
                count += 1
    return count


def run_batch(args: argparse.Namespace, batch_index: int, offset: int) -> None:
    batch_id = f"batch_{batch_index:02d}"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = output_dir / f"verification_summary_{batch_id}.json"
    pages_path = output_dir / f"verification_pages_{batch_id}.jsonl"
    run_log_path = output_dir / f"verification_runlog_{batch_id}.jsonl"
    failed_path = output_dir / f"verification_failed_pages_{batch_id}.jsonl"

    cmd = [
        sys.executable,
        str(VERIFY_SCRIPT),
        "--db",
        args.db,
        "--pdf-base",
        args.pdf_base,
        "--limit",
        str(args.batch_size),
        "--offset",
        str(offset),
        "--summary",
        str(summary_path),
        "--pages",
        str(pages_path),
        "--run-log",
        str(run_log_path),
        "--progress-every",
        str(args.progress_every),
    ]
    if args.resume_batch:
        cmd.append("--resume")

    print(f"[{batch_id}] Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    failed_count = write_failed_pages(pages_path, failed_path)
    print(f"[{batch_id}] Failed pages: {failed_count} -> {failed_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run verification in batches")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--batches", type=int, default=32)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument("--db", default=str(PROJECT_ROOT / "peraturan" / "data" / "peraturan.db"))
    parser.add_argument("--pdf-base", default=str(PROJECT_ROOT / "peraturan" / "data"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "docs" / "reports" / "verification_batches"))
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--resume-batch", action="store_true")
    args = parser.parse_args()

    for i in range(args.batches):
        batch_index = i + 1
        offset = args.start_offset + (i * args.batch_size)
        run_batch(args, batch_index, offset)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
