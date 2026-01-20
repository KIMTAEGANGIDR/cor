#!/usr/bin/env python3
"""
인도네시아 법령정보시스템 종합 보고서 생성 스크립트

Part 1: 법령 구조분석 (구조, 관계성, 현행성 표현)
Part 2: 시스템 설계 (메타데이터 추출, Neo4j, XML)
"""

import json
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


def add_code_block(doc: Document, code: str, title: str = None):
    if title:
        add_para(doc, title, bold=True)
    para = doc.add_paragraph()
    run = para.add_run(code)
    run.font.name = 'Consolas'
    run.font.size = Pt(9)
    return para


# === 데이터 수집 함수들 ===

def get_structure_stats_by_type():
    query = f"""
        SELECT jenis, COUNT(*) as total,
               SUM(parsed_bab_count) as total_bab,
               SUM(parsed_pasal_count) as total_pasal,
               SUM(parsed_ayat_count) as total_ayat,
               SUM(parsed_huruf_count) as total_huruf,
               AVG(parsed_pasal_count) as avg_pasal,
               MAX(parsed_pasal_count) as max_pasal,
               SUM(CASE WHEN parsed_bab_count > 0 THEN 1 ELSE 0 END) as has_bab,
               SUM(CASE WHEN parsed_pasal_count > 0 THEN 1 ELSE 0 END) as has_pasal,
               SUM(CASE WHEN parsed_ayat_count > 0 THEN 1 ELSE 0 END) as has_ayat,
               SUM(CASE WHEN parsed_huruf_count > 0 THEN 1 ELSE 0 END) as has_huruf
        FROM peraturan WHERE {RFP_WHERE} GROUP BY jenis ORDER BY total DESC
    """
    return query_db(query)


def get_structure_stats_by_era():
    query = f"""
        SELECT
            CASE WHEN tahun < 1960 THEN '1945-1959'
                 WHEN tahun < 1980 THEN '1960-1979'
                 WHEN tahun < 2000 THEN '1980-1999'
                 WHEN tahun < 2010 THEN '2000-2009'
                 WHEN tahun < 2020 THEN '2010-2019'
                 ELSE '2020-현재' END as era,
            COUNT(*) as cnt, AVG(parsed_pasal_count) as avg_pasal,
            MAX(parsed_pasal_count) as max_pasal,
            SUM(parsed_bab_count) as total_bab,
            SUM(parsed_pasal_count) as total_pasal,
            SUM(parsed_ayat_count) as total_ayat
        FROM peraturan WHERE {RFP_WHERE} AND parsed_pasal_count > 0
        GROUP BY era ORDER BY era
    """
    return query_db(query)


def get_structure_by_type_and_era():
    query = f"""
        SELECT jenis,
            CASE WHEN tahun < 1960 THEN '1945-1959'
                 WHEN tahun < 1980 THEN '1960-1979'
                 WHEN tahun < 2000 THEN '1980-1999'
                 WHEN tahun < 2010 THEN '2000-2009'
                 WHEN tahun < 2020 THEN '2010-2019'
                 ELSE '2020-현재' END as era,
            COUNT(*) as cnt, AVG(parsed_pasal_count) as avg_pasal
        FROM peraturan WHERE {RFP_WHERE} AND parsed_pasal_count > 0
        GROUP BY jenis, era ORDER BY jenis, era
    """
    return query_db(query)


def get_validity_stats():
    conn = sqlite3.connect(PERATURAN_DB)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE}")
    total = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND status = 'Berlaku'")
    berlaku = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND status LIKE 'Tidak Berlaku%'")
    tidak_berlaku = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND status LIKE '%Dicabut Oleh%'")
    dicabut = cur.fetchone()[0]
    conn.close()
    return {'total': total, 'berlaku': berlaku, 'tidak_berlaku': tidak_berlaku, 'dicabut': dicabut}


def get_text_patterns():
    conn = sqlite3.connect(PERATURAN_DB)
    cur = conn.cursor()
    patterns = {
        '폐지선언 (dicabut dan dinyatakan tidak berlaku)': '%dicabut dan dinyatakan tidak berlaku%',
        '개정참조 (diubah dengan)': '%diubah dengan%',
        '시행일 (mulai berlaku)': '%mulai berlaku%',
        '공포 (diundangkan di)': '%diundangkan di%',
        '법적근거 (Mengingat)': '%Mengingat%:%',
        '조건부효력 (sepanjang tidak bertentangan)': '%sepanjang tidak bertentangan%',
        '법률참조 (Undang-Undang Nomor)': '%Undang-Undang Nomor%',
    }
    results = {}
    for name, pattern in patterns.items():
        cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND extracted_text LIKE ?", (pattern,))
        results[name] = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND extracted_text IS NOT NULL AND extracted_text != ''")
    results['총 텍스트 보유'] = cur.fetchone()[0]
    conn.close()
    return results


def get_metadata_completeness():
    fields = ['status', 'tanggal_penetapan', 'pemrakarsa', 'tempat_penetapan', 'pejabat_penetapan']
    results = {}
    conn = sqlite3.connect(PERATURAN_DB)
    cur = conn.cursor()
    for field in fields:
        cur.execute(f"""SELECT COUNT(*) as total,
                       SUM(CASE WHEN {field} IS NOT NULL AND {field} != '' THEN 1 ELSE 0 END) as filled
                FROM peraturan WHERE {RFP_WHERE}""")
        row = cur.fetchone()
        results[field] = {'total': row[0], 'filled': row[1]}
    conn.close()
    return results


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


def get_text_pattern_examples():
    conn = sqlite3.connect(PERATURAN_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    examples = {}

    # 폐지 선언
    cur.execute(f"SELECT slug, extracted_text FROM peraturan WHERE {RFP_WHERE} AND extracted_text LIKE '%dicabut dan dinyatakan tidak berlaku%' LIMIT 1")
    row = cur.fetchone()
    if row:
        match = re.search(r'.{0,60}(dicabut\s+dan\s+dinyatakan\s+tidak\s+berlaku).{0,100}', row['extracted_text'], re.IGNORECASE | re.DOTALL)
        if match:
            examples['폐지선언'] = {'slug': row['slug'], 'text': match.group().replace('\n', ' ')[:200]}

    # 시행일
    cur.execute(f"SELECT slug, extracted_text FROM peraturan WHERE {RFP_WHERE} AND extracted_text LIKE '%mulai berlaku pada tanggal%' LIMIT 1")
    row = cur.fetchone()
    if row:
        match = re.search(r'.{0,40}(mulai\s+berlaku\s+pada\s+tanggal).{0,80}', row['extracted_text'], re.IGNORECASE | re.DOTALL)
        if match:
            examples['시행일'] = {'slug': row['slug'], 'text': match.group().replace('\n', ' ')[:180]}

    # 조건부 효력
    cur.execute(f"SELECT slug, extracted_text FROM peraturan WHERE {RFP_WHERE} AND extracted_text LIKE '%sepanjang tidak bertentangan%' LIMIT 1")
    row = cur.fetchone()
    if row:
        match = re.search(r'.{0,60}(sepanjang\s+tidak\s+bertentangan).{0,80}', row['extracted_text'], re.IGNORECASE | re.DOTALL)
        if match:
            examples['조건부효력'] = {'slug': row['slug'], 'text': match.group().replace('\n', ' ')[:180]}

    conn.close()
    return examples


def generate_report():
    print("=" * 60)
    print("인도네시아 법령정보시스템 종합 보고서 생성")
    print("=" * 60)

    doc = Document()

    # === 표지 ===
    title = doc.add_heading("인도네시아 법령정보시스템", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = doc.add_heading("종합 분석 및 설계 보고서", level=1)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    info = doc.add_paragraph()
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info.add_run(f"\n\n\n법조문 구조분석 · 관계성/현행성 표현\n메타데이터 추출 · Neo4j 그래프 모델 · 국제표준 XML 설계\n\n\n작성일: {datetime.now().strftime('%Y-%m-%d')}")
    run.font.size = Pt(12)

    doc.add_page_break()

    # === 목차 ===
    add_heading(doc, "목차", 1)
    toc = """
Part I. 법령 구조 분석
  1. 분석 대상 현황
  2. 문서 구조 분석
  3. 현행성 판단 지표
  4. 관계성 표현 분석
  5. 텍스트 표현 예시
  6. 핵심 발견사항

Part II. 시스템 설계
  7. 메타데이터 추출 전략
  8. Neo4j 그래프 모델 설계
  9. 법령 표현 한글 번역 체계
  10. 국제표준 XML 설계 (Akoma Ntoso)
  11. 구현 로드맵

부록
  A. 법령 구조 용어 대조표
  B. 전체 용어 대조표
"""
    doc.add_paragraph(toc)
    doc.add_page_break()

    # ============================================================
    # PART I: 법령 구조 분석
    # ============================================================
    part1_title = doc.add_heading("Part I. 법령 구조 분석", level=0)
    part1_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 1. 분석 대상 현황 ===
    add_heading(doc, "1. 분석 대상 현황", 1)

    total = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE}")
    with_text = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND extracted_text IS NOT NULL AND extracted_text != ''")
    parsed = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND parsed_pasal_count > 0")

    add_para(doc, "1.1 데이터 현황", bold=True)
    summary_data = [
        ["항목", "값", "비율"],
        ["RFP 대상 법령 총계", fmt(total), "100%"],
        ["텍스트 추출 완료", fmt(with_text), pct(with_text, total)],
        ["구조 파싱 완료", fmt(parsed), pct(parsed, total)],
    ]
    create_table(doc, summary_data)
    doc.add_paragraph()

    total_bab = query_one(f"SELECT SUM(parsed_bab_count) FROM peraturan WHERE {RFP_WHERE}")
    total_pasal = query_one(f"SELECT SUM(parsed_pasal_count) FROM peraturan WHERE {RFP_WHERE}")
    total_ayat = query_one(f"SELECT SUM(parsed_ayat_count) FROM peraturan WHERE {RFP_WHERE}")
    total_huruf = query_one(f"SELECT SUM(parsed_huruf_count) FROM peraturan WHERE {RFP_WHERE}")

    add_para(doc, "1.2 추출된 구조 요소 총계", bold=True)
    struct_summary = [
        ["구조 요소", "인도네시아어", "한국어", "총 개수"],
        ["BAB", "장", "章", fmt(total_bab or 0)],
        ["Pasal", "조", "條", fmt(total_pasal or 0)],
        ["Ayat", "항", "項", fmt(total_ayat or 0)],
        ["Huruf", "호", "號", fmt(total_huruf or 0)],
    ]
    create_table(doc, struct_summary)
    doc.add_paragraph()

    # === 2. 문서 구조 분석 ===
    add_heading(doc, "2. 문서 구조 분석", 1)

    add_para(doc, "2.1 유형별 구조 요소 보유율", bold=True)
    stats = get_structure_stats_by_type()
    type_struct_data = [["유형", "약어", "문서 수", "BAB", "Pasal", "Ayat", "Huruf"]]
    for row in stats:
        jenis, total_cnt, _, _, _, _, _, _, has_bab, has_pasal, has_ayat, has_huruf = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        type_struct_data.append([
            RFP_TYPE_KOREAN.get(jenis, jenis[:10]), abbr, fmt(total_cnt),
            pct(has_bab, total_cnt), pct(has_pasal, total_cnt),
            pct(has_ayat, total_cnt), pct(has_huruf, total_cnt)
        ])
    create_table(doc, type_struct_data)
    doc.add_paragraph()

    add_para(doc, "2.2 유형별 평균 Pasal 수", bold=True)
    type_avg_data = [["유형 (약어)", "평균 Pasal", "최대 Pasal", "총 Pasal"]]
    for row in stats:
        jenis, _, _, total_pasal_t, _, _, avg_pasal, max_pasal, _, _, _, _ = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        type_avg_data.append([
            f"{RFP_TYPE_KOREAN.get(jenis, jenis[:8])} ({abbr})",
            f"{avg_pasal:.1f}" if avg_pasal else "0",
            fmt(max_pasal or 0), fmt(total_pasal_t or 0)
        ])
    create_table(doc, type_avg_data)
    doc.add_paragraph()

    add_para(doc, "2.3 연대별 구조 변화 추이", bold=True)
    era_stats = get_structure_stats_by_era()
    era_data = [["연대", "문서 수", "평균 Pasal", "최대 Pasal"]]
    for row in era_stats:
        era, cnt, avg_pasal, max_pasal, _, _, _ = row
        era_data.append([era, fmt(cnt), f"{avg_pasal:.1f}" if avg_pasal else "0", fmt(max_pasal or 0)])
    create_table(doc, era_data)
    doc.add_paragraph("※ 1950년대 평균 9.5조 → 2020년대 평균 36.6조로 법령 복잡도 증가")

    # 유형별 연대별 매트릭스
    add_para(doc, "2.4 유형별 연대별 평균 Pasal 수", bold=True)
    type_era = get_structure_by_type_and_era()
    eras = ['1945-1959', '1960-1979', '1980-1999', '2000-2009', '2010-2019', '2020-현재']
    types_order = ['UU', 'PP', 'PERPRES', 'PERPPU', 'PERMEN', 'PERBAN']
    matrix_data = defaultdict(dict)
    for row in type_era:
        jenis, era, cnt, avg_pasal = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        matrix_data[abbr][era] = f"{avg_pasal:.1f}" if avg_pasal else "-"
    matrix_table = [["유형"] + eras]
    for t in types_order:
        row_data = [t] + [matrix_data.get(t, {}).get(era, "-") for era in eras]
        matrix_table.append(row_data)
    create_table(doc, matrix_table)
    doc.add_paragraph()

    # === 3. 현행성 판단 지표 ===
    add_heading(doc, "3. 현행성 판단 지표", 1)

    add_para(doc, "3.1 메타데이터 기반 현행성 상태", bold=True)
    validity = get_validity_stats()
    validity_data = [
        ["상태", "건수", "비율"],
        ["현행 (Berlaku)", fmt(validity['berlaku']), pct(validity['berlaku'], validity['total'])],
        ["폐지 (Tidak Berlaku)", fmt(validity['tidak_berlaku']), pct(validity['tidak_berlaku'], validity['total'])],
        ["  └ Dicabut Oleh (폐지 법령 명시)", fmt(validity['dicabut']), pct(validity['dicabut'], validity['total'])],
    ]
    create_table(doc, validity_data)
    doc.add_paragraph()
    doc.add_paragraph("※ status 필드에 폐지한 법령 정보가 함께 기록됨")
    doc.add_paragraph("   예: 'Tidak BerlakuDicabut Oleh: Peraturan Menteri Keuangan Nomor 3 Tahun 2024...'")

    add_para(doc, "3.2 메타데이터 필드 완성도", bold=True)
    metadata = get_metadata_completeness()
    metadata_data = [["필드명", "용도", "완성도"]]
    field_purposes = {
        'status': '현행성 상태', 'tanggal_penetapan': '제정일',
        'pemrakarsa': '발령기관', 'tempat_penetapan': '제정장소', 'pejabat_penetapan': '제정자'
    }
    for field, data in metadata.items():
        metadata_data.append([field, field_purposes.get(field, ''), pct(data['filled'], data['total'])])
    create_table(doc, metadata_data)
    doc.add_paragraph()

    # === 4. 관계성 표현 분석 ===
    add_heading(doc, "4. 관계성 표현 분석", 1)

    add_para(doc, "4.1 본문 텍스트 패턴 검출 현황", bold=True)
    patterns = get_text_patterns()
    total_text = patterns.pop('총 텍스트 보유')
    pattern_data = [["패턴 (인도네시아어)", "검출 건수", "비율"]]
    for name, count in patterns.items():
        pattern_data.append([name, fmt(count), pct(count, total_text)])
    create_table(doc, pattern_data)
    doc.add_paragraph(f"(텍스트 보유 문서: {fmt(total_text)}건)")
    doc.add_paragraph()

    refs_data = load_references_data()
    if refs_data:
        add_para(doc, "4.2 참조 관계 추출 현황", bold=True)
        refs_table = [
            ["참조 유형", "총 건수", "관련 문서 수", "문서당 평균"],
            ["외부 참조 (다른 법령)", fmt(refs_data['ext_ref_count']),
             f"{fmt(refs_data['docs_with_ext'])} ({pct(refs_data['docs_with_ext'], refs_data['total_docs'])})",
             f"{refs_data['ext_ref_count']/refs_data['docs_with_ext']:.1f}" if refs_data['docs_with_ext'] else "0"],
            ["내부 참조 (동일 법령)", fmt(refs_data['int_ref_count']),
             f"{fmt(refs_data['docs_with_int'])} ({pct(refs_data['docs_with_int'], refs_data['total_docs'])})",
             f"{refs_data['int_ref_count']/refs_data['docs_with_int']:.1f}" if refs_data['docs_with_int'] else "0"],
            ["조건부 조항", fmt(refs_data['conditional_count']),
             f"{fmt(refs_data['docs_with_cond'])} ({pct(refs_data['docs_with_cond'], refs_data['total_docs'])})",
             f"{refs_data['conditional_count']/refs_data['docs_with_cond']:.1f}" if refs_data['docs_with_cond'] else "0"],
        ]
        create_table(doc, refs_table)
        doc.add_paragraph()

        add_para(doc, "4.3 조건부 조항 유형별 분포", bold=True)
        cond_meanings = {
            'DICABUT_DAN_TIDAK_BERLAKU': '폐지 선언',
            'SEPANJANG_TIDAK_BERTENTANGAN': '상충되지 않는 한 (조건부 효력)',
            'TETAP_BERLAKU_SAMPAI': '~까지 유효 (경과 규정)'
        }
        cond_type_data = [["조항 유형", "한국어 의미", "건수"]]
        for ctype, count in refs_data['conditional_types'].most_common():
            cond_type_data.append([ctype, cond_meanings.get(ctype, ''), fmt(count)])
        create_table(doc, cond_type_data)
        doc.add_paragraph()

    # === 5. 텍스트 표현 예시 ===
    add_heading(doc, "5. 관계성/현행성 텍스트 표현 예시", 1)
    examples = get_text_pattern_examples()

    if examples.get('폐지선언'):
        add_para(doc, "5.1 폐지 선언 (dicabut dan dinyatakan tidak berlaku)", bold=True)
        doc.add_paragraph(f"문서: {examples['폐지선언']['slug']}")
        doc.add_paragraph(f"「{examples['폐지선언']['text']}」")
        doc.add_paragraph("→ 한국어: \"폐지되고 효력이 없다고 선언됨\"")
        doc.add_paragraph()

    if examples.get('시행일'):
        add_para(doc, "5.2 시행일 표현 (mulai berlaku pada tanggal)", bold=True)
        doc.add_paragraph(f"문서: {examples['시행일']['slug']}")
        doc.add_paragraph(f"「{examples['시행일']['text']}」")
        doc.add_paragraph("→ 한국어: \"~일부터 시행\"")
        doc.add_paragraph()

    if examples.get('조건부효력'):
        add_para(doc, "5.3 조건부 효력 (sepanjang tidak bertentangan)", bold=True)
        doc.add_paragraph(f"문서: {examples['조건부효력']['slug']}")
        doc.add_paragraph(f"「{examples['조건부효력']['text']}」")
        doc.add_paragraph("→ 한국어: \"상충되지 않는 한 (계속 유효)\"")
        doc.add_paragraph()

    # === 6. 핵심 발견사항 ===
    add_heading(doc, "6. 핵심 발견사항", 1)

    doc.add_paragraph("▶ 문서 구조", style='List Bullet')
    doc.add_paragraph("• 인도네시아 법령은 BAB(장) → Pasal(조) → Ayat(항) → Huruf(호) 계층 구조")
    doc.add_paragraph("• 법률(UU) 평균 41.8조로 가장 복잡, 대통령령(PERPRES) 평균 15.7조로 단순")
    doc.add_paragraph("• 시대별 복잡도 증가: 1950년대 9.5조 → 2020년대 36.6조")
    doc.add_paragraph()

    doc.add_paragraph("▶ 현행성 판단", style='List Bullet')
    doc.add_paragraph("• 메타데이터 status 필드로 99.6% 자동 판단 가능")
    doc.add_paragraph("• 폐지 법령의 경우 폐지한 법령 정보가 status 필드에 포함")
    doc.add_paragraph()

    doc.add_paragraph("▶ 관계성 추출", style='List Bullet')
    doc.add_paragraph("• 외부 참조: 23,328건, 내부 참조: 261,451건")
    doc.add_paragraph("• 조건부 효력 'sepanjang tidak bertentangan': 2,285건 (법률가 검토 필요)")

    doc.add_page_break()

    # ============================================================
    # PART II: 시스템 설계
    # ============================================================
    part2_title = doc.add_heading("Part II. 시스템 설계", level=0)
    part2_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # === 7. 메타데이터 추출 전략 ===
    add_heading(doc, "7. 메타데이터 추출 전략", 1)

    add_para(doc, "7.1 데이터베이스 필드 활용", bold=True)
    metadata_fields = [
        ["필드명", "용도", "추출 정보", "활용도"],
        ["slug", "고유 식별자", "법령 ID", "★★★"],
        ["jenis", "법령 유형", "UU, PP, PERPRES 등", "★★★"],
        ["nomor", "법령 번호", "숫자/기호", "★★★"],
        ["tahun", "제정 연도", "1945~2026", "★★★"],
        ["status", "현행성 + 관계", "Berlaku/Tidak Berlaku + 폐지법령", "★★★"],
        ["pemrakarsa", "발령기관", "부처/기관명", "★★☆"],
        ["tanggal_penetapan", "제정일", "날짜", "★★☆"],
    ]
    create_table(doc, metadata_fields)
    doc.add_paragraph()

    add_para(doc, "7.2 status 필드에서 관계 추출", bold=True)
    status_code = """[status 필드 구조]
Berlaku                    → 현행 법령
Tidak BerlakuDicabut Oleh: → 폐지 + 폐지한 법령 정보
  Peraturan Menteri Keuangan Nomor 3 Tahun 2024 Tentang...

[추출 정규식]
Dicabut Oleh\\s*:\\s*(Peraturan\\s+\\w+(?:\\s+\\w+)*)\\s+Nomor\\s+(\\S+)\\s+Tahun\\s+(\\d{4})

[추출 결과]
- 폐지한 법령 유형: Peraturan Menteri Keuangan
- 폐지한 법령 번호: 3
- 폐지한 법령 연도: 2024"""
    add_code_block(doc, status_code)
    doc.add_paragraph()

    add_para(doc, "7.3 본문 텍스트 관계 패턴", bold=True)
    text_patterns = [
        ["패턴", "한국어", "관계 유형", "건수"],
        ["dicabut dan dinyatakan tidak berlaku", "폐지되고 효력 없음 선언", "REVOKES", "9,305"],
        ["diubah dengan", "~에 의해 개정됨", "AMENDED_BY", "3,435"],
        ["untuk melaksanakan", "~을 시행하기 위해", "IMPLEMENTS", "5,242"],
        ["berdasarkan", "~에 근거하여", "BASED_ON", "21,521"],
        ["sepanjang tidak bertentangan", "상충되지 않는 한", "CONDITIONAL", "2,285"],
    ]
    create_table(doc, text_patterns)
    doc.add_paragraph()

    # === 8. Neo4j 그래프 모델 ===
    add_heading(doc, "8. Neo4j 그래프 모델 설계", 1)

    add_para(doc, "8.1 노드 유형 (Node Types)", bold=True)
    node_model = """(:Law)                    법령 노드
  - id: string            고유 식별자 (slug)
  - jenis: string         법령 유형
  - nomor: string         법령 번호
  - tahun: integer        제정 연도
  - tentang: string       제목
  - status: string        현행성 상태
  - validity: string      현행/폐지/조건부

(:Agency)                 발령기관 노드
  - name: string          기관명

(:Condition)              조건 노드 (조건부 효력용)
  - type: string          조건 유형
  - text: string          조건 텍스트"""
    add_code_block(doc, node_model)
    doc.add_paragraph()

    add_para(doc, "8.2 관계 유형 (Relationship Types)", bold=True)
    rel_model = """-[:REVOKES]->             폐지 관계 (A가 B를 폐지)
-[:AMENDS]->              개정 관계 (A가 B를 개정)
-[:REFERENCES]->          참조 관계 (A가 B를 인용)
-[:IMPLEMENTS]->          시행 관계 (A가 B의 시행령)
-[:CONDITIONAL_VALID]->   조건부 효력 (상충되지 않는 한 유효)
-[:ISSUED_BY]->           발령 관계 (기관이 법령 발령)"""
    add_code_block(doc, rel_model)
    doc.add_paragraph()

    add_para(doc, "8.3 현행성 판단 Cypher 쿼리", bold=True)
    cypher = """// 특정 법령이 현재 유효한지 확인
MATCH (law:Law {id: 'uu-no-11-tahun-2020'})
OPTIONAL MATCH (law)<-[:REVOKES]-(revoker:Law)
RETURN law.tentang AS 제목,
       CASE
         WHEN revoker IS NOT NULL THEN '폐지됨'
         WHEN law.status = 'Berlaku' THEN '현행'
         ELSE '확인필요'
       END AS 현행성,
       revoker.id AS 폐지한_법령

// 조건부 효력 법령 조회
MATCH (law:Law)-[r:CONDITIONAL_VALID]->(condition)
WHERE r.condition_type = 'SEPANJANG_TIDAK_BERTENTANGAN'
RETURN law.id, r.text_ko AS 조건_한국어"""
    add_code_block(doc, cypher)
    doc.add_paragraph()

    add_para(doc, "8.4 그래프 시각화 예시", bold=True)
    graph_viz = """
    ┌───────────────┐                    ┌───────────────┐
    │  UU 11/2020   │───[:REVOKES]──────>│  UU 13/2003   │
    │  Cipta Kerja  │                    │  Ketenagakerjaan│
    │  status:현행   │                    │  status:폐지   │
    └───────┬───────┘                    └───────────────┘
            │
            │[:CONDITIONAL_VALID]
            ▼
    ┌────────────────────────────────────────┐
    │  "상충되지 않는 한 유효"                  │
    │  (sepanjang tidak bertentangan)        │
    └────────────────────────────────────────┘"""
    add_code_block(doc, graph_viz)

    # === 9. 법령 표현 한글 번역 체계 ===
    doc.add_page_break()
    add_heading(doc, "9. 법령 표현 한글 번역 체계", 1)

    add_para(doc, "9.1 현행성 관련 표현", bold=True)
    validity_terms = [
        ["인도네시아어", "한국어 번역", "의미"],
        ["Berlaku", "현행", "효력 있음"],
        ["Tidak Berlaku", "폐지", "효력 없음"],
        ["Dicabut", "폐지됨", "다른 법령에 의해 폐지"],
        ["Dicabut dan dinyatakan tidak berlaku", "폐지되고 효력 없음 선언", "명시적 폐지"],
        ["Diubah", "개정됨", "수정된 상태"],
        ["Mulai berlaku", "시행", "효력 발생"],
        ["Berlaku surut", "소급 적용", "과거로 소급"],
    ]
    create_table(doc, validity_terms)
    doc.add_paragraph()

    add_para(doc, "9.2 조건부 효력 표현 (★ 핵심)", bold=True)
    doc.add_paragraph("다음 표현은 '조건부 현행' 상태로, 시스템에서 특별 처리 필요:")
    conditional_terms = [
        ["인도네시아어", "한국어 번역", "건수", "처리"],
        ["sepanjang tidak bertentangan", "상충되지 않는 한", "2,285", "조건부 현행"],
        ["sepanjang belum diatur", "아직 규정되지 않은 한", "23", "조건부 현행"],
        ["tetap berlaku", "계속 유효", "4,777", "경과 규정"],
        ["tetap berlaku sampai", "~까지 유효", "67", "한시적"],
    ]
    create_table(doc, conditional_terms)
    doc.add_paragraph()
    doc.add_paragraph("※ '상충되지 않는 한'은 새 법령과 충돌하지 않는 범위에서 기존 규정이 유효함을 의미.")
    doc.add_paragraph("   법률가의 해석이 필요한 회색지대(gray area)에 해당.")

    add_para(doc, "9.3 관계 표현", bold=True)
    relation_terms = [
        ["인도네시아어", "한국어 번역", "관계 방향"],
        ["mencabut", "~을 폐지함", "A → B"],
        ["dicabut oleh", "~에 의해 폐지됨", "B ← A"],
        ["mengubah", "~을 개정함", "A → B"],
        ["diubah dengan", "~에 의해 개정됨", "B ← A"],
        ["perubahan atas", "~의 개정", "A는 B의 개정본"],
        ["sebagaimana telah diubah", "이미 개정된 바와 같이", "개정 이력"],
        ["untuk melaksanakan", "~을 시행하기 위해", "A는 B의 시행령"],
        ["berdasarkan", "~에 근거하여", "A가 B를 근거로 함"],
    ]
    create_table(doc, relation_terms)
    doc.add_paragraph()

    add_para(doc, "9.4 시행일 표현", bold=True)
    effective_terms = [
        ["인도네시아어", "한국어 번역", "건수"],
        ["mulai berlaku pada tanggal diundangkan", "공포일에 시행", "8,301"],
        ["mulai berlaku pada tanggal ditetapkan", "제정일에 시행", "1,763"],
        ["mulai berlaku setelah ... hari", "~일 후 시행", "1,012"],
        ["berlaku surut sejak tanggal", "~일부터 소급 적용", "369"],
    ]
    create_table(doc, effective_terms)

    # === 10. 국제표준 XML 설계 ===
    doc.add_page_break()
    add_heading(doc, "10. 국제표준 XML 설계 (Akoma Ntoso)", 1)

    add_para(doc, "10.1 Akoma Ntoso 개요", bold=True)
    doc.add_paragraph("Akoma Ntoso(AKN)는 OASIS에서 표준화한 법률 문서 마크업 언어")
    doc.add_paragraph("• 표준: OASIS LegalDocML TC")
    doc.add_paragraph("• 버전: Akoma Ntoso 3.0")
    doc.add_paragraph("• 네임스페이스: http://docs.oasis-open.org/legaldocml/ns/akn/3.0")

    add_para(doc, "10.2 문서 구조", bold=True)
    akn_structure = """<akomaNtoso>
  <act>
    <meta>                 메타데이터
      <identification>     식별 정보
      <lifecycle>          생애주기 (제정, 개정, 폐지)
      <analysis>           참조/관계 분석
      <references>         참조 법령 목록
    </meta>
    <preface>              전문 (Menimbang, Mengingat)
    <body>                 본문
      <chapter>            BAB (장)
        <article>          Pasal (조)
          <paragraph>      Ayat (항)
            <point>        Huruf (호)
    </body>
    <conclusions>          결론/폐지조항
  </act>
</akomaNtoso>"""
    add_code_block(doc, akn_structure)
    doc.add_paragraph()

    add_para(doc, "10.3 조건부 효력 XML 표현", bold=True)
    doc.add_paragraph("'상충되지 않는 한' 조건부 효력을 XML로 표현하는 방법:")
    conditional_xml = """<article eId="art_185">
  <num>Pasal 185</num>
  <content>
    <p>semua peraturan pelaksanaan... dinyatakan masih
      <mod type="conditionalValidity">
        <quotedText>tetap berlaku sepanjang tidak bertentangan</quotedText>
        <quotedText xml:lang="ko">상충되지 않는 한 계속 유효</quotedText>
      </mod>
      dengan ketentuan dalam Undang-Undang ini.</p>
  </content>
</article>"""
    add_code_block(doc, conditional_xml)
    doc.add_paragraph()

    add_para(doc, "10.4 폐지 조항 XML 표현", bold=True)
    revoke_xml = """<article eId="art_186">
  <content>
    <ref href="/akn/id/act/uu/2003/13">Undang-Undang Nomor 13 Tahun 2003
       tentang Ketenagakerjaan</ref>
    <remark status="revoked" xml:lang="ko">폐지됨</remark>
    <mod>
      <quotedText>dicabut dan dinyatakan tidak berlaku</quotedText>
      <quotedText xml:lang="ko">폐지되고 효력 없음 선언</quotedText>
    </mod>
  </content>
</article>"""
    add_code_block(doc, revoke_xml)

    # === 11. 구현 로드맵 ===
    doc.add_page_break()
    add_heading(doc, "11. 구현 로드맵", 1)

    add_para(doc, "11.1 데이터 파이프라인", bold=True)
    pipeline = """
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   PDF 원본   │────>│  텍스트 추출  │────>│  구조 파싱   │
│   (54GB)    │     │  (PyMuPDF)  │     │  (정규식)   │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
      ┌────────────────────────────────────────┘
      ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  관계 추출   │────>│  Neo4j 적재  │────>│  XML 변환   │
│ (NLP/정규식) │     │  (그래프DB)  │     │ (AKN 3.0)  │
└─────────────┘     └─────────────┘     └─────────────┘"""
    add_code_block(doc, pipeline)
    doc.add_paragraph()

    add_para(doc, "11.2 단계별 작업", bold=True)
    roadmap = [
        ["단계", "작업 내용", "산출물"],
        ["1단계", "메타데이터 정제", "정제된 peraturan.db"],
        ["2단계", "관계 추출 (status 필드)", "relations.json"],
        ["3단계", "본문 관계 추출 (NLP)", "text_relations.json"],
        ["4단계", "Neo4j 스키마 생성", "Cypher DDL"],
        ["5단계", "Neo4j 데이터 적재", "법령 그래프 DB"],
        ["6단계", "XML 변환기 개발", "AKN Converter"],
        ["7단계", "XML 일괄 변환", "35,000+ XML 파일"],
        ["8단계", "검증 및 품질검사", "QA 보고서"],
    ]
    create_table(doc, roadmap)

    # === 부록 ===
    doc.add_page_break()
    add_heading(doc, "부록 A. 법령 구조 용어 대조표", 1)
    struct_terms = [
        ["인도네시아어", "한국어", "설명"],
        ["BAB", "장 (章)", "법령의 대분류"],
        ["Pasal", "조 (條)", "법령의 기본 조항 단위"],
        ["Ayat", "항 (項)", "조 아래 세부 항목"],
        ["Huruf", "호 (號)", "항 아래 열거 항목 (a, b, c...)"],
        ["Angka", "목 (目)", "호 아래 세부 항목 (1, 2, 3...)"],
    ]
    create_table(doc, struct_terms)

    doc.add_paragraph()
    add_heading(doc, "부록 B. 전체 용어 대조표", 1)
    full_terms = [
        ["인도네시아어", "한국어", "영어"],
        ["Undang-Undang (UU)", "법률", "Law/Act"],
        ["Peraturan Pemerintah (PP)", "정부령", "Government Regulation"],
        ["Peraturan Presiden (Perpres)", "대통령령", "Presidential Regulation"],
        ["Peraturan Menteri (Permen)", "장관령", "Ministerial Regulation"],
        ["Berlaku", "현행", "In Force"],
        ["Tidak Berlaku", "폐지", "Not In Force"],
        ["Dicabut", "폐지됨", "Revoked"],
        ["Diubah", "개정됨", "Amended"],
        ["Menimbang", "고려사항", "Considering"],
        ["Mengingat", "근거조항", "Having Regard To"],
        ["Memutuskan", "결의하다", "Has Decided"],
        ["Menetapkan", "제정하다", "To Enact"],
        ["sepanjang tidak bertentangan", "상충되지 않는 한", "insofar as not contrary"],
        ["tetap berlaku", "계속 유효", "remains in force"],
        ["mulai berlaku", "시행", "enters into force"],
        ["berlaku surut", "소급 적용", "retroactive effect"],
    ]
    create_table(doc, full_terms)

    # 저장
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_FILE)
    print(f"\n✅ 종합 보고서 생성 완료: {OUTPUT_FILE}")
    print(f"   파일 크기: {OUTPUT_FILE.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    generate_report()
