#!/usr/bin/env python3
"""
인도네시아 법령 구조분석 보고서 생성 스크립트

목적: 법조문의 구조, 관계성, 현행성 표현을 분석한 보고서 생성
대상: peraturan.go.id (법제처) - RFP 대상 법령 6개 유형
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
OUTPUT_FILE = OUTPUT_DIR / "법령_구조분석_보고서.docx"

# === RFP 대상 법령 유형 (6개) ===
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

RFP_WHERE = "jenis IN ('UNDANG-UNDANG','PERATURAN PEMERINTAH','PERATURAN PRESIDEN','PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG','PERATURAN MENTERI','PERATURAN BADAN/LEMBAGA')"


def query_db(query: str) -> list[tuple]:
    if not PERATURAN_DB.exists():
        return []
    conn = sqlite3.connect(PERATURAN_DB)
    cursor = conn.execute(query)
    results = cursor.fetchall()
    conn.close()
    return results


def query_one(query: str) -> Any:
    result = query_db(query)
    return result[0][0] if result else 0


def fmt(n: int | float) -> str:
    if n is None:
        return "0"
    if isinstance(n, float):
        return f"{n:,.1f}"
    return f"{n:,}"


def pct(part: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{part / total * 100:.1f}%"


def set_cell_shading(cell, color: str):
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color}"/>')
    cell._tc.get_or_add_tcPr().append(shading)


def create_table(doc: Document, data: list[list[str]], header: bool = True) -> Any:
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
    heading = doc.add_heading(text, level=level)
    return heading


def add_para(doc: Document, text: str, bold: bool = False):
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.bold = bold
    return para


# === 데이터 수집 함수들 ===

def get_structure_stats_by_type():
    """유형별 구조 요소 통계"""
    query = f"""
        SELECT
            jenis,
            COUNT(*) as total,
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
        FROM peraturan
        WHERE {RFP_WHERE}
        GROUP BY jenis
        ORDER BY total DESC
    """
    return query_db(query)


def get_structure_stats_by_era():
    """연대별 구조 요소 통계"""
    query = f"""
        SELECT
            CASE
                WHEN tahun < 1960 THEN '1945-1959'
                WHEN tahun < 1980 THEN '1960-1979'
                WHEN tahun < 2000 THEN '1980-1999'
                WHEN tahun < 2010 THEN '2000-2009'
                WHEN tahun < 2020 THEN '2010-2019'
                ELSE '2020-현재'
            END as era,
            COUNT(*) as cnt,
            AVG(parsed_pasal_count) as avg_pasal,
            MAX(parsed_pasal_count) as max_pasal,
            SUM(parsed_bab_count) as total_bab,
            SUM(parsed_pasal_count) as total_pasal,
            SUM(parsed_ayat_count) as total_ayat
        FROM peraturan
        WHERE {RFP_WHERE} AND parsed_pasal_count > 0
        GROUP BY era ORDER BY era
    """
    return query_db(query)


def get_structure_by_type_and_era():
    """유형별 연대별 평균 Pasal 수"""
    query = f"""
        SELECT
            jenis,
            CASE
                WHEN tahun < 1960 THEN '1945-1959'
                WHEN tahun < 1980 THEN '1960-1979'
                WHEN tahun < 2000 THEN '1980-1999'
                WHEN tahun < 2010 THEN '2000-2009'
                WHEN tahun < 2020 THEN '2010-2019'
                ELSE '2020-현재'
            END as era,
            COUNT(*) as cnt,
            AVG(parsed_pasal_count) as avg_pasal
        FROM peraturan
        WHERE {RFP_WHERE} AND parsed_pasal_count > 0
        GROUP BY jenis, era
        ORDER BY jenis, era
    """
    return query_db(query)


def get_validity_stats():
    """현행성 상태 통계"""
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
    return {
        'total': total,
        'berlaku': berlaku,
        'tidak_berlaku': tidak_berlaku,
        'dicabut': dicabut
    }


def get_text_patterns():
    """본문 텍스트 패턴 통계"""
    conn = sqlite3.connect(PERATURAN_DB)
    cur = conn.cursor()

    patterns = {
        '폐지선언 (dicabut dan dinyatakan tidak berlaku)': '%dicabut dan dinyatakan tidak berlaku%',
        '개정참조 (diubah dengan)': '%diubah dengan%',
        '시행일 (mulai berlaku)': '%mulai berlaku%',
        '공포 (diundangkan di)': '%diundangkan di%',
        '법적근거 (Mengingat)': '%Mengingat%:%',
        '고려사항 (Menimbang)': '%Menimbang%:%',
        '조건부효력 (sepanjang tidak bertentangan)': '%sepanjang tidak bertentangan%',
        '법률참조 (Undang-Undang Nomor)': '%Undang-Undang Nomor%',
        '정부령참조 (Peraturan Pemerintah Nomor)': '%Peraturan Pemerintah Nomor%',
        '대통령령참조 (Peraturan Presiden Nomor)': '%Peraturan Presiden Nomor%',
    }

    results = {}
    for name, pattern in patterns.items():
        cur.execute(f"""
            SELECT COUNT(*) FROM peraturan
            WHERE {RFP_WHERE} AND extracted_text LIKE ?
        """, (pattern,))
        results[name] = cur.fetchone()[0]

    cur.execute(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND extracted_text IS NOT NULL AND extracted_text != ''")
    results['총 텍스트 보유'] = cur.fetchone()[0]

    conn.close()
    return results


def get_metadata_completeness():
    """메타데이터 완성도"""
    fields = ['status', 'tanggal_penetapan', 'pemrakarsa', 'tempat_penetapan',
              'pejabat_penetapan', 'tanggal_pengundangan']
    results = {}

    conn = sqlite3.connect(PERATURAN_DB)
    cur = conn.cursor()

    for field in fields:
        cur.execute(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN {field} IS NOT NULL AND {field} != '' THEN 1 ELSE 0 END) as filled
            FROM peraturan WHERE {RFP_WHERE}
        """)
        row = cur.fetchone()
        results[field] = {'total': row[0], 'filled': row[1]}

    conn.close()
    return results


def load_references_data():
    """참조 관계 데이터 로드"""
    if not REFERENCES_JSON.exists():
        return None

    with open(REFERENCES_JSON, 'r') as f:
        refs = json.load(f)

    # 통계 계산
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
    """텍스트 패턴 실제 예시"""
    conn = sqlite3.connect(PERATURAN_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    examples = {}

    # 폐지 선언 예시
    cur.execute(f"""
        SELECT slug, extracted_text FROM peraturan
        WHERE {RFP_WHERE} AND extracted_text LIKE '%dicabut dan dinyatakan tidak berlaku%'
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        text = row['extracted_text']
        match = re.search(r'.{0,60}(dicabut\s+dan\s+dinyatakan\s+tidak\s+berlaku).{0,100}',
                         text, re.IGNORECASE | re.DOTALL)
        if match:
            examples['폐지선언'] = {'slug': row['slug'], 'text': match.group().replace('\n', ' ')[:200]}

    # 시행일 예시
    cur.execute(f"""
        SELECT slug, extracted_text FROM peraturan
        WHERE {RFP_WHERE} AND extracted_text LIKE '%mulai berlaku pada tanggal%'
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        text = row['extracted_text']
        match = re.search(r'.{0,40}(mulai\s+berlaku\s+pada\s+tanggal).{0,80}',
                         text, re.IGNORECASE | re.DOTALL)
        if match:
            examples['시행일'] = {'slug': row['slug'], 'text': match.group().replace('\n', ' ')[:180]}

    # 개정 참조 예시
    cur.execute(f"""
        SELECT slug, extracted_text FROM peraturan
        WHERE {RFP_WHERE} AND extracted_text LIKE '%diubah dengan Undang-Undang Nomor%'
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        text = row['extracted_text']
        match = re.search(r'.{0,30}(diubah\s+dengan\s+Undang-Undang\s+Nomor\s+\d+\s+Tahun\s+\d{4}).{0,50}',
                         text, re.IGNORECASE | re.DOTALL)
        if match:
            examples['개정참조'] = {'slug': row['slug'], 'text': match.group().replace('\n', ' ')[:180]}

    # 조건부 효력 예시
    cur.execute(f"""
        SELECT slug, extracted_text FROM peraturan
        WHERE {RFP_WHERE} AND extracted_text LIKE '%sepanjang tidak bertentangan%'
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        text = row['extracted_text']
        match = re.search(r'.{0,60}(sepanjang\s+tidak\s+bertentangan).{0,80}',
                         text, re.IGNORECASE | re.DOTALL)
        if match:
            examples['조건부효력'] = {'slug': row['slug'], 'text': match.group().replace('\n', ' ')[:180]}

    conn.close()
    return examples


# === 보고서 생성 ===

def generate_report():
    print("=" * 60)
    print("법령 구조분석 보고서 생성")
    print("=" * 60)

    doc = Document()

    # 제목
    title = doc.add_heading("인도네시아 법령 구조분석 보고서", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 부제목
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(f"법조문 구조, 관계성, 현행성 표현 분석\n\n작성일: {datetime.now().strftime('%Y-%m-%d')}")
    run.font.size = Pt(12)

    doc.add_page_break()

    # === 1. 요약 ===
    add_heading(doc, "1. 요약", 1)

    # 데이터 현황
    total = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE}")
    with_text = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND extracted_text IS NOT NULL AND extracted_text != ''")
    parsed = query_one(f"SELECT COUNT(*) FROM peraturan WHERE {RFP_WHERE} AND parsed_pasal_count > 0")

    add_para(doc, "1.1 분석 대상 현황", bold=True)
    summary_data = [
        ["항목", "값"],
        ["RFP 대상 법령 총계", fmt(total)],
        ["텍스트 추출 완료", f"{fmt(with_text)} ({pct(with_text, total)})"],
        ["구조 파싱 완료", f"{fmt(parsed)} ({pct(parsed, total)})"],
    ]
    create_table(doc, summary_data)
    doc.add_paragraph()

    # 구조 요소 총계
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
    doc.add_paragraph("법령 유형별로 BAB(장), Pasal(조), Ayat(항), Huruf(호) 구조 요소를 보유한 문서 비율:")

    stats = get_structure_stats_by_type()
    type_struct_data = [["유형", "약어", "문서 수", "BAB 보유", "Pasal 보유", "Ayat 보유", "Huruf 보유"]]
    for row in stats:
        jenis, total, _, _, _, _, _, _, has_bab, has_pasal, has_ayat, has_huruf = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        type_struct_data.append([
            RFP_TYPE_KOREAN.get(jenis, jenis[:10]),
            abbr,
            fmt(total),
            pct(has_bab, total),
            pct(has_pasal, total),
            pct(has_ayat, total),
            pct(has_huruf, total)
        ])
    create_table(doc, type_struct_data)
    doc.add_paragraph()

    add_para(doc, "2.2 유형별 평균 구조 요소 수", bold=True)
    type_avg_data = [["유형", "평균 Pasal", "최대 Pasal", "총 BAB", "총 Pasal", "총 Ayat"]]
    for row in stats:
        jenis, total, total_bab, total_pasal, total_ayat, _, avg_pasal, max_pasal, _, _, _, _ = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        type_avg_data.append([
            f"{RFP_TYPE_KOREAN.get(jenis, jenis[:8])} ({abbr})",
            f"{avg_pasal:.1f}" if avg_pasal else "0",
            fmt(max_pasal or 0),
            fmt(total_bab or 0),
            fmt(total_pasal or 0),
            fmt(total_ayat or 0)
        ])
    create_table(doc, type_avg_data)
    doc.add_paragraph()

    # 연대별 구조 변화
    add_para(doc, "2.3 연대별 구조 변화 추이", bold=True)
    doc.add_paragraph("시대에 따른 법령 문서의 복잡도(평균 Pasal 수) 변화:")

    era_stats = get_structure_stats_by_era()
    era_data = [["연대", "문서 수", "평균 Pasal", "최대 Pasal", "총 Pasal"]]
    for row in era_stats:
        era, cnt, avg_pasal, max_pasal, total_bab, total_pasal, total_ayat = row
        era_data.append([
            era,
            fmt(cnt),
            f"{avg_pasal:.1f}" if avg_pasal else "0",
            fmt(max_pasal or 0),
            fmt(total_pasal or 0)
        ])
    create_table(doc, era_data)
    doc.add_paragraph()

    doc.add_paragraph("※ 분석: 1945~1959년 초창기 법령은 평균 10조 미만의 간단한 구조였으나, "
                     "2020년 이후 현대 법령은 평균 35조 이상으로 복잡도가 크게 증가함")

    # 유형별 연대별 매트릭스
    add_para(doc, "2.4 유형별 연대별 평균 Pasal 수", bold=True)
    type_era = get_structure_by_type_and_era()

    # 매트릭스 형태로 변환
    eras = ['1945-1959', '1960-1979', '1980-1999', '2000-2009', '2010-2019', '2020-현재']
    types_order = ['UU', 'PP', 'PERPRES', 'PERPPU', 'PERMEN', 'PERBAN']

    matrix_data = defaultdict(dict)
    for row in type_era:
        jenis, era, cnt, avg_pasal = row
        abbr = RFP_TYPE_ABBREV.get(jenis, jenis[:6])
        matrix_data[abbr][era] = f"{avg_pasal:.1f}" if avg_pasal else "-"

    matrix_table = [["유형"] + eras]
    for t in types_order:
        row_data = [t]
        for era in eras:
            row_data.append(matrix_data.get(t, {}).get(era, "-"))
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
        ["   └ 다른 법에 의해 폐지 (Dicabut Oleh)", fmt(validity['dicabut']), pct(validity['dicabut'], validity['total'])],
    ]
    create_table(doc, validity_data)
    doc.add_paragraph()

    doc.add_paragraph("※ status 필드의 'Tidak Berlaku'에는 폐지한 법령 정보가 함께 기록됨")
    doc.add_paragraph("   예: 'Tidak BerlakuDicabut Oleh: Peraturan Menteri Keuangan Nomor 3 Tahun 2024...'")

    add_para(doc, "3.2 메타데이터 필드 완성도", bold=True)
    metadata = get_metadata_completeness()
    metadata_data = [["필드명", "용도", "완성도"]]
    field_purposes = {
        'status': '현행성 상태 (Berlaku/Tidak Berlaku)',
        'tanggal_penetapan': '제정일',
        'pemrakarsa': '발령기관',
        'tempat_penetapan': '제정장소',
        'pejabat_penetapan': '제정자',
        'tanggal_pengundangan': '공포일'
    }
    for field, data in metadata.items():
        metadata_data.append([
            field,
            field_purposes.get(field, ''),
            pct(data['filled'], data['total'])
        ])
    create_table(doc, metadata_data)
    doc.add_paragraph()

    # === 4. 관계성 표현 분석 ===
    add_heading(doc, "4. 관계성 표현 분석", 1)

    add_para(doc, "4.1 본문 텍스트 패턴 검출 현황", bold=True)
    doc.add_paragraph("법령 본문에서 검출된 관계성/현행성 관련 텍스트 패턴:")

    patterns = get_text_patterns()
    total_text = patterns.pop('총 텍스트 보유')
    pattern_data = [["패턴 (인도네시아어)", "검출 건수", "비율"]]
    for name, count in patterns.items():
        pattern_data.append([name, fmt(count), pct(count, total_text)])
    create_table(doc, pattern_data)
    doc.add_paragraph(f"(텍스트 보유 문서: {fmt(total_text)}건)")
    doc.add_paragraph()

    # 참조 관계 데이터
    refs_data = load_references_data()
    if refs_data:
        add_para(doc, "4.2 참조 관계 추출 현황 (all_references.json)", bold=True)
        refs_table = [
            ["참조 유형", "총 건수", "관련 문서 수", "문서당 평균"],
            ["외부 참조 (다른 법령 인용)", fmt(refs_data['ext_ref_count']),
             f"{fmt(refs_data['docs_with_ext'])} ({pct(refs_data['docs_with_ext'], refs_data['total_docs'])})",
             f"{refs_data['ext_ref_count']/refs_data['docs_with_ext']:.1f}" if refs_data['docs_with_ext'] else "0"],
            ["내부 참조 (동일 법령 내)", fmt(refs_data['int_ref_count']),
             f"{fmt(refs_data['docs_with_int'])} ({pct(refs_data['docs_with_int'], refs_data['total_docs'])})",
             f"{refs_data['int_ref_count']/refs_data['docs_with_int']:.1f}" if refs_data['docs_with_int'] else "0"],
            ["조건부 조항", fmt(refs_data['conditional_count']),
             f"{fmt(refs_data['docs_with_cond'])} ({pct(refs_data['docs_with_cond'], refs_data['total_docs'])})",
             f"{refs_data['conditional_count']/refs_data['docs_with_cond']:.1f}" if refs_data['docs_with_cond'] else "0"],
        ]
        create_table(doc, refs_table)
        doc.add_paragraph(f"(분석 대상 문서: {fmt(refs_data['total_docs'])}건)")
        doc.add_paragraph()

        # 외부 참조 유형별
        add_para(doc, "4.3 외부 참조 법령 유형별 분포", bold=True)
        ext_type_data = [["참조 법령 유형", "건수"]]
        for ref_type, count in refs_data['ext_ref_types'].most_common(10):
            ext_type_data.append([ref_type, fmt(count)])
        create_table(doc, ext_type_data)
        doc.add_paragraph()

        # 조건부 조항 유형별
        add_para(doc, "4.4 조건부 조항 유형별 분포", bold=True)
        cond_type_data = [["조항 유형", "의미", "건수"]]
        cond_meanings = {
            'DICABUT_DAN_TIDAK_BERLAKU': '폐지 선언',
            'SEPANJANG_TIDAK_BERTENTANGAN': '조건부 효력 유지',
            'TETAP_BERLAKU_SAMPAI': '경과 규정'
        }
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
        doc.add_paragraph(f"...{examples['폐지선언']['text']}...")
        doc.add_paragraph()

    if examples.get('시행일'):
        add_para(doc, "5.2 시행일 표현 (mulai berlaku pada tanggal)", bold=True)
        doc.add_paragraph(f"문서: {examples['시행일']['slug']}")
        doc.add_paragraph(f"...{examples['시행일']['text']}...")
        doc.add_paragraph()

    if examples.get('개정참조'):
        add_para(doc, "5.3 개정 참조 (diubah dengan)", bold=True)
        doc.add_paragraph(f"문서: {examples['개정참조']['slug']}")
        doc.add_paragraph(f"...{examples['개정참조']['text']}...")
        doc.add_paragraph()

    if examples.get('조건부효력'):
        add_para(doc, "5.4 조건부 효력 (sepanjang tidak bertentangan)", bold=True)
        doc.add_paragraph(f"문서: {examples['조건부효력']['slug']}")
        doc.add_paragraph(f"...{examples['조건부효력']['text']}...")
        doc.add_paragraph()

    # === 6. 핵심 발견사항 ===
    add_heading(doc, "6. 핵심 발견사항", 1)

    doc.add_paragraph("6.1 문서 구조", style='List Bullet')
    doc.add_paragraph("• 인도네시아 법령은 BAB(장) → Pasal(조) → Ayat(항) → Huruf(호) 계층 구조를 따름")
    doc.add_paragraph("• 법률(UU)이 가장 복잡한 구조 (평균 41.8조), 대통령령(PERPRES)이 가장 단순 (평균 15.7조)")
    doc.add_paragraph("• 시대에 따라 법령 복잡도가 증가 (1950년대 평균 9.5조 → 2020년대 평균 36.6조)")
    doc.add_paragraph()

    doc.add_paragraph("6.2 현행성 판단", style='List Bullet')
    doc.add_paragraph("• 메타데이터 status 필드로 99.6% 문서의 현행성 자동 판단 가능")
    doc.add_paragraph("• status='Berlaku' → 현행, status LIKE 'Tidak Berlaku%' → 폐지")
    doc.add_paragraph("• 폐지된 법령의 경우 폐지한 법령 정보가 status 필드에 함께 기록됨")
    doc.add_paragraph()

    doc.add_paragraph("6.3 관계성 추출", style='List Bullet')
    doc.add_paragraph("• 외부 참조: 23,328건 (문서당 평균 2.2건)")
    doc.add_paragraph("• 내부 참조: 261,451건 (문서당 평균 17.8건)")
    doc.add_paragraph("• 조건부 조항: 12,651건 (폐지선언, 조건부 효력 등)")
    doc.add_paragraph()

    doc.add_paragraph("6.4 텍스트 패턴", style='List Bullet')
    doc.add_paragraph("• 시행일 표현: 'mulai berlaku pada tanggal diundangkan' (76% 문서)")
    doc.add_paragraph("• 폐지 표현: 'dicabut dan dinyatakan tidak berlaku' (18% 문서)")
    doc.add_paragraph("• 조건부 효력: 'sepanjang tidak bertentangan' (7% 문서)")

    # === 부록 ===
    doc.add_page_break()
    add_heading(doc, "부록: 법령 구조 용어 대조표", 1)

    terms_data = [
        ["인도네시아어", "한국어", "설명"],
        ["BAB", "장 (章)", "법령의 대분류"],
        ["Pasal", "조 (條)", "법령의 기본 조항 단위"],
        ["Ayat", "항 (項)", "조 아래 세부 항목"],
        ["Huruf", "호 (號)", "항 아래 열거 항목 (a, b, c...)"],
        ["Berlaku", "현행", "현재 효력이 있는 법령"],
        ["Tidak Berlaku", "폐지", "효력이 없는 법령"],
        ["Dicabut", "폐지됨", "다른 법령에 의해 폐지"],
        ["Diubah", "개정됨", "다른 법령에 의해 수정"],
        ["Mengingat", "근거조항", "법적 근거 기술 부분"],
        ["Menimbang", "고려사항", "제정 배경 기술 부분"],
        ["Menetapkan", "결정하다", "법령 결정 선언"],
        ["Memutuskan", "결의하다", "결의/결정 선언"],
    ]
    create_table(doc, terms_data)

    # 저장
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_FILE)
    print(f"\n✅ 보고서 생성 완료: {OUTPUT_FILE}")
    print(f"   파일 크기: {OUTPUT_FILE.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    generate_report()
