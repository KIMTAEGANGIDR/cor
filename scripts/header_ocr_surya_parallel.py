#!/usr/bin/env python3
"""
헤더 OCR with Surya - 8병렬 처리
각 워커가 독립적으로 문서를 가져와서 처리 (lock-free)

Usage:
    python scripts/header_ocr_surya_parallel.py --workers 8
    python scripts/header_ocr_surya_parallel.py --workers 8 --limit 1000
    python scripts/header_ocr_surya_parallel.py --status
"""

import argparse
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import Manager
from pathlib import Path
from typing import Optional
import signal
import sys

import fitz  # PyMuPDF

# 경로 설정
DB_PATH = Path("/home/tylor/cor/peraturan/data/db/ocr_pipeline.db")
PDF_BASE_DIR = Path("/home/tylor/peraturan_pdfs/peraturan_pdfs")

# 헤더 영역 설정
HEADER_RATIO = 0.35
IMAGE_DPI = 150

# 전역 종료 플래그
shutdown_flag = False


def signal_handler(signum, frame):
    global shutdown_flag
    print("\n[!] 종료 신호 받음. 현재 작업 완료 후 종료...")
    shutdown_flag = True


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def get_connection():
    """DB 연결 (각 프로세스별로 새로 생성)"""
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def extract_header_image(pdf_path: str, output_path: str) -> bool:
    """PDF 첫 페이지 상단(헤더 영역)을 이미지로 추출"""
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        rect = page.rect
        header_rect = fitz.Rect(
            rect.x0, rect.y0, rect.x1,
            rect.y0 + rect.height * HEADER_RATIO
        )
        mat = fitz.Matrix(IMAGE_DPI / 72, IMAGE_DPI / 72)
        pix = page.get_pixmap(matrix=mat, clip=header_rect)
        pix.save(output_path)
        doc.close()
        return True
    except Exception:
        return False


def run_surya_ocr(image_path: str, output_dir: str) -> Optional[dict]:
    """Surya OCR 실행"""
    try:
        result = subprocess.run(
            ["surya_ocr", image_path, "--output_dir", output_dir],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode != 0:
            return None

        image_name = Path(image_path).stem
        result_file = Path(output_dir) / image_name / "results.json"

        if not result_file.exists():
            return None

        with open(result_file) as f:
            data = json.load(f)

        if image_name not in data or not data[image_name]:
            return None

        page_data = data[image_name][0]
        text_lines = page_data.get("text_lines", [])

        if not text_lines:
            return None

        lines = []
        confidences = []
        for line in text_lines:
            text = line.get("text", "").strip()
            text = text.replace("<b>", "").replace("</b>", "")
            text = text.replace("<i>", "").replace("</i>", "")
            if text:
                lines.append(text)
                confidences.append(line.get("confidence", 0.0))

        if not lines:
            return None

        return {
            "text": "\n".join(lines),
            "confidence": sum(confidences) / len(confidences),
            "lines": lines,
        }
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def resolve_pdf_path(pdf_path: str) -> Optional[str]:
    """DB의 상대 경로를 실제 경로로 변환"""
    if pdf_path.startswith("peraturan/data/pdfs/"):
        relative = pdf_path.replace("peraturan/data/pdfs/", "")
        full_path = PDF_BASE_DIR / relative
        if full_path.exists():
            return str(full_path)
    if os.path.exists(pdf_path):
        return pdf_path
    return None


def claim_and_process_batch(worker_id: int, batch_size: int = 10) -> dict:
    """
    배치 단위로 문서를 claim하고 처리
    각 워커가 독립적으로 DB에서 문서를 가져감 (경쟁 방식)
    """
    stats = {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    while not shutdown_flag:
        conn = get_connection()

        # 처리할 문서 가져오기 (LIMIT으로 배치 크기 제한)
        try:
            rows = conn.execute("""
                SELECT d.id as doc_id, d.pdf_path, d.jenis
                FROM documents d
                JOIN headers h ON d.id = h.document_id
                WHERE h.surya_text IS NULL
                ORDER BY RANDOM()
                LIMIT ?
            """, (batch_size,)).fetchall()
        except sqlite3.OperationalError as e:
            conn.close()
            time.sleep(1)
            continue

        if not rows:
            conn.close()
            break

        for row in rows:
            if shutdown_flag:
                break

            doc_id = row["doc_id"]
            pdf_path = resolve_pdf_path(row["pdf_path"])

            if not pdf_path:
                stats["skipped"] += 1
                continue

            # 처리 시작
            with tempfile.TemporaryDirectory(prefix=f"surya_w{worker_id}_") as tmp_dir:
                img_path = os.path.join(tmp_dir, f"{doc_id}_header.png")

                if not extract_header_image(pdf_path, img_path):
                    stats["failed"] += 1
                    continue

                result = run_surya_ocr(img_path, tmp_dir)

                if result:
                    # DB 저장 (재시도 로직 포함)
                    for attempt in range(3):
                        try:
                            conn.execute("""
                                UPDATE headers SET
                                    surya_text = ?,
                                    surya_confidence = ?,
                                    lines = ?
                                WHERE document_id = ?
                                AND surya_text IS NULL
                            """, (
                                result["text"],
                                result["confidence"],
                                json.dumps(result["lines"], ensure_ascii=False),
                                doc_id
                            ))
                            conn.commit()
                            stats["success"] += 1
                            break
                        except sqlite3.OperationalError:
                            time.sleep(0.5 * (attempt + 1))
                    else:
                        stats["failed"] += 1
                else:
                    stats["failed"] += 1

            stats["processed"] += 1

        conn.close()

    return stats


def worker_process(args: tuple) -> dict:
    """워커 프로세스 메인 함수"""
    worker_id, total_limit = args

    stats = {"worker_id": worker_id, "processed": 0, "success": 0, "failed": 0, "skipped": 0}
    batch_size = 5  # 한 번에 처리할 문서 수

    while not shutdown_flag:
        if total_limit and stats["processed"] >= total_limit:
            break

        batch_stats = claim_and_process_batch(worker_id, batch_size)

        stats["processed"] += batch_stats["processed"]
        stats["success"] += batch_stats["success"]
        stats["failed"] += batch_stats["failed"]
        stats["skipped"] += batch_stats["skipped"]

        if batch_stats["processed"] == 0:
            break  # 더 이상 처리할 문서 없음

    return stats


def run_parallel(workers: int = 8, limit: Optional[int] = None):
    """병렬 처리 실행"""
    conn = get_connection()

    # 현재 상태 확인
    total = conn.execute("SELECT COUNT(*) FROM headers").fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM headers WHERE surya_text IS NOT NULL").fetchone()[0]
    pending = total - done
    conn.close()

    if pending == 0:
        print("[*] 처리할 문서가 없습니다.")
        return

    if limit:
        pending = min(pending, limit)

    per_worker_limit = (limit // workers + 1) if limit else None

    print(f"[*] 병렬 헤더 OCR 시작")
    print(f"    워커 수: {workers}")
    print(f"    처리 대상: {pending:,}개")
    print(f"    헤더 영역: 상단 {int(HEADER_RATIO*100)}%, DPI: {IMAGE_DPI}")
    print()

    start_time = time.time()

    # 워커 실행
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(worker_process, (i, per_worker_limit)): i
            for i in range(workers)
        }

        total_stats = {"processed": 0, "success": 0, "failed": 0}

        try:
            for future in as_completed(futures):
                worker_id = futures[future]
                try:
                    stats = future.result()
                    total_stats["processed"] += stats["processed"]
                    total_stats["success"] += stats["success"]
                    total_stats["failed"] += stats["failed"]

                    elapsed = time.time() - start_time
                    rate = total_stats["processed"] / elapsed if elapsed > 0 else 0

                    print(f"  [Worker {worker_id}] 완료: {stats['success']} 성공, {stats['failed']} 실패")
                except Exception as e:
                    print(f"  [Worker {worker_id}] 오류: {e}")
        except KeyboardInterrupt:
            print("\n[!] 중단됨")

    elapsed = time.time() - start_time
    rate = total_stats["processed"] / elapsed if elapsed > 0 else 0

    print()
    print(f"[*] 완료!")
    print(f"    처리: {total_stats['processed']:,}개")
    print(f"    성공: {total_stats['success']:,}개")
    print(f"    실패: {total_stats['failed']:,}개")
    print(f"    소요: {elapsed:.1f}초")
    print(f"    속도: {rate:.2f}개/초")


def show_status():
    """상태 확인"""
    conn = get_connection()

    total = conn.execute("SELECT COUNT(*) FROM headers").fetchone()[0]
    surya_done = conn.execute("SELECT COUNT(*) FROM headers WHERE surya_text IS NOT NULL").fetchone()[0]
    pending = total - surya_done

    print("=== 헤더 OCR 상태 (Surya 병렬) ===")
    print(f"전체 헤더:    {total:,}")
    print(f"Surya 완료:   {surya_done:,} ({surya_done/total*100:.1f}%)")
    print(f"Surya 대기:   {pending:,}")

    # 예상 시간 (0.8개/초 기준 - 8워커)
    if pending > 0:
        hours = pending / 0.8 / 3600
        print(f"\n예상 완료:    {hours:.1f}시간 (8워커, 0.8/s 기준)")

    print("\n=== 법령 유형별 ===")
    rows = conn.execute("""
        SELECT d.jenis,
               COUNT(*) as total,
               SUM(CASE WHEN h.surya_text IS NOT NULL THEN 1 ELSE 0 END) as done
        FROM documents d
        JOIN headers h ON d.id = h.document_id
        GROUP BY d.jenis
        ORDER BY total DESC
    """).fetchall()

    for row in rows:
        pct = (row["done"] / row["total"] * 100) if row["total"] > 0 else 0
        bar_len = int(pct / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {row['jenis'][:30]:30s} [{bar}] {row['done']:>6,}/{row['total']:>6,} ({pct:5.1f}%)")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Header OCR with Surya (Parallel)")
    parser.add_argument("--workers", "-w", type=int, default=8, help="병렬 워커 수 (기본: 8)")
    parser.add_argument("--limit", "-l", type=int, help="최대 처리 문서 수")
    parser.add_argument("--status", "-s", action="store_true", help="상태 확인")

    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        run_parallel(workers=args.workers, limit=args.limit)


if __name__ == "__main__":
    main()
