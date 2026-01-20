#!/usr/bin/env python3
"""
인도네시아 법령정보시스템 설계 문서 생성 스크립트

목적: 메타데이터 추출, Neo4j 그래프 모델, 조건부 표현 번역, XML 설계
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
OUTPUT_DIR = PROJECT_ROOT / "docs" / "exports"
OUTPUT_FILE = OUTPUT_DIR / "법령정보시스템_설계문서.docx"

RFP_WHERE = "jenis IN ('UNDANG-UNDANG','PERATURAN PEMERINTAH','PERATURAN PRESIDEN','PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG','PERATURAN MENTERI','PERATURAN BADAN/LEMBAGA')"


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


def fmt(n) -> str:
    if n is None:
        return "0"
    if isinstance(n, float):
        return f"{n:,.1f}"
    return f"{n:,}"


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


def generate_report():
    print("=" * 60)
    print("법령정보시스템 설계문서 생성")
    print("=" * 60)

    doc = Document()

    # === 제목 ===
    title = doc.add_heading("인도네시아 법령정보시스템 설계 문서", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(f"메타데이터 추출 · Neo4j 그래프 모델 · 국제표준 XML 설계\n\n작성일: {datetime.now().strftime('%Y-%m-%d')}")
    run.font.size = Pt(12)

    doc.add_page_break()

    # === 1. 메타데이터 추출 전략 ===
    add_heading(doc, "1. 메타데이터 추출 전략", 1)

    add_para(doc, "1.1 데이터베이스 필드 활용", bold=True)
    doc.add_paragraph("peraturan.db의 메타데이터 필드에서 추출 가능한 정보:")

    metadata_fields = [
        ["필드명", "용도", "추출 정보", "활용도"],
        ["slug", "고유 식별자", "법령 ID", "★★★"],
        ["jenis", "법령 유형", "UU, PP, PERPRES 등", "★★★"],
        ["nomor", "법령 번호", "숫자/기호", "★★★"],
        ["tahun", "제정 연도", "1945~2026", "★★★"],
        ["tentang", "법령 제목", "법령 내용 요약", "★★★"],
        ["status", "현행성 + 관계", "Berlaku/Tidak Berlaku + 폐지법령 정보", "★★★"],
        ["pemrakarsa", "발령기관", "부처/기관명", "★★☆"],
        ["tanggal_penetapan", "제정일", "날짜", "★★☆"],
        ["pejabat_penetapan", "제정자", "서명자 이름", "★☆☆"],
    ]
    create_table(doc, metadata_fields)
    doc.add_paragraph()

    add_para(doc, "1.2 status 필드에서 관계 추출", bold=True)
    doc.add_paragraph("status 필드에는 현행성 상태와 함께 폐지한 법령 정보가 포함됨:")

    status_code = """[status 필드 구조]
┌─────────────────────────────────────────────────────────────┐
│ Berlaku                    → 현행 법령                        │
│ Tidak BerlakuDicabut Oleh: → 폐지 + 폐지한 법령 정보            │
│   Peraturan Menteri Keuangan Nomor 3 Tahun 2024             │
│   Tentang Tarif Layanan...                                   │
└─────────────────────────────────────────────────────────────┘

[추출 정규식]
Dicabut Oleh\\s*:\\s*(Peraturan\\s+\\w+(?:\\s+\\w+)*)\\s+Nomor\\s+(\\S+)\\s+Tahun\\s+(\\d{4})

[추출 결과]
- 폐지한 법령 유형: Peraturan Menteri Keuangan
- 폐지한 법령 번호: 3
- 폐지한 법령 연도: 2024
"""
    add_code_block(doc, status_code)
    doc.add_paragraph()

    add_para(doc, "1.3 본문 텍스트에서 관계 추출", bold=True)
    doc.add_paragraph("extracted_text 필드에서 정규식으로 추출 가능한 관계 패턴:")

    text_patterns = [
        ["패턴 (인도네시아어)", "한국어 의미", "관계 유형", "검출 건수"],
        ["dicabut dan dinyatakan tidak berlaku", "폐지되고 효력 없음 선언", "REVOKES", "9,305"],
        ["mencabut", "~을 폐지함", "REVOKES", "1,400"],
        ["diubah dengan", "~에 의해 개정됨", "AMENDED_BY", "3,435"],
        ["mengubah", "~을 개정함", "AMENDS", "2,982"],
        ["perubahan atas", "~의 개정", "AMENDMENT_OF", "4,748"],
        ["sebagaimana telah diubah", "이미 개정된 바와 같이", "AMENDED_BY", "2,784"],
        ["untuk melaksanakan", "~을 시행하기 위해", "IMPLEMENTS", "5,242"],
        ["berdasarkan", "~에 근거하여", "BASED_ON", "21,521"],
        ["sepanjang tidak bertentangan", "상충되지 않는 한", "CONDITIONAL", "2,285"],
    ]
    create_table(doc, text_patterns)
    doc.add_paragraph()

    # === 2. Neo4j 그래프 모델 ===
    doc.add_page_break()
    add_heading(doc, "2. Neo4j 그래프 모델 설계", 1)

    add_para(doc, "2.1 노드 유형 (Node Types)", bold=True)

    node_model = """[노드 유형]
┌─────────────────────────────────────────────────────────────┐
│  (:Law)                    법령 노드                          │
│    - id: string            고유 식별자 (slug)                  │
│    - jenis: string         법령 유형                          │
│    - nomor: string         법령 번호                          │
│    - tahun: integer        제정 연도                          │
│    - tentang: string       제목                              │
│    - status: string        현행성 상태                        │
│    - tanggal_penetapan: date  제정일                         │
│    - validity: string      현행/폐지/조건부                    │
├─────────────────────────────────────────────────────────────┤
│  (:Agency)                 발령기관 노드                       │
│    - name: string          기관명                             │
│    - type: string          기관 유형 (부처/위원회/청 등)         │
├─────────────────────────────────────────────────────────────┤
│  (:Article)                조항 노드 (선택적)                   │
│    - law_id: string        소속 법령                          │
│    - number: string        조항 번호 (Pasal 1, 2, ...)        │
│    - content: text         조항 내용                          │
└─────────────────────────────────────────────────────────────┘
"""
    add_code_block(doc, node_model)
    doc.add_paragraph()

    add_para(doc, "2.2 관계 유형 (Relationship Types)", bold=True)

    rel_model = """[관계 유형]
┌─────────────────────────────────────────────────────────────┐
│  -[:REVOKES]->             폐지 관계                          │
│    (law_a)-[:REVOKES]->(law_b)                              │
│    "law_a가 law_b를 폐지함"                                   │
│    properties: { date, clause }                             │
├─────────────────────────────────────────────────────────────┤
│  -[:AMENDS]->              개정 관계                          │
│    (law_a)-[:AMENDS]->(law_b)                               │
│    "law_a가 law_b를 개정함"                                   │
│    properties: { amendment_number, date }                   │
├─────────────────────────────────────────────────────────────┤
│  -[:REFERENCES]->          참조 관계                          │
│    (law_a)-[:REFERENCES]->(law_b)                           │
│    "law_a가 law_b를 인용함"                                   │
│    properties: { context, section }                         │
├─────────────────────────────────────────────────────────────┤
│  -[:IMPLEMENTS]->          시행 관계                          │
│    (law_a)-[:IMPLEMENTS]->(law_b)                           │
│    "law_a가 law_b를 시행/구체화함"                             │
│    properties: { type }                                     │
├─────────────────────────────────────────────────────────────┤
│  -[:CONDITIONAL_VALID]->   조건부 효력 관계                    │
│    (law_a)-[:CONDITIONAL_VALID {until: law_b}]->(condition) │
│    "law_a는 ~하는 한 유효함"                                   │
│    properties: { condition_type, condition_text }           │
├─────────────────────────────────────────────────────────────┤
│  -[:ISSUED_BY]->           발령 관계                          │
│    (law)-[:ISSUED_BY]->(agency)                             │
│    "해당 기관이 법령을 발령함"                                  │
└─────────────────────────────────────────────────────────────┘
"""
    add_code_block(doc, rel_model)
    doc.add_paragraph()

    add_para(doc, "2.3 현행성 판단 Cypher 쿼리", bold=True)
    doc.add_paragraph("Neo4j에서 법령의 현행성을 조회하는 쿼리 예시:")

    cypher_queries = """// 1. 특정 법령이 현재 유효한지 확인
MATCH (law:Law {id: 'uu-no-11-tahun-2020'})
OPTIONAL MATCH (law)<-[:REVOKES]-(revoker:Law)
RETURN law.tentang AS 제목,
       law.status AS 상태,
       CASE
         WHEN revoker IS NOT NULL THEN '폐지됨'
         WHEN law.status = 'Berlaku' THEN '현행'
         ELSE '확인필요'
       END AS 현행성,
       revoker.id AS 폐지한_법령

// 2. 특정 법령을 폐지한 모든 법령 조회
MATCH (law:Law {id: 'pp-no-24-tahun-2018'})<-[:REVOKES]-(revoker:Law)
RETURN revoker.jenis + ' No. ' + revoker.nomor + ' Tahun ' + revoker.tahun AS 폐지법령

// 3. 조건부 효력 법령 조회
MATCH (law:Law)-[r:CONDITIONAL_VALID]->(condition)
WHERE r.condition_type = 'SEPANJANG_TIDAK_BERTENTANGAN'
RETURN law.id, law.tentang, r.condition_text

// 4. 법령 개정 이력 추적
MATCH path = (current:Law {id: 'uu-no-6-tahun-2023'})-[:AMENDS*]->(original:Law)
RETURN [n IN nodes(path) | n.id] AS 개정이력

// 5. 특정 법령을 참조하는 모든 법령 조회
MATCH (referencing:Law)-[:REFERENCES]->(law:Law {id: 'uu-no-11-tahun-2020'})
RETURN referencing.id, referencing.jenis, referencing.tentang
ORDER BY referencing.tahun DESC
"""
    add_code_block(doc, cypher_queries)
    doc.add_paragraph()

    add_para(doc, "2.4 그래프 시각화 예시", bold=True)
    graph_example = """
    ┌───────────────┐                    ┌───────────────┐
    │  UU 11/2020   │───[:REVOKES]──────>│  UU 13/2003   │
    │  Cipta Kerja  │                    │  Ketenagakerjaan│
    │  status:현행   │                    │  status:폐지   │
    └───────┬───────┘                    └───────────────┘
            │
            │[:IMPLEMENTS]
            ▼
    ┌───────────────┐       ┌───────────────┐
    │  PP 35/2021   │──────>│  Kemnaker     │
    │  Ketenagakerjaan│      │  (Agency)     │
    │  status:현행   │       └───────────────┘
    └───────┬───────┘
            │
            │[:CONDITIONAL_VALID]
            ▼
    ┌────────────────────────────────────────┐
    │  condition: "sepanjang tidak          │
    │  bertentangan dengan UU ini"          │
    │  (상충되지 않는 한)                      │
    └────────────────────────────────────────┘
"""
    add_code_block(doc, graph_example)

    # === 3. 조건부 효력 표현 한글 번역 ===
    doc.add_page_break()
    add_heading(doc, "3. 법령 표현 한글 번역 체계", 1)

    add_para(doc, "3.1 현행성 관련 표현", bold=True)

    validity_terms = [
        ["인도네시아어", "한국어 번역", "의미 설명"],
        ["Berlaku", "현행", "현재 효력이 있는 법령"],
        ["Tidak Berlaku", "폐지", "효력이 없는 법령"],
        ["Dicabut", "폐지됨", "다른 법령에 의해 폐지된 상태"],
        ["Dicabut dan dinyatakan tidak berlaku", "폐지되고 효력 없음이 선언됨", "명시적 폐지 선언"],
        ["Diubah", "개정됨", "다른 법령에 의해 수정된 상태"],
        ["Mulai berlaku", "시행", "효력 발생 시점"],
        ["Berlaku surut", "소급 적용", "과거 시점으로 소급하여 적용"],
    ]
    create_table(doc, validity_terms)
    doc.add_paragraph()

    add_para(doc, "3.2 조건부 효력 표현 (★ 중요)", bold=True)
    doc.add_paragraph("다음 표현들은 '조건부 현행' 상태를 나타내며, 시스템에서 특별 처리 필요:")

    conditional_terms = [
        ["인도네시아어", "한국어 번역", "검출 건수", "처리 방식"],
        ["sepanjang tidak bertentangan", "상충되지 않는 한", "2,285", "조건부 현행"],
        ["sepanjang belum diatur", "아직 규정되지 않은 한", "23", "조건부 현행"],
        ["sepanjang tidak diatur lain", "달리 규정되지 않는 한", "31", "조건부 현행"],
        ["tetap berlaku", "계속 유효", "4,777", "경과 규정"],
        ["masih tetap berlaku", "여전히 유효", "1,067", "경과 규정"],
        ["tetap berlaku sampai", "~까지 유효", "67", "한시적 유효"],
    ]
    create_table(doc, conditional_terms)
    doc.add_paragraph()

    doc.add_paragraph("※ '상충되지 않는 한' 조건부 효력의 의미:")
    doc.add_paragraph("   새로운 법령이 제정되더라도, 기존 규정이 새 법령과 충돌하지 않는 범위 내에서")
    doc.add_paragraph("   계속 효력을 유지함. 법률가의 해석이 필요한 회색지대(gray area)에 해당.")

    add_para(doc, "3.3 관계 표현", bold=True)

    relation_terms = [
        ["인도네시아어", "한국어 번역", "관계 방향"],
        ["mencabut", "~을 폐지함", "A → B (A가 B를 폐지)"],
        ["dicabut oleh", "~에 의해 폐지됨", "B ← A (B가 A에 의해 폐지됨)"],
        ["mengubah", "~을 개정함", "A → B (A가 B를 개정)"],
        ["diubah dengan", "~에 의해 개정됨", "B ← A"],
        ["perubahan atas", "~의 개정", "A는 B의 개정본"],
        ["sebagaimana telah diubah", "이미 개정된 바와 같이", "개정 이력 참조"],
        ["sebagaimana telah beberapa kali diubah", "여러 차례 개정된 바와 같이", "다중 개정 이력"],
        ["untuk melaksanakan", "~을 시행하기 위해", "A는 B의 시행령"],
        ["sebagai pelaksanaan", "~의 시행으로서", "A는 B의 시행령"],
        ["berdasarkan", "~에 근거하여", "A가 B를 근거로 함"],
    ]
    create_table(doc, relation_terms)
    doc.add_paragraph()

    add_para(doc, "3.4 시행일 표현", bold=True)

    effective_terms = [
        ["인도네시아어", "한국어 번역", "검출 건수"],
        ["mulai berlaku pada tanggal diundangkan", "공포일에 시행", "8,301"],
        ["mulai berlaku pada tanggal ditetapkan", "제정일에 시행", "1,763"],
        ["mulai berlaku setelah ... hari", "~일 후 시행", "1,012"],
        ["mulai berlaku pada tanggal 1 Januari", "1월 1일 시행", "-"],
        ["berlaku surut sejak tanggal", "~일부터 소급 적용", "369"],
    ]
    create_table(doc, effective_terms)

    # === 4. 국제표준 XML 설계 (Akoma Ntoso) ===
    doc.add_page_break()
    add_heading(doc, "4. 국제표준 XML 설계 (Akoma Ntoso / LegalDocML)", 1)

    add_para(doc, "4.1 Akoma Ntoso 개요", bold=True)
    doc.add_paragraph("Akoma Ntoso(AKN)는 OASIS에서 표준화한 법률 문서 마크업 언어로,")
    doc.add_paragraph("LegalDocML(Legal Documents Markup Language)이라고도 함.")
    doc.add_paragraph()
    doc.add_paragraph("• 표준: OASIS LegalDocML TC (oasis-open.org)")
    doc.add_paragraph("• 버전: Akoma Ntoso 3.0")
    doc.add_paragraph("• 네임스페이스: http://docs.oasis-open.org/legaldocml/ns/akn/3.0")

    add_para(doc, "4.2 문서 구조", bold=True)

    akn_structure = """[Akoma Ntoso 기본 구조]
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>                          <!-- 법령 문서 -->
    <meta>                       <!-- 메타데이터 -->
      <identification>           <!-- 식별 정보 -->
      <lifecycle>                <!-- 생애주기 (제정, 개정, 폐지) -->
      <workflow>                 <!-- 입법 과정 -->
      <analysis>                 <!-- 참조/관계 분석 -->
      <references>               <!-- 참조 법령 목록 -->
    </meta>
    <preface>                    <!-- 전문 (Menimbang, Mengingat) -->
    <preamble>                   <!-- 서문 -->
    <body>                       <!-- 본문 -->
      <chapter>                  <!-- BAB (장) -->
        <article>                <!-- Pasal (조) -->
          <paragraph>            <!-- Ayat (항) -->
            <point>              <!-- Huruf (호) -->
    </body>
    <conclusions>                <!-- 결론/폐지조항 -->
  </act>
</akomaNtoso>
"""
    add_code_block(doc, akn_structure)
    doc.add_paragraph()

    add_para(doc, "4.3 인도네시아 법령 XML 샘플", bold=True)
    doc.add_paragraph("UU No. 11 Tahun 2020 (Cipta Kerja) 일부를 Akoma Ntoso로 변환한 예시:")

    xml_sample = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <act name="uu-no-11-tahun-2020">

    <!-- 메타데이터 -->
    <meta>
      <identification source="#jdih">
        <FRBRWork>
          <FRBRcountry value="id"/>
          <FRBRthis value="/akn/id/act/uu/2020/11"/>
          <FRBRuri value="/akn/id/act/uu/2020/11"/>
          <FRBRdate date="2020-11-02" name="enacted"/>
          <FRBRauthor href="#dpr"/>
          <FRBRnumber value="11"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRthis value="/akn/id/act/uu/2020/11/ind@2020-11-02"/>
          <FRBRuri value="/akn/id/act/uu/2020/11/ind@2020-11-02"/>
          <FRBRdate date="2020-11-02" name="publication"/>
          <FRBRauthor href="#jdih"/>
          <FRBRlanguage language="ind"/>
        </FRBRExpression>
      </identification>

      <!-- 생애주기 -->
      <lifecycle source="#jdih">
        <eventRef date="2020-11-02" type="enacted" source="#dpr"/>
        <eventRef date="2020-11-02" type="published" source="#setneg"/>
        <eventRef date="2020-11-02" type="entryIntoForce"/>
      </lifecycle>

      <!-- 현행성 상태 -->
      <analysis source="#jdih">
        <activeModifications>
          <textualMod type="repeal">
            <source href="/akn/id/act/uu/2020/11"/>
            <destination href="/akn/id/act/uu/2003/13"/>
            <force period="#period1"/>
          </textualMod>
        </activeModifications>

        <!-- 조건부 효력 -->
        <otherAnalysis>
          <conditionalValidity type="sepanjangTidakBertentangan">
            <condition>
              <p xml:lang="id">sepanjang tidak bertentangan dengan
                 ketentuan dalam Undang-Undang ini</p>
              <p xml:lang="ko">본 법률의 규정과 상충되지 않는 한</p>
            </condition>
            <affectedLegislation>
              <ref href="/akn/id/act/pp/2018/24"/>
            </affectedLegislation>
          </conditionalValidity>
        </otherAnalysis>
      </analysis>

      <!-- 참조 법령 -->
      <references source="#jdih">
        <TLCOrganization eId="dpr" href="/akn/id/ontology/organization/dpr"
                         showAs="DPR RI"/>
        <TLCOrganization eId="setneg" href="/akn/id/ontology/organization/setneg"
                         showAs="Sekretariat Negara"/>
        <passiveRef href="/akn/id/act/uu/2003/13" showAs="UU 13/2003 Ketenagakerjaan"/>
        <passiveRef href="/akn/id/act/pp/2018/24" showAs="PP 24/2018"/>
      </references>
    </meta>

    <!-- 전문 (Menimbang) -->
    <preface>
      <longTitle>
        <docTitle>UNDANG-UNDANG REPUBLIK INDONESIA</docTitle>
        <docNumber>NOMOR 11 TAHUN 2020</docNumber>
        <docPurpose>TENTANG CIPTA KERJA</docPurpose>
      </longTitle>

      <container name="menimbang">
        <p>Menimbang:</p>
        <blockList>
          <item><num>a.</num>
            <p>bahwa untuk mewujudkan tujuan pembentukan Pemerintah Negara Indonesia
               dan mewujudkan masyarakat Indonesia yang sejahtera, adil, dan makmur
               berdasarkan Pancasila...</p>
          </item>
        </blockList>
      </container>

      <container name="mengingat">
        <p>Mengingat:</p>
        <blockList>
          <item><num>1.</num>
            <p><ref href="/akn/id/act/uud/1945">Pasal 4 ayat (1), Pasal 5 ayat (1),
               Pasal 18, Pasal 18A, Pasal 18B, Pasal 20, Pasal 22D ayat (2), Pasal 27
               ayat (2), Pasal 28D ayat (1) dan ayat (2), dan Pasal 33 Undang-Undang
               Dasar Negara Republik Indonesia Tahun 1945</ref>;</p>
          </item>
        </blockList>
      </container>
    </preface>

    <!-- 본문 -->
    <body>
      <chapter eId="chp_I">
        <num>BAB I</num>
        <heading>KETENTUAN UMUM</heading>

        <article eId="art_1">
          <num>Pasal 1</num>
          <paragraph eId="art_1__para_1">
            <num>(1)</num>
            <content>
              <p>Cipta Kerja adalah upaya penciptaan kerja melalui usaha kemudahan,
                 perlindungan, dan pemberdayaan koperasi dan usaha mikro, kecil, dan
                 menengah, peningkatan ekosistem investasi dan kemudahan berusaha, dan
                 investasi Pemerintah Pusat dan percepatan proyek strategis nasional.</p>
            </content>
          </paragraph>
          <paragraph eId="art_1__para_2">
            <num>(2)</num>
            <content>
              <p>Pemerintah Pusat adalah Presiden Republik Indonesia yang memegang
                 kekuasaan pemerintahan negara Republik Indonesia yang dibantu oleh
                 Wakil Presiden dan menteri sebagaimana dimaksud dalam Undang-Undang
                 Dasar Negara Republik Indonesia Tahun 1945.</p>
            </content>
          </paragraph>
        </article>
      </chapter>

      <!-- 폐지 조항 -->
      <chapter eId="chp_XV">
        <num>BAB XV</num>
        <heading>KETENTUAN PENUTUP</heading>

        <article eId="art_185">
          <num>Pasal 185</num>
          <content>
            <p>Pada saat Undang-Undang ini mulai berlaku:</p>
            <blockList>
              <item eId="art_185__item_a">
                <num>a.</num>
                <p><ref href="/akn/id/act/uu/2003/13">Undang-Undang Nomor 13 Tahun 2003
                   tentang Ketenagakerjaan</ref>
                   (<remark status="revoked" xml:lang="ko">폐지됨</remark>)
                   <mod>
                     <quotedText>dicabut dan dinyatakan tidak berlaku</quotedText>
                     <quotedText xml:lang="ko">폐지되고 효력 없음이 선언됨</quotedText>
                   </mod>;</p>
              </item>
              <item eId="art_185__item_b">
                <num>b.</num>
                <p>semua peraturan pelaksanaan dari Undang-Undang yang dicabut
                   sebagaimana dimaksud pada huruf a, dinyatakan masih
                   <mod type="conditionalValidity">
                     <quotedText>tetap berlaku sepanjang tidak bertentangan</quotedText>
                     <quotedText xml:lang="ko">상충되지 않는 한 계속 유효</quotedText>
                   </mod>
                   dengan ketentuan dalam Undang-Undang ini.</p>
              </item>
            </blockList>
          </content>
        </article>

        <article eId="art_186">
          <num>Pasal 186</num>
          <content>
            <p>Undang-Undang ini
               <event type="entryIntoForce">
                 <quotedText>mulai berlaku pada tanggal diundangkan</quotedText>
                 <quotedText xml:lang="ko">공포일에 시행</quotedText>
               </event>.</p>
          </content>
        </article>
      </chapter>
    </body>

    <!-- 결론 (제정 정보) -->
    <conclusions>
      <signature>
        <location>
          <p>Ditetapkan di Jakarta</p>
          <p xml:lang="ko">자카르타에서 제정</p>
        </location>
        <date date="2020-11-02">pada tanggal 2 November 2020</date>
        <person refersTo="#presiden">
          <role>PRESIDEN REPUBLIK INDONESIA</role>
          <person>JOKO WIDODO</person>
        </person>
      </signature>

      <signature>
        <location>
          <p>Diundangkan di Jakarta</p>
          <p xml:lang="ko">자카르타에서 공포</p>
        </location>
        <date date="2020-11-02">pada tanggal 2 November 2020</date>
        <person refersTo="#menkumham">
          <role>MENTERI HUKUM DAN HAK ASASI MANUSIA</role>
          <person>YASONNA H. LAOLY</person>
        </person>
      </signature>
    </conclusions>

  </act>
</akomaNtoso>
"""
    add_code_block(doc, xml_sample)

    # === 5. 구현 로드맵 ===
    doc.add_page_break()
    add_heading(doc, "5. 구현 로드맵", 1)

    add_para(doc, "5.1 데이터 파이프라인", bold=True)

    pipeline = """[데이터 처리 파이프라인]

┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   PDF 원본   │────>│  텍스트 추출  │────>│  구조 파싱   │
│   (54GB)    │     │  (PyMuPDF)  │     │  (정규식)   │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
      ┌────────────────────────────────────────┘
      │
      ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  관계 추출   │────>│  Neo4j 적재  │────>│  XML 변환   │
│  (NLP/정규식)│     │  (그래프DB)  │     │ (AKN 3.0)  │
└─────────────┘     └─────────────┘     └─────────────┘
"""
    add_code_block(doc, pipeline)
    doc.add_paragraph()

    add_para(doc, "5.2 단계별 작업", bold=True)

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
    doc.add_paragraph()

    add_para(doc, "5.3 Neo4j 데이터 적재 스크립트 예시", bold=True)

    neo4j_script = """# Python으로 Neo4j에 데이터 적재
from neo4j import GraphDatabase

driver = GraphDatabase.driver("bolt://localhost:7687",
                              auth=("neo4j", "password"))

def create_law_node(tx, law):
    tx.run('''
        MERGE (l:Law {id: $id})
        SET l.jenis = $jenis,
            l.nomor = $nomor,
            l.tahun = $tahun,
            l.tentang = $tentang,
            l.status = $status,
            l.validity = $validity
    ''', **law)

def create_revokes_relation(tx, from_id, to_id, date=None):
    tx.run('''
        MATCH (a:Law {id: $from_id})
        MATCH (b:Law {id: $to_id})
        MERGE (a)-[r:REVOKES]->(b)
        SET r.date = $date
    ''', from_id=from_id, to_id=to_id, date=date)

def create_conditional_validity(tx, law_id, condition_type, condition_text):
    tx.run('''
        MATCH (l:Law {id: $law_id})
        MERGE (c:Condition {type: $condition_type})
        MERGE (l)-[r:CONDITIONAL_VALID]->(c)
        SET r.text = $condition_text,
            r.text_ko = CASE $condition_type
                WHEN 'SEPANJANG_TIDAK_BERTENTANGAN' THEN '상충되지 않는 한'
                WHEN 'TETAP_BERLAKU_SAMPAI' THEN '~까지 유효'
                ELSE $condition_type
            END
    ''', law_id=law_id, condition_type=condition_type,
         condition_text=condition_text)
"""
    add_code_block(doc, neo4j_script)

    # === 부록 ===
    doc.add_page_break()
    add_heading(doc, "부록: 전체 용어 대조표", 1)

    full_terms = [
        ["인도네시아어", "한국어", "영어", "용도"],
        ["Undang-Undang (UU)", "법률", "Law/Act", "국회 제정"],
        ["Peraturan Pemerintah (PP)", "정부령", "Government Regulation", "대통령 제정"],
        ["Peraturan Presiden (Perpres)", "대통령령", "Presidential Regulation", "대통령 제정"],
        ["Peraturan Menteri (Permen)", "장관령", "Ministerial Regulation", "장관 제정"],
        ["Berlaku", "현행", "In Force", "효력 상태"],
        ["Tidak Berlaku", "폐지", "Not In Force", "효력 상태"],
        ["Dicabut", "폐지됨", "Revoked", "관계"],
        ["Diubah", "개정됨", "Amended", "관계"],
        ["Mencabut", "폐지함", "Revokes", "관계"],
        ["Mengubah", "개정함", "Amends", "관계"],
        ["BAB", "장", "Chapter", "구조"],
        ["Pasal", "조", "Article", "구조"],
        ["Ayat", "항", "Paragraph", "구조"],
        ["Huruf", "호", "Point/Letter", "구조"],
        ["Menimbang", "고려사항", "Considering", "전문"],
        ["Mengingat", "근거조항", "Having Regard To", "전문"],
        ["Memutuskan", "결의하다", "Has Decided", "결의"],
        ["Menetapkan", "제정하다", "To Enact", "결의"],
        ["Ditetapkan di", "~에서 제정", "Enacted at", "결론"],
        ["Diundangkan di", "~에서 공포", "Promulgated at", "결론"],
        ["sepanjang tidak bertentangan", "상충되지 않는 한", "insofar as not contrary", "조건부"],
        ["tetap berlaku", "계속 유효", "remains in force", "조건부"],
        ["mulai berlaku", "시행", "enters into force", "시행일"],
        ["berlaku surut", "소급 적용", "retroactive effect", "시행일"],
    ]
    create_table(doc, full_terms)

    # 저장
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_FILE)
    print(f"\n✅ 설계 문서 생성 완료: {OUTPUT_FILE}")
    print(f"   파일 크기: {OUTPUT_FILE.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    generate_report()
