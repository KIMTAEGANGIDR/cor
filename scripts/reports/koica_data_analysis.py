#!/usr/bin/env python3
"""KOICA Data Analysis Report Generator.

Generates comprehensive data analysis reports for the KOICA Indonesian Legal
Information System project. Outputs JSON, Markdown, and Word (DOCX) formats.

Usage:
    python scripts/koica_data_analysis.py --format all --output-dir docs/exports/koica
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    from peraturan.src.services.koica_analyzer import KoicaAnalyzer, JENIS_KOREAN
except ImportError:
    # When running from project root
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "peraturan"))
    from src.services.koica_analyzer import KoicaAnalyzer, JENIS_KOREAN


def generate_json_reports(results: dict, output_dir: Path) -> list[Path]:
    """Generate individual JSON report files.

    Args:
        results: Full analysis results
        output_dir: Output directory

    Returns:
        List of generated file paths
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_files = []

    # Map of section names to filenames
    sections = {
        "document_types": "01_document_types.json",
        "year_distribution": "02_year_distribution.json",
        "agency_distribution": "03_agency_distribution.json",
        "data_completeness": "04_data_completeness.json",
        "pdf_availability": "05_pdf_availability.json",
        "legal_status": "06_legal_status.json",
        "bpk_comparison": "07_bpk_comparison.json",
    }

    # Write individual section files
    for section_name, filename in sections.items():
        if section_name in results:
            file_path = output_dir / filename
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(results[section_name], f, ensure_ascii=False, indent=2)
            generated_files.append(file_path)

    # Write summary file
    summary_path = output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    generated_files.append(summary_path)

    return generated_files


def generate_markdown_report(results: dict, output_dir: Path) -> Path:
    """Generate Markdown format report in Korean.

    Args:
        results: Full analysis results
        output_dir: Output directory

    Returns:
        Path to generated file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "KOICA_사전분석_데이터현황보고서.md"

    lines = []

    # Title
    lines.append("# KOICA 사전분석: 인도네시아 법령 데이터 현황 보고서")
    lines.append("")
    lines.append(f"**분석일시:** {results['meta']['analysis_date']}")
    lines.append("")

    # 1. Overview
    lines.append("## 1. 개요")
    lines.append("")
    lines.append("### 1.1 분석 목적 및 배경")
    lines.append("")
    lines.append("본 보고서는 KOICA 인도네시아 법령정보 시스템 구축 사업의 사전분석을 위해 ")
    lines.append("수집된 법령 데이터의 현황을 분석한 결과입니다.")
    lines.append("")
    lines.append("### 1.2 데이터 소스 개요")
    lines.append("")
    lines.append("| 데이터 소스 | 총 건수 | 설명 |")
    lines.append("|------------|--------|------|")
    lines.append(f"| peraturan.go.id (법제처) | {results['summary']['peraturan_total']:,}건 | 인도네시아 법제처 공식 데이터 |")
    if results['bpk_comparison'].get('available'):
        lines.append(f"| peraturan.bpk.go.id (BPK) | {results['summary']['bpk_total']:,}건 | 인도네시아 감사원 JDIH 데이터 |")
    lines.append("")

    # Key findings
    lines.append("### 1.3 핵심 지표 요약")
    lines.append("")
    for finding in results['summary'].get('key_findings', []):
        lines.append(f"- {finding}")
    lines.append("")

    # 2. Document Types
    lines.append("## 2. 법령 유형별 분포")
    lines.append("")
    lines.append("| 유형 | 건수 | 비율 | 유효 건수 | 유효율 |")
    lines.append("|------|-----|------|----------|-------|")
    for t in results['document_types']['types']:
        lines.append(
            f"| {t['jenis_korean']} | {t['total']:,} | {t['percentage']}% | "
            f"{t['berlaku']:,} | {t['berlaku_rate']}% |"
        )
    lines.append("")
    lines.append(f"**총 {results['document_types']['total_documents']:,}건**, "
                f"{results['document_types']['type_count']}개 유형")
    lines.append("")

    # 3. Year Distribution
    lines.append("## 3. 연도별 분포")
    lines.append("")
    year_data = results['year_distribution']
    lines.append(f"- **연도 범위:** {year_data['year_range']['min']}년 ~ {year_data['year_range']['max']}년 "
                f"({year_data['span_years']}년간)")
    lines.append("")

    lines.append("### 10년 단위 분포")
    lines.append("")
    lines.append("| 기간 | 건수 | 유효 건수 |")
    lines.append("|------|-----|----------|")
    for d in year_data['decade_summary']:
        lines.append(f"| {d['decade']} | {d['total']:,} | {d['berlaku']:,} |")
    lines.append("")

    lines.append("### 최다 법령 연도 (Top 5)")
    lines.append("")
    for i, y in enumerate(year_data['top_years'], 1):
        lines.append(f"{i}. **{y['year']}년**: {y['total']:,}건")
    lines.append("")

    if year_data['missing_years']:
        lines.append(f"**결측 연도:** {len(year_data['missing_years'])}개년 "
                    f"({', '.join(map(str, year_data['missing_years'][:10]))}...)")
        lines.append("")

    # 4. Agency Distribution
    lines.append("## 4. 기관별 분포")
    lines.append("")
    agency_data = results['agency_distribution']
    lines.append(f"- **고유 기관 수:** {agency_data['unique_agencies']:,}개")
    lines.append(f"- **기관 미기재:** {agency_data['without_agency']:,}건 ({agency_data['without_agency_pct']}%)")
    lines.append("")

    lines.append("### Top 20 기관")
    lines.append("")
    lines.append("| 순위 | 기관명 | 건수 | 비율 | 유효율 |")
    lines.append("|-----|-------|-----|------|-------|")
    for i, a in enumerate(agency_data['top_20_agencies'], 1):
        name = a['pemrakarsa'][:40] + "..." if len(a['pemrakarsa']) > 40 else a['pemrakarsa']
        lines.append(f"| {i} | {name} | {a['total']:,} | {a['percentage']}% | {a['berlaku_rate']}% |")
    lines.append("")

    # 5. Data Completeness
    lines.append("## 5. 데이터 완성도")
    lines.append("")
    comp_data = results['data_completeness']
    lines.append(f"- **전체 완성도:** {comp_data['overall_completeness_pct']}%")
    lines.append(f"- **완전 필드:** {comp_data['complete_fields_count']}개")
    lines.append(f"- **부분 필드:** {comp_data['partial_fields_count']}개")
    lines.append(f"- **희소 필드:** {comp_data['sparse_fields_count']}개")
    lines.append("")

    lines.append("### 필드별 완성률")
    lines.append("")
    lines.append("| 필드명 | 완성 건수 | 완성률 | 고유값 수 |")
    lines.append("|-------|----------|-------|----------|")
    for f in comp_data['fields'][:15]:  # Top 15 fields
        lines.append(f"| {f['field']} | {f['complete']:,} | {f['completeness_pct']}% | {f['distinct_values']:,} |")
    lines.append("")

    # 6. PDF Availability
    lines.append("## 6. PDF 가용성")
    lines.append("")
    pdf_data = results['pdf_availability']
    lines.append(f"- **PDF URL 보유:** {pdf_data['with_url']:,}건 ({pdf_data['url_availability_pct']}%)")
    lines.append(f"- **다운로드 완료:** {pdf_data['downloaded']:,}건 ({pdf_data['overall_download_pct']}%)")
    lines.append(f"- **다운로드 대기/실패:** {pdf_data['failed_or_pending']:,}건")
    lines.append(f"- **PDF URL 없음:** {pdf_data['no_url']:,}건")
    lines.append("")

    if pdf_data['storage']['pdf_files_found'] > 0:
        lines.append(f"**저장소 현황:** {pdf_data['storage']['pdf_files_found']:,}개 파일, "
                    f"총 {pdf_data['storage']['total_size_gb']}GB "
                    f"(평균 {pdf_data['storage']['avg_file_size_mb']}MB/파일)")
        lines.append("")

    lines.append("### 유형별 PDF 확보율")
    lines.append("")
    lines.append("| 유형 | 총 건수 | URL 보유 | 다운로드 | 확보율 |")
    lines.append("|------|--------|---------|---------|-------|")
    for t in pdf_data['by_type']:
        lines.append(
            f"| {t['jenis_korean']} | {t['total']:,} | {t['with_url']:,} | "
            f"{t['downloaded']:,} | {t['download_rate']}% |"
        )
    lines.append("")

    # 7. Legal Status
    lines.append("## 7. 현행성 상태")
    lines.append("")
    status_data = results['legal_status']
    lines.append(f"- **현행 (Berlaku):** {status_data['berlaku']:,}건 ({status_data['berlaku_pct']}%)")
    lines.append(f"- **폐지/실효 (Tidak Berlaku):** {status_data['tidak_berlaku']:,}건 ({status_data['tidak_berlaku_pct']}%)")
    lines.append(f"- **미확인:** {status_data['unknown']:,}건 ({status_data['unknown_pct']}%)")
    lines.append(f"- **고유 상태값:** {status_data['unique_status_values']:,}개")
    lines.append("")

    lines.append("### 유형별 현행율")
    lines.append("")
    lines.append("| 유형 | 총 건수 | 현행 | 폐지 | 현행율 |")
    lines.append("|------|--------|-----|------|-------|")
    for t in status_data['by_type']:
        lines.append(
            f"| {t['jenis_korean']} | {t['total']:,} | {t['berlaku']:,} | "
            f"{t['tidak_berlaku']:,} | {t['berlaku_rate']}% |"
        )
    lines.append("")

    # 8. BPK Comparison
    if results['bpk_comparison'].get('available'):
        lines.append("## 8. BPK 데이터 비교")
        lines.append("")
        bpk_data = results['bpk_comparison']
        lines.append(f"- **법제처 총 건수:** {bpk_data['peraturan_total']:,}건")
        lines.append(f"- **BPK 총 건수:** {bpk_data['bpk_total']:,}건")
        lines.append(f"- **비교 가능 유형:** {bpk_data['comparable_types_count']}개")
        lines.append("")

        lines.append("### 유형별 건수 비교")
        lines.append("")
        lines.append("| 유형 | 법제처 | BPK | 차이 |")
        lines.append("|------|-------|-----|------|")
        for t in bpk_data['type_comparison']:
            diff_sign = "+" if t['difference'] > 0 else ""
            lines.append(
                f"| {t['type_korean']} | {t['peraturan_count']:,} | {t['bpk_count']:,} | "
                f"{diff_sign}{t['difference']:,} |"
            )
        lines.append("")

        lines.append("### 현행율 비교")
        lines.append("")
        lines.append("| 유형 | 법제처 현행율 | BPK 현행율 | 차이 |")
        lines.append("|------|-------------|-----------|------|")
        for t in bpk_data['status_comparison']:
            lines.append(
                f"| {t['type_korean']} | {t['peraturan_berlaku_rate']}% | "
                f"{t['bpk_berlaku_rate']}% | {t['rate_difference']}%p |"
            )
        lines.append("")

        lines.append("**비고:**")
        for note in bpk_data.get('notes', []):
            lines.append(f"- {note}")
        lines.append("")

    # 9. Conclusion
    lines.append("## 9. 결론 및 제언")
    lines.append("")
    lines.append("### 9.1 KOICA 프로젝트 활용 방안")
    lines.append("")
    lines.append("1. **법령 데이터 기반 구축:** 법제처 데이터 35,000건 이상 활용 가능")
    lines.append("2. **PDF 문서 분석:** 다운로드 완료된 PDF를 활용한 텍스트 추출 및 분석")
    lines.append("3. **현행성 판단 시스템:** 다양한 상태값 정규화를 통한 현행성 판단 로직 개발")
    lines.append("4. **BPK 데이터 보완:** 법제처 데이터와 BPK 데이터 상호 보완")
    lines.append("")

    lines.append("### 9.2 데이터 품질 개선 필요사항")
    lines.append("")
    lines.append("1. **필드 완성도 개선:** pengundangan 관련 필드 데이터 확보")
    lines.append("2. **상태값 정규화:** 2,809개 고유 상태값 → 표준 분류 체계 구축")
    lines.append("3. **PDF 다운로드 완료:** 미확보 PDF 추가 다운로드")
    lines.append("4. **연도 결측 보완:** 누락 연도 데이터 확인 및 보완")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*본 보고서는 {datetime.now().strftime('%Y-%m-%d')}에 자동 생성되었습니다.*")

    # Write file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path


def generate_docx_report(results: dict, output_dir: Path) -> Optional[Path]:
    """Generate Word (DOCX) format report in Korean.

    Args:
        results: Full analysis results
        output_dir: Output directory

    Returns:
        Path to generated file or None if python-docx not available
    """
    if not DOCX_AVAILABLE:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "KOICA_사전분석_데이터현황보고서.docx"

    doc = Document()

    # Title
    title = doc.add_heading("KOICA 사전분석: 인도네시아 법령 데이터 현황 보고서", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Meta info
    doc.add_paragraph(f"분석일시: {results['meta']['analysis_date']}")
    doc.add_paragraph("")

    # 1. Overview
    doc.add_heading("1. 개요", level=1)

    doc.add_heading("1.1 분석 목적 및 배경", level=2)
    doc.add_paragraph(
        "본 보고서는 KOICA 인도네시아 법령정보 시스템 구축 사업의 사전분석을 위해 "
        "수집된 법령 데이터의 현황을 분석한 결과입니다."
    )

    doc.add_heading("1.2 데이터 소스 개요", level=2)

    # Data source table
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "데이터 소스"
    hdr_cells[1].text = "총 건수"
    hdr_cells[2].text = "설명"

    row_cells = table.add_row().cells
    row_cells[0].text = "peraturan.go.id (법제처)"
    row_cells[1].text = f"{results['summary']['peraturan_total']:,}건"
    row_cells[2].text = "인도네시아 법제처 공식 데이터"

    if results['bpk_comparison'].get('available'):
        row_cells = table.add_row().cells
        row_cells[0].text = "peraturan.bpk.go.id (BPK)"
        row_cells[1].text = f"{results['summary']['bpk_total']:,}건"
        row_cells[2].text = "인도네시아 감사원 JDIH 데이터"

    doc.add_paragraph("")

    doc.add_heading("1.3 핵심 지표 요약", level=2)
    for finding in results['summary'].get('key_findings', []):
        doc.add_paragraph(finding, style="List Bullet")

    # 2. Document Types
    doc.add_heading("2. 법령 유형별 분포", level=1)

    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "유형"
    hdr_cells[1].text = "건수"
    hdr_cells[2].text = "비율"
    hdr_cells[3].text = "유효 건수"
    hdr_cells[4].text = "유효율"

    for t in results['document_types']['types']:
        row_cells = table.add_row().cells
        row_cells[0].text = t['jenis_korean']
        row_cells[1].text = f"{t['total']:,}"
        row_cells[2].text = f"{t['percentage']}%"
        row_cells[3].text = f"{t['berlaku']:,}"
        row_cells[4].text = f"{t['berlaku_rate']}%"

    doc.add_paragraph(
        f"총 {results['document_types']['total_documents']:,}건, "
        f"{results['document_types']['type_count']}개 유형"
    )

    # 3. Year Distribution
    doc.add_heading("3. 연도별 분포", level=1)

    year_data = results['year_distribution']
    doc.add_paragraph(
        f"연도 범위: {year_data['year_range']['min']}년 ~ {year_data['year_range']['max']}년 "
        f"({year_data['span_years']}년간)"
    )

    doc.add_heading("10년 단위 분포", level=2)

    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "기간"
    hdr_cells[1].text = "건수"
    hdr_cells[2].text = "유효 건수"

    for d in year_data['decade_summary']:
        row_cells = table.add_row().cells
        row_cells[0].text = d['decade']
        row_cells[1].text = f"{d['total']:,}"
        row_cells[2].text = f"{d['berlaku']:,}"

    doc.add_heading("최다 법령 연도 (Top 5)", level=2)
    for i, y in enumerate(year_data['top_years'], 1):
        doc.add_paragraph(f"{i}. {y['year']}년: {y['total']:,}건", style="List Number")

    # 4. Agency Distribution
    doc.add_heading("4. 기관별 분포", level=1)

    agency_data = results['agency_distribution']
    doc.add_paragraph(f"고유 기관 수: {agency_data['unique_agencies']:,}개")
    doc.add_paragraph(f"기관 미기재: {agency_data['without_agency']:,}건 ({agency_data['without_agency_pct']}%)")

    doc.add_heading("Top 20 기관", level=2)

    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "순위"
    hdr_cells[1].text = "기관명"
    hdr_cells[2].text = "건수"
    hdr_cells[3].text = "비율"
    hdr_cells[4].text = "유효율"

    for i, a in enumerate(agency_data['top_20_agencies'][:10], 1):  # Top 10 for DOCX
        row_cells = table.add_row().cells
        name = a['pemrakarsa'][:30] + "..." if len(a['pemrakarsa']) > 30 else a['pemrakarsa']
        row_cells[0].text = str(i)
        row_cells[1].text = name
        row_cells[2].text = f"{a['total']:,}"
        row_cells[3].text = f"{a['percentage']}%"
        row_cells[4].text = f"{a['berlaku_rate']}%"

    # 5. Data Completeness
    doc.add_heading("5. 데이터 완성도", level=1)

    comp_data = results['data_completeness']
    doc.add_paragraph(f"전체 완성도: {comp_data['overall_completeness_pct']}%")
    doc.add_paragraph(f"완전 필드: {comp_data['complete_fields_count']}개")
    doc.add_paragraph(f"부분 필드: {comp_data['partial_fields_count']}개")
    doc.add_paragraph(f"희소 필드: {comp_data['sparse_fields_count']}개")

    doc.add_heading("필드별 완성률", level=2)

    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "필드명"
    hdr_cells[1].text = "완성 건수"
    hdr_cells[2].text = "완성률"
    hdr_cells[3].text = "고유값 수"

    for f in comp_data['fields'][:12]:  # Top 12 fields for DOCX
        row_cells = table.add_row().cells
        row_cells[0].text = f['field']
        row_cells[1].text = f"{f['complete']:,}"
        row_cells[2].text = f"{f['completeness_pct']}%"
        row_cells[3].text = f"{f['distinct_values']:,}"

    # 6. PDF Availability
    doc.add_heading("6. PDF 가용성", level=1)

    pdf_data = results['pdf_availability']
    doc.add_paragraph(f"PDF URL 보유: {pdf_data['with_url']:,}건 ({pdf_data['url_availability_pct']}%)")
    doc.add_paragraph(f"다운로드 완료: {pdf_data['downloaded']:,}건 ({pdf_data['overall_download_pct']}%)")
    doc.add_paragraph(f"다운로드 대기/실패: {pdf_data['failed_or_pending']:,}건")
    doc.add_paragraph(f"PDF URL 없음: {pdf_data['no_url']:,}건")

    if pdf_data['storage']['pdf_files_found'] > 0:
        doc.add_paragraph(
            f"저장소 현황: {pdf_data['storage']['pdf_files_found']:,}개 파일, "
            f"총 {pdf_data['storage']['total_size_gb']}GB "
            f"(평균 {pdf_data['storage']['avg_file_size_mb']}MB/파일)"
        )

    doc.add_heading("유형별 PDF 확보율", level=2)

    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "유형"
    hdr_cells[1].text = "총 건수"
    hdr_cells[2].text = "URL 보유"
    hdr_cells[3].text = "다운로드"
    hdr_cells[4].text = "확보율"

    for t in pdf_data['by_type']:
        row_cells = table.add_row().cells
        row_cells[0].text = t['jenis_korean']
        row_cells[1].text = f"{t['total']:,}"
        row_cells[2].text = f"{t['with_url']:,}"
        row_cells[3].text = f"{t['downloaded']:,}"
        row_cells[4].text = f"{t['download_rate']}%"

    # 7. Legal Status
    doc.add_heading("7. 현행성 상태", level=1)

    status_data = results['legal_status']
    doc.add_paragraph(f"현행 (Berlaku): {status_data['berlaku']:,}건 ({status_data['berlaku_pct']}%)")
    doc.add_paragraph(f"폐지/실효 (Tidak Berlaku): {status_data['tidak_berlaku']:,}건 ({status_data['tidak_berlaku_pct']}%)")
    doc.add_paragraph(f"미확인: {status_data['unknown']:,}건 ({status_data['unknown_pct']}%)")
    doc.add_paragraph(f"고유 상태값: {status_data['unique_status_values']:,}개")

    doc.add_heading("유형별 현행율", level=2)

    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "유형"
    hdr_cells[1].text = "총 건수"
    hdr_cells[2].text = "현행"
    hdr_cells[3].text = "폐지"
    hdr_cells[4].text = "현행율"

    for t in status_data['by_type']:
        row_cells = table.add_row().cells
        row_cells[0].text = t['jenis_korean']
        row_cells[1].text = f"{t['total']:,}"
        row_cells[2].text = f"{t['berlaku']:,}"
        row_cells[3].text = f"{t['tidak_berlaku']:,}"
        row_cells[4].text = f"{t['berlaku_rate']}%"

    # 8. BPK Comparison
    if results['bpk_comparison'].get('available'):
        doc.add_heading("8. BPK 데이터 비교", level=1)

        bpk_data = results['bpk_comparison']
        doc.add_paragraph(f"법제처 총 건수: {bpk_data['peraturan_total']:,}건")
        doc.add_paragraph(f"BPK 총 건수: {bpk_data['bpk_total']:,}건")
        doc.add_paragraph(f"비교 가능 유형: {bpk_data['comparable_types_count']}개")

        doc.add_heading("유형별 건수 비교", level=2)

        table = doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = "유형"
        hdr_cells[1].text = "법제처"
        hdr_cells[2].text = "BPK"
        hdr_cells[3].text = "차이"

        for t in bpk_data['type_comparison']:
            row_cells = table.add_row().cells
            diff_sign = "+" if t['difference'] > 0 else ""
            row_cells[0].text = t['type_korean']
            row_cells[1].text = f"{t['peraturan_count']:,}"
            row_cells[2].text = f"{t['bpk_count']:,}"
            row_cells[3].text = f"{diff_sign}{t['difference']:,}"

        doc.add_heading("현행율 비교", level=2)

        table = doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = "유형"
        hdr_cells[1].text = "법제처 현행율"
        hdr_cells[2].text = "BPK 현행율"
        hdr_cells[3].text = "차이"

        for t in bpk_data['status_comparison']:
            row_cells = table.add_row().cells
            row_cells[0].text = t['type_korean']
            row_cells[1].text = f"{t['peraturan_berlaku_rate']}%"
            row_cells[2].text = f"{t['bpk_berlaku_rate']}%"
            row_cells[3].text = f"{t['rate_difference']}%p"

        doc.add_paragraph("")
        doc.add_paragraph("비고:")
        for note in bpk_data.get('notes', []):
            doc.add_paragraph(note, style="List Bullet")

    # 9. Conclusion
    doc.add_heading("9. 결론 및 제언", level=1)

    doc.add_heading("9.1 KOICA 프로젝트 활용 방안", level=2)
    doc.add_paragraph("1. 법령 데이터 기반 구축: 법제처 데이터 35,000건 이상 활용 가능", style="List Number")
    doc.add_paragraph("2. PDF 문서 분석: 다운로드 완료된 PDF를 활용한 텍스트 추출 및 분석", style="List Number")
    doc.add_paragraph("3. 현행성 판단 시스템: 다양한 상태값 정규화를 통한 현행성 판단 로직 개발", style="List Number")
    doc.add_paragraph("4. BPK 데이터 보완: 법제처 데이터와 BPK 데이터 상호 보완", style="List Number")

    doc.add_heading("9.2 데이터 품질 개선 필요사항", level=2)
    doc.add_paragraph("1. 필드 완성도 개선: pengundangan 관련 필드 데이터 확보", style="List Number")
    doc.add_paragraph("2. 상태값 정규화: 2,809개 고유 상태값 → 표준 분류 체계 구축", style="List Number")
    doc.add_paragraph("3. PDF 다운로드 완료: 미확보 PDF 추가 다운로드", style="List Number")
    doc.add_paragraph("4. 연도 결측 보완: 누락 연도 데이터 확인 및 보완", style="List Number")

    # Footer
    doc.add_paragraph("")
    doc.add_paragraph(f"본 보고서는 {datetime.now().strftime('%Y-%m-%d')}에 자동 생성되었습니다.")

    # Save
    doc.save(str(output_path))

    return output_path


def main(
    peraturan_db: Path,
    bpk_db: Optional[Path],
    output_dir: Path,
    output_format: str = "all",
) -> dict:
    """Main function to run analysis and generate reports.

    Args:
        peraturan_db: Path to peraturan database
        bpk_db: Path to BPK database (optional)
        output_dir: Output directory
        output_format: Output format (json, md, docx, all)

    Returns:
        Dictionary with generated file paths
    """
    print(f"Starting KOICA data analysis...")
    print(f"  Peraturan DB: {peraturan_db}")
    print(f"  BPK DB: {bpk_db or 'Not provided'}")
    print(f"  Output: {output_dir}")
    print(f"  Format: {output_format}")
    print()

    # Run analysis
    analyzer = KoicaAnalyzer(peraturan_db, bpk_db)
    results = analyzer.run_full_analysis()

    generated_files = {"json": [], "markdown": None, "docx": None}

    # Generate reports based on format
    if output_format in ("json", "all"):
        json_dir = output_dir / "data" / "koica_analysis"
        generated_files["json"] = generate_json_reports(results, json_dir)
        print(f"Generated {len(generated_files['json'])} JSON files in {json_dir}")

    if output_format in ("md", "all"):
        generated_files["markdown"] = generate_markdown_report(results, output_dir)
        print(f"Generated Markdown report: {generated_files['markdown']}")

    if output_format in ("docx", "all"):
        docx_path = generate_docx_report(results, output_dir)
        if docx_path:
            generated_files["docx"] = docx_path
            print(f"Generated DOCX report: {docx_path}")
        else:
            print("DOCX generation skipped (python-docx not installed)")

    print()
    print("Analysis complete!")
    print(f"Key findings:")
    for finding in results['summary'].get('key_findings', [])[:5]:
        print(f"  - {finding}")

    return generated_files


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="KOICA Data Analysis Report Generator")
    parser.add_argument(
        "--peraturan-db",
        type=Path,
        default=Path("peraturan/data/peraturan.db"),
        help="Path to peraturan.go.id database",
    )
    parser.add_argument(
        "--bpk-db",
        type=Path,
        default=Path("bpk/data/peraturan_bpk.db"),
        help="Path to BPK database",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=Path("docs/exports/koica"),
        help="Output directory",
    )
    parser.add_argument(
        "-f", "--format",
        type=str,
        choices=["json", "md", "docx", "all"],
        default="all",
        help="Output format",
    )

    args = parser.parse_args()

    # Check if BPK DB exists
    bpk_db = args.bpk_db if args.bpk_db.exists() else None

    main(
        peraturan_db=args.peraturan_db,
        bpk_db=bpk_db,
        output_dir=args.output_dir,
        output_format=args.format,
    )
