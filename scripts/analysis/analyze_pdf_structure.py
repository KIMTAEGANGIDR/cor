#!/usr/bin/env python3
"""Analyze PDF structure for text extraction planning."""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import re

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Installing PyMuPDF...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "PyMuPDF", "-q"])
    import fitz


@dataclass
class PDFAnalysis:
    """Analysis result for a single PDF."""
    filename: str
    category: str
    total_pages: int = 0
    has_text: bool = False
    text_length: int = 0
    is_scanned: bool = False

    # Structure markers found
    has_memutuskan: bool = False
    has_menetapkan: bool = False
    has_pasal: bool = False
    pasal_count: int = 0
    has_ayat: bool = False
    has_ditetapkan: bool = False

    # Noise patterns
    header_pattern: Optional[str] = None
    footer_pattern: Optional[str] = None
    has_page_numbers: bool = False
    has_watermark: bool = False

    # Sample text
    first_page_sample: str = ""
    body_start_marker: str = ""
    body_end_marker: str = ""

    # Raw text for detailed analysis
    full_text: str = field(default="", repr=False)


def analyze_pdf(pdf_path: Path) -> PDFAnalysis:
    """Analyze a single PDF file."""

    category = pdf_path.parent.name
    analysis = PDFAnalysis(
        filename=pdf_path.name,
        category=category
    )

    try:
        doc = fitz.open(pdf_path)
        analysis.total_pages = len(doc)

        full_text_parts = []
        page_texts = []

        for page_num, page in enumerate(doc):
            text = page.get_text()
            page_texts.append(text)
            full_text_parts.append(text)

            if page_num == 0:
                analysis.first_page_sample = text[:1500] if text else "[NO TEXT - SCANNED?]"

        full_text = "\n".join(full_text_parts)
        analysis.full_text = full_text
        analysis.text_length = len(full_text)
        analysis.has_text = len(full_text.strip()) > 100

        # Check if scanned (very little text for many pages)
        if analysis.total_pages > 0:
            avg_text_per_page = analysis.text_length / analysis.total_pages
            analysis.is_scanned = avg_text_per_page < 100 and analysis.total_pages > 1

        # Find structure markers
        text_upper = full_text.upper()

        # Key markers
        analysis.has_memutuskan = "MEMUTUSKAN" in text_upper
        analysis.has_menetapkan = "MENETAPKAN" in text_upper
        analysis.has_pasal = "PASAL" in text_upper
        analysis.has_ditetapkan = "DITETAPKAN DI" in text_upper

        # Count Pasal occurrences
        pasal_pattern = re.compile(r'\bPASAL\s+\d+', re.IGNORECASE)
        analysis.pasal_count = len(pasal_pattern.findall(full_text))

        # Check for Ayat
        ayat_pattern = re.compile(r'\(\d+\)\s+\w', re.IGNORECASE)
        analysis.has_ayat = bool(ayat_pattern.search(full_text))

        # Detect page numbers
        page_num_pattern = re.compile(r'^\s*-?\s*\d+\s*-?\s*$', re.MULTILINE)
        analysis.has_page_numbers = bool(page_num_pattern.search(full_text))

        # Detect watermark patterns
        watermark_patterns = ['SALINAN', 'RAHASIA', 'DRAFT', 'www.peraturan.go.id']
        for wm in watermark_patterns:
            if wm.upper() in text_upper:
                analysis.has_watermark = True
                break

        # Find repeating headers (text that appears on multiple pages)
        if len(page_texts) >= 3:
            first_lines = []
            for pt in page_texts[:5]:
                lines = [l.strip() for l in pt.split('\n') if l.strip()]
                if lines:
                    first_lines.append(lines[0][:50])

            # Check for common header
            from collections import Counter
            line_counts = Counter(first_lines)
            common = line_counts.most_common(1)
            if common and common[0][1] >= 2:
                analysis.header_pattern = common[0][0]

        # Identify body start marker
        if analysis.has_memutuskan:
            match = re.search(r'MEMUTUSKAN\s*:', full_text, re.IGNORECASE)
            if match:
                analysis.body_start_marker = "MEMUTUSKAN:"
        elif analysis.has_menetapkan:
            analysis.body_start_marker = "MENETAPKAN:"

        # Identify body end marker
        if analysis.has_ditetapkan:
            analysis.body_end_marker = "Ditetapkan di"

        doc.close()

    except Exception as e:
        analysis.first_page_sample = f"ERROR: {str(e)}"

    return analysis


def print_analysis(analysis: PDFAnalysis, verbose: bool = False):
    """Print analysis results."""

    print(f"\n{'='*70}")
    print(f"📄 {analysis.filename}")
    print(f"   카테고리: {analysis.category.upper()}")
    print(f"{'='*70}")

    # Basic info
    print(f"\n📊 기본 정보:")
    print(f"   - 페이지 수: {analysis.total_pages}")
    print(f"   - 텍스트 길이: {analysis.text_length:,} 자")
    print(f"   - 텍스트 추출 가능: {'✅ 예' if analysis.has_text else '❌ 아니오'}")
    print(f"   - 스캔 이미지: {'⚠️ 예 (OCR 필요)' if analysis.is_scanned else '✅ 아니오'}")

    # Structure
    print(f"\n📋 문서 구조:")
    print(f"   - MEMUTUSKAN: {'✅' if analysis.has_memutuskan else '❌'}")
    print(f"   - MENETAPKAN: {'✅' if analysis.has_menetapkan else '❌'}")
    print(f"   - Pasal 수: {analysis.pasal_count}개")
    print(f"   - Ayat 포함: {'✅' if analysis.has_ayat else '❌'}")
    print(f"   - Ditetapkan di: {'✅' if analysis.has_ditetapkan else '❌'}")

    # Markers
    print(f"\n🎯 추출 마커:")
    print(f"   - 본문 시작: {analysis.body_start_marker or '(미확인)'}")
    print(f"   - 본문 종료: {analysis.body_end_marker or '(미확인)'}")

    # Noise
    print(f"\n🔇 노이즈 패턴:")
    print(f"   - 반복 헤더: {analysis.header_pattern or '(없음)'}")
    print(f"   - 페이지 번호: {'✅' if analysis.has_page_numbers else '❌'}")
    print(f"   - 워터마크: {'✅' if analysis.has_watermark else '❌'}")

    # First page sample
    if verbose:
        print(f"\n📝 첫 페이지 샘플 (처음 800자):")
        print("-" * 50)
        sample = analysis.first_page_sample[:800].replace('\n', '\n   ')
        print(f"   {sample}")
        print("-" * 50)


def main():
    """Run PDF structure analysis."""

    # Define samples - mix of old and recent for each category
    samples = {
        "uu": [
            "data/pdfs/uu/uu-no-1-tahun-1946.pdf",      # Old
            "data/pdfs/uu/uu-no-1-tahun-2020.pdf",      # Recent
            "data/pdfs/uu/uu-no-11-tahun-2020.pdf",     # Recent (Cipta Kerja)
            "data/pdfs/uu/uu-no-99-tahun-2024.pdf",     # Very recent
        ],
        "pp": [
            "data/pdfs/pp/pp-no-1-tahun-1946.pdf",      # Old
            "data/pdfs/pp/pp-no-1-tahun-2020.pdf",      # Recent
            "data/pdfs/pp/pp-no-5-tahun-2021.pdf",      # Recent
        ],
        "perpres": [
            "data/pdfs/perpres/perpres-no-1-tahun-1959.pdf",   # Old
            "data/pdfs/perpres/perpres-no-1-tahun-2020.pdf",   # Recent
            "data/pdfs/perpres/perpres-no-10-tahun-2021.pdf",  # Recent
        ],
        "perppu": [
            "data/pdfs/perppu/perppu-no-1-tahun-1963.pdf",   # Old
            "data/pdfs/perppu/perppu-no-1-tahun-1998.pdf",   # More recent
        ],
        "permen": [
            "data/pdfs/permen/permendagri-no-43-tahun-2015.pdf",
            "data/pdfs/permen/permenparekraf-no-18-tahun-2020.pdf",
        ],
        "other": [
            "data/pdfs/other/-no-1-tahun-2022.pdf",
            "data/pdfs/other/-no-1-tahun-2023.pdf",
        ],
    }

    all_analyses = []

    for category, paths in samples.items():
        print(f"\n\n{'#'*70}")
        print(f"# {category.upper()} 분석")
        print(f"{'#'*70}")

        for path_str in paths:
            path = Path(path_str)
            if path.exists():
                analysis = analyze_pdf(path)
                all_analyses.append(analysis)
                print_analysis(analysis, verbose=True)
            else:
                print(f"\n⚠️ 파일 없음: {path_str}")

    # Summary
    print("\n\n")
    print("=" * 70)
    print("📊 전체 요약")
    print("=" * 70)

    total = len(all_analyses)
    text_extractable = sum(1 for a in all_analyses if a.has_text and not a.is_scanned)
    scanned = sum(1 for a in all_analyses if a.is_scanned)
    has_structure = sum(1 for a in all_analyses if a.has_memutuskan or a.has_menetapkan)

    print(f"\n분석된 PDF: {total}개")
    print(f"  - 텍스트 추출 가능: {text_extractable}개 ({100*text_extractable/total:.1f}%)")
    print(f"  - 스캔 이미지 (OCR 필요): {scanned}개 ({100*scanned/total:.1f}%)")
    print(f"  - 구조 마커 있음: {has_structure}개 ({100*has_structure/total:.1f}%)")

    # Common patterns
    print("\n\n공통 패턴:")
    print("-" * 40)

    start_markers = [a.body_start_marker for a in all_analyses if a.body_start_marker]
    end_markers = [a.body_end_marker for a in all_analyses if a.body_end_marker]

    from collections import Counter
    print(f"  본문 시작 마커: {Counter(start_markers).most_common()}")
    print(f"  본문 종료 마커: {Counter(end_markers).most_common()}")

    avg_pasal = sum(a.pasal_count for a in all_analyses) / total if total else 0
    print(f"  평균 Pasal 수: {avg_pasal:.1f}개")

    return all_analyses


if __name__ == "__main__":
    analyses = main()
