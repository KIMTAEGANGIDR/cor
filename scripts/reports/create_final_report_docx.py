#!/usr/bin/env python3
"""Generate Final Report as Word Document with proper tables."""

from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def set_cell_shading(cell, color):
    """Set cell background color."""
    shading = OxmlElement('w:shd')
    shading.set(qn('w:fill'), color)
    cell._tc.get_or_add_tcPr().append(shading)

def add_table(doc, headers, rows, header_color="4472C4"):
    """Add a formatted table to the document."""
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Header row
    header_cells = table.rows[0].cells
    for i, header in enumerate(headers):
        header_cells[i].text = header
        header_cells[i].paragraphs[0].runs[0].bold = True
        header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
        set_cell_shading(header_cells[i], header_color)

    # Data rows
    for row_idx, row_data in enumerate(rows):
        row_cells = table.rows[row_idx + 1].cells
        for col_idx, cell_text in enumerate(row_data):
            row_cells[col_idx].text = str(cell_text)

    return table

def create_report():
    doc = Document()

    # Title
    title = doc.add_heading('인도네시아 법령 현행성 판단 심층 분석 최종 보고서', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Metadata
    doc.add_paragraph('분석 일시: 2025-12-26')
    doc.add_paragraph('분석 대상: 법제처 35,316건 + BPK 34,549건')
    doc.add_paragraph()

    # Executive Summary
    doc.add_heading('Executive Summary', level=1)
    doc.add_heading('핵심 결론', level=2)

    add_table(doc,
        ['항목', '결과'],
        [
            ['자동화 가능 비율', '90% (31,800건)'],
            ['인간 검토 필요', '10% (3,500건)'],
            ['예상 효율성 향상', '기존 대비 10배']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('업무 분담 요약', level=2)
    add_table(doc,
        ['구분', '비율', '처리 방법'],
        [
            ['완전 자동', '75%', '시스템 자동 처리'],
            ['반자동 (확인)', '15%', '시스템 처리 + 샘플 확인'],
            ['인간 검토', '10%', '법률 전문가 검토']
        ]
    )
    doc.add_paragraph()

    # Phase 1
    doc.add_heading('Phase 1: 메타데이터 분석 결과', level=1)
    doc.add_heading('데이터 품질 비교', level=2)

    add_table(doc,
        ['지표', '법제처', 'BPK'],
        [
            ['전체 점수', '81.0', '99.3 ✓'],
            ['status 표준화', '0% (2,809개 값)', '100% (2개 값) ✓'],
            ['PDF 가용성', '92.1%', '99.4% ✓'],
            ['시행일 정보', '83.4%', '93.9% ✓']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('DB 간 교차 검증', level=2)
    add_table(doc,
        ['구분', '건수', '비율'],
        [
            ['일치', '7,802건', '81.1%'],
            ['불일치 (우선 검토)', '1,822건', '18.9%']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('자동 판단 가능 범위', level=2)
    add_table(doc,
        ['카테고리', '건수', '비율', '처리 방법'],
        [
            ['A. 확정 유효', '31,509', '89.2%', '시스템 자동'],
            ['B. 확정 무효', '3,681', '10.4%', '시스템 자동'],
            ['C. 교차검증 필요', '1,822', '5.2%', '반자동'],
            ['D. 조건부 (인간)', '3,780', '10.7%', '변호사 검토'],
            ['E. 데이터 부족', '126', '0.4%', '추가 조사']
        ]
    )
    doc.add_paragraph()

    # Phase 2
    doc.add_heading('Phase 2: 법조문 분석 결과', level=1)
    doc.add_heading('1. 폐지 표현 (자동 추출 가능)', level=2)

    add_table(doc,
        ['패턴', '건수', '의미', '자동화'],
        [
            ['dicabut', '11,660', '폐지됨', '✅ 가능'],
            ['dinyatakan tidak berlaku', '9,308', '효력 없음 선언', '✅ 가능'],
            ['mencabut', '1,401', '폐지함', '✅ 가능'],
            ['sebagian dicabut', '2,626', '부분 폐지', '⚠️ 부분 가능'],
            ['dicabut sepanjang', '828', '조건부 폐지', '❌ 인간 검토']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('2. 조건부 유효 표현 (인간 검토 필요)', level=2)
    add_table(doc,
        ['패턴', '건수', '복잡도'],
        [
            ['sepanjang tidak bertentangan', '2,287', 'HIGH'],
            ['tetap berlaku sepanjang', '2,683', 'HIGH'],
            ['다중 조건', '5,545', 'VERY HIGH'],
            ['tetap berlaku', '4,779', 'LOW'],
            ['sampai dengan', '7,422', 'MEDIUM']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('3. 개정 표현', level=2)
    add_table(doc,
        ['패턴', '건수', '의미'],
        [
            ['perubahan', '16,172', '개정'],
            ['diubah', '9,584', '개정됨'],
            ['sebagaimana telah diubah', '2,785', '이미 개정된'],
            ['Perubahan Atas (제목)', '3,278', '개정법령'],
            ['Perubahan Kedua Atas', '833', '2차 개정'],
            ['Perubahan Ketiga Atas', '305', '3차 개정']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('4. 참조 관계', level=2)
    add_table(doc,
        ['참조 유형', '건수'],
        [
            ['Peraturan Menteri', '13,433'],
            ['Lembaran Negara', '7,277'],
            ['Undang-Undang', '4,764'],
            ['Peraturan Pemerintah', '2,954'],
            ['Peraturan Presiden', '1,268']
        ]
    )
    doc.add_paragraph()

    # Phase 3
    doc.add_heading('Phase 3: 그래프 DB 스키마', level=1)
    doc.add_heading('노드 구조', level=2)

    add_table(doc,
        ['노드', '설명', '주요 속성'],
        [
            ['Peraturan', '법령', 'slug, jenis, nomor, tahun, status_bpk, needs_review'],
            ['Pemrakarsa', '발의 기관', 'name, type'],
            ['Subjek', '주제/분야', 'name, category']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('관계(엣지) 구조', level=2)
    add_table(doc,
        ['관계', '방향', '설명', '예상 건수'],
        [
            ['MENCABUT', '신법 → 구법', '폐지 관계', '3,681'],
            ['MENGUBAH', '개정법 → 원법', '개정 관계', '4,000'],
            ['REFERENCES', '하위법 → 상위법', '참조 관계', '25,000'],
            ['CONDITIONALLY_VALID', '신법 → 구법', '조건부 유효', '2,500'],
            ['TEMPORARILY_VALID', '신법 → 구법', '임시 유효 (경과규정)', '1,000']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('예상 그래프 규모', level=2)
    add_table(doc,
        ['항목', '수량'],
        [
            ['노드 (Peraturan)', '35,316'],
            ['총 엣지 (관계)', '~36,000'],
            ['MENCABUT 관계', '3,681'],
            ['MENGUBAH 관계', '4,000'],
            ['REFERENCES 관계', '25,000']
        ]
    )
    doc.add_paragraph()

    # Phase 4
    doc.add_heading('Phase 4: 검증 및 업무 분담', level=1)
    doc.add_heading('샘플링 전략', level=2)

    add_table(doc,
        ['방법', '샘플 수', '목적'],
        [
            ['계층적 무작위', '400', '전체 대표성'],
            ['경계 케이스', '200', '예외 처리 검증'],
            ['총 검증 샘플', '600', '']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('품질 목표', level=2)
    add_table(doc,
        ['지표', '목표', '측정 방법'],
        [
            ['Accuracy', '≥ 98%', '샘플 검증'],
            ['Recall (검토 필요 탐지)', '≥ 99%', '패턴 매칭'],
            ['처리 속도', '< 1초/건', '자동 분류']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('업무 분담 상세', level=2)
    add_table(doc,
        ['구분', '비율', '건수', '작업 내용', '인간 개입'],
        [
            ['A. 완전 자동화', '75%', '~26,500', 'status 일치 판정, 폐지관계 추출, 그래프 구축', '0%'],
            ['B. 반자동 확인', '15%', '~5,300', '경과규정 만료 체크, 개정 체인 결정, 불일치 우선순위', '10%'],
            ['C. 인간 검토 필수', '10%', '~3,500', '조건부 유효 해석, 부분 폐지 범위, 법률 충돌', '100%']
        ]
    )
    doc.add_paragraph()

    doc.add_heading('인간 검토 우선순위', level=2)
    add_table(doc,
        ['우선순위', '케이스', '건수', '예상 시간'],
        [
            ['1', 'sepanjang tidak bertentangan (상충 조건)', '2,287', '380시간'],
            ['2', 'sebagian dicabut (부분 폐지)', '800', '130시간'],
            ['3', '복합 조건', '400', '70시간'],
            ['합계', '', '3,487', '580시간']
        ]
    )
    doc.add_paragraph()

    # Conclusion
    doc.add_heading('결론', level=1)

    p = doc.add_paragraph()
    p.add_run('90%의 법령 현행성 판단을 시스템이 자동으로 처리').bold = True
    p.add_run('할 수 있으며, 법률 전문가는 ')
    p.add_run('10%의 복잡한 케이스(~3,500건)에만 집중').bold = True
    p.add_run('하면 됩니다.')

    doc.add_paragraph()
    doc.add_paragraph('이를 통해:')
    doc.add_paragraph('• 업무 효율 10배 향상', style='List Bullet')
    doc.add_paragraph('• 인적 오류 최소화', style='List Bullet')
    doc.add_paragraph('• 실시간 현행성 정보 제공 가능', style='List Bullet')

    doc.add_paragraph()
    doc.add_paragraph()

    # Footer
    footer = doc.add_paragraph('Report generated: 2025-12-26')
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer2 = doc.add_paragraph('Analysis files: /docs/analysis/')
    footer2.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # Save
    output_path = 'docs/인도네시아_법령_현행성_심층분석_최종보고서.docx'
    doc.save(output_path)
    print(f"Saved: {output_path}")
    return output_path

if __name__ == '__main__':
    create_report()
