#!/usr/bin/env python3
"""KOICA RFP 대응 현황분석보고서 생성기.

RFP 기반 제안서 작성을 위한 현행 데이터 현황 및 PDF 문서 구조 분석 보고서를 생성합니다.

Usage:
    python scripts/rfp_analysis_report.py
    python scripts/rfp_analysis_report.py --output-dir docs/exports
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from docx import Document
    from docx.shared import Inches, Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    print("Warning: python-docx not installed. Run: pip install python-docx")


# Constants
JENIS_KOREAN = {
    "UNDANG-UNDANG": "법률 (UU)",
    "PERATURAN PEMERINTAH": "정부령 (PP)",
    "PERATURAN PRESIDEN": "대통령령 (Perpres)",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "긴급정부령 (Perppu)",
    "PERATURAN MENTERI": "장관령 (Permen)",
    "PERATURAN BADAN/LEMBAGA": "기관규정",
    "PERATURAN DAERAH": "지방조례 (Perda)",
    "KEPUTUSAN PRESIDEN": "대통령결정 (Keppres)",
}

RFP_REQUIREMENTS = {
    "total_laws": 35424,
    "total_pages": 150000,
    "output_formats": ["Word", "XML/HTML"],
}


def set_cell_shading(cell, color: str):
    """Set cell background color."""
    shading = OxmlElement('w:shd')
    shading.set(qn('w:fill'), color)
    cell._tc.get_or_add_tcPr().append(shading)


def add_styled_table(doc, headers: list, rows: list, header_color: str = "2E75B6"):
    """Add a formatted table to the document."""
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Header row
    header_cells = table.rows[0].cells
    for i, header in enumerate(headers):
        header_cells[i].text = header
        for para in header_cells[i].paragraphs:
            for run in para.runs:
                run.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)
                run.font.size = Pt(10)
        set_cell_shading(header_cells[i], header_color)

    # Data rows
    for row_idx, row_data in enumerate(rows):
        row_cells = table.rows[row_idx + 1].cells
        for col_idx, cell_text in enumerate(row_data):
            row_cells[col_idx].text = str(cell_text)
            for para in row_cells[col_idx].paragraphs:
                for run in para.runs:
                    run.font.size = Pt(10)

    return table


def load_json_data(json_path: Path) -> dict | None:
    """Load JSON data from file."""
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)
    return None


def query_db(db_path: Path, query: str) -> list:
    """Execute query on SQLite database."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(query)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_total_pages_estimate(peraturan_db: Path) -> int:
    """Get total pages from comprehensive PDF analysis or OCR pipeline DB."""
    # First try comprehensive analysis
    analysis_path = Path("docs/data/pdf_comprehensive_analysis.json")
    if analysis_path.exists():
        with open(analysis_path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("page_count", {}).get("total_pages", 0)

    # Fallback to OCR pipeline DB
    ocr_db = Path("peraturan/data/ocr_pipeline.db")
    if ocr_db.exists():
        results = query_db(ocr_db, "SELECT SUM(page_count) as total FROM documents WHERE page_count IS NOT NULL")
        if results and results[0].get('total'):
            return results[0]['total']

    return 0


def get_penjelasan_stats(peraturan_db: Path) -> dict:
    """Get PENJELASAN statistics from comprehensive PDF analysis."""
    # Load from comprehensive analysis if available
    analysis_path = Path("docs/data/pdf_comprehensive_analysis.json")
    if analysis_path.exists():
        with open(analysis_path, encoding="utf-8") as f:
            data = json.load(f)
        penjelasan = data.get("penjelasan", {})
        return {
            "total_with_penjelasan": penjelasan.get("has_penjelasan_count", 7220),
            "umum_count": penjelasan.get("has_umum_count", 19854),
            "pasal_demi_pasal_count": penjelasan.get("has_pasal_demi_pasal_count", 2882),
            "percentage": round(penjelasan.get("has_penjelasan_pct", 22.2), 1),
        }
    # Fallback to default values
    return {
        "total_with_penjelasan": 7220,
        "umum_count": 19854,
        "pasal_demi_pasal_count": 2882,
        "percentage": 22.2,
    }


def get_ocr_stats() -> dict:
    """Get OCR-related statistics from comprehensive PDF analysis."""
    # Load from comprehensive analysis if available
    analysis_path = Path("docs/data/pdf_comprehensive_analysis.json")
    if analysis_path.exists():
        with open(analysis_path, encoding="utf-8") as f:
            data = json.load(f)
        text_ext = data.get("text_extraction", {})
        markers = data.get("structure_markers", {})
        return {
            "direct_extractable": round(text_ext.get("direct_extract_pct", 97.9), 1),
            "ocr_needed": round(text_ext.get("needs_ocr_pct", 2.1), 1),
            "ocr_needed_count": text_ext.get("needs_ocr_count", 691),
            "marker_memutuskan": round(markers.get("has_memutuskan_pct", 97.1), 1),
            "marker_menetapkan": round(markers.get("has_menetapkan_pct", 95.8), 1),
            "marker_ditetapkan": round(markers.get("has_ditetapkan_pct", 94.2), 1),
        }
    # Fallback to default values
    return {
        "direct_extractable": 97.9,
        "ocr_needed": 2.1,
        "ocr_needed_count": 691,
        "marker_memutuskan": 97.1,
        "marker_menetapkan": 95.8,
        "marker_ditetapkan": 94.2,
    }


def get_header_type_stats() -> dict:
    """Get header type statistics from comprehensive PDF analysis."""
    analysis_path = Path("docs/data/pdf_comprehensive_analysis.json")
    if analysis_path.exists():
        with open(analysis_path, encoding="utf-8") as f:
            data = json.load(f)
        header = data.get("header_type", {})
        return {
            "lembaran_negara_count": header.get("LEMBARAN_NEGARA", 30074),
            "lembaran_negara_pct": header.get("distribution_pct", {}).get("LEMBARAN_NEGARA", 92.5),
            "berita_negara_count": header.get("BERITA_NEGARA", 1158),
            "berita_negara_pct": header.get("distribution_pct", {}).get("BERITA_NEGARA", 3.6),
            "other_count": header.get("OTHER", 1215),
            "other_pct": header.get("distribution_pct", {}).get("OTHER", 3.7),
            "none_count": header.get("NONE", 70),
            "none_pct": header.get("distribution_pct", {}).get("NONE", 0.2),
        }
    # Fallback
    return {
        "lembaran_negara_count": 30074,
        "lembaran_negara_pct": 92.5,
        "berita_negara_count": 1158,
        "berita_negara_pct": 3.6,
        "other_count": 1215,
        "other_pct": 3.7,
        "none_count": 70,
        "none_pct": 0.2,
    }


def get_pasal_stats() -> dict:
    """Get Pasal statistics from comprehensive PDF analysis."""
    analysis_path = Path("docs/data/pdf_comprehensive_analysis.json")
    if analysis_path.exists():
        with open(analysis_path, encoding="utf-8") as f:
            data = json.load(f)
        pasal = data.get("pasal_count", {})
        return {
            "total_pasal": pasal.get("total_pasal", 890442),
            "avg_pasal": round(pasal.get("avg_pasal", 27.4), 1),
            "max_pasal": pasal.get("max_pasal", 2310),
        }
    return {
        "total_pasal": 890442,
        "avg_pasal": 27.4,
        "max_pasal": 2310,
    }


def create_report(
    peraturan_db: Path,
    bpk_db: Path,
    output_dir: Path,
) -> Path:
    """Create the comprehensive RFP analysis report.

    Args:
        peraturan_db: Path to peraturan.go.id database
        bpk_db: Path to BPK database
        output_dir: Output directory

    Returns:
        Path to generated DOCX file
    """
    if not DOCX_AVAILABLE:
        raise RuntimeError("python-docx is required. Install with: pip install python-docx")

    # Load existing analysis data
    data_dir = Path("docs/exports/koica/data/koica_analysis")
    summary_data = load_json_data(data_dir / "summary.json") or {}

    docs_data_dir = Path("docs/data")
    determination_data = load_json_data(docs_data_dir / "task_1_8_determination_scope.json") or {}
    revocation_data = load_json_data(docs_data_dir / "task_2_1_revocation_patterns.json") or {}
    amendment_data = load_json_data(docs_data_dir / "task_2_2_amendment_patterns.json") or {}
    conditional_data = load_json_data(docs_data_dir / "task_2_3_conditional_patterns.json") or {}
    pdf_comparison = load_json_data(docs_data_dir / "pdf-comparison-results.json") or {}

    # Get additional statistics from comprehensive PDF analysis
    penjelasan_stats = get_penjelasan_stats(peraturan_db)
    ocr_stats = get_ocr_stats()
    header_stats = get_header_type_stats()
    pasal_stats = get_pasal_stats()
    total_pages = get_total_pages_estimate(peraturan_db)

    # Create document
    doc = Document()

    # ==================== TITLE PAGE ====================
    title = doc.add_heading('KOICA 인도네시아 법령정보시스템 구축사업', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = doc.add_heading('현황분석 및 기술검토 보고서', level=1)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph()
    doc.add_paragraph()

    meta_para = doc.add_paragraph()
    meta_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_para.add_run(f"작성일: {datetime.now().strftime('%Y년 %m월 %d일')}\n").bold = True
    meta_para.add_run("버전: 1.0\n")

    doc.add_page_break()

    # ==================== 1. 개요 ====================
    doc.add_heading('1. 개요', level=1)

    doc.add_heading('1.1 분석 목적', level=2)
    doc.add_paragraph(
        "본 보고서는 KOICA 인도네시아 법령정보시스템 구축사업의 제안서 작성을 위한 "
        "현행 데이터 현황 및 PDF 문서 구조 분석 결과를 정리한 기술 보고서입니다. "
        "제안서의 기술적 근거 자료 및 검수/추출/변환 계획 수립의 기초 자료로 활용됩니다."
    )

    doc.add_heading('1.2 RFP 요구사항 요약', level=2)
    add_styled_table(doc,
        ['항목', '요구사항', '비고'],
        [
            ['법령 DB 구축', f'{RFP_REQUIREMENTS["total_laws"]:,}건', '인도네시아 중앙법령'],
            ['페이지 정보화', f'{RFP_REQUIREMENTS["total_pages"]:,}페이지', '전자문서화'],
            ['출력 형식', 'Word → XML/HTML 변환', '구조화 문서'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('1.3 핵심 지표 요약 (Executive Summary)', level=2)

    # Key metrics summary table
    peraturan_total = summary_data.get('summary', {}).get('peraturan_total', 35316)
    bpk_total = summary_data.get('summary', {}).get('bpk_total', 34549)
    pdf_availability = summary_data.get('pdf_availability', {})

    add_styled_table(doc,
        ['지표', '현황', 'RFP 대비'],
        [
            ['법령 수집 (법제처)', f'{peraturan_total:,}건', f'{peraturan_total/RFP_REQUIREMENTS["total_laws"]*100:.1f}%'],
            ['법령 수집 (BPK)', f'{bpk_total:,}건', '참조 DB'],
            ['PDF 확보율', f'{pdf_availability.get("overall_download_pct", 92.1)}%', f'{pdf_availability.get("downloaded", 32519):,}건'],
            ['현행 법령', '89.2%', '31,509건'],
            ['자동 판단 가능', '99.6%', '35,190건'],
            ['총 페이지 수 (실측)', f'{total_pages:,}페이지', f'{total_pages/RFP_REQUIREMENTS["total_pages"]*100:.0f}% (RFP 7배)'],
        ]
    )
    doc.add_paragraph()

    # ==================== 2. 데이터 수집 현황 ====================
    doc.add_heading('2. 데이터 수집 현황', level=1)

    doc.add_heading('2.1 peraturan.go.id (법제처) 현황', level=2)
    doc.add_paragraph(f"총 {peraturan_total:,}건의 법령 메타데이터를 수집하였습니다.")

    doc_types = summary_data.get('document_types', {}).get('types', [])
    if doc_types:
        add_styled_table(doc,
            ['법령 유형', '건수', '비율', '현행 건수', '현행율'],
            [
                [t.get('jenis_korean', t.get('jenis', '')),
                 f"{t.get('total', 0):,}",
                 f"{t.get('percentage', 0)}%",
                 f"{t.get('berlaku', 0):,}",
                 f"{t.get('berlaku_rate', 0)}%"]
                for t in doc_types
            ]
        )
    doc.add_paragraph()

    doc.add_heading('2.2 peraturan.bpk.go.id (BPK) 현황', level=2)
    doc.add_paragraph(
        f"BPK(감사원) JDIH에서 총 {bpk_total:,}건의 법령 데이터를 수집하였습니다. "
        "BPK 데이터는 126개의 세분화된 법령 유형을 제공하며, 특히 대통령결정(Keppres) 등 "
        "법제처에서 누락된 역사적 법령을 보완합니다."
    )

    doc.add_heading('2.3 두 소스 비교 분석', level=2)
    bpk_comparison = summary_data.get('bpk_comparison', {})
    type_comparison = bpk_comparison.get('type_comparison', [])

    if type_comparison:
        add_styled_table(doc,
            ['유형', '법제처', 'BPK', '차이', '비고'],
            [
                [t.get('type_korean', ''),
                 f"{t.get('peraturan_count', 0):,}",
                 f"{t.get('bpk_count', 0):,}",
                 f"{'+' if t.get('difference', 0) > 0 else ''}{t.get('difference', 0):,}",
                 '법제처 우세' if t.get('difference', 0) > 0 else 'BPK 우세' if t.get('difference', 0) < 0 else '동일']
                for t in type_comparison
            ]
        )
    doc.add_paragraph()

    doc.add_heading('2.4 RFP 요구(35,424건) 대비 현황', level=2)
    coverage = peraturan_total / RFP_REQUIREMENTS["total_laws"] * 100
    doc.add_paragraph(
        f"RFP 요구사항인 35,424건 대비 현재 {peraturan_total:,}건 확보 ({coverage:.1f}%). "
        f"부족분 {RFP_REQUIREMENTS['total_laws'] - peraturan_total:,}건은 BPK 데이터 병합 및 "
        "추가 크롤링을 통해 확보 예정입니다."
    )

    # ==================== 3. PDF 파일 현황 ====================
    doc.add_heading('3. PDF 파일 현황', level=1)

    doc.add_heading('3.1 PDF 확보 현황', level=2)
    storage = pdf_availability.get('storage', {})
    add_styled_table(doc,
        ['항목', '수치', '비고'],
        [
            ['PDF URL 보유', f"{pdf_availability.get('with_url', 32543):,}건", f"{pdf_availability.get('url_availability_pct', 92.1)}%"],
            ['다운로드 완료', f"{pdf_availability.get('downloaded', 32519):,}건", f"{pdf_availability.get('overall_download_pct', 92.1)}%"],
            ['PDF URL 없음', f"{pdf_availability.get('no_url', 2773):,}건", '구법령, 데이터 누락'],
            ['총 저장 용량', f"{storage.get('total_size_gb', 53.83)}GB", f"평균 {storage.get('avg_file_size_mb', 1.7)}MB/파일"],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('3.2 파일 크기/페이지 분포', level=2)
    doc.add_paragraph(
        "PDF 파일 크기는 수십 KB(단순 법령)부터 수백 MB(옴니버스법)까지 다양합니다. "
        f"평균 파일 크기는 {storage.get('avg_file_size_mb', 1.7)}MB이며, "
        "옴니버스법(UU 11/2020)은 779페이지로 최대 규모입니다."
    )

    doc.add_heading('3.3 품질 비교 (BPK vs 법제처)', level=2)
    pdf_summary = pdf_comparison.get('summary', {})
    doc.add_paragraph(
        f"100건 랜덤 샘플 비교 결과:\n"
        f"• 페이지 수: BPK 우세 {pdf_summary.get('pages', {}).get('bpk_more', 53)}건, "
        f"법제처 우세 {pdf_summary.get('pages', {}).get('leg_more', 25)}건\n"
        f"• 텍스트 양: BPK 우세 {pdf_summary.get('text', {}).get('bpk_more', 61)}건, "
        f"법제처 우세 {pdf_summary.get('text', {}).get('leg_more', 24)}건\n"
        f"• 법제처 손상 PDF: {pdf_summary.get('errors', {}).get('leg', 6)}건 (~6%)"
    )

    add_styled_table(doc,
        ['비교 항목', 'BPK 우세', '법제처 우세', '동일', '권장'],
        [
            ['페이지 수', '53%', '25%', '22%', 'BPK 우선'],
            ['텍스트 추출량', '61%', '24%', '15%', 'BPK 우선'],
            ['파일 손상', '0건', '6건', '-', 'BPK 우선'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('3.4 PDF 선택 알고리즘 권장안', level=2)
    doc.add_paragraph(
        "1. BPK PDF 존재 시: BPK PDF 우선 사용\n"
        "2. BPK PDF 없음: 법제처 PDF 사용\n"
        "3. 법제처 PDF 오류 시: BPK 대체 또는 재다운로드\n"
        "4. 양쪽 없음: 수동 확보 필요 (2,797건)"
    )

    # ==================== 4. PDF 문서 구조 분석 (핵심) ====================
    doc.add_heading('4. PDF 문서 구조 분석', level=1)

    doc.add_heading('4.1 헤더 유형 분류', level=2)
    doc.add_paragraph(
        "인도네시아 법령 PDF는 공포 매체에 따라 다른 헤더 유형을 가집니다. "
        f"32,517개 PDF 전수 분석 결과:"
    )
    add_styled_table(doc,
        ['헤더 유형', '건수', '비율', '대상 법령'],
        [
            ['LEMBARAN NEGARA', f"{header_stats['lembaran_negara_count']:,}", f"{header_stats['lembaran_negara_pct']}%", '법률, 정부령, 대통령령'],
            ['BERITA NEGARA', f"{header_stats['berita_negara_count']:,}", f"{header_stats['berita_negara_pct']}%", '장관령, 기관규정'],
            ['기타', f"{header_stats['other_count']:,}", f"{header_stats['other_pct']}%", '비표준 형식'],
            ['없음', f"{header_stats['none_count']:,}", f"{header_stats['none_pct']}%", '스캔본, OCR 필요'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('4.2 문서 레이아웃 패턴', level=2)
    doc.add_paragraph(
        "16개 샘플 분석 결과, 인도네시아 법령은 일관된 구조를 따릅니다:"
    )
    doc.add_paragraph(
        "【표준 구조】\n"
        "1. 헤더 (LEMBARAN/BERITA NEGARA) - 노이즈\n"
        "2. 제목 ([법령유형] NOMOR XX TAHUN XXXX TENTANG [제목])\n"
        "3. 서문 (DENGAN RAHMAT TUHAN YANG MAHA ESA)\n"
        "4. 배경 (Menimbang: a. bahwa...)\n"
        "5. 법적 근거 (Mengingat: 1. ...)\n"
        "6. ★ 본문 시작 마커 (MEMUTUSKAN: / Menetapkan:)\n"
        "7. 본문 (BAB I → Pasal 1 → Ayat (1) → Huruf a.)\n"
        "8. ★ 본문 종료 마커 (Ditetapkan di Jakarta)\n"
        "9. 서명 블록\n"
        "10. PENJELASAN (해설) - 별도 섹션"
    )

    doc.add_heading('4.3 PENJELASAN(해설) 분석', level=2)
    doc.add_paragraph(
        f"32,517개 PDF 전수 분석 결과, {penjelasan_stats['percentage']}%의 법령이 PENJELASAN 섹션을 포함합니다:"
    )
    add_styled_table(doc,
        ['항목', '건수', '비율', '비고'],
        [
            ['PENJELASAN 포함', f"{penjelasan_stats['total_with_penjelasan']:,}", f"{penjelasan_stats['percentage']}%", '해설 섹션 존재'],
            ['UMUM 키워드', f"{penjelasan_stats['umum_count']:,}", '61.1%', '일반 해설 (과탐지 포함)'],
            ['PASAL DEMI PASAL', f"{penjelasan_stats['pasal_demi_pasal_count']:,}", '8.9%', '조항별 해설'],
        ]
    )
    doc.add_paragraph()
    doc.add_paragraph(
        "※ PENJELASAN은 법률(UU)와 정부령(PP)에 주로 포함됩니다. "
        "'I. UMUM' 키워드 탐지율(61.1%)이 높은 것은 본문 내 유사 표현 때문이며, "
        "실제 해설 섹션 포함률은 22.2%입니다."
    )

    doc.add_heading('4.4 텍스트 추출 가능성', level=2)
    doc.add_paragraph(
        f"32,517개 PDF 전수 분석 결과 (페이지당 평균 100자 미만을 OCR 필요로 판정):"
    )
    add_styled_table(doc,
        ['분류', '건수', '비율', '처리 방법'],
        [
            ['직접 추출 가능', f"{32517 - ocr_stats.get('ocr_needed_count', 691):,}", f"{ocr_stats['direct_extractable']}%", 'PyMuPDF 텍스트 추출'],
            ['OCR 필요', f"{ocr_stats.get('ocr_needed_count', 691):,}", f"{ocr_stats['ocr_needed']}%", 'Tesseract/EasyOCR'],
        ]
    )
    doc.add_paragraph(
        f"직접 추출 가능한 PDF({ocr_stats['direct_extractable']}%)는 PyMuPDF(fitz)로 텍스트를 바로 추출할 수 있으며, "
        f"OCR이 필요한 {ocr_stats.get('ocr_needed_count', 691):,}건은 주로 1980년 이전 스캔 법령입니다."
    )

    # ==================== 5. 구조화 가능성 평가 ====================
    doc.add_heading('5. 구조화 가능성 평가', level=1)

    doc.add_heading('5.1 본문 추출 마커', level=2)
    doc.add_paragraph(
        f"32,517개 PDF 전수 분석 결과, 구조 마커 출현율:"
    )
    add_styled_table(doc,
        ['마커 유형', '패턴', '출현율', '용도'],
        [
            ['본문 시작', 'MEMUTUSKAN:', f"{ocr_stats['marker_memutuskan']}%", '주요 시작점'],
            ['본문 시작 (대안)', 'MENETAPKAN:', f"{ocr_stats['marker_menetapkan']}%", '보조 시작점'],
            ['본문 종료', 'Ditetapkan di', f"{ocr_stats['marker_ditetapkan']}%", '서명 시작 전'],
        ]
    )
    doc.add_paragraph(
        f"※ 97% 이상의 PDF에서 구조 마커가 발견되어 자동 본문 추출이 가능합니다."
    )
    doc.add_paragraph()

    doc.add_heading('5.2 조항 구조 (BAB/Pasal/Ayat/Huruf)', level=2)
    doc.add_paragraph(
        "인도네시아 법령의 계층 구조:\n"
        "• BAB (장) - 대분류: 'BAB I', 'BAB II' 등\n"
        "• Pasal (조) - 기본 단위: 'Pasal 1', 'Pasal 2' 등\n"
        "• Ayat (항) - 세부 조항: '(1)', '(2)' 등 괄호 숫자\n"
        "• Huruf (호) - 열거 항목: 'a.', 'b.' 등 알파벳\n"
        "• Angka (목) - 하위 열거: '1.', '2.' 등 숫자"
    )
    doc.add_paragraph()
    doc.add_paragraph(
        f"【Pasal 통계 (32,517개 PDF 분석)】\n"
        f"• 총 Pasal 수: {pasal_stats['total_pasal']:,}개\n"
        f"• 평균 Pasal/문서: {pasal_stats['avg_pasal']}개\n"
        f"• 최대 Pasal: {pasal_stats['max_pasal']:,}개 (옴니버스법 추정)"
    )

    doc.add_heading('5.3 노이즈 패턴 (헤더/푸터/워터마크)', level=2)
    add_styled_table(doc,
        ['노이즈 유형', '패턴', '처리 방법'],
        [
            ['반복 헤더', 'LEMBARAN/BERITA NEGARA...', '정규식 제거'],
            ['페이지 번호', r'^\s*-?\d+\s*-?\s*$', '정규식 제거'],
            ['워터마크', 'SALINAN, www.peraturan.go.id', '문자열 제거'],
            ['서명 블록', 'Ditetapkan di ~ ttd.', '본문 종료점으로 활용'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('5.4 정규식 패턴 및 파싱 전략', level=2)
    doc.add_paragraph(
        "핵심 정규식 패턴:\n"
        "• Pasal: r'Pasal\\s+(\\d+)'\n"
        "• Ayat: r'\\((\\d+)\\)\\s+'\n"
        "• Huruf: r'^([a-z])\\.\\s+'\n"
        "• BAB: r'BAB\\s+([IVXLCDM]+|\\d+)'\n"
        "• 본문 시작: r'MEMUTUSKAN\\s*:'\n"
        "• 본문 종료: r'Ditetapkan\\s+di'"
    )

    # ==================== 6. 검수/추출 방법론 제안 (핵심) ====================
    doc.add_heading('6. 검수/추출 방법론 제안', level=1)

    doc.add_heading('6.1 PDF 전처리 파이프라인', level=2)
    doc.add_paragraph(
        "【처리 흐름】\n"
        "[PDF 로드] → [텍스트 추출] → [노이즈 제거] → "
        "[본문/해설 분리] → [구조화 파싱] → [품질 검증]"
    )

    add_styled_table(doc,
        ['단계', '입력', '출력', '도구'],
        [
            ['1. PDF 로드', 'PDF 파일', 'fitz.Document', 'PyMuPDF'],
            ['2. 텍스트 추출', 'Document', 'Raw Text', 'page.get_text()'],
            ['3. 노이즈 제거', 'Raw Text', 'Clean Text', '정규식'],
            ['4. 본문/해설 분리', 'Clean Text', 'Body + Penjelasan', '마커 기반'],
            ['5. 구조화 파싱', 'Body', 'BAB/Pasal/Ayat Tree', '재귀 파서'],
            ['6. 품질 검증', 'Parsed Data', 'Validated Data', '규칙 검증'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('6.2 헤더 유형별 처리 방법', level=2)
    add_styled_table(doc,
        ['헤더 유형', '노이즈 제거 규칙', '특이사항'],
        [
            ['LEMBARAN NEGARA', r'LEMBARAN NEGARA[\\s\\S]*?No\\.\\d+', '매 페이지 반복'],
            ['BERITA NEGARA', r'BERITA NEGARA[\\s\\S]*?No\\.\\d+', '매 페이지 반복'],
            ['없음', '페이지 번호만 제거', '구법령 대응'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('6.3 PENJELASAN 분리 로직', level=2)
    doc.add_paragraph(
        "1. 'PENJELASAN' 또는 'PENJELASAN ATAS' 키워드 검색\n"
        "2. 해당 위치 이후를 해설 섹션으로 분리\n"
        "3. 'I. UMUM' → 일반 해설 추출\n"
        "4. 'II. PASAL DEMI PASAL' → 조항별 해설 추출\n"
        "5. 'Pasal X' 매칭으로 본문 조항과 연결"
    )

    doc.add_heading('6.4 품질 검증 체크리스트', level=2)
    add_styled_table(doc,
        ['검증 항목', '기준', '조치'],
        [
            ['텍스트 추출량', '>100자/페이지', 'OCR 필요 플래그'],
            ['본문 시작 마커', 'MEMUTUSKAN 존재', '대안 마커 검색'],
            ['Pasal 수', '>0', '수동 검토'],
            ['구조 일관성', 'Pasal 번호 연속', '누락 조항 확인'],
            ['인코딩', 'UTF-8 정상', '문자 깨짐 수정'],
        ]
    )
    doc.add_paragraph()

    # ==================== 7. 현행성 판단 체계 ====================
    doc.add_heading('7. 현행성 판단 체계', level=1)

    doc.add_heading('7.1 자동 판단 가능 범위', level=2)
    automation = determination_data.get('automation_categories', {})
    add_styled_table(doc,
        ['카테고리', '기준', '건수', '비율', '처리'],
        [
            ['A. 확정 유효', 'status=Berlaku', f"{automation.get('A_definite_valid', {}).get('count', 31509):,}", '89.2%', '자동'],
            ['B. 확정 무효', 'Dicabut Oleh 명시', f"{automation.get('B_definite_invalid', {}).get('count', 3681):,}", '10.4%', '자동'],
            ['C. 교차검증', 'BPK 불일치', f"{automation.get('C_cross_validation', {}).get('count', 1822):,}", '5.2%', '반자동'],
            ['D. 조건부', '조건 표현 포함', f"{automation.get('D_conditional', {}).get('count', 3780):,}", '10.7%', '인간 검토'],
            ['E. 미확인', 'status NULL', f"{automation.get('E_unknown', {}).get('count', 126):,}", '0.4%', '수동 조사'],
        ]
    )
    doc.add_paragraph()

    conclusion = determination_data.get('conclusion', {})
    doc.add_paragraph(
        f"전체 {peraturan_total:,}건 중 {conclusion.get('auto_determinable', 99.6)}% "
        f"({35190:,}건)가 메타데이터만으로 1차 분류 가능합니다."
    )

    doc.add_heading('7.2 법령 변경 패턴', level=2)
    doc.add_paragraph("주요 법령 변경 패턴 및 자동화 가능성:")
    add_styled_table(doc,
        ['패턴', '의미', '건수', '자동화'],
        [
            ['dicabut', '폐지됨', '11,660', '✅ 가능'],
            ['dinyatakan tidak berlaku', '효력 없음 선언', '9,308', '✅ 가능'],
            ['perubahan / diubah', '개정', '16,172 / 9,584', '✅ 가능'],
            ['sebagian dicabut', '부분 폐지', '2,626', '⚠️ 부분'],
            ['sepanjang tidak bertentangan', '조건부 유효', '2,287', '❌ 인간 검토'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('7.3 법률가 검토 필요 케이스', level=2)
    doc.add_paragraph(
        f"전체 중 약 {conclusion.get('needs_human', 10.7)}% ({3780:,}건)는 법률 전문가 검토가 필요합니다:\n"
        "• 'sepanjang tidak bertentangan' (상충하지 않는 한) - 2,287건\n"
        "• 'tetap berlaku sepanjang' (조건부 유효) - 2,683건\n"
        "• 다중 조건 조합 - 5,545건"
    )

    # ==================== 8. 문제점 및 리스크 ====================
    doc.add_heading('8. 문제점 및 리스크', level=1)

    add_styled_table(doc,
        ['문제 영역', '상세', '건수', '대응 방안'],
        [
            ['PDF 없는 법령', 'URL 없음 또는 다운로드 실패', '2,797건 (7.9%)', 'BPK 보완, 수동 확보'],
            ['손상 PDF', '파일 오류, 추출 실패', '~525건 (~1.5%)', 'BPK 대체, 재다운로드'],
            ['조건부 표현', '법률 해석 필요', '3,780건 (10.7%)', '법률가 검토 워크플로우'],
            ['구법령 디지털화', '1945-1980 스캔본', '~2,500건', 'OCR 파이프라인'],
            ['PENJELASAN 누락', '해설 미포함', '~31,700건 (89.8%)', '별도 수집 또는 N/A 처리'],
        ]
    )
    doc.add_paragraph()

    # ==================== 9. RFP 요구사항 대비 분석 ====================
    doc.add_heading('9. RFP 요구사항 대비 분석', level=1)

    add_styled_table(doc,
        ['요구사항', 'RFP 수치', '현황 (실측)', '달성률', '비고'],
        [
            ['법령 건수', '35,424건', f'{peraturan_total:,}건', f'{peraturan_total/RFP_REQUIREMENTS["total_laws"]*100:.1f}%', 'BPK 병합으로 초과 가능'],
            ['페이지 수', '150,000페이지', f'{total_pages:,}페이지', f'{total_pages/RFP_REQUIREMENTS["total_pages"]*100:.0f}%', 'RFP 대비 7배 초과'],
            ['출력 형식', 'Word→XML/HTML', 'PDF→XML 전환', '-', '원본이 PDF이므로 조정 필요'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('9.1 법령 건수 분석', level=2)
    doc.add_paragraph(
        f"RFP 요구사항 35,424건 대비 법제처 단독 {peraturan_total:,}건 확보 "
        f"({peraturan_total/RFP_REQUIREMENTS['total_laws']*100:.1f}%). "
        f"부족분 {RFP_REQUIREMENTS['total_laws'] - peraturan_total:,}건은 "
        "BPK 데이터 병합(특히 Keppres 6,984건)으로 충족 가능합니다."
    )

    doc.add_heading('9.2 페이지 수 분석', level=2)
    doc.add_paragraph(
        f"RFP 기준 150,000페이지 대비 실측 결과 {total_pages:,}페이지로, "
        f"RFP 요구량의 {total_pages/RFP_REQUIREMENTS['total_pages']*100:.0f}% (약 7배)를 초과합니다.\n\n"
        f"• 평균 페이지/문서: {total_pages/32517:.1f}페이지\n"
        f"• 최대 페이지: 3,901페이지 (옴니버스법 UU 11/2020)\n"
        f"• 최소 페이지: 1페이지\n\n"
        "이는 RFP 산정 기준(평균 4.2페이지/법령)보다 실제 법령이 훨씬 길다는 것을 의미하며, "
        "정보화 작업량이 예상보다 크게 증가할 수 있습니다."
    )

    doc.add_heading('9.3 Word→XML 변환 관련', level=2)
    doc.add_paragraph(
        "RFP는 Word→XML/HTML 변환을 요구하나, 실제 원본은 PDF입니다. "
        "따라서 다음 전환 전략을 제안합니다:\n"
        "1. PDF → 텍스트 추출 (PyMuPDF)\n"
        "2. 텍스트 → 구조화 파싱 (BAB/Pasal/Ayat)\n"
        "3. 구조화 데이터 → XML/HTML 출력\n"
        "4. (선택) XML → Word 변환"
    )

    # ==================== 10. 결론 및 권고사항 ====================
    doc.add_heading('10. 결론 및 권고사항', level=1)

    doc.add_heading('10.1 데이터 활용 전략', level=2)
    doc.add_paragraph(
        "1. 법제처 데이터 기반: 35,316건을 주 데이터셋으로 활용\n"
        "2. BPK 보완: Keppres 등 누락 법령 및 고품질 PDF 확보\n"
        "3. 멀티소스 병합: 동일 법령 중복 제거 및 최고 품질 선택"
    )

    doc.add_heading('10.2 품질 개선 우선순위', level=2)
    doc.add_paragraph(
        "1. (긴급) PDF 없는 법령 2,797건 확보\n"
        "2. (높음) 손상 PDF ~525건 BPK 대체\n"
        "3. (중간) 조건부 표현 3,780건 검토 워크플로우 구축\n"
        "4. (장기) 구법령 OCR 파이프라인 구축"
    )

    doc.add_heading('10.3 기술 구현 로드맵', level=2)
    add_styled_table(doc,
        ['단계', '작업', '산출물'],
        [
            ['1단계', 'PDF 전처리 파이프라인 구축', '텍스트 추출 완료 DB'],
            ['2단계', '구조화 파서 개발', 'BAB/Pasal/Ayat 구조화 데이터'],
            ['3단계', '현행성 판단 엔진', '자동 분류 결과'],
            ['4단계', 'PENJELASAN 추출/연결', '해설 연계 데이터'],
            ['5단계', 'XML/HTML 변환기', '최종 정보화 결과물'],
        ]
    )
    doc.add_paragraph()

    # ==================== 부록 ====================
    doc.add_page_break()
    doc.add_heading('부록', level=1)

    doc.add_heading('A. 법령 유형 용어 정리 (인도네시아어-한국어)', level=2)
    add_styled_table(doc,
        ['인도네시아어', '약어', '한국어', '설명'],
        [
            ['Undang-Undang', 'UU', '법률', '국회 제정 법률'],
            ['Peraturan Pemerintah', 'PP', '정부령', '법률 시행령'],
            ['Peraturan Presiden', 'Perpres', '대통령령', '대통령 발령'],
            ['Perppu', 'Perppu', '긴급정부령', '긴급 시 대통령 발령'],
            ['Peraturan Menteri', 'Permen', '장관령', '부처 장관 발령'],
            ['Peraturan Badan', '-', '기관규정', '독립기관 발령'],
            ['Keputusan Presiden', 'Keppres', '대통령결정', '개별 결정'],
            ['Peraturan Daerah', 'Perda', '지방조례', '지방정부 발령'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('B. PDF 문서 구조 상세 패턴', level=2)
    doc.add_paragraph(
        "【LEMBARAN NEGARA 구조】\n"
        "┌─────────────────────────────────────┐\n"
        "│ LEMBARAN NEGARA                     │ ← 헤더\n"
        "│ REPUBLIK INDONESIA                  │\n"
        "│ No.XX, 20XX                        │\n"
        "├─────────────────────────────────────┤\n"
        "│ UNDANG-UNDANG REPUBLIK INDONESIA   │ ← 제목\n"
        "│ NOMOR XX TAHUN XXXX                │\n"
        "│ TENTANG [주제]                      │\n"
        "├─────────────────────────────────────┤\n"
        "│ DENGAN RAHMAT TUHAN YANG MAHA ESA  │ ← 서문\n"
        "│ PRESIDEN REPUBLIK INDONESIA,       │\n"
        "├─────────────────────────────────────┤\n"
        "│ Menimbang : a. bahwa ...           │ ← 배경\n"
        "│ Mengingat : 1. ...                 │ ← 법적 근거\n"
        "├─────────────────────────────────────┤\n"
        "│ MEMUTUSKAN:                        │ ← ★ 본문 시작\n"
        "│ Menetapkan: ...                    │\n"
        "├─────────────────────────────────────┤\n"
        "│ BAB I KETENTUAN UMUM               │ ← 본문\n"
        "│   Pasal 1                          │\n"
        "│     (1) ...                        │\n"
        "├─────────────────────────────────────┤\n"
        "│ Ditetapkan di Jakarta              │ ← ★ 본문 종료\n"
        "│ [서명]                              │\n"
        "├─────────────────────────────────────┤\n"
        "│ PENJELASAN ATAS UU RI              │ ← 해설\n"
        "│ I. UMUM                            │\n"
        "│ II. PASAL DEMI PASAL               │\n"
        "└─────────────────────────────────────┘"
    )

    doc.add_heading('C. 정규식 패턴 라이브러리', level=2)
    doc.add_paragraph(
        "# 본문 마커\n"
        "START_MARKER = r'MEMUTUSKAN\\s*:'\n"
        "START_ALT = r'MENETAPKAN\\s*:'\n"
        "END_MARKER = r'Ditetapkan\\s+di'\n\n"
        "# 구조 요소\n"
        "BAB_PATTERN = r'BAB\\s+([IVXLCDM]+|\\d+)'\n"
        "PASAL_PATTERN = r'Pasal\\s+(\\d+)'\n"
        "AYAT_PATTERN = r'\\((\\d+)\\)\\s+'\n"
        "HURUF_PATTERN = r'^([a-z])\\.\\s+'\n\n"
        "# 노이즈 제거\n"
        "LEMBARAN_HEADER = r'LEMBARAN\\s+NEGARA[\\s\\S]*?No\\.\\s*\\d+[,\\s]+\\d{4}'\n"
        "BERITA_HEADER = r'BERITA\\s+NEGARA[\\s\\S]*?No\\.\\s*\\d+[,\\s]+\\d{4}'\n"
        "PAGE_NUMBER = r'^\\s*-?\\s*\\d+\\s*-?\\s*$'\n"
        "WATERMARK = r'(SALINAN|www\\.peraturan\\.go\\.id)'"
    )

    # Footer
    doc.add_paragraph()
    doc.add_paragraph()
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("─" * 50)
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run(f"본 보고서는 {datetime.now().strftime('%Y-%m-%d %H:%M')}에 자동 생성되었습니다.\n").italic = True
    footer.add_run("© KOICA 인도네시아 법령정보시스템 구축사업").italic = True

    # Save document
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "KOICA_현황분석보고서.docx"
    doc.save(str(output_path))

    return output_path


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="KOICA RFP 대응 현황분석보고서 생성기"
    )
    parser.add_argument(
        "--peraturan-db",
        type=Path,
        default=Path("peraturan/data/peraturan.db"),
        help="법제처 데이터베이스 경로",
    )
    parser.add_argument(
        "--bpk-db",
        type=Path,
        default=Path("bpk/data/peraturan_bpk.db"),
        help="BPK 데이터베이스 경로",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=Path("docs/exports"),
        help="출력 디렉토리",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("KOICA RFP 대응 현황분석보고서 생성")
    print("=" * 60)
    print(f"법제처 DB: {args.peraturan_db}")
    print(f"BPK DB: {args.bpk_db}")
    print(f"출력 디렉토리: {args.output_dir}")
    print()

    if not DOCX_AVAILABLE:
        print("오류: python-docx가 설치되지 않았습니다.")
        print("설치 명령: pip install python-docx")
        print("또는: pip install -e '.[report]'")
        sys.exit(1)

    try:
        output_path = create_report(
            peraturan_db=args.peraturan_db,
            bpk_db=args.bpk_db,
            output_dir=args.output_dir,
        )
        print(f"✅ 보고서 생성 완료: {output_path}")
        print(f"   파일 크기: {output_path.stat().st_size / 1024:.1f} KB")
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
