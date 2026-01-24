#!/usr/bin/env python3
"""
헤더 OCR with Surya - PDF 첫 페이지 상단(헤더)만 크롭해서 OCR

Usage:
    python scripts/header_ocr_surya.py --limit 100
    python scripts/header_ocr_surya.py --jenis uu --limit 50
    python scripts/header_ocr_surya.py --status
"""

import argparse
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

# 경로 설정
DB_PATH = Path("/home/tylor/cor/peraturan/data/db/ocr_pipeline.db")
PDF_BASE_DIR = Path("/home/tylor/peraturan_pdfs/peraturan_pdfs")

# 헤더 영역 설정
HEADER_RATIO = 0.35  # 페이지 상단 35%만 추출
IMAGE_DPI = 150  # 해상도 (높을수록 정확하지만 느림)

JENIS_MAP = {
    "uu": "UNDANG-UNDANG",
    "pp": "PERATURAN PEMERINTAH",
    "perpres": "PERATURAN PRESIDEN",
    "permen": "PERATURAN MENTERI",
    "perban": "PERATURAN BADAN/LEMBAGA",
    "perppu": "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG",
}


def get_connection():
    """DB 연결"""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def extract_header_image(pdf_path: str, output_path: str) -> bool:
    """
    PDF 첫 페이지 상단(헤더 영역)을 이미지로 추출

    Returns:
        성공 여부
    """
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]  # 첫 페이지

        # 헤더 영역만 클립
        rect = page.rect
        header_rect = fitz.Rect(
            rect.x0,
            rect.y0,
            rect.x1,
            rect.y0 + rect.height * HEADER_RATIO
        )

        # 이미지로 렌더링
        mat = fitz.Matrix(IMAGE_DPI / 72, IMAGE_DPI / 72)
        pix = page.get_pixmap(matrix=mat, clip=header_rect)
        pix.save(output_path)

        doc.close()
        return True
    except Exception as e:
        return False


def run_surya_ocr_on_image(image_path: str, output_dir: str) -> Optional[dict]:
    """
    이미지에 대해 Surya OCR 실행

    Returns:
        {"text": str, "confidence": float, "lines": list} or None
    """
    try:
        result = subprocess.run(
            ["surya_ocr", image_path, "--output_dir", output_dir],
            capture_output=True,
            text=True,
            timeout=30  # 이미지라서 더 빠름
        )

        if result.returncode != 0:
            return None

        # 결과 파일 찾기
        image_name = Path(image_path).stem
        result_dir = Path(output_dir) / image_name
        result_file = result_dir / "results.json"

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
            # HTML 태그 제거
            text = text.replace("<b>", "").replace("</b>", "")
            text = text.replace("<i>", "").replace("</i>", "")

            if text:
                lines.append(text)
                confidences.append(line.get("confidence", 0.0))

        if not lines:
            return None

        avg_confidence = sum(confidences) / len(confidences)
        full_text = "\n".join(lines)

        return {
            "text": full_text,
            "confidence": avg_confidence,
            "lines": lines,
        }

    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def process_single(args: tuple) -> dict:
    """단일 문서 처리 (병렬 실행용)"""
    doc_id, pdf_path = args
    start_time = time.time()

    with tempfile.TemporaryDirectory(prefix="surya_") as tmp_dir:
        # 1. 헤더 이미지 추출
        header_image = os.path.join(tmp_dir, f"{doc_id}_header.png")

        if not extract_header_image(pdf_path, header_image):
            return {
                "document_id": doc_id,
                "success": False,
                "error": "Image extraction failed",
                "duration_ms": int((time.time() - start_time) * 1000)
            }

        # 2. Surya OCR 실행
        result = run_surya_ocr_on_image(header_image, tmp_dir)

    duration_ms = int((time.time() - start_time) * 1000)

    if result:
        return {
            "document_id": doc_id,
            "success": True,
            "text": result["text"],
            "lines": result["lines"],
            "confidence": result["confidence"],
            "duration_ms": duration_ms
        }
    else:
        return {
            "document_id": doc_id,
            "success": False,
            "error": "OCR failed",
            "duration_ms": duration_ms
        }


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


def get_pending_documents(jenis: Optional[str] = None, limit: Optional[int] = None) -> list:
    """처리할 문서 목록 조회"""
    conn = get_connection()

    query = """
        SELECT d.id as doc_id, d.pdf_path, d.jenis
        FROM documents d
        JOIN headers h ON d.id = h.document_id
        WHERE h.surya_text IS NULL
    """

    if jenis:
        jenis_full = JENIS_MAP.get(jenis.lower(), jenis.upper())
        query += f" AND d.jenis = '{jenis_full}'"

    query += " ORDER BY d.tahun DESC"

    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    conn.close()

    return [dict(row) for row in rows]


def save_result(doc_id: str, result: dict):
    """결과 저장"""
    conn = get_connection()

    if result["success"]:
        conn.execute("""
            UPDATE headers SET
                surya_text = ?,
                surya_confidence = ?,
                lines = ?
            WHERE document_id = ?
        """, (
            result["text"],
            result["confidence"],
            json.dumps(result["lines"], ensure_ascii=False),
            doc_id
        ))

    conn.commit()
    conn.close()


def run_batch(jenis: Optional[str] = None, limit: Optional[int] = None, batch_size: int = 20):
    """배치 처리 (순차)"""
    docs = get_pending_documents(jenis=jenis, limit=limit)

    if not docs:
        print("[*] 처리할 문서가 없습니다.")
        return

    # PDF 경로 변환 및 유효한 것만 필터링
    tasks = []
    skipped = 0
    for doc in docs:
        pdf_path = resolve_pdf_path(doc["pdf_path"])
        if pdf_path:
            tasks.append((doc["doc_id"], pdf_path))
        else:
            skipped += 1

    if not tasks:
        print(f"[*] 유효한 PDF가 없습니다. (스킵: {skipped})")
        return

    print(f"[*] {len(tasks)}개 문서 처리 시작 (스킵: {skipped})")
    print(f"[*] 헤더 영역: 상단 {int(HEADER_RATIO*100)}%, DPI: {IMAGE_DPI}")

    stats = {"total": 0, "success": 0, "failed": 0}
    start_time = time.time()

    for i, (doc_id, pdf_path) in enumerate(tasks, 1):
        stats["total"] += 1

        with tempfile.TemporaryDirectory(prefix="surya_") as tmp_dir:
            # 1. 헤더 이미지 추출
            img_path = os.path.join(tmp_dir, f"{doc_id}_header.png")
            if not extract_header_image(pdf_path, img_path):
                stats["failed"] += 1
                print(f"  [{i}/{len(tasks)}] ✗ {doc_id}: 이미지 추출 실패")
                continue

            # 2. Surya OCR 실행
            try:
                result = subprocess.run(
                    ["surya_ocr", img_path, "--output_dir", tmp_dir],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            except subprocess.TimeoutExpired:
                stats["failed"] += 1
                print(f"  [{i}/{len(tasks)}] ✗ {doc_id}: 타임아웃")
                continue

            if result.returncode != 0:
                stats["failed"] += 1
                print(f"  [{i}/{len(tasks)}] ✗ {doc_id}: OCR 실패")
                continue

            # 3. 결과 파싱
            result_file = Path(tmp_dir) / f"{doc_id}_header" / "results.json"
            if not result_file.exists():
                stats["failed"] += 1
                print(f"  [{i}/{len(tasks)}] ✗ {doc_id}: 결과 없음")
                continue

            try:
                with open(result_file) as f:
                    data = json.load(f)

                key = f"{doc_id}_header"
                if key not in data or not data[key]:
                    stats["failed"] += 1
                    print(f"  [{i}/{len(tasks)}] ✗ {doc_id}: 빈 결과")
                    continue

                page_data = data[key][0]
                text_lines = page_data.get("text_lines", [])

                lines = []
                confidences = []
                for line in text_lines:
                    text = line.get("text", "").strip()
                    text = text.replace("<b>", "").replace("</b>", "")
                    text = text.replace("<i>", "").replace("</i>", "")
                    if text:
                        lines.append(text)
                        confidences.append(line.get("confidence", 0.0))

                if lines:
                    avg_conf = sum(confidences) / len(confidences)
                    full_text = "\n".join(lines)
                    save_result(doc_id, {
                        "success": True,
                        "text": full_text,
                        "lines": lines,
                        "confidence": avg_conf
                    })
                    stats["success"] += 1

                    elapsed = time.time() - start_time
                    rate = stats["total"] / elapsed if elapsed > 0 else 0
                    print(f"  [{i}/{len(tasks)}] ✓ {doc_id} (conf: {avg_conf:.2f}, {rate:.1f}/s)")
                else:
                    stats["failed"] += 1
                    print(f"  [{i}/{len(tasks)}] ✗ {doc_id}: 텍스트 없음")

            except Exception as e:
                stats["failed"] += 1
                print(f"  [{i}/{len(tasks)}] ✗ {doc_id}: {str(e)}")

    elapsed = time.time() - start_time
    rate = stats["total"] / elapsed if elapsed > 0 else 0

    print(f"\n[*] 완료! 성공: {stats['success']}, 실패: {stats['failed']}")
    print(f"[*] 소요 시간: {elapsed:.1f}초, 속도: {rate:.2f}개/초")


def show_status():
    """상태 확인"""
    conn = get_connection()

    try:
        surya_done = conn.execute("SELECT COUNT(*) FROM headers WHERE surya_text IS NOT NULL").fetchone()[0]
    except sqlite3.OperationalError:
        surya_done = 0

    total = conn.execute("SELECT COUNT(*) FROM headers").fetchone()[0]

    print("=== 헤더 OCR 상태 (Surya) ===")
    print(f"전체 헤더:    {total:,}")
    print(f"Surya 완료:   {surya_done:,}")
    print(f"Surya 대기:   {total - surya_done:,}")

    print("\n=== 법령 유형별 ===")
    rows = conn.execute("""
        SELECT d.jenis,
               COUNT(*) as total,
               SUM(CASE WHEN h.surya_text IS NOT NULL THEN 1 ELSE 0 END) as surya_done
        FROM documents d
        JOIN headers h ON d.id = h.document_id
        GROUP BY d.jenis
        ORDER BY total DESC
    """).fetchall()

    for row in rows:
        pct = (row["surya_done"] / row["total"] * 100) if row["total"] > 0 else 0
        print(f"  {row['jenis']:40s}: {row['surya_done']:6,} / {row['total']:6,} ({pct:5.1f}%)")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Header OCR with Surya (Cropped)")
    parser.add_argument("--limit", "-l", type=int, help="최대 처리 문서 수")
    parser.add_argument("--jenis", "-j", type=str, help="법령 유형 필터 (uu, pp, permen 등)")
    parser.add_argument("--status", "-s", action="store_true", help="상태 확인")

    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        run_batch(jenis=args.jenis, limit=args.limit)


if __name__ == "__main__":
    main()
