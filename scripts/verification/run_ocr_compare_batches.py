#!/usr/bin/env python3
"""Run OCR compare in batches and enforce pass-rate threshold."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OCR_COMPARE_SCRIPT = PROJECT_ROOT / "scripts" / "verification" / "sample_ocr_compare.py"


def load_pass_rate(summary_path: Path) -> float | None:
    if not summary_path.exists():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    total = data.get("total_docs", 0)
    passed = data.get("passed", 0)
    return (passed / total) if total else 0.0


def run_batch(args: argparse.Namespace, batch_index: int, offset: int) -> None:
    batch_id = f"batch_{batch_index:03d}"
    batch_dir = Path(args.output_root) / f"ocr_compare_{batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    summary_path = batch_dir / "ocr_compare_summary.json"
    run_log_path = batch_dir / "ocr_compare_runlog.jsonl"

    if args.resume:
        pass_rate = load_pass_rate(summary_path)
        if pass_rate is not None and pass_rate >= args.target_pass_rate:
            print(f"[{batch_id}] Skipping (pass_rate={pass_rate:.4f})")
            return
        if pass_rate is not None and pass_rate < args.target_pass_rate and not args.rerun_failed:
            raise SystemExit(
                f"[{batch_id}] Existing summary below target ({pass_rate:.4f} < {args.target_pass_rate:.4f}). "
                "Fix pipeline and rerun with --rerun-failed."
            )

    cmd = [
        sys.executable,
        str(OCR_COMPARE_SCRIPT),
        "--db",
        args.db,
        "--pdf-base",
        args.pdf_base,
        "--output-dir",
        str(batch_dir),
        "--limit",
        str(args.batch_size),
        "--offset",
        str(offset),
        "--order-by",
        args.order_by,
        "--image-dpi",
        str(args.image_dpi),
        "--num-workers",
        str(args.num_workers),
        "--run-log",
        str(run_log_path),
        "--progress-every",
        str(args.progress_every),
    ]
    if args.max_pages > 0:
        cmd.extend(["--max-pages", str(args.max_pages)])
    if args.deskew:
        cmd.append("--deskew")
    if args.skip_ocr:
        cmd.append("--skip-ocr")
    if args.resume:
        cmd.append("--resume")
    if args.fail_fast_after > 0:
        cmd.extend(["--fail-fast-after", str(args.fail_fast_after)])

    print(f"[{batch_id}] Running: {' '.join(cmd)}")
    env = os.environ.copy()
    if args.disable_model_source_check:
        env["DISABLE_MODEL_SOURCE_CHECK"] = "True"
    if args.cpu_threads_per_worker > 0:
        threads = str(args.cpu_threads_per_worker)
        env["OMP_NUM_THREADS"] = threads
        env["MKL_NUM_THREADS"] = threads
        env["OPENBLAS_NUM_THREADS"] = threads
        env["NUMEXPR_NUM_THREADS"] = threads
    result = subprocess.run(cmd, check=False, env=env)
    if result.returncode not in (0, 2):
        raise SystemExit(f"[{batch_id}] OCR compare failed with exit code {result.returncode}")

    pass_rate = load_pass_rate(summary_path)
    if pass_rate is None:
        raise SystemExit(f"[{batch_id}] Summary missing or unreadable: {summary_path}")
    if pass_rate < args.target_pass_rate:
        raise SystemExit(
            f"[{batch_id}] Pass rate below target ({pass_rate:.4f} < {args.target_pass_rate:.4f}). "
            "Fix pipeline and rerun with --rerun-failed."
        )
    print(f"[{batch_id}] Pass rate OK: {pass_rate:.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OCR compare in batches")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--batches", type=int, default=320)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument("--db", default=str(PROJECT_ROOT / "peraturan" / "data" / "peraturan.db"))
    parser.add_argument("--pdf-base", default=str(PROJECT_ROOT / "peraturan" / "data"))
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "docs" / "reports" / "ocr_compare_batches"))
    parser.add_argument("--target-pass-rate", type=float, default=0.999)
    parser.add_argument("--order-by", default="tahun DESC, slug")
    parser.add_argument("--image-dpi", type=int, default=150)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--cpu-threads-per-worker", type=int, default=1)
    parser.add_argument("--max-utilization", action="store_true")
    parser.add_argument("--disable-model-source-check", action="store_true")
    parser.add_argument("--deskew", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rerun-failed", action="store_true")
    parser.add_argument("--fail-fast-after", type=int, default=0)
    args = parser.parse_args()

    if args.max_utilization:
        if args.num_workers <= 0:
            args.num_workers = min(16, os.cpu_count() or 1)
        if args.image_dpi == 150:
            args.image_dpi = 200
        if args.progress_every == 25:
            args.progress_every = 10
        if not args.deskew:
            args.deskew = True
        if args.cpu_threads_per_worker == 1:
            args.cpu_threads_per_worker = 1
        if not args.disable_model_source_check:
            args.disable_model_source_check = True

    if args.num_workers <= 0:
        args.num_workers = max(1, (os.cpu_count() or 1))

    for i in range(args.batches):
        batch_index = i + 1
        offset = args.start_offset + (i * args.batch_size)
        run_batch(args, batch_index, offset)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
