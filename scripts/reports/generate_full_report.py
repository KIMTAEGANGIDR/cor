#!/usr/bin/env python3
"""
인도네시아 법령정보시스템 종합 보고서 생성 스크립트

전체 파이프라인 포함:
- Part I: 데이터 수집 (크롤링, 다운로드, 추출, 파싱)
- Part II: 법령 구조 분석
- Part III: 시스템 설계
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
from docx.shared import Pt, RGBColor, Inches

# === 경로 설정 ===
PROJECT_ROOT = Path(__file__).parent.parent
PERATURAN_DB = PROJECT_ROOT / "peraturan" / "data" / "peraturan.db"
PDF_ROOT = PROJECT_ROOT / "peraturan" / "data" / "pdfs"
REFERENCES_JSON = PROJECT_ROOT / "peraturan" / "data" / "all_references.json"
OUTPUT_DIR = PROJECT_ROOT / "docs" / "exports"
OUTPUT_FILE = OUTPUT_DIR / "인도네시아_법령정보시스템_종합보고서.docx"

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


def query_db(query: str, params=None) -> list[tuple]:
    if not PERATURAN_DB.exists():
        return []
    conn = sqlite3.connect(PERATURAN_DB)
    if params:
        cursor = conn.execute(query, params)
    else:
        cursor = conn.execute(query)
    results = cursor.fetchall()
    conn.close()
    return results


def query_one(query: str) -> Any:
    result = query_db(query)
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
                set_cell_shading(cell, "4472C4")
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


def get_pdf_folder_stats():
    """PDF 폴더 통계"""
    if not PDF_ROOT.exists():
        return {}

    stats = {}
    total_size = 0
    total_count = 0

    for subdir in PDF_ROOT.iterdir():
        if subdir.is_dir():
            size = 0
            count = 0
            for f in subdir.rglob('*.pdf'):
                size += f.stat().st_size
                count += 1
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
    print("=" * 60)
    print("인도네시아 법령정보시스템 종합 보고서 생성")
    print("=" * 60)

    doc = Document()

    # === 표지 ===
    for _ in range(3):
        doc.add_paragraph()

    title = doc.add_heading("인도네시아 법령정보시스템", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = doc.add_heading("종합 분석 및 설계 보고서", level=1)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for _ in range(2):
        doc.add_paragraph()

    info = doc.add_paragraph()
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info.add_run("데이터 수집 · 텍스트 추출 · 구조 분석\nNeo4j 그래프 모델 · 국제표준 XML 설계")
    run.font.size = Pt(14)

    for _ in range(4):
        doc.add_paragraph()

    date_para = doc.add_paragraph()
    date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = date_para.add_run(f"작성일: {datetime.now().strftime('%Y년 %m월 %d일')}")
    run.font.size = Pt(12)

    doc.add_page_break()

    # === 목차 ===
    add_heading(doc, "목차", 1)
    toc = """
Part I. 데이터 수집
  1. 수집 개요
  2. 크롤링 대상 및 방법
  3. 메타데이터 수집 결과
  4. PDF 다운로드 현황
  5. 텍스트 추출 현황
  6. 구조 파싱 현황

Part II. 법령 구조 분석
  7. 분석 대상 (RFP 기준)
  8. 문서 구조 분석
  9. 현행성 판단 지표
  10. 관계성 표현 분석

Part III. 시스템 설계
  11. 메타데이터 추출 전략
  12. Neo4j 그래프 모델
  13. 법령 표현 한글 번역
  14. 국제표준 XML 설계 (Akoma Ntoso)
  15. 구현 로드맵

부록
  A. 법령 유형 용어 대조표
  B. 관계 표현 용어 대조표
"""
    doc.add_paragraph(toc)
    doc.add_page_break()

    # ============================================================
    # PART I: 데이터 수집
    # ============================================================
    part1_title = doc.add_heading("Part I. 데이터 수집", level=0)
    part1_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 1. 수집 개요 ===
    add_heading(doc, "1. 수집 개요", 1)

    add_para(doc, "1.1 프로젝트 목적", bold=True)
    doc.add_paragraph("인도네시아 법령정보시스템 구축을 위한 기초 데이터 수집 및 분석")
    doc.add_paragraph("• 인도네시아 국가법령정보센터(peraturan.go.id)의 모든 법령 메타데이터 수집")
    doc.add_paragraph("• PDF 원문 다운로드 및 텍스트 추출")
    doc.add_paragraph("• 법령 구조(장/조/항/호) 파싱 및 관계성 분석")
    doc.add_paragraph()

    add_para(doc, "1.2 수집 기간 및 범위", bold=True)

    total = query_one("SELECT COUNT(*) FROM peraturan")
    min_year = query_one("SELECT MIN(tahun) FROM peraturan")
    max_year = query_one("SELECT MAX(tahun) FROM peraturan")

    overview_data = [
        ["항목", "내용"],
        ["수집 대상", "peraturan.go.id (인도네시아 국가법령정보센터)"],
        ["수집 기간", "2025년 12월"],
        ["법령 연도 범위", f"{min_year}년 ~ {max_year}년"],
        ["총 수집 건수", f"{fmt(total)}건"],
    ]
    create_table(doc, overview_data)
    doc.add_paragraph()

    add_para(doc, "1.3 전체 파이프라인", bold=True)
    pipeline = """
┌─────────────────────────────────────────────────────────────────┐
│  1. 크롤링        peraturan.go.id에서 메타데이터 수집            │
│     (httpx)       → 41,053건 법령 정보                          │
├─────────────────────────────────────────────────────────────────┤
│  2. PDF 다운로드   원문 PDF 파일 다운로드                         │
│     (httpx)       → 36,037개 파일, 54.3GB                       │
├─────────────────────────────────────────────────────────────────┤
│  3. 텍스트 추출    PDF에서 텍스트 추출                            │
│     (PyMuPDF)     → 32,005건 성공 (97.9%)                       │
│     + OCR         → 512건 스캔 이미지 (Tesseract)                │
├─────────────────────────────────────────────────────────────────┤
│  4. 구조 파싱      BAB/Pasal/Ayat/Huruf 구조 추출                │
│     (정규식)      → 30,833건 파싱 완료                           │
├─────────────────────────────────────────────────────────────────┤
│  5. 관계 분석      법령 간 참조/폐지/개정 관계 추출               │
│     (NLP/정규식)  → 외부참조 23,328건, 내부참조 261,451건        │
└─────────────────────────────────────────────────────────────────┘
"""
    add_code_block(doc, pipeline)
    doc.add_paragraph()

    # === 2. 크롤링 대상 및 방법 ===
    add_heading(doc, "2. 크롤링 대상 및 방법", 1)

    add_para(doc, "2.1 대상 사이트", bold=True)
    site_data = [
        ["항목", "내용"],
        ["사이트명", "JDIH Nasional (국가법령정보네트워크)"],
        ["URL", "https://peraturan.go.id"],
        ["운영기관", "법무인권부 (Kemenkumham)"],
        ["데이터 형태", "HTML 페이지 + PDF 파일"],
    ]
    create_table(doc, site_data)
    doc.add_paragraph()

    add_para(doc, "2.2 크롤링 방법", bold=True)
    doc.add_paragraph("• 언어/프레임워크: Python 3.11, httpx (비동기 HTTP), BeautifulSoup (HTML 파싱)")
    doc.add_paragraph("• 수집 방식: 법령 목록 페이지 순회 → 상세 페이지 크롤링 → 메타데이터 추출")
    doc.add_paragraph("• 동시성 제어: 연속 에러 10회 발생 시 30초 대기")
    doc.add_paragraph("• 자동 복구: Watchdog 모니터링 (180초 stall 감지 시 재시작)")
    doc.add_paragraph()

    add_para(doc, "2.3 수집 메타데이터 필드", bold=True)
    fields_data = [
        ["필드명", "설명", "예시"],
        ["slug", "고유 식별자", "uu-no-11-tahun-2020"],
        ["jenis", "법령 유형", "UNDANG-UNDANG"],
        ["nomor", "법령 번호", "11"],
        ["tahun", "제정 연도", "2020"],
        ["tentang", "법령 제목", "CIPTA KERJA"],
        ["status", "현행성 상태", "Berlaku / Tidak Berlaku"],
        ["pemrakarsa", "발령기관", "Kementerian Keuangan"],
        ["tanggal_penetapan", "제정일", "2020-11-02"],
        ["pdf_url", "PDF 다운로드 URL", "https://..."],
    ]
    create_table(doc, fields_data)
    doc.add_paragraph()

    # === 3. 메타데이터 수집 결과 ===
    add_heading(doc, "3. 메타데이터 수집 결과", 1)

    add_para(doc, "3.1 전체 수집 현황", bold=True)

    has_pdf_url = query_one("SELECT COUNT(*) FROM peraturan WHERE pdf_url IS NOT NULL AND pdf_url != ''")
    no_pdf_url = total - has_pdf_url

    meta_summary = [
        ["항목", "건수", "비율"],
        ["메타데이터 수집 완료", fmt(total), "100%"],
        ["PDF URL 있음", fmt(has_pdf_url), pct(has_pdf_url, total)],
        ["PDF URL 없음 (원본 미제공)", fmt(no_pdf_url), pct(no_pdf_url, total)],
    ]
    create_table(doc, meta_summary)
    doc.add_paragraph()

    add_para(doc, "3.2 법령 유형별 수집 현황", bold=True)

    type_stats = query_db("""
        SELECT jenis, COUNT(*) as cnt,
               SUM(CASE WHEN pdf_url IS NOT NULL AND pdf_url != '' THEN 1 ELSE 0 END) as has_url,
               SUM(CASE WHEN local_pdf_path IS NOT NULL AND local_pdf_path != '' THEN 1 ELSE 0 END) as downloaded
        FROM peraturan GROUP BY jenis ORDER BY cnt DESC
    """)

    type_data = [["법령 유형", "한국어", "수집 건수", "PDF URL", "다운로드"]]
    for row in type_stats:
        jenis, cnt, has_url, downloaded = row
        korean = ALL_TYPE_KOREAN.get(jenis, jenis[:10])
        type_data.append([
            jenis[:30], korean, fmt(cnt), fmt(has_url), fmt(downloaded)
        ])
    create_table(doc, type_data)
    doc.add_paragraph()

    add_para(doc, "3.3 연도별 수집 현황", bold=True)

    year_stats = query_db("""
        SELECT
            CASE
                WHEN tahun < 1960 THEN '1945-1959'
                WHEN tahun < 1980 THEN '1960-1979'
                WHEN tahun < 2000 THEN '1980-1999'
                WHEN tahun < 2010 THEN '2000-2009'
                WHEN tahun < 2020 THEN '2010-2019'
                ELSE '2020-현재'
            END as era,
            COUNT(*) as cnt
        FROM peraturan GROUP BY era ORDER BY era
    """)

    year_data = [["연대", "수집 건수"]]
    for row in year_stats:
        year_data.append([row[0], fmt(row[1])])
    create_table(doc, year_data)
    doc.add_paragraph()

    # === 4. PDF 다운로드 현황 ===
    add_heading(doc, "4. PDF 다운로드 현황", 1)

    add_para(doc, "4.1 다운로드 결과 요약", bold=True)

    downloaded = query_one("SELECT COUNT(*) FROM peraturan WHERE local_pdf_path IS NOT NULL AND local_pdf_path != ''")
    pdf_stats = get_pdf_folder_stats()
    total_size = pdf_stats.get('_total', {}).get('size', 0)
    total_files = pdf_stats.get('_total', {}).get('count', 0)

    download_summary = [
        ["항목", "값"],
        ["다운로드 대상 (PDF URL 있음)", fmt(has_pdf_url)],
        ["다운로드 완료", fmt(downloaded)],
        ["다운로드 성공률", pct(downloaded, has_pdf_url)],
        ["총 파일 수", f"{fmt(total_files)}개"],
        ["총 용량", f"{total_size/1024/1024/1024:.1f} GB"],
        ["평균 파일 크기", f"{total_size/total_files/1024/1024:.1f} MB" if total_files else "0"],
    ]
    create_table(doc, download_summary)
    doc.add_paragraph()

    add_para(doc, "4.2 폴더별 용량", bold=True)

    folder_data = [["폴더", "파일 수", "용량"]]
    for folder, stats in sorted(pdf_stats.items()):
        if folder != '_total':
            size_gb = stats['size'] / 1024 / 1024 / 1024
            folder_data.append([folder, fmt(stats['count']), f"{size_gb:.1f} GB"])
    create_table(doc, folder_data)
    doc.add_paragraph()

    add_para(doc, "4.3 PDF 미제공 원인 분석", bold=True)
    doc.add_paragraph("원본 사이트에서 PDF가 제공되지 않는 경우:")
    doc.add_paragraph("• 역사적 문서 (1945-1970년대): 물리적 원본 훼손/분실, 디지털화 미완료")
    doc.add_paragraph("• 내부 행정규정: 공개 우선순위 낮음")
    doc.add_paragraph("• 긴급법률(PERPPU): 1959-1960년대 정치적 혼란기 발령, 대부분 이후 법률로 대체")
    doc.add_paragraph()

    # === 5. 텍스트 추출 현황 ===
    add_heading(doc, "5. 텍스트 추출 현황", 1)

    add_para(doc, "5.1 추출 방법", bold=True)
    doc.add_paragraph("• 1차 추출: PyMuPDF (fitz) 라이브러리로 텍스트 레이어 추출")
    doc.add_paragraph("• 2차 추출: 스캔 이미지 PDF의 경우 Tesseract OCR 사용")
    doc.add_paragraph("• 언어 설정: 인도네시아어 (ind)")
    doc.add_paragraph()

    add_para(doc, "5.2 추출 결과", bold=True)

    extracted = query_one("SELECT COUNT(*) FROM peraturan WHERE extraction_success = 1")
    needs_ocr = query_one("SELECT COUNT(*) FROM peraturan WHERE needs_ocr = 1")
    extract_failed = query_one("SELECT COUNT(*) FROM peraturan WHERE extraction_success = 0 AND local_pdf_path IS NOT NULL AND local_pdf_path != ''")

    extract_summary = [
        ["항목", "건수", "비율"],
        ["추출 대상 (PDF 다운로드 완료)", fmt(downloaded), "100%"],
        ["1차 추출 성공 (PyMuPDF)", fmt(extracted - 179), pct(extracted - 179, downloaded)],
        ["2차 추출 성공 (OCR)", "179", "-"],
        ["총 추출 성공", fmt(extracted), pct(extracted, downloaded)],
        ["OCR 필요 (100페이지 이상)", fmt(needs_ocr), pct(needs_ocr, downloaded)],
        ["추출 실패 (파일 손상)", fmt(extract_failed), pct(extract_failed, downloaded)],
    ]
    create_table(doc, extract_summary)
    doc.add_paragraph()

    add_para(doc, "5.3 추출 통계", bold=True)

    total_chars = query_one("SELECT SUM(LENGTH(extracted_text)) FROM peraturan WHERE extracted_text IS NOT NULL")
    avg_chars = query_one("SELECT AVG(LENGTH(extracted_text)) FROM peraturan WHERE extracted_text IS NOT NULL AND extracted_text != ''")

    char_summary = [
        ["항목", "값"],
        ["총 추출 문자 수", f"{fmt(total_chars)} ({total_chars/100000000:.1f}억 자)" if total_chars else "0"],
        ["평균 문서 길이", f"{fmt(int(avg_chars))} 자" if avg_chars else "0"],
    ]
    create_table(doc, char_summary)
    doc.add_paragraph()

    # === 6. 구조 파싱 현황 ===
    add_heading(doc, "6. 구조 파싱 현황", 1)

    add_para(doc, "6.1 파싱 방법", bold=True)
    doc.add_paragraph("• 정규식 기반 구조 추출")
    doc.add_paragraph("• BAB (장) → Pasal (조) → Ayat (항) → Huruf (호) 계층 구조 파싱")
    doc.add_paragraph("• 본문 시작 마커: 'MEMUTUSKAN', 'MENETAPKAN'")
    doc.add_paragraph("• 본문 종료 마커: 'Ditetapkan di', 'Diundangkan di'")
    doc.add_paragraph()

    add_para(doc, "6.2 파싱 결과", bold=True)

    parsed = query_one("SELECT COUNT(*) FROM peraturan WHERE parsed_pasal_count > 0")
    total_bab = query_one("SELECT SUM(parsed_bab_count) FROM peraturan")
    total_pasal = query_one("SELECT SUM(parsed_pasal_count) FROM peraturan")
    total_ayat = query_one("SELECT SUM(parsed_ayat_count) FROM peraturan")
    total_huruf = query_one("SELECT SUM(parsed_huruf_count) FROM peraturan")

    parse_summary = [
        ["항목", "건수"],
        ["파싱 대상 (텍스트 추출 성공)", fmt(extracted)],
        ["파싱 완료", f"{fmt(parsed)} ({pct(parsed, extracted)})"],
        ["추출된 BAB (장)", fmt(total_bab or 0)],
        ["추출된 Pasal (조)", fmt(total_pasal or 0)],
        ["추출된 Ayat (항)", fmt(total_ayat or 0)],
        ["추출된 Huruf (호)", fmt(total_huruf or 0)],
    ]
    create_table(doc, parse_summary)
    doc.add_paragraph()

    add_para(doc, "6.3 유형별 파싱 결과", bold=True)

    type_parse = query_db(f"""
        SELECT jenis, COUNT(*) as total,
               SUM(CASE WHEN parsed_pasal_count > 0 THEN 1 ELSE 0 END) as parsed,
               AVG(parsed_pasal_count) as avg_pasal
        FROM peraturan WHERE {RFP_WHERE}
        GROUP BY jenis ORDER BY total DESC
    """)

    type_parse_data = [["법령 유형", "총 건수", "파싱 완료", "평균 Pasal"]]
    for row in type_parse:
        jenis, total_t, parsed_t, avg = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        type_parse_data.append([
            f"{RFP_TYPE_KOREAN.get(jenis, jenis[:8])} ({abbr})",
            fmt(total_t), f"{fmt(parsed_t)} ({pct(parsed_t, total_t)})",
            f"{avg:.1f}" if avg else "0"
        ])
    create_table(doc, type_parse_data)

    doc.add_page_break()

    # ============================================================
    # PART II: 법령 구조 분석
    # ============================================================
    part2_title = doc.add_heading("Part II. 법령 구조 분석", level=0)
    part2_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 7. 분석 대상 (RFP 기준) ===
    add_heading(doc, "7. 분석 대상 (RFP 기준)", 1)

    add_para(doc, "7.1 RFP 대상 법령 유형", bold=True)
    doc.add_paragraph("제안요청서(RFP)에서 요청한 6개 법령 유형만 분석 대상으로 선정:")

    rfp_total = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE}")

    rfp_data = [["약어", "인도네시아어", "한국어"]]
    for jenis, abbr in RFP_TYPE_ABBREV.items():
        korean = RFP_TYPE_KOREAN.get(jenis, '')
        rfp_data.append([abbr, jenis, korean])
    create_table(doc, rfp_data)
    doc.add_paragraph()
    doc.add_paragraph(f"※ RFP 대상 법령 총계: {fmt(rfp_total)}건")
    doc.add_paragraph()

    add_para(doc, "7.2 분석 제외 대상", bold=True)
    doc.add_paragraph("• KEPUTUSAN PRESIDEN (대통령결정): 5,220건 - RFP 미포함")
    doc.add_paragraph("• INSTRUKSI PRESIDEN (대통령지시): 385건 - RFP 미포함")
    doc.add_paragraph("• PERATURAN DAERAH (지방규정): 12건 - 지방법령 제외")
    doc.add_paragraph()

    # === 8. 문서 구조 분석 ===
    add_heading(doc, "8. 문서 구조 분석", 1)

    add_para(doc, "8.1 유형별 구조 요소 보유율", bold=True)

    struct_stats = query_db(f"""
        SELECT jenis, COUNT(*) as total,
               SUM(CASE WHEN parsed_bab_count > 0 THEN 1 ELSE 0 END) as has_bab,
               SUM(CASE WHEN parsed_pasal_count > 0 THEN 1 ELSE 0 END) as has_pasal,
               SUM(CASE WHEN parsed_ayat_count > 0 THEN 1 ELSE 0 END) as has_ayat,
               SUM(CASE WHEN parsed_huruf_count > 0 THEN 1 ELSE 0 END) as has_huruf,
               AVG(parsed_pasal_count) as avg_pasal,
               MAX(parsed_pasal_count) as max_pasal
        FROM peraturan WHERE {RFP_WHERE} GROUP BY jenis ORDER BY total DESC
    """)

    struct_data = [["유형", "문서수", "BAB", "Pasal", "Ayat", "평균Pasal"]]
    for row in struct_stats:
        jenis, total_s, has_bab, has_pasal, has_ayat, has_huruf, avg, max_p = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        struct_data.append([
            abbr, fmt(total_s),
            pct(has_bab, total_s), pct(has_pasal, total_s), pct(has_ayat, total_s),
            f"{avg:.1f}" if avg else "0"
        ])
    create_table(doc, struct_data)
    doc.add_paragraph()

    add_para(doc, "8.2 연대별 구조 변화", bold=True)

    era_struct = query_db(f"""
        SELECT
            CASE WHEN tahun < 1960 THEN '1945-1959'
                 WHEN tahun < 1980 THEN '1960-1979'
                 WHEN tahun < 2000 THEN '1980-1999'
                 WHEN tahun < 2010 THEN '2000-2009'
                 WHEN tahun < 2020 THEN '2010-2019'
                 ELSE '2020-현재' END as era,
            COUNT(*) as cnt, AVG(parsed_pasal_count) as avg_pasal
        FROM peraturan WHERE {RFP_WHERE} AND parsed_pasal_count > 0
        GROUP BY era ORDER BY era
    """)

    era_data = [["연대", "문서 수", "평균 Pasal"]]
    for row in era_struct:
        era_data.append([row[0], fmt(row[1]), f"{row[2]:.1f}" if row[2] else "0"])
    create_table(doc, era_data)
    doc.add_paragraph("※ 1950년대 평균 9.5조 → 2020년대 평균 36.6조로 법령 복잡도 증가")
    doc.add_paragraph()

    # === 9. 현행성 판단 지표 ===
    add_heading(doc, "9. 현행성 판단 지표", 1)

    add_para(doc, "9.1 현행성 상태 분포", bold=True)

    berlaku = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND status = 'Berlaku'")
    tidak_berlaku = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND status LIKE 'Tidak Berlaku%'")
    dicabut = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND status LIKE '%Dicabut Oleh%'")

    validity_data = [
        ["상태", "건수", "비율"],
        ["현행 (Berlaku)", fmt(berlaku), pct(berlaku, rfp_total)],
        ["폐지 (Tidak Berlaku)", fmt(tidak_berlaku), pct(tidak_berlaku, rfp_total)],
        ["  └ 폐지 법령 명시됨", fmt(dicabut), pct(dicabut, rfp_total)],
    ]
    create_table(doc, validity_data)
    doc.add_paragraph()

    add_para(doc, "9.2 status 필드 구조", bold=True)
    doc.add_paragraph("폐지된 법령의 경우 status 필드에 폐지한 법령 정보가 함께 기록됨:")

    status_example = """[status 필드 예시]
현행: "Berlaku"
폐지: "Tidak BerlakuDicabut Oleh :Peraturan Menteri Keuangan
       Nomor 3 Tahun 2024 Tentang Tarif Layanan..."

→ 추출 가능 정보:
  - 현행성: Tidak Berlaku (폐지)
  - 폐지한 법령: Peraturan Menteri Keuangan No. 3 Tahun 2024"""
    add_code_block(doc, status_example)
    doc.add_paragraph()

    # === 10. 관계성 표현 분석 ===
    add_heading(doc, "10. 관계성 표현 분석", 1)

    add_para(doc, "10.1 본문 텍스트 패턴", bold=True)

    conn = sqlite3.connect(PERATURAN_DB)
    cur = conn.cursor()

    patterns = {
        'dicabut dan dinyatakan tidak berlaku': ('폐지 선언', 'REVOKES'),
        'diubah dengan': ('개정 참조', 'AMENDED_BY'),
        'untuk melaksanakan': ('시행 근거', 'IMPLEMENTS'),
        'berdasarkan': ('법적 근거', 'BASED_ON'),
        'sepanjang tidak bertentangan': ('조건부 효력', 'CONDITIONAL'),
        'mulai berlaku': ('시행일', 'EFFECTIVE'),
    }

    pattern_data = [["패턴", "한국어", "관계유형", "검출 건수"]]
    for pattern, (korean, rel_type) in patterns.items():
        cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND extracted_text LIKE ?", (f'%{pattern}%',))
        count = cur.fetchone()[0]
        pattern_data.append([pattern, korean, rel_type, fmt(count)])
    create_table(doc, pattern_data)
    conn.close()
    doc.add_paragraph()

    add_para(doc, "10.2 참조 관계 추출 현황", bold=True)

    refs_data = load_references_data()
    if refs_data:
        refs_table = [
            ["참조 유형", "총 건수", "관련 문서"],
            ["외부 참조 (다른 법령 인용)", fmt(refs_data['ext_ref_count']), fmt(refs_data['docs_with_ext'])],
            ["내부 참조 (동일 법령 내)", fmt(refs_data['int_ref_count']), fmt(refs_data['docs_with_int'])],
            ["조건부 조항", fmt(refs_data['conditional_count']), fmt(refs_data['docs_with_cond'])],
        ]
        create_table(doc, refs_table)
        doc.add_paragraph()

    add_para(doc, "10.3 조건부 효력 표현 (★ 중요)", bold=True)
    doc.add_paragraph("'상충되지 않는 한' 등 조건부 효력 표현은 법률가 검토가 필요한 회색지대:")

    if refs_data:
        cond_data = [["조항 유형", "한국어", "건수"]]
        cond_meanings = {
            'DICABUT_DAN_TIDAK_BERLAKU': '폐지되고 효력 없음 선언',
            'SEPANJANG_TIDAK_BERTENTANGAN': '상충되지 않는 한 (조건부 현행)',
            'TETAP_BERLAKU_SAMPAI': '~까지 유효 (경과 규정)'
        }
        for ctype, count in refs_data['conditional_types'].most_common():
            cond_data.append([ctype, cond_meanings.get(ctype, ''), fmt(count)])
        create_table(doc, cond_data)

    doc.add_page_break()

    # ============================================================
    # PART III: 시스템 설계
    # ============================================================
    part3_title = doc.add_heading("Part III. 시스템 설계", level=0)
    part3_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 11. 메타데이터 추출 전략 ===
    add_heading(doc, "11. 메타데이터 추출 전략", 1)

    add_para(doc, "11.1 DB 필드 활용", bold=True)
    db_fields = [
        ["필드", "용도", "활용도"],
        ["slug", "고유 식별자 (Neo4j 노드 ID)", "★★★"],
        ["jenis + nomor + tahun", "법령 참조키", "★★★"],
        ["status", "현행성 + 폐지법령 관계", "★★★"],
        ["pemrakarsa", "발령기관 노드 생성", "★★☆"],
        ["tanggal_penetapan", "제정일 (시행일 계산)", "★★☆"],
    ]
    create_table(doc, db_fields)
    doc.add_paragraph()

    add_para(doc, "11.2 status 필드 관계 추출 정규식", bold=True)
    regex_code = """# Python 정규식
import re

pattern = r'Dicabut Oleh\\s*:\\s*(Peraturan\\s+\\w+(?:\\s+\\w+)*)\\s+Nomor\\s+(\\S+)\\s+Tahun\\s+(\\d{4})'

status = "Tidak BerlakuDicabut Oleh :Peraturan Menteri Keuangan Nomor 3 Tahun 2024..."
match = re.search(pattern, status)

# 결과:
# match.group(1) = "Peraturan Menteri Keuangan"  (폐지한 법령 유형)
# match.group(2) = "3"                           (법령 번호)
# match.group(3) = "2024"                        (연도)"""
    add_code_block(doc, regex_code)
    doc.add_paragraph()

    # === 12. Neo4j 그래프 모델 ===
    add_heading(doc, "12. Neo4j 그래프 모델", 1)

    add_para(doc, "12.1 노드 유형", bold=True)
    node_model = """(:Law)                    법령 노드
  - id: string            고유 식별자 (slug)
  - jenis: string         법령 유형
  - nomor: string         번호
  - tahun: integer        연도
  - tentang: string       제목
  - validity: string      현행/폐지/조건부

(:Agency)                 발령기관 노드
  - name: string          기관명

(:Condition)              조건 노드
  - type: string          조건 유형
  - text_ko: string       한국어 번역"""
    add_code_block(doc, node_model)
    doc.add_paragraph()

    add_para(doc, "12.2 관계 유형", bold=True)
    rel_model = """-[:REVOKES]->             폐지 (A가 B를 폐지)
-[:AMENDS]->              개정 (A가 B를 개정)
-[:REFERENCES]->          참조 (A가 B를 인용)
-[:IMPLEMENTS]->          시행 (A는 B의 시행령)
-[:CONDITIONAL_VALID]->   조건부 효력
-[:ISSUED_BY]->           발령 (기관→법령)"""
    add_code_block(doc, rel_model)
    doc.add_paragraph()

    add_para(doc, "12.3 Cypher 쿼리 예시", bold=True)
    cypher = """// 특정 법령의 현행성 확인
MATCH (law:Law {id: 'uu-no-11-tahun-2020'})
OPTIONAL MATCH (law)<-[:REVOKES]-(revoker:Law)
RETURN law.tentang AS 제목,
       CASE WHEN revoker IS NOT NULL THEN '폐지됨'
            WHEN law.status = 'Berlaku' THEN '현행'
            ELSE '확인필요' END AS 현행성

// 조건부 효력 법령 조회
MATCH (law:Law)-[r:CONDITIONAL_VALID]->(c:Condition)
WHERE c.type = 'SEPANJANG_TIDAK_BERTENTANGAN'
RETURN law.id, c.text_ko AS 조건"""
    add_code_block(doc, cypher)
    doc.add_paragraph()

    # === 13. 법령 표현 한글 번역 ===
    add_heading(doc, "13. 법령 표현 한글 번역", 1)

    add_para(doc, "13.1 현행성 표현", bold=True)
    validity_terms = [
        ["인도네시아어", "한국어", "의미"],
        ["Berlaku", "현행", "효력 있음"],
        ["Tidak Berlaku", "폐지", "효력 없음"],
        ["Dicabut dan dinyatakan tidak berlaku", "폐지되고 효력 없음 선언", "명시적 폐지"],
        ["Mulai berlaku", "시행", "효력 발생"],
        ["Berlaku surut", "소급 적용", "과거로 소급"],
    ]
    create_table(doc, validity_terms)
    doc.add_paragraph()

    add_para(doc, "13.2 조건부 효력 표현 (★ 핵심)", bold=True)
    conditional_terms = [
        ["인도네시아어", "한국어", "처리"],
        ["sepanjang tidak bertentangan", "상충되지 않는 한", "조건부 현행"],
        ["sepanjang belum diatur", "아직 규정되지 않은 한", "조건부 현행"],
        ["tetap berlaku", "계속 유효", "경과 규정"],
        ["tetap berlaku sampai", "~까지 유효", "한시적"],
    ]
    create_table(doc, conditional_terms)
    doc.add_paragraph()

    add_para(doc, "13.3 관계 표현", bold=True)
    relation_terms = [
        ["인도네시아어", "한국어", "방향"],
        ["mencabut", "~을 폐지함", "A→B"],
        ["dicabut oleh", "~에 의해 폐지됨", "B←A"],
        ["mengubah", "~을 개정함", "A→B"],
        ["perubahan atas", "~의 개정", "A는 B의 개정본"],
        ["untuk melaksanakan", "~을 시행하기 위해", "A는 B의 시행령"],
        ["berdasarkan", "~에 근거하여", "A가 B를 근거"],
    ]
    create_table(doc, relation_terms)
    doc.add_paragraph()

    # === 14. 국제표준 XML 설계 ===
    add_heading(doc, "14. 국제표준 XML 설계 (Akoma Ntoso)", 1)

    add_para(doc, "14.1 Akoma Ntoso 개요", bold=True)
    doc.add_paragraph("• 표준: OASIS LegalDocML TC")
    doc.add_paragraph("• 버전: Akoma Ntoso 3.0")
    doc.add_paragraph("• 네임스페이스: http://docs.oasis-open.org/legaldocml/ns/akn/3.0")
    doc.add_paragraph()

    add_para(doc, "14.2 문서 구조", bold=True)
    akn_structure = """<akomaNtoso>
  <act>
    <meta>                 메타데이터
      <identification>     식별 정보
      <lifecycle>          생애주기 (제정/개정/폐지)
      <analysis>           참조/관계 분석
    <preface>              전문 (Menimbang, Mengingat)
    <body>                 본문
      <chapter>            BAB (장)
        <article>          Pasal (조)
          <paragraph>      Ayat (항)
            <point>        Huruf (호)
    <conclusions>          결론/폐지조항
  </act>
</akomaNtoso>"""
    add_code_block(doc, akn_structure)
    doc.add_paragraph()

    add_para(doc, "14.3 조건부 효력 XML 표현", bold=True)
    cond_xml = """<article eId="art_185">
  <content>
    <p>peraturan pelaksanaan... dinyatakan masih
      <mod type="conditionalValidity">
        <quotedText>tetap berlaku sepanjang tidak bertentangan</quotedText>
        <quotedText xml:lang="ko">상충되지 않는 한 계속 유효</quotedText>
      </mod>
    </p>
  </content>
</article>"""
    add_code_block(doc, cond_xml)
    doc.add_paragraph()

    # === 15. 구현 로드맵 ===
    add_heading(doc, "15. 구현 로드맵", 1)

    roadmap = [
        ["단계", "작업 내용", "산출물"],
        ["1", "메타데이터 정제", "정제된 peraturan.db"],
        ["2", "관계 추출 (status 필드)", "relations.json"],
        ["3", "본문 관계 추출", "text_relations.json"],
        ["4", "Neo4j 스키마 생성", "Cypher DDL"],
        ["5", "Neo4j 데이터 적재", "법령 그래프 DB"],
        ["6", "XML 변환기 개발", "AKN Converter"],
        ["7", "XML 일괄 변환", "35,000+ XML"],
        ["8", "검증 및 품질검사", "QA 보고서"],
    ]
    create_table(doc, roadmap)

    # === 부록 ===
    doc.add_page_break()
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
    ]
    create_table(doc, type_terms)

    doc.add_paragraph()
    add_heading(doc, "부록 B. 관계 표현 용어 대조표", 1)
    rel_terms = [
        ["인도네시아어", "한국어", "영어"],
        ["Berlaku", "현행", "In Force"],
        ["Tidak Berlaku", "폐지", "Not In Force"],
        ["Dicabut", "폐지됨", "Revoked"],
        ["Diubah", "개정됨", "Amended"],
        ["BAB", "장 (章)", "Chapter"],
        ["Pasal", "조 (條)", "Article"],
        ["Ayat", "항 (項)", "Paragraph"],
        ["Huruf", "호 (號)", "Point"],
        ["Menimbang", "고려사항", "Considering"],
        ["Mengingat", "근거조항", "Having Regard To"],
        ["sepanjang tidak bertentangan", "상충되지 않는 한", "insofar as not contrary"],
        ["mulai berlaku", "시행", "enters into force"],
    ]
    create_table(doc, rel_terms)

    # 저장
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_FILE)
    print(f"\n✅ 종합 보고서 생성 완료: {OUTPUT_FILE}")
    print(f"   파일 크기: {OUTPUT_FILE.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    generate_report()
