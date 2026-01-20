#!/usr/bin/env python3
"""PDF 유효성 검증 및 분리 스크립트.

HTML-PDF (가짜)와 실제 PDF를 분리합니다.
"""

import os
import shutil
import json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import Optional
import fitz  # PyMuPDF


@dataclass
class PDFAnalysis:
    """PDF 분석 결과."""
    path: str
    category: str
    is_valid: bool
    is_scanned: bool  # OCR 필요 여부
    page_count: int
    text_length: int
    has_images: bool
    year: Optional[int]
    error: Optional[str] = None


# HTML-PDF 판별 마커
HTML_MARKERS = ['Beranda', 'Progsun', 'Peraturan', 'E-Harmonisasi', 'Pengundangan']


def extract_year_from_filename(filename: str) -> Optional[int]:
    """파일명에서 연도 추출."""
    import re
    match = re.search(r'tahun-(\d{4})', filename)
    if match:
        return int(match.group(1))
    return None


def analyze_pdf(pdf_path: str) -> PDFAnalysis:
    """단일 PDF 분석."""
    path = Path(pdf_path)
    category = path.parent.name
    year = extract_year_from_filename(path.name)

    try:
        doc = fitz.open(pdf_path)
        page_count = len(doc)

        # 첫 페이지 분석
        first_page = doc[0]
        first_text = first_page.get_text()

        # 전체 텍스트 길이 (처음 3페이지)
        total_text = ""
        for i in range(min(3, page_count)):
            total_text += doc[i].get_text()

        text_length = len(total_text.strip())

        # 이미지 존재 여부
        has_images = len(first_page.get_images()) > 0

        # HTML-PDF 판별
        is_html_pdf = any(marker in first_text for marker in HTML_MARKERS)

        # 스캔 PDF 판별 (텍스트가 거의 없고 이미지가 있으면)
        is_scanned = (text_length < 100 and has_images) or \
                     (text_length < 500 and page_count > 5 and has_images)

        doc.close()

        return PDFAnalysis(
            path=pdf_path,
            category=category,
            is_valid=not is_html_pdf,
            is_scanned=is_scanned and not is_html_pdf,
            page_count=page_count,
            text_length=text_length,
            has_images=has_images,
            year=year,
        )

    except Exception as e:
        return PDFAnalysis(
            path=pdf_path,
            category=category,
            is_valid=False,
            is_scanned=False,
            page_count=0,
            text_length=0,
            has_images=False,
            year=year,
            error=str(e),
        )


def process_category(category: str, base_dir: str = "data/pdfs") -> list[PDFAnalysis]:
    """카테고리별 PDF 처리."""
    pdf_dir = Path(base_dir) / category
    if not pdf_dir.exists():
        return []

    results = []
    pdfs = list(pdf_dir.glob("*.pdf"))

    # 병렬 처리
    with ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(analyze_pdf, str(p)): p for p in pdfs}

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"Error processing {futures[future]}: {e}")

    return results


def move_invalid_pdfs(results: list[PDFAnalysis], invalid_dir: str = "data/pdfs_invalid"):
    """유효하지 않은 PDF 이동."""
    moved_count = 0

    for r in results:
        if not r.is_valid:
            src = Path(r.path)
            dst = Path(invalid_dir) / r.category / src.name

            if src.exists():
                shutil.move(str(src), str(dst))
                moved_count += 1

    return moved_count


def generate_report(results: list[PDFAnalysis]) -> dict:
    """분석 보고서 생성."""
    from collections import defaultdict

    stats = defaultdict(lambda: {
        'total': 0,
        'valid': 0,
        'invalid': 0,
        'scanned': 0,
        'text_extractable': 0,
        'by_year': defaultdict(lambda: {'valid': 0, 'invalid': 0}),
        'avg_pages': 0,
        'avg_text_length': 0,
    })

    for r in results:
        cat = r.category
        stats[cat]['total'] += 1

        if r.is_valid:
            stats[cat]['valid'] += 1
            if r.is_scanned:
                stats[cat]['scanned'] += 1
            else:
                stats[cat]['text_extractable'] += 1
            stats[cat]['avg_pages'] += r.page_count
            stats[cat]['avg_text_length'] += r.text_length
        else:
            stats[cat]['invalid'] += 1

        if r.year:
            if r.is_valid:
                stats[cat]['by_year'][r.year]['valid'] += 1
            else:
                stats[cat]['by_year'][r.year]['invalid'] += 1

    # 평균 계산
    for cat in stats:
        if stats[cat]['valid'] > 0:
            stats[cat]['avg_pages'] /= stats[cat]['valid']
            stats[cat]['avg_text_length'] /= stats[cat]['valid']

    return dict(stats)


if __name__ == "__main__":
    import sys

    categories = ['uu', 'pp', 'perpres', 'perppu', 'permen', 'other', 'keppres']

    all_results = []

    for cat in categories:
        print(f"Processing {cat}...", file=sys.stderr)
        results = process_category(cat)
        all_results.extend(results)
        print(f"  {len(results)} files processed", file=sys.stderr)

    # 보고서 생성
    report = generate_report(all_results)

    # JSON 출력
    print(json.dumps(report, indent=2, default=str))

    # 이동 여부 확인
    if "--move" in sys.argv:
        print("\nMoving invalid PDFs...", file=sys.stderr)
        moved = move_invalid_pdfs(all_results)
        print(f"Moved {moved} invalid PDFs", file=sys.stderr)
