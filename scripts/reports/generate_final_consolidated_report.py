#!/usr/bin/env python3
"""
최종 통합 보고서 생성 스크립트

docs/exports 폴더의 모든 문서를 분석하고
유의미한 데이터만 추출하여 최종 1개 파일로 통합
"""

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Pt, RGBColor, Inches, Cm

# === 경로 설정 ===
PROJECT_ROOT = Path(__file__).parent.parent
PERATURAN_DB = PROJECT_ROOT / "peraturan" / "data" / "peraturan.db"
BPK_DB = PROJECT_ROOT / "bpk" / "data" / "peraturan_bpk.db"
PDF_ROOT = PROJECT_ROOT / "peraturan" / "data" / "pdfs"
BPK_PDF_ROOT = PROJECT_ROOT / "bpk" / "data" / "pdfs"
REFERENCES_JSON = PROJECT_ROOT / "peraturan" / "data" / "all_references.json"
EXPORTS_DIR = PROJECT_ROOT / "docs" / "exports"
OUTPUT_FILE = EXPORTS_DIR / "인도네시아_법령정보시스템_최종통합보고서.docx"

RFP_WHERE = "jenis IN ('UNDANG-UNDANG','PERATURAN PEMERINTAH','PERATURAN PRESIDEN','PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG','PERATURAN MENTERI','PERATURAN BADAN/LEMBAGA')"

RFP_TYPE_ABBREV = {
    "UNDANG-UNDANG": "UU",
    "PERATURAN PEMERINTAH": "PP",
    "PERATURAN PRESIDEN": "PERPRES",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "PERPPU",
    "PERATURAN MENTERI": "PERMEN",
    "PERATURAN BADAN/LEMBAGA": "PERBAN",
}

RFP_TYPE_KOREAN = {
    "UNDANG-UNDANG": "법률",
    "PERATURAN PEMERINTAH": "정부령",
    "PERATURAN PRESIDEN": "대통령령",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "긴급법률",
    "PERATURAN MENTERI": "장관령",
    "PERATURAN BADAN/LEMBAGA": "기관규정",
}

ALL_TYPE_KOREAN = {
    "UNDANG-UNDANG": "법률",
    "PERATURAN PEMERINTAH": "정부령",
    "PERATURAN PRESIDEN": "대통령령",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "긴급법률",
    "PERATURAN MENTERI": "장관령",
    "PERATURAN BADAN/LEMBAGA": "기관규정",
    "KEPUTUSAN PRESIDEN": "대통령결정",
    "INSTRUKSI PRESIDEN": "대통령지시",
    "PENETAPAN PRESIDEN": "대통령확정",
    "KETETAPAN MAJELIS PERMUSYAWARATAN RAKYAT": "국민협의회결의",
    "PERATURAN DAERAH": "지방규정",
}


def query_db(db_path: Path, query: str, params=None) -> list[tuple]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        if params:
            cursor = conn.execute(query, params)
        else:
            cursor = conn.execute(query)
        results = cursor.fetchall()
    finally:
        conn.close()
    return results


def query_peraturan(query: str, params=None) -> list[tuple]:
    return query_db(PERATURAN_DB, query, params)


def query_bpk(query: str, params=None) -> list[tuple]:
    return query_db(BPK_DB, query, params)


def query_one(query: str, db='peraturan') -> Any:
    if db == 'peraturan':
        result = query_peraturan(query)
    else:
        result = query_bpk(query)
    return result[0][0] if result else 0


def fmt(n) -> str:
    if n is None:
        return "0"
    if isinstance(n, float):
        return f"{n:,.1f}"
    return f"{n:,}"


def pct(part, total) -> str:
    if total == 0:
        return "0%"
    return f"{part / total * 100:.1f}%"


def set_cell_shading(cell, color: str):
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color}"/>')
    cell._tc.get_or_add_tcPr().append(shading)


def create_table(doc: Document, data: list[list[str]], header: bool = True):
    if not data:
        return None
    table = doc.add_table(rows=len(data), cols=len(data[0]))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, row_data in enumerate(data):
        row = table.rows[i]
        for j, cell_text in enumerate(row_data):
            cell = row.cells[j]
            cell.text = str(cell_text)
            para = cell.paragraphs[0]
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if header and i == 0:
                set_cell_shading(cell, "1F4E79")
                for run in para.runs:
                    run.font.color.rgb = RGBColor(255, 255, 255)
                    run.font.bold = True
    return table


def add_heading(doc: Document, text: str, level: int = 1):
    return doc.add_heading(text, level=level)


def add_para(doc: Document, text: str, bold: bool = False):
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.bold = bold
    return para


def add_code_block(doc: Document, code: str):
    para = doc.add_paragraph()
    run = para.add_run(code)
    run.font.name = 'Consolas'
    run.font.size = Pt(9)
    return para


def get_pdf_folder_stats(root: Path):
    """PDF 폴더 통계"""
    if not root.exists():
        return {}

    stats = {}
    total_size = 0
    total_count = 0

    for subdir in root.iterdir():
        if subdir.is_dir():
            size = 0
            count = 0
            for f in subdir.rglob('*.pdf'):
                size += f.stat().st_size
                count += 1
            if count > 0:
                stats[subdir.name] = {'size': size, 'count': count}
                total_size += size
                total_count += count

    stats['_total'] = {'size': total_size, 'count': total_count}
    return stats


def load_references_data():
    if not REFERENCES_JSON.exists():
        return None
    with open(REFERENCES_JSON, 'r') as f:
        refs = json.load(f)

    ext_ref_count = 0
    int_ref_count = 0
    conditional_count = 0
    ext_ref_types = Counter()
    conditional_types = Counter()
    docs_with_ext = 0
    docs_with_int = 0
    docs_with_cond = 0

    for doc in refs:
        if doc.get('external_refs'):
            docs_with_ext += 1
            for ref in doc['external_refs']:
                ext_ref_count += 1
                if isinstance(ref, dict):
                    ext_ref_types[ref.get('jenis', 'unknown')] += 1
        if doc.get('internal_refs'):
            docs_with_int += 1
            int_ref_count += len(doc['internal_refs'])
        if doc.get('conditional_clauses'):
            docs_with_cond += 1
            conditional_count += len(doc['conditional_clauses'])
            for clause in doc['conditional_clauses']:
                if isinstance(clause, dict):
                    conditional_types[clause.get('clause_type', 'unknown')] += 1

    return {
        'total_docs': len(refs),
        'ext_ref_count': ext_ref_count,
        'int_ref_count': int_ref_count,
        'conditional_count': conditional_count,
        'docs_with_ext': docs_with_ext,
        'docs_with_int': docs_with_int,
        'docs_with_cond': docs_with_cond,
        'ext_ref_types': ext_ref_types,
        'conditional_types': conditional_types
    }


def generate_report():
    print("=" * 70)
    print("인도네시아 법령정보시스템 최종 통합 보고서 생성")
    print("=" * 70)

    doc = Document()

    # === 표지 ===
    for _ in range(4):
        doc.add_paragraph()

    title = doc.add_heading("인도네시아 법령정보시스템 구축", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = doc.add_heading("기초자료 종합 보고서", level=1)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for _ in range(2):
        doc.add_paragraph()

    info = doc.add_paragraph()
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info.add_run("데이터 수집 · 구조 분석 · 현행성 판단 · 시스템 설계")
    run.font.size = Pt(14)

    doc.add_paragraph()
    info2 = doc.add_paragraph()
    info2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run2 = info2.add_run("법제처(peraturan.go.id) + BPK(peraturan.bpk.go.id) 통합 분석")
    run2.font.size = Pt(12)

    for _ in range(5):
        doc.add_paragraph()

    date_para = doc.add_paragraph()
    date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = date_para.add_run(f"작성일: {datetime.now().strftime('%Y년 %m월 %d일')}")
    run.font.size = Pt(12)

    doc.add_page_break()

    # === Executive Summary ===
    add_heading(doc, "Executive Summary", 1)

    # 핵심 지표
    total_peraturan = query_one("SELECT COUNT(*) FROM peraturan")
    total_bpk = query_one("SELECT COUNT(*) FROM peraturan", 'bpk')
    pdf_peraturan = query_one("SELECT COUNT(*) FROM peraturan WHERE local_pdf_path IS NOT NULL AND local_pdf_path != ''")
    pdf_bpk = query_one("SELECT COUNT(*) FROM peraturan WHERE pdf_path IS NOT NULL AND pdf_path != ''", 'bpk')
    extracted = query_one("SELECT COUNT(*) FROM peraturan WHERE extraction_success = 1")
    parsed = query_one("SELECT COUNT(*) FROM peraturan WHERE parsed_pasal_count > 0")
    refs_data = load_references_data()

    pdf_stats_peraturan = get_pdf_folder_stats(PDF_ROOT)
    pdf_stats_bpk = get_pdf_folder_stats(BPK_PDF_ROOT)
    total_pdf_size = pdf_stats_peraturan.get('_total', {}).get('size', 0) + pdf_stats_bpk.get('_total', {}).get('size', 0)

    add_para(doc, "핵심 지표 요약", bold=True)
    summary_data = [
        ["항목", "법제처", "BPK", "합계/비고"],
        ["메타데이터 수집", fmt(total_peraturan), fmt(total_bpk), f"{fmt(total_peraturan + total_bpk)}건"],
        ["PDF 다운로드", fmt(pdf_peraturan), fmt(pdf_bpk), f"{fmt(pdf_peraturan + pdf_bpk)}건"],
        ["PDF 용량", f"{pdf_stats_peraturan.get('_total', {}).get('size', 0)/1024/1024/1024:.1f}GB",
         f"{pdf_stats_bpk.get('_total', {}).get('size', 0)/1024/1024/1024:.1f}GB",
         f"{total_pdf_size/1024/1024/1024:.1f}GB"],
        ["텍스트 추출", fmt(extracted), "-", f"{pct(extracted, pdf_peraturan)} 성공"],
        ["구조 파싱", fmt(parsed), "-", f"{pct(parsed, extracted)} 성공"],
    ]
    create_table(doc, summary_data)
    doc.add_paragraph()

    add_para(doc, "주요 발견 사항", bold=True)
    doc.add_paragraph("1. BPK 소스가 법제처보다 PDF 제공률 높음 (99.4% vs 87.8%)")
    doc.add_paragraph("2. 조건부 효력 표현('상충되지 않는 한') 2,285건 - 법률가 검토 필요")
    doc.add_paragraph("3. 구법령(1945-1980) 디지털화 미완료로 PDF 미확보 다수")
    doc.add_paragraph("4. 메타데이터 보완 필요: tanggal_pengundangan, tanggal_berlaku 필드 공백")
    doc.add_paragraph()

    add_para(doc, "권고사항", bold=True)
    doc.add_paragraph("• PDF 활용: BPK 우선, 법제처 보완 활용")
    doc.add_paragraph("• 메타데이터: 양쪽 소스 병합하여 완성도 제고")
    doc.add_paragraph("• OCR: 스캔 이미지 691건 별도 처리 파이프라인 구축")
    doc.add_paragraph("• 현행성: status 필드 + 본문 분석 이중 검증")

    doc.add_page_break()

    # === 목차 ===
    add_heading(doc, "목 차", 1)
    toc = """
Part I. 데이터 수집 현황
  1. 크롤링 개요
  2. 법제처(peraturan.go.id) 수집 결과
  3. BPK(peraturan.bpk.go.id) 수집 결과
  4. 두 소스 비교 분석

Part II. PDF 및 텍스트 처리
  5. PDF 다운로드 현황
  6. 텍스트 추출 현황
  7. 구조 파싱 현황
  8. PDF 품질 비교

Part III. 법령 구조 분석
  9. 문서 구조 (BAB/Pasal/Ayat/Huruf)
  10. 유형별·연대별 분석
  11. 관계 표현 분석

Part IV. 현행성 판단 체계
  12. 현행성 상태 분포
  13. 자동 판단 가능 범위
  14. 조건부 효력 (회색지대)
  15. 업무 분담 설계

Part V. 시스템 설계
  16. 메타데이터 추출 전략
  17. Neo4j 그래프 모델
  18. 국제표준 XML (Akoma Ntoso)
  19. 구현 로드맵

부록
  A. 법령 유형 용어 대조표
  B. 관계 표현 용어 대조표
  C. 조건부 효력 표현 상세
"""
    doc.add_paragraph(toc)
    doc.add_page_break()

    # ============================================================
    # PART I: 데이터 수집 현황
    # ============================================================
    part1 = doc.add_heading("Part I. 데이터 수집 현황", level=0)
    part1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 1. 크롤링 개요 ===
    add_heading(doc, "1. 크롤링 개요", 1)

    add_para(doc, "1.1 프로젝트 목적", bold=True)
    doc.add_paragraph("인도네시아 법령정보시스템 구축을 위한 기초 데이터 수집")
    doc.add_paragraph("• 인도네시아 국가법령정보센터 2개 소스 크롤링")
    doc.add_paragraph("• PDF 원문 다운로드 및 텍스트 추출")
    doc.add_paragraph("• 법령 구조 파싱 및 관계성 분석")
    doc.add_paragraph()

    add_para(doc, "1.2 수집 대상 사이트", bold=True)
    site_data = [
        ["구분", "법제처", "BPK"],
        ["사이트명", "JDIH Nasional", "JDIH BPK RI"],
        ["URL", "peraturan.go.id", "peraturan.bpk.go.id"],
        ["운영기관", "법무인권부", "감사원"],
        ["특징", "공식 법령 DB", "감사 관련 규정 포함"],
    ]
    create_table(doc, site_data)
    doc.add_paragraph()

    add_para(doc, "1.3 수집 기술 스택", bold=True)
    doc.add_paragraph("• 언어: Python 3.11")
    doc.add_paragraph("• HTTP: httpx (비동기)")
    doc.add_paragraph("• HTML 파싱: BeautifulSoup + lxml")
    doc.add_paragraph("• PDF 추출: PyMuPDF (fitz)")
    doc.add_paragraph("• OCR: Tesseract (인도네시아어)")
    doc.add_paragraph("• DB: SQLite")
    doc.add_paragraph()

    add_para(doc, "1.4 RFP 대상 법령 유형 (6종)", bold=True)
    rfp_data = [
        ["약어", "인도네시아어", "한국어", "대상"],
        ["UU", "Undang-Undang", "법률", "●"],
        ["PP", "Peraturan Pemerintah", "정부령", "●"],
        ["PERPRES", "Peraturan Presiden", "대통령령", "●"],
        ["PERPPU", "Perppu", "긴급법률", "●"],
        ["PERMEN", "Peraturan Menteri", "장관령", "●"],
        ["PERBAN", "Peraturan Badan/Lembaga", "기관규정", "●"],
    ]
    create_table(doc, rfp_data)
    doc.add_paragraph()

    # === 2. 법제처 수집 결과 ===
    add_heading(doc, "2. 법제처(peraturan.go.id) 수집 결과", 1)

    add_para(doc, "2.1 메타데이터 수집 현황", bold=True)

    min_year = query_one("SELECT MIN(tahun) FROM peraturan")
    max_year = query_one("SELECT MAX(tahun) FROM peraturan")
    has_pdf_url = query_one("SELECT COUNT(*) FROM peraturan WHERE pdf_url IS NOT NULL AND pdf_url != ''")

    peraturan_summary = [
        ["항목", "값", "비고"],
        ["총 수집 건수", fmt(total_peraturan), "100%"],
        ["연도 범위", f"{min_year}년 ~ {max_year}년", f"{max_year - min_year + 1}년"],
        ["PDF URL 보유", fmt(has_pdf_url), pct(has_pdf_url, total_peraturan)],
        ["DB 용량", "1.4 GB", "peraturan.db"],
    ]
    create_table(doc, peraturan_summary)
    doc.add_paragraph()

    add_para(doc, "2.2 법령 유형별 분포", bold=True)
    type_stats = query_peraturan("""
        SELECT jenis, COUNT(*) as cnt FROM peraturan GROUP BY jenis ORDER BY cnt DESC LIMIT 10
    """)

    type_data = [["법령 유형", "한국어", "건수", "비율"]]
    for jenis, cnt in type_stats:
        korean = ALL_TYPE_KOREAN.get(jenis, '-')
        type_data.append([jenis[:25], korean, fmt(cnt), pct(cnt, total_peraturan)])
    create_table(doc, type_data)
    doc.add_paragraph()

    add_para(doc, "2.3 수집 메타데이터 필드", bold=True)
    fields_data = [
        ["필드명", "설명", "채움률"],
        ["slug", "고유 식별자", "100%"],
        ["jenis", "법령 유형", "100%"],
        ["nomor", "법령 번호", "100%"],
        ["tahun", "제정 연도", "100%"],
        ["tentang", "법령 제목", "100%"],
        ["status", "현행성 상태", "100%"],
        ["pemrakarsa", "발령기관", "~90%"],
        ["tanggal_penetapan", "제정일", "~85%"],
        ["tanggal_pengundangan", "공포일", "0%"],
        ["tanggal_berlaku", "시행일", "0%"],
    ]
    create_table(doc, fields_data)
    doc.add_paragraph()

    # === 3. BPK 수집 결과 ===
    add_heading(doc, "3. BPK(peraturan.bpk.go.id) 수집 결과", 1)

    add_para(doc, "3.1 메타데이터 수집 현황", bold=True)

    min_year_bpk = query_one("SELECT MIN(tahun) FROM peraturan", 'bpk')
    max_year_bpk = query_one("SELECT MAX(tahun) FROM peraturan", 'bpk')
    has_pdf_url_bpk = query_one("SELECT COUNT(*) FROM peraturan WHERE pdf_url IS NOT NULL AND pdf_url != ''", 'bpk')

    bpk_summary = [
        ["항목", "값", "비고"],
        ["총 수집 건수", fmt(total_bpk), "100%"],
        ["연도 범위", f"{min_year_bpk}년 ~ {max_year_bpk}년", f"{max_year_bpk - min_year_bpk + 1 if min_year_bpk and max_year_bpk else 0}년"],
        ["PDF URL 보유", fmt(has_pdf_url_bpk), pct(has_pdf_url_bpk, total_bpk) if total_bpk else "0%"],
        ["DB 용량", "33 MB", "peraturan_bpk.db"],
    ]
    create_table(doc, bpk_summary)
    doc.add_paragraph()

    add_para(doc, "3.2 법제처 대비 추가 유형", bold=True)
    doc.add_paragraph("BPK에는 법제처에 없는 다음 유형이 포함됨:")
    doc.add_paragraph("• KEPUTUSAN PRESIDEN (KEPPRES) - 대통령결정")
    doc.add_paragraph("• INSTRUKSI PRESIDEN (INPRES) - 대통령지시")
    doc.add_paragraph("• UU DARURAT - 긴급법률 (구형식)")
    doc.add_paragraph("• PENETAPAN PRESIDEN - 대통령확정")
    doc.add_paragraph()

    # === 4. 두 소스 비교 분석 ===
    add_heading(doc, "4. 두 소스 비교 분석", 1)

    add_para(doc, "4.1 소스별 장단점", bold=True)
    compare_data = [
        ["항목", "법제처", "BPK", "권장"],
        ["메타데이터 완성도", "○", "◎", "병합 활용"],
        ["PDF 제공률", "87.8%", "99.4%", "BPK"],
        ["PDF 품질", "일부 손상", "양호", "BPK"],
        ["법령 유형 다양성", "11종", "120+종", "BPK"],
        ["최신성", "◎", "○", "법제처"],
    ]
    create_table(doc, compare_data)
    doc.add_paragraph()

    add_para(doc, "4.2 활용 전략", bold=True)
    doc.add_paragraph("1) PDF: BPK 우선 사용 → 누락시 법제처 보완")
    doc.add_paragraph("2) 메타데이터: 양쪽 병합 (tanggal_pengundangan, tanggal_berlaku는 BPK에서)")
    doc.add_paragraph("3) 중복 법령: slug 기준 deduplication")

    doc.add_page_break()

    # ============================================================
    # PART II: PDF 및 텍스트 처리
    # ============================================================
    part2 = doc.add_heading("Part II. PDF 및 텍스트 처리", level=0)
    part2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 5. PDF 다운로드 현황 ===
    add_heading(doc, "5. PDF 다운로드 현황", 1)

    add_para(doc, "5.1 다운로드 결과", bold=True)

    pdf_summary = [
        ["구분", "법제처", "BPK", "합계"],
        ["다운로드 대상", fmt(has_pdf_url), fmt(has_pdf_url_bpk), fmt(has_pdf_url + has_pdf_url_bpk)],
        ["다운로드 완료", fmt(pdf_peraturan), fmt(pdf_bpk), fmt(pdf_peraturan + pdf_bpk)],
        ["성공률", pct(pdf_peraturan, has_pdf_url), pct(pdf_bpk, has_pdf_url_bpk) if has_pdf_url_bpk else "-", "-"],
        ["총 용량", f"{pdf_stats_peraturan.get('_total', {}).get('size', 0)/1024/1024/1024:.1f}GB",
         f"{pdf_stats_bpk.get('_total', {}).get('size', 0)/1024/1024/1024:.1f}GB",
         f"{total_pdf_size/1024/1024/1024:.1f}GB"],
    ]
    create_table(doc, pdf_summary)
    doc.add_paragraph()

    add_para(doc, "5.2 PDF 미제공 원인", bold=True)
    doc.add_paragraph("• 역사적 문서 (1945-1970년대): 물리적 원본 훼손/분실")
    doc.add_paragraph("• 디지털화 미완료: 구법령 상당수")
    doc.add_paragraph("• 내부 행정규정: 공개 우선순위 낮음")
    doc.add_paragraph("• PERPPU (긴급법률): 1959-1960년대 정치적 혼란기 발령분")
    doc.add_paragraph()

    add_para(doc, "5.3 유형별 PDF 확보율", bold=True)
    type_pdf = query_peraturan(f"""
        SELECT jenis, COUNT(*) as total,
               SUM(CASE WHEN local_pdf_path IS NOT NULL AND local_pdf_path != '' THEN 1 ELSE 0 END) as downloaded
        FROM peraturan WHERE {RFP_WHERE} GROUP BY jenis ORDER BY total DESC
    """)

    pdf_type_data = [["유형", "총 건수", "PDF 확보", "확보율"]]
    for row in type_pdf:
        jenis, total_t, downloaded = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        pdf_type_data.append([abbr, fmt(total_t), fmt(downloaded), pct(downloaded, total_t)])
    create_table(doc, pdf_type_data)
    doc.add_paragraph()

    # === 6. 텍스트 추출 현황 ===
    add_heading(doc, "6. 텍스트 추출 현황", 1)

    add_para(doc, "6.1 추출 방법", bold=True)
    doc.add_paragraph("• 1차: PyMuPDF (fitz) - 텍스트 레이어 직접 추출")
    doc.add_paragraph("• 2차: Tesseract OCR - 스캔 이미지 PDF용")
    doc.add_paragraph("• 언어: 인도네시아어 (ind)")
    doc.add_paragraph()

    add_para(doc, "6.2 추출 결과", bold=True)
    needs_ocr = query_one("SELECT COUNT(*) FROM peraturan WHERE needs_ocr = 1")
    extract_failed = query_one("SELECT COUNT(*) FROM peraturan WHERE extraction_success = 0 AND local_pdf_path IS NOT NULL AND local_pdf_path != ''")

    extract_data = [
        ["항목", "건수", "비율"],
        ["추출 대상 (PDF 보유)", fmt(pdf_peraturan), "100%"],
        ["1차 추출 성공 (PyMuPDF)", fmt(extracted - 179), pct(extracted - 179, pdf_peraturan)],
        ["2차 추출 성공 (OCR)", "179", "-"],
        ["총 추출 성공", fmt(extracted), pct(extracted, pdf_peraturan)],
        ["OCR 대기 (100p 이상)", fmt(needs_ocr), pct(needs_ocr, pdf_peraturan)],
        ["추출 실패 (파일 손상)", fmt(extract_failed), pct(extract_failed, pdf_peraturan)],
    ]
    create_table(doc, extract_data)
    doc.add_paragraph()

    add_para(doc, "6.3 추출 통계", bold=True)
    total_chars = query_one("SELECT SUM(LENGTH(extracted_text)) FROM peraturan WHERE extracted_text IS NOT NULL")
    avg_chars = query_one("SELECT AVG(LENGTH(extracted_text)) FROM peraturan WHERE extracted_text IS NOT NULL AND extracted_text != ''")

    char_data = [
        ["항목", "값"],
        ["총 추출 문자 수", f"{fmt(total_chars)} ({total_chars/100000000:.1f}억 자)" if total_chars else "0"],
        ["평균 문서 길이", f"{fmt(int(avg_chars))} 자" if avg_chars else "0"],
    ]
    create_table(doc, char_data)
    doc.add_paragraph()

    # === 7. 구조 파싱 현황 ===
    add_heading(doc, "7. 구조 파싱 현황", 1)

    add_para(doc, "7.1 파싱 대상 구조", bold=True)
    struct_hierarchy = """인도네시아 법령 구조 계층:

    법령 (Peraturan)
    └── BAB (장)
        └── Bagian (편) - 선택적
            └── Paragraf (절) - 선택적
                └── Pasal (조) ★
                    └── Ayat (항)
                        └── Huruf (호)
                            └── Angka (목)

★ Pasal(조)가 핵심 단위 - 법령 검색/인용의 기본 단위"""
    add_code_block(doc, struct_hierarchy)
    doc.add_paragraph()

    add_para(doc, "7.2 파싱 결과", bold=True)
    total_bab = query_one("SELECT SUM(parsed_bab_count) FROM peraturan")
    total_pasal = query_one("SELECT SUM(parsed_pasal_count) FROM peraturan")
    total_ayat = query_one("SELECT SUM(parsed_ayat_count) FROM peraturan")
    total_huruf = query_one("SELECT SUM(parsed_huruf_count) FROM peraturan")

    parse_data = [
        ["구조 요소", "인도네시아어", "추출 건수"],
        ["장", "BAB", fmt(total_bab or 0)],
        ["조", "Pasal", fmt(total_pasal or 0)],
        ["항", "Ayat", fmt(total_ayat or 0)],
        ["호", "Huruf", fmt(total_huruf or 0)],
    ]
    create_table(doc, parse_data)
    doc.add_paragraph()

    add_para(doc, "7.3 유형별 평균 조문 수", bold=True)
    type_pasal = query_peraturan(f"""
        SELECT jenis, AVG(parsed_pasal_count) as avg_pasal, MAX(parsed_pasal_count) as max_pasal
        FROM peraturan WHERE {RFP_WHERE} AND parsed_pasal_count > 0 GROUP BY jenis
    """)

    pasal_data = [["유형", "평균 Pasal", "최대 Pasal"]]
    for row in type_pasal:
        jenis, avg, max_p = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        pasal_data.append([abbr, f"{avg:.1f}" if avg else "0", fmt(max_p)])
    create_table(doc, pasal_data)
    doc.add_paragraph()

    # === 8. PDF 품질 비교 ===
    add_heading(doc, "8. PDF 품질 비교", 1)

    add_para(doc, "8.1 품질 비교 결과", bold=True)
    quality_data = [
        ["항목", "법제처", "BPK"],
        ["텍스트 추출 가능", "97.9%", "99%+"],
        ["스캔 이미지 비율", "~2%", "~1%"],
        ["파일 손상", "6%", "1% 미만"],
        ["평균 파일 크기", "1.5MB", "1.2MB"],
    ]
    create_table(doc, quality_data)
    doc.add_paragraph()

    add_para(doc, "8.2 결론", bold=True)
    doc.add_paragraph("• BPK PDF가 전반적으로 품질 우수")
    doc.add_paragraph("• 법제처 PDF 중 6%는 파일 손상 또는 미제공")
    doc.add_paragraph("• 권장: BPK PDF 우선 사용, 법제처는 보완용")

    doc.add_page_break()

    # ============================================================
    # PART III: 법령 구조 분석
    # ============================================================
    part3 = doc.add_heading("Part III. 법령 구조 분석", level=0)
    part3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 9. 문서 구조 ===
    add_heading(doc, "9. 문서 구조 (BAB/Pasal/Ayat/Huruf)", 1)

    add_para(doc, "9.1 본문 영역 식별", bold=True)
    doc.add_paragraph("인도네시아 법령은 다음 구조로 구성:")
    doc.add_paragraph()

    body_structure = """┌─────────────────────────────────────┐
│ 전문 (Preface)                      │
│   - Menimbang (고려사항)            │
│   - Mengingat (근거조항)            │
├─────────────────────────────────────┤
│ 결정문 마커                          │
│   MEMUTUSKAN:                       │ ← 본문 시작점
│   Menetapkan: ...                   │
├─────────────────────────────────────┤
│ 본문 (Body)                         │
│   BAB I - KETENTUAN UMUM            │
│     Pasal 1                         │
│       (1) ...                       │
│       (2) ...                       │
│   BAB II - ...                      │
│     Pasal 2                         │
│       ...                           │
├─────────────────────────────────────┤
│ 폐쇄문 마커                          │
│   Ditetapkan di Jakarta             │ ← 본문 종료점
│   pada tanggal...                   │
│   Diundangkan di Jakarta            │
└─────────────────────────────────────┘"""
    add_code_block(doc, body_structure)
    doc.add_paragraph()

    add_para(doc, "9.2 본문 마커 검출 현황", bold=True)
    conn = sqlite3.connect(PERATURAN_DB)
    cur = conn.cursor()

    markers = {
        'MEMUTUSKAN': '본문 시작',
        'MENETAPKAN': '본문 시작 (대체)',
        'Ditetapkan di': '본문 종료',
        'Diundangkan di': '공포문',
    }

    rfp_total = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE}")
    marker_data = [["마커", "의미", "검출 건수", "검출률"]]
    for marker, meaning in markers.items():
        cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND extracted_text LIKE ?", (f'%{marker}%',))
        count = cur.fetchone()[0]
        marker_data.append([marker, meaning, fmt(count), pct(count, rfp_total)])
    conn.close()
    create_table(doc, marker_data)
    doc.add_paragraph()

    # === 10. 유형별·연대별 분석 ===
    add_heading(doc, "10. 유형별·연대별 분석", 1)

    add_para(doc, "10.1 연대별 복잡도 변화", bold=True)
    era_stats = query_peraturan(f"""
        SELECT
            CASE WHEN tahun < 1960 THEN '1945-1959'
                 WHEN tahun < 1980 THEN '1960-1979'
                 WHEN tahun < 2000 THEN '1980-1999'
                 WHEN tahun < 2010 THEN '2000-2009'
                 WHEN tahun < 2020 THEN '2010-2019'
                 ELSE '2020-현재' END as era,
            COUNT(*) as cnt,
            AVG(parsed_pasal_count) as avg_pasal,
            AVG(parsed_bab_count) as avg_bab
        FROM peraturan WHERE {RFP_WHERE} AND parsed_pasal_count > 0
        GROUP BY era ORDER BY era
    """)

    era_data = [["연대", "문서 수", "평균 Pasal", "평균 BAB"]]
    for row in era_stats:
        era_data.append([row[0], fmt(row[1]), f"{row[2]:.1f}" if row[2] else "0", f"{row[3]:.1f}" if row[3] else "0"])
    create_table(doc, era_data)
    doc.add_paragraph()
    doc.add_paragraph("※ 법령 복잡도가 시대에 따라 증가: 1950년대 평균 9.5조 → 2020년대 36.6조")
    doc.add_paragraph()

    # === 11. 관계 표현 분석 ===
    add_heading(doc, "11. 관계 표현 분석", 1)

    add_para(doc, "11.1 본문 텍스트 패턴", bold=True)
    conn = sqlite3.connect(PERATURAN_DB)
    cur = conn.cursor()

    patterns = {
        'dicabut dan dinyatakan tidak berlaku': ('폐지 선언', 'REVOKES'),
        'diubah dengan': ('~에 의해 개정됨', 'AMENDED_BY'),
        'mengubah': ('~을 개정함', 'AMENDS'),
        'untuk melaksanakan': ('시행 근거', 'IMPLEMENTS'),
        'berdasarkan': ('법적 근거', 'BASED_ON'),
        'sebagaimana dimaksud dalam': ('참조', 'REFERENCES'),
        'mulai berlaku': ('시행일', 'EFFECTIVE'),
    }

    pattern_data = [["패턴", "한국어", "관계유형", "검출 건수"]]
    for pattern, (korean, rel_type) in patterns.items():
        cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND extracted_text LIKE ?", (f'%{pattern}%',))
        count = cur.fetchone()[0]
        pattern_data.append([pattern[:30], korean, rel_type, fmt(count)])
    conn.close()
    create_table(doc, pattern_data)
    doc.add_paragraph()

    add_para(doc, "11.2 참조 관계 추출 현황", bold=True)
    if refs_data:
        refs_table = [
            ["참조 유형", "총 건수", "관련 문서"],
            ["외부 참조 (다른 법령)", fmt(refs_data['ext_ref_count']), f"{fmt(refs_data['docs_with_ext'])}건"],
            ["내부 참조 (동일 법령 내)", fmt(refs_data['int_ref_count']), f"{fmt(refs_data['docs_with_int'])}건"],
            ["조건부 조항", fmt(refs_data['conditional_count']), f"{fmt(refs_data['docs_with_cond'])}건"],
        ]
        create_table(doc, refs_table)

    doc.add_page_break()

    # ============================================================
    # PART IV: 현행성 판단 체계
    # ============================================================
    part4 = doc.add_heading("Part IV. 현행성 판단 체계", level=0)
    part4.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 12. 현행성 상태 분포 ===
    add_heading(doc, "12. 현행성 상태 분포", 1)

    add_para(doc, "12.1 status 필드 기반 분류", bold=True)
    berlaku = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND status = 'Berlaku'")
    tidak_berlaku = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND status LIKE 'Tidak Berlaku%'")
    dicabut = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND status LIKE '%Dicabut Oleh%'")

    validity_data = [
        ["상태", "건수", "비율", "처리"],
        ["현행 (Berlaku)", fmt(berlaku), pct(berlaku, rfp_total), "자동 판단"],
        ["폐지 (Tidak Berlaku)", fmt(tidak_berlaku), pct(tidak_berlaku, rfp_total), "자동 판단"],
        ["  └ 폐지 법령 명시", fmt(dicabut), pct(dicabut, rfp_total), "관계 추출 가능"],
    ]
    create_table(doc, validity_data)
    doc.add_paragraph()

    add_para(doc, "12.2 status 필드 구조 분석", bold=True)
    doc.add_paragraph("폐지된 법령의 경우 status 필드에 폐지한 법령 정보가 함께 포함:")
    status_example = """
[현행 법령]
status = "Berlaku"

[폐지된 법령]
status = "Tidak BerlakuDicabut Oleh :Peraturan Menteri Keuangan
         Nomor 3 Tahun 2024 Tentang Tarif Layanan..."

→ 추출 가능 정보:
  - 현행성: Tidak Berlaku (폐지)
  - 폐지한 법령: Peraturan Menteri Keuangan No. 3/2024
  - 관계: REVOKED_BY"""
    add_code_block(doc, status_example)
    doc.add_paragraph()

    # === 13. 자동 판단 가능 범위 ===
    add_heading(doc, "13. 자동 판단 가능 범위", 1)

    add_para(doc, "13.1 자동 판단 가능률", bold=True)
    auto_judge = berlaku + dicabut
    auto_pct = auto_judge / rfp_total * 100 if rfp_total else 0

    auto_data = [
        ["구분", "건수", "비율"],
        ["자동 판단 가능", fmt(auto_judge), f"{auto_pct:.1f}%"],
        ["  - 현행 (Berlaku)", fmt(berlaku), pct(berlaku, rfp_total)],
        ["  - 폐지 (명시적)", fmt(dicabut), pct(dicabut, rfp_total)],
        ["수동 검토 필요", fmt(rfp_total - auto_judge), f"{100-auto_pct:.1f}%"],
    ]
    create_table(doc, auto_data)
    doc.add_paragraph()

    add_para(doc, "13.2 자동 판단 로직", bold=True)
    judge_logic = """def judge_validity(law):
    if law.status == 'Berlaku':
        return '현행'
    elif 'Dicabut Oleh' in law.status:
        revoker = extract_revoking_law(law.status)
        return f'폐지됨 (by {revoker})'
    elif 'Tidak Berlaku' in law.status:
        return '폐지됨 (상세 불명)'
    else:
        return '확인 필요'"""
    add_code_block(doc, judge_logic)
    doc.add_paragraph()

    # === 14. 조건부 효력 (회색지대) ===
    add_heading(doc, "14. 조건부 효력 (회색지대)", 1)

    add_para(doc, "14.1 조건부 효력이란?", bold=True)
    doc.add_paragraph("신법이 구법을 전면 폐지하지 않고, 특정 조건 하에서 구법의 효력을 유지시키는 경우")
    doc.add_paragraph("→ 법률가 검토 없이는 현행/폐지 판단 불가 (회색지대)")
    doc.add_paragraph()

    add_para(doc, "14.2 주요 조건부 표현", bold=True)
    conn = sqlite3.connect(PERATURAN_DB)
    cur = conn.cursor()

    conditional_patterns = {
        'sepanjang tidak bertentangan': '상충되지 않는 한',
        'sepanjang belum diatur': '아직 규정되지 않은 한',
        'tetap berlaku sepanjang': '~하는 한 계속 유효',
        'tetap berlaku sampai': '~까지 계속 유효',
        'dinyatakan masih tetap berlaku': '계속 유효한 것으로 선언',
    }

    cond_data = [["인도네시아어", "한국어", "검출 건수", "처리"]]
    for pattern, korean in conditional_patterns.items():
        cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND extracted_text LIKE ?", (f'%{pattern}%',))
        count = cur.fetchone()[0]
        cond_data.append([pattern[:30], korean, fmt(count), "법률가 검토"])
    conn.close()
    create_table(doc, cond_data)
    doc.add_paragraph()

    add_para(doc, "14.3 조건부 효력 예시", bold=True)
    cond_example = """[예시: UU No. 11 Tahun 2020 (Cipta Kerja)]

Pasal 185:
"Peraturan pelaksanaan dari Undang-Undang yang telah diubah
berdasarkan Undang-Undang ini dinyatakan masih tetap berlaku
sepanjang tidak bertentangan dengan ketentuan dalam
Undang-Undang ini."

번역:
"이 법률에 의해 개정된 법률의 시행규정은 이 법률의 규정과
상충되지 않는 한 계속 유효한 것으로 선언한다."

→ 구법 시행규정 수백개가 "조건부 현행" 상태
→ 개별 검토 없이는 현행/폐지 판단 불가"""
    add_code_block(doc, cond_example)
    doc.add_paragraph()

    # === 15. 업무 분담 설계 ===
    add_heading(doc, "15. 업무 분담 설계", 1)

    add_para(doc, "15.1 판단 주체별 분류", bold=True)
    work_data = [
        ["구분", "판단 주체", "건수", "내용"],
        ["자동 판단", "시스템", f"~{fmt(auto_judge)}", "Berlaku/Dicabut 명확한 경우"],
        ["조건부", "법률가", f"~{fmt(refs_data['conditional_count']) if refs_data else '2,000'}+", "sepanjang tidak bertentangan 등"],
        ["불명확", "법률가", f"~{fmt(rfp_total - auto_judge - (refs_data['conditional_count'] if refs_data else 2000))}", "기타 판단 어려운 경우"],
    ]
    create_table(doc, work_data)
    doc.add_paragraph()

    add_para(doc, "15.2 법률가 검토 워크플로우", bold=True)
    workflow = """┌──────────────────────────────────────────────┐
│ 1. 시스템 자동 분류                            │
│    - 현행 (Berlaku): 표시                     │
│    - 폐지 (Dicabut): 표시 + 폐지법령 링크     │
│    - 조건부: "법률가 검토 필요" 플래그          │
└──────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────┐
│ 2. 법률가 검토 큐                              │
│    - 조건부 효력 표현 포함 법령 목록           │
│    - 관련 신법/구법 링크                       │
│    - 검토 의견 입력 폼                         │
└──────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────┐
│ 3. 최종 상태 업데이트                          │
│    - 현행 / 폐지 / 부분현행 중 선택           │
│    - 검토 근거 기록                            │
│    - 검토일시 + 검토자 기록                    │
└──────────────────────────────────────────────┘"""
    add_code_block(doc, workflow)

    doc.add_page_break()

    # ============================================================
    # PART V: 시스템 설계
    # ============================================================
    part5 = doc.add_heading("Part V. 시스템 설계", level=0)
    part5.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 16. 메타데이터 추출 전략 ===
    add_heading(doc, "16. 메타데이터 추출 전략", 1)

    add_para(doc, "16.1 DB 필드 활용", bold=True)
    db_fields = [
        ["필드", "용도", "활용도"],
        ["slug", "고유 식별자 (Neo4j 노드 ID)", "★★★"],
        ["jenis + nomor + tahun", "법령 참조키", "★★★"],
        ["status", "현행성 + 폐지법령 관계", "★★★"],
        ["pemrakarsa", "발령기관 노드 생성", "★★☆"],
        ["tanggal_penetapan", "제정일", "★★☆"],
        ["extracted_text", "본문 관계 추출", "★★★"],
    ]
    create_table(doc, db_fields)
    doc.add_paragraph()

    add_para(doc, "16.2 status 필드 관계 추출", bold=True)
    regex_code = """import re

# 폐지 법령 추출 정규식
pattern = r'Dicabut Oleh\\s*:\\s*(Peraturan\\s+\\w+(?:\\s+\\w+)*)\\s+Nomor\\s+(\\S+)\\s+Tahun\\s+(\\d{4})'

status = "Tidak BerlakuDicabut Oleh :Peraturan Menteri Keuangan Nomor 3 Tahun 2024..."
match = re.search(pattern, status)

# 결과:
# match.group(1) = "Peraturan Menteri Keuangan"
# match.group(2) = "3"
# match.group(3) = "2024" """
    add_code_block(doc, regex_code)
    doc.add_paragraph()

    # === 17. Neo4j 그래프 모델 ===
    add_heading(doc, "17. Neo4j 그래프 모델", 1)

    add_para(doc, "17.1 노드 유형", bold=True)
    node_model = """(:Law)                    법령 노드
  - id: string            고유 식별자 (slug)
  - jenis: string         법령 유형
  - nomor: string         번호
  - tahun: integer        연도
  - tentang: string       제목
  - validity: string      현행/폐지/조건부

(:Agency)                 발령기관 노드
  - name: string          기관명
  - name_ko: string       한국어명

(:Condition)              조건 노드 (조건부 효력용)
  - type: string          조건 유형
  - text: string          원문
  - text_ko: string       한국어 번역"""
    add_code_block(doc, node_model)
    doc.add_paragraph()

    add_para(doc, "17.2 관계 유형", bold=True)
    rel_model = """-[:REVOKES]->             폐지 (A가 B를 폐지)
-[:AMENDS]->              개정 (A가 B를 개정)
-[:REFERENCES]->          참조 (A가 B를 인용)
-[:IMPLEMENTS]->          시행 (A는 B의 시행령)
-[:BASED_ON]->            근거 (A가 B에 근거)
-[:CONDITIONAL_VALID]->   조건부 효력 (A가 B의 조건부 효력)
-[:ISSUED_BY]->           발령 (기관→법령)"""
    add_code_block(doc, rel_model)
    doc.add_paragraph()

    add_para(doc, "17.3 Cypher 쿼리 예시", bold=True)
    cypher = """// 특정 법령의 현행성 확인
MATCH (law:Law {id: 'uu-no-11-tahun-2020'})
OPTIONAL MATCH (law)<-[:REVOKES]-(revoker:Law)
RETURN law.tentang AS 제목,
       CASE WHEN revoker IS NOT NULL THEN '폐지됨'
            WHEN law.validity = 'Berlaku' THEN '현행'
            ELSE '확인필요' END AS 현행성

// 조건부 효력 법령 조회
MATCH (old:Law)-[r:CONDITIONAL_VALID]->(c:Condition)
WHERE c.type = 'SEPANJANG_TIDAK_BERTENTANGAN'
RETURN old.id, c.text_ko AS 조건, old.validity

// 특정 법령을 참조하는 모든 법령
MATCH (referrer:Law)-[:REFERENCES]->(target:Law {id: 'uu-no-13-tahun-2003'})
RETURN referrer.id, referrer.tahun ORDER BY referrer.tahun DESC"""
    add_code_block(doc, cypher)
    doc.add_paragraph()

    # === 18. 국제표준 XML (Akoma Ntoso) ===
    add_heading(doc, "18. 국제표준 XML (Akoma Ntoso)", 1)

    add_para(doc, "18.1 Akoma Ntoso 개요", bold=True)
    doc.add_paragraph("• 표준: OASIS LegalDocML TC")
    doc.add_paragraph("• 버전: Akoma Ntoso 3.0")
    doc.add_paragraph("• 네임스페이스: http://docs.oasis-open.org/legaldocml/ns/akn/3.0")
    doc.add_paragraph("• 목적: 법령 문서의 국제 표준화 및 상호운용성")
    doc.add_paragraph()

    add_para(doc, "18.2 문서 구조", bold=True)
    akn_structure = """<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="uu-no-11-tahun-2020">
    <meta>
      <identification source="#jdih">
        <FRBRWork> ... </FRBRWork>
        <FRBRExpression> ... </FRBRExpression>
        <FRBRManifestation> ... </FRBRManifestation>
      </identification>
      <lifecycle source="#jdih">
        <eventRef date="2020-11-02" type="enactment"/>
        <eventRef date="2023-12-31" type="repeal"/>
      </lifecycle>
      <analysis source="#system">
        <passiveRef href="/akn/id/act/2023/6" showAs="UU 6/2023"/>
      </analysis>
    </meta>
    <preface> ... </preface>
    <body>
      <chapter eId="bab_1">
        <num>BAB I</num>
        <heading>KETENTUAN UMUM</heading>
        <article eId="art_1">
          <num>Pasal 1</num>
          <paragraph eId="art_1__para_1">
            <num>(1)</num>
            <content><p>...</p></content>
          </paragraph>
        </article>
      </chapter>
    </body>
    <conclusions> ... </conclusions>
  </act>
</akomaNtoso>"""
    add_code_block(doc, akn_structure)
    doc.add_paragraph()

    add_para(doc, "18.3 조건부 효력 XML 표현", bold=True)
    cond_xml = """<article eId="art_185">
  <num>Pasal 185</num>
  <content>
    <p>Peraturan pelaksanaan... dinyatakan masih
      <mod type="conditionalValidity"
           refersTo="#sepanjang-tidak-bertentangan">
        <quotedText>tetap berlaku sepanjang tidak bertentangan</quotedText>
        <quotedText xml:lang="ko">상충되지 않는 한 계속 유효</quotedText>
      </mod>
      dengan ketentuan dalam Undang-Undang ini.
    </p>
  </content>
</article>"""
    add_code_block(doc, cond_xml)
    doc.add_paragraph()

    # === 19. 구현 로드맵 ===
    add_heading(doc, "19. 구현 로드맵", 1)

    add_para(doc, "19.1 단계별 작업", bold=True)
    roadmap = [
        ["단계", "작업 내용", "산출물", "우선순위"],
        ["1", "메타데이터 정제 및 병합", "통합 DB", "★★★"],
        ["2", "status 필드 관계 추출", "relations.json", "★★★"],
        ["3", "본문 관계 추출", "text_relations.json", "★★☆"],
        ["4", "Neo4j 스키마 생성", "Cypher DDL", "★★★"],
        ["5", "Neo4j 데이터 적재", "법령 그래프 DB", "★★★"],
        ["6", "현행성 자동 판단", "validity 필드", "★★★"],
        ["7", "XML 변환기 개발", "AKN Converter", "★★☆"],
        ["8", "XML 일괄 변환", "35,000+ XML", "★★☆"],
        ["9", "OCR 파이프라인", "691건 추가 추출", "★☆☆"],
        ["10", "법률가 검토 UI", "Review Dashboard", "★★☆"],
    ]
    create_table(doc, roadmap)
    doc.add_paragraph()

    add_para(doc, "19.2 기술 스택", bold=True)
    tech_data = [
        ["영역", "기술", "용도"],
        ["DB", "SQLite → PostgreSQL", "메타데이터 저장"],
        ["그래프", "Neo4j", "법령 관계 표현"],
        ["검색", "Elasticsearch", "전문 검색"],
        ["XML", "lxml + 자체 변환기", "Akoma Ntoso 생성"],
        ["OCR", "Tesseract + PaddleOCR", "이미지 PDF 처리"],
        ["UI", "React + FastAPI", "검토 대시보드"],
    ]
    create_table(doc, tech_data)

    doc.add_page_break()

    # === 부록 ===
    add_heading(doc, "부록", level=0)
    doc.add_page_break()

    # 부록 A
    add_heading(doc, "부록 A. 법령 유형 용어 대조표", 1)
    type_terms = [
        ["인도네시아어", "약어", "한국어", "영어"],
        ["Undang-Undang", "UU", "법률", "Law"],
        ["Peraturan Pemerintah", "PP", "정부령", "Government Regulation"],
        ["Peraturan Presiden", "Perpres", "대통령령", "Presidential Regulation"],
        ["Peraturan Pemerintah Pengganti UU", "Perppu", "긴급법률", "Emergency Law"],
        ["Peraturan Menteri", "Permen", "장관령", "Ministerial Regulation"],
        ["Peraturan Badan/Lembaga", "-", "기관규정", "Agency Regulation"],
        ["Keputusan Presiden", "Keppres", "대통령결정", "Presidential Decision"],
        ["Instruksi Presiden", "Inpres", "대통령지시", "Presidential Instruction"],
        ["Penetapan Presiden", "Penpres", "대통령확정", "Presidential Stipulation"],
        ["Peraturan Daerah", "Perda", "지방규정", "Regional Regulation"],
    ]
    create_table(doc, type_terms)
    doc.add_paragraph()

    # 부록 B
    add_heading(doc, "부록 B. 관계 표현 용어 대조표", 1)
    rel_terms = [
        ["인도네시아어", "한국어", "영어", "용례"],
        ["Berlaku", "현행", "In Force", "status 필드"],
        ["Tidak Berlaku", "폐지", "Not In Force", "status 필드"],
        ["Dicabut", "폐지됨", "Revoked", "status 필드"],
        ["Diubah", "개정됨", "Amended", "본문"],
        ["mencabut", "~을 폐지함", "revokes", "본문"],
        ["mengubah", "~을 개정함", "amends", "본문"],
        ["berdasarkan", "~에 근거하여", "based on", "전문"],
        ["untuk melaksanakan", "~을 시행하기 위해", "to implement", "전문"],
        ["mulai berlaku", "시행", "enters into force", "본문"],
        ["Menimbang", "고려사항", "Considering", "전문"],
        ["Mengingat", "근거조항", "Having Regard To", "전문"],
        ["MEMUTUSKAN", "결정하다", "DECIDES", "본문 시작"],
        ["Ditetapkan di", "~에서 확정됨", "Enacted at", "본문 종료"],
    ]
    create_table(doc, rel_terms)
    doc.add_paragraph()

    # 부록 C
    add_heading(doc, "부록 C. 조건부 효력 표현 상세", 1)
    cond_terms = [
        ["인도네시아어", "한국어", "의미", "시스템 처리"],
        ["sepanjang tidak bertentangan", "상충되지 않는 한", "신법과 충돌 없으면 유효", "조건부 현행"],
        ["sepanjang belum diatur", "아직 규정되지 않은 한", "신법에 규정 없으면 유효", "조건부 현행"],
        ["tetap berlaku", "계속 유효", "명시적 유지 선언", "현행"],
        ["tetap berlaku sepanjang", "~하는 한 계속 유효", "조건부 유지", "조건부 현행"],
        ["tetap berlaku sampai", "~까지 계속 유효", "한시적 유지", "조건부 현행 (기한)"],
        ["dinyatakan masih berlaku", "유효한 것으로 선언", "명시적 유지 선언", "현행"],
        ["tidak bertentangan dengan", "~와 상충되지 않는", "조건 표현", "조건부"],
        ["dicabut dan dinyatakan tidak berlaku", "폐지되고 효력 없음 선언", "명시적 폐지", "폐지"],
    ]
    create_table(doc, cond_terms)

    # 저장
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_FILE)
    print(f"\n✅ 최종 통합 보고서 생성 완료: {OUTPUT_FILE}")
    print(f"   파일 크기: {OUTPUT_FILE.stat().st_size / 1024:.1f} KB")

    return OUTPUT_FILE


if __name__ == "__main__":
    generate_report()
