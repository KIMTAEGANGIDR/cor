#!/usr/bin/env python3
"""Generate Comprehensive Detailed Report as Word Document."""

from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def set_cell_shading(cell, color):
    """Set cell background color."""
    shading = OxmlElement('w:shd')
    shading.set(qn('w:fill'), color)
    cell._tc.get_or_add_tcPr().append(shading)

def add_table(doc, headers, rows, header_color="4472C4"):
    """Add a formatted table."""
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Table Grid'

    header_cells = table.rows[0].cells
    for i, header in enumerate(headers):
        header_cells[i].text = header
        for run in header_cells[i].paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor(255, 255, 255)
        set_cell_shading(header_cells[i], header_color)

    for row_idx, row_data in enumerate(rows):
        row_cells = table.rows[row_idx + 1].cells
        for col_idx, cell_text in enumerate(row_data):
            row_cells[col_idx].text = str(cell_text)

    return table

def add_code_block(doc, code):
    """Add a code block with monospace font."""
    p = doc.add_paragraph()
    run = p.add_run(code)
    run.font.name = 'Courier New'
    run.font.size = Pt(9)
    p.paragraph_format.left_indent = Cm(1)

def create_report():
    doc = Document()

    # =====================================================
    # TITLE PAGE
    # =====================================================
    doc.add_paragraph()
    doc.add_paragraph()
    title = doc.add_heading('인도네시아 법령 현행성 판단', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = doc.add_heading('심층 분석 종합 보고서', 0)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph()
    doc.add_paragraph()

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run('분석 일시: 2025-12-26\n').bold = True
    meta.add_run('분석 대상: 법제처 35,316건 + BPK 34,549건\n')
    meta.add_run('총 분석 관계: ~70,000건')

    doc.add_page_break()

    # =====================================================
    # 목차
    # =====================================================
    doc.add_heading('목차', level=1)
    toc = [
        '1. 개요 및 핵심 결론',
        '2. 데이터 소스 상세 분석',
        '   2.1 법제처 데이터베이스 구조',
        '   2.2 BPK 데이터베이스 구조',
        '   2.3 두 데이터베이스 비교',
        '3. 메타데이터 필드별 상세 분석',
        '   3.1 법령 식별 필드',
        '   3.2 상태(status) 필드 심층 분석',
        '   3.3 시간 관련 필드',
        '   3.4 관계 정보 필드',
        '4. 법조문 패턴 분석 및 추출 방법',
        '   4.1 폐지(Revocation) 패턴',
        '   4.2 개정(Amendment) 패턴',
        '   4.3 조건부 유효 패턴',
        '   4.4 경과 규정 패턴',
        '5. 그래프 데이터베이스 설계',
        '   5.1 노드 설계',
        '   5.2 엣지(관계) 설계',
        '   5.3 추출 알고리즘',
        '6. 인간 개입 필요 케이스 상세',
        '   6.1 왜 인간 판단이 필요한가',
        '   6.2 케이스별 상세 분석',
        '   6.3 실제 사례 분석',
        '7. 업무 분담 및 워크플로우',
        '8. 결론 및 권장사항'
    ]
    for item in toc:
        doc.add_paragraph(item)

    doc.add_page_break()

    # =====================================================
    # 1. 개요 및 핵심 결론
    # =====================================================
    doc.add_heading('1. 개요 및 핵심 결론', level=1)

    doc.add_heading('1.1 프로젝트 목적', level=2)
    doc.add_paragraph(
        '본 분석의 목적은 인도네시아 법령의 현행성(berlaku/tidak berlaku) 판단을 '
        '자동화할 수 있는 범위를 정의하고, 시스템과 인간의 업무를 명확히 분담하는 것입니다.'
    )

    doc.add_heading('1.2 핵심 결론', level=2)
    add_table(doc,
        ['구분', '비율', '건수', '처리 방법'],
        [
            ['완전 자동화 가능', '75%', '26,500건', '시스템이 100% 처리'],
            ['반자동 (시스템+확인)', '15%', '5,300건', '시스템 처리 후 샘플 검증'],
            ['인간 검토 필수', '10%', '3,500건', '법률 전문가 판단 필요'],
        ]
    )
    doc.add_paragraph()

    p = doc.add_paragraph()
    p.add_run('결론: ').bold = True
    p.add_run('전체 법령의 90%는 시스템이 자동으로 현행성을 판단할 수 있으며, ')
    p.add_run('법률 전문가는 10%의 복잡한 케이스에만 집중').bold = True
    p.add_run('하면 됩니다.')

    doc.add_page_break()

    # =====================================================
    # 2. 데이터 소스 상세 분석
    # =====================================================
    doc.add_heading('2. 데이터 소스 상세 분석', level=1)

    doc.add_heading('2.1 법제처 데이터베이스 구조', level=2)
    doc.add_paragraph(
        '법제처(peraturan.go.id)는 인도네시아 정부의 공식 법령 데이터베이스입니다. '
        '중앙정부 법령을 중심으로 수집되며, 총 35,316건의 법령이 저장되어 있습니다.'
    )

    doc.add_heading('테이블 스키마: peraturan', level=3)
    add_table(doc,
        ['필드명', '타입', '설명', '완전성', '용도'],
        [
            ['slug', 'TEXT (PK)', '고유 식별자 (URL slug)', '100%', '법령 식별'],
            ['jenis', 'TEXT', '법령 유형 (UU, PP, Perpres 등)', '100%', '계층 분류'],
            ['nomor', 'TEXT', '법령 번호', '100%', '법령 식별'],
            ['tahun', 'INTEGER', '제정 연도', '100%', '시간 분석'],
            ['tentang', 'TEXT', '법령 제목/내용', '100%', '내용 파악'],
            ['pemrakarsa', 'TEXT', '발의 기관', '27.4%', '기관별 분류'],
            ['status', 'TEXT', '현행 상태 (복잡한 텍스트)', '99.6%', '★ 현행성 판단'],
            ['tanggal_penetapan', 'TEXT', '제정일 (YYYY-MM-DD)', '83.4%', '시간 계산'],
            ['pdf_url', 'TEXT', 'PDF 다운로드 URL', '92.1%', '원문 확인'],
            ['extracted_text', 'TEXT', 'PDF에서 추출한 텍스트', '90.6%', '★ 관계 추출'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('법령 유형(jenis) 분포', level=3)
    add_table(doc,
        ['법령 유형', '인도네시아어', '건수', '비율'],
        [
            ['장관령', 'PERATURAN MENTERI', '19,211', '54.4%'],
            ['기관령', 'PERATURAN BADAN/LEMBAGA', '6,406', '18.1%'],
            ['정부령', 'PERATURAN PEMERINTAH', '4,969', '14.1%'],
            ['대통령령', 'PERATURAN PRESIDEN', '2,614', '7.4%'],
            ['법률', 'UNDANG-UNDANG', '1,905', '5.4%'],
            ['긴급법률', 'PERPU', '202', '0.6%'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('2.2 BPK 데이터베이스 구조', level=2)
    doc.add_paragraph(
        'BPK(peraturan.bpk.go.id)는 인도네시아 감사원이 운영하는 법령 데이터베이스입니다. '
        '법제처보다 더 넓은 범위(지방법령 포함)를 다루며, 총 34,549건이 저장되어 있습니다.'
    )

    doc.add_heading('테이블 스키마: peraturan', level=3)
    add_table(doc,
        ['필드명', '타입', '설명', '완전성', '용도'],
        [
            ['id', 'TEXT (PK)', '고유 식별자', '100%', '법령 식별'],
            ['judul', 'TEXT', '법령 제목', '100%', '내용 파악'],
            ['bentuk', 'TEXT', '법령 형태 (126개 유형)', '100%', '상세 분류'],
            ['status', 'TEXT', 'Berlaku / Tidak Berlaku', '100%', '★ 현행성 판단'],
            ['tanggal_penetapan', 'TEXT', '제정일', '99.0%', '시간 분석'],
            ['tanggal_berlaku', 'TEXT', '시행일', '93.9%', '★ 시행 시점'],
            ['tanggal_pengundangan', 'TEXT', '공포일', '72.5%', '공포 시점'],
            ['pdf_url', 'TEXT', 'PDF URL', '99.4%', '원문 확인'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('2.3 두 데이터베이스 비교', level=2)

    doc.add_heading('품질 비교', level=3)
    add_table(doc,
        ['품질 지표', '법제처', 'BPK', '승자'],
        [
            ['전체 품질 점수', '81.0', '99.3', 'BPK ✓'],
            ['status 표준화', '2,809개 고유값', '2개 값 (Berlaku/Tidak)', 'BPK ✓'],
            ['PDF 가용성', '92.1%', '99.4%', 'BPK ✓'],
            ['시행일 정보', '없음', '93.9% (tanggal_berlaku)', 'BPK ✓'],
            ['법조문 텍스트', '90.6% (extracted_text)', '없음', '법제처 ✓'],
            ['폐지 법령 참조', 'status에 포함', '없음', '법제처 ✓'],
        ]
    )
    doc.add_paragraph()

    p = doc.add_paragraph()
    p.add_run('핵심 차이점: ').bold = True
    p.add_run('BPK는 현행성 판단이 명확(2개 값)하지만, 법제처는 폐지된 법령이 ')
    p.add_run('어떤 법령에 의해 폐지되었는지').italic = True
    p.add_run(' 정보를 status 필드에 포함하고 있어 관계 추출에 필수적입니다.')

    doc.add_heading('교차 검증 결과', level=3)
    doc.add_paragraph('중앙법령 9,624건을 양쪽 DB에서 매칭하여 비교한 결과:')
    add_table(doc,
        ['구분', '건수', '비율', '의미'],
        [
            ['status 일치', '7,802건', '81.1%', '자동 확정 가능'],
            ['status 불일치', '1,822건', '18.9%', '검증 필요'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('불일치 사례', level=3)
    add_table(doc,
        ['법령', '법제처 status', 'BPK status', '판단'],
        [
            ['PP No.14/2024', 'Berlaku', 'Tidak Berlaku', '검증 필요'],
            ['PP No.15/2023', 'Berlaku', 'Tidak Berlaku', '검증 필요'],
            ['PP No.1/2020', 'Berlaku', 'Tidak Berlaku', '검증 필요'],
        ]
    )
    doc.add_paragraph()

    doc.add_page_break()

    # =====================================================
    # 3. 메타데이터 필드별 상세 분석
    # =====================================================
    doc.add_heading('3. 메타데이터 필드별 상세 분석', level=1)

    doc.add_heading('3.1 법령 식별 필드', level=2)
    doc.add_paragraph(
        '법령을 고유하게 식별하기 위해 jenis(유형) + nomor(번호) + tahun(연도) 조합을 사용합니다.'
    )

    doc.add_heading('식별자 구성 예시', level=3)
    add_table(doc,
        ['법령', 'jenis', 'nomor', 'tahun', '고유 식별자'],
        [
            ['건강법', 'UNDANG-UNDANG', '17', '2023', 'UU-17-2023'],
            ['공무원법', 'UNDANG-UNDANG', '20', '2023', 'UU-20-2023'],
            ['재무부령 3호', 'PERATURAN MENTERI', '3', '2025', 'PERMEN-3-2025'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('3.2 상태(status) 필드 심층 분석', level=2)

    p = doc.add_paragraph()
    p.add_run('이것이 현행성 판단의 핵심 필드입니다. ').bold = True
    p.add_run('그러나 두 데이터베이스의 status 필드는 완전히 다른 형태를 가집니다.')

    doc.add_heading('BPK status (단순, 2개 값)', level=3)
    add_table(doc,
        ['값', '의미', '건수', '비율'],
        [
            ['Berlaku', '현행 (유효)', '24,958', '72.2%'],
            ['Tidak Berlaku', '폐지 (무효)', '9,591', '27.8%'],
        ]
    )
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run('장점: ').bold = True
    p.add_run('명확한 이진 분류로 자동 판단에 최적')
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run('단점: ').bold = True
    p.add_run('왜 폐지되었는지, 어떤 법령에 의해 대체되었는지 정보 없음')

    doc.add_heading('법제처 status (복잡, 2,809개 고유값)', level=3)
    doc.add_paragraph('법제처의 status 필드는 단순 상태가 아니라, 폐지 사유와 대체 법령 정보를 포함합니다.')

    doc.add_heading('status 필드 실제 예시', level=4)
    add_table(doc,
        ['유형', 'status 값 예시'],
        [
            ['단순 유효', 'Berlaku'],
            ['폐지 + 대체법령', 'Tidak BerlakuDicabut Oleh :Peraturan Menteri Keuangan Nomor 3 Tahun 2024 Tentang Tarif Layanan...'],
            ['폐지 + 법률', 'Tidak BerlakuDicabut Oleh :Undang-Undang Nomor 17 Tahun 2023 Tentang Kesehatan'],
        ]
    )
    doc.add_paragraph()

    p = doc.add_paragraph()
    p.add_run('핵심 발견: ').bold = True
    p.add_run('법제처 status에서 "Dicabut Oleh :" 뒤의 텍스트를 파싱하면 ')
    p.add_run('폐지 관계를 추출').bold = True
    p.add_run('할 수 있습니다.')

    doc.add_heading('status에서 추출 가능한 정보', level=4)
    add_table(doc,
        ['정보', '추출 방법', '활용'],
        [
            ['현행 여부', '"Berlaku" 또는 "Tidak Berlaku" 포함 여부', '1차 분류'],
            ['폐지 법령', '"Dicabut Oleh :" 뒤의 법령명', '그래프 엣지 생성'],
            ['폐지 법령 유형', 'Undang-Undang, Peraturan Pemerintah 등', '노드 유형 결정'],
            ['폐지 법령 번호', 'Nomor X Tahun YYYY 패턴', '노드 식별'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('3.3 시간 관련 필드', level=2)
    add_table(doc,
        ['필드', 'DB', '형식', '완전성', '용도'],
        [
            ['tanggal_penetapan', '법제처', 'YYYY-MM-DD', '83.4%', '제정일'],
            ['tanggal_penetapan', 'BPK', '다양', '99.0%', '제정일'],
            ['tanggal_berlaku', 'BPK', '다양', '93.9%', '★ 시행일 (매우 유용)'],
            ['tanggal_pengundangan', 'BPK', '다양', '72.5%', '공포일'],
        ]
    )
    doc.add_paragraph()

    p = doc.add_paragraph()
    p.add_run('tanggal_berlaku(시행일)의 중요성: ').bold = True
    p.add_run('경과규정이 "시행일로부터 1년간 유효"라고 명시할 때, 시행일 없이는 만료 시점을 계산할 수 없습니다.')

    doc.add_heading('3.4 관계 정보 필드', level=2)
    doc.add_paragraph('법령 간 관계 정보는 주로 두 곳에서 추출됩니다:')

    add_table(doc,
        ['소스', '필드', '포함 정보', '추출 건수'],
        [
            ['법제처', 'status', '"Dicabut Oleh :" 폐지 관계', '3,681건'],
            ['법제처', 'extracted_text', '폐지, 개정, 참조 관계', '~25,000건'],
            ['법제처', 'tentang', '"Perubahan Atas" 개정 관계', '3,278건'],
        ]
    )
    doc.add_paragraph()

    doc.add_page_break()

    # =====================================================
    # 4. 법조문 패턴 분석 및 추출 방법
    # =====================================================
    doc.add_heading('4. 법조문 패턴 분석 및 추출 방법', level=1)

    doc.add_paragraph(
        '법령 본문(extracted_text)에서 현행성 판단에 필요한 정보를 추출하기 위해 '
        '다양한 법적 표현 패턴을 분석했습니다.'
    )

    doc.add_heading('4.1 폐지(Revocation) 패턴', level=2)

    doc.add_heading('4.1.1 직접 폐지 표현', level=3)
    add_table(doc,
        ['인도네시아어', '의미', '출현 건수', '추출 가능'],
        [
            ['dicabut', '폐지됨', '11,660', '✅'],
            ['mencabut', '폐지함', '1,401', '✅'],
            ['dinyatakan tidak berlaku', '효력 없음 선언', '9,308', '✅'],
            ['tidak berlaku lagi', '더 이상 유효하지 않음', '592', '✅'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('추출 정규식', level=4)
    add_code_block(doc, '''# 폐지 관계 추출 정규식
REVOCATION_PATTERN = r"(?:dicabut|mencabut|dinyatakan tidak berlaku).*?"
                     r"(Undang-Undang|Peraturan\\s+(?:Pemerintah|Presiden|Menteri))\\s+"
                     r"Nomor\\s+(\\d+)\\s+Tahun\\s+(\\d{4})"

# 매칭 예시:
# "dicabut oleh Undang-Undang Nomor 17 Tahun 2023"
# → 그룹1: "Undang-Undang", 그룹2: "17", 그룹3: "2023"''')

    doc.add_heading('4.1.2 부분 폐지 (복잡 케이스)', level=3)
    add_table(doc,
        ['표현', '의미', '건수', '처리'],
        [
            ['sebagian dicabut', '일부 폐지됨', '2,626', '⚠️ 범위 확인 필요'],
            ['dicabut sebagian', '일부를 폐지함', '436', '⚠️ 범위 확인 필요'],
        ]
    )
    doc.add_paragraph()

    p = doc.add_paragraph()
    p.add_run('왜 인간 검토가 필요한가: ').bold = True
    p.add_run('부분 폐지는 "어떤 조항이 폐지되었는가"를 파악해야 하며, '
              '이는 법조문 구조 분석이 필요합니다.')

    doc.add_heading('4.2 개정(Amendment) 패턴', level=2)

    doc.add_heading('4.2.1 개정 관계 표현', level=3)
    add_table(doc,
        ['표현', '의미', '건수', '용도'],
        [
            ['mengubah', '개정함', '2,985', '개정법 → 원법 관계'],
            ['diubah', '개정됨', '9,584', '원법 → 개정법 관계'],
            ['Perubahan Atas (제목)', '~에 대한 개정', '3,278', '개정법 식별'],
            ['sebagaimana telah diubah', '이미 개정된 바와 같이', '2,785', '개정 이력 참조'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('개정 차수 표현', level=4)
    add_table(doc,
        ['표현', '의미', '건수'],
        [
            ['Perubahan Atas', '1차 개정', '3,278'],
            ['Perubahan Kedua Atas', '2차 개정', '833'],
            ['Perubahan Ketiga Atas', '3차 개정', '305'],
            ['Perubahan Keempat Atas', '4차 개정', '~50'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('추출 정규식', level=4)
    add_code_block(doc, '''# 개정 관계 추출 (제목에서)
AMENDMENT_TITLE = r"Perubahan\\s+(Kedua|Ketiga|Keempat)?\\s*Atas\\s+"
                  r"(Undang-Undang|Peraturan.*?)\\s+Nomor\\s+(\\d+)\\s+Tahun\\s+(\\d{4})"

# 예시: "Perubahan Kedua Atas Undang-Undang Nomor 11 Tahun 2008"
# → 그룹1: "Kedua" (2차), 그룹2: "Undang-Undang", 그룹3: "11", 그룹4: "2008"''')

    doc.add_heading('4.3 조건부 유효 패턴 (★ 인간 검토 필수)', level=2)

    p = doc.add_paragraph()
    p.add_run('이 패턴들은 자동 판단이 불가능하며, 법률 전문가의 해석이 필요합니다.').bold = True

    doc.add_heading('4.3.1 핵심 조건부 표현', level=3)
    add_table(doc,
        ['인도네시아어', '한국어 의미', '건수', '인간 판단 필요 이유'],
        [
            ['sepanjang tidak bertentangan', '상충하지 않는 한', '2,287', '무엇과 상충하는지 판단 필요'],
            ['tetap berlaku sepanjang', '~하는 한 계속 유효', '2,683', '조건 충족 여부 판단 필요'],
            ['dinyatakan masih berlaku', '여전히 유효하다고 선언', '1,668', '맥락 파악 필요'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('4.3.2 실제 사례 분석', level=3)

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run('사례 1: UU 21/2023 (수도이전법 개정)').bold = True

    doc.add_paragraph(
        '원문: "...dinyatakan masih tetap berlaku sepanjang tidak bertentangan dengan '
        'Undang-Undang ini dan wajib disesuaikan paling lama 2 (dua) bulan..."'
    )
    doc.add_paragraph(
        '번역: "본 법률과 상충하지 않는 한 여전히 유효하며, 2개월 이내에 조정해야 함"'
    )

    p = doc.add_paragraph()
    p.add_run('인간 판단 필요 사항:').bold = True
    doc.add_paragraph('• 구법의 어떤 조항이 신법과 "상충"하는가?', style='List Bullet')
    doc.add_paragraph('• 2개월 조정 기간이 지난 현재, 조정이 완료되었는가?', style='List Bullet')
    doc.add_paragraph('• 조정되지 않은 조항은 자동 폐지인가?', style='List Bullet')

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run('사례 2: UU 4/2023 (금융법)').bold = True

    doc.add_paragraph(
        '원문: "...melakukan penawaran umum efek melalui Pasar Modal sepanjang tidak '
        'bertentangan dengan Prinsip Syariah..."'
    )
    doc.add_paragraph(
        '번역: "샤리아 원칙과 상충하지 않는 한 자본시장을 통해 공모 가능"'
    )

    p = doc.add_paragraph()
    p.add_run('인간 판단 필요 사항:').bold = True
    doc.add_paragraph('• "샤리아 원칙"의 구체적 내용은 무엇인가?', style='List Bullet')
    doc.add_paragraph('• 특정 금융 상품이 샤리아와 상충하는지 어떻게 판단하는가?', style='List Bullet')

    doc.add_heading('4.4 경과 규정 패턴', level=2)

    doc.add_heading('4.4.1 KETENTUAN PERALIHAN (경과규정 장)', level=3)
    doc.add_paragraph(
        '대부분의 법령은 마지막 부분에 "KETENTUAN PERALIHAN" (경과규정) 장을 두어 '
        '신법 시행 전 사안의 처리를 규정합니다.'
    )

    add_table(doc,
        ['패턴', '의미', '건수', '처리'],
        [
            ['KETENTUAN PERALIHAN', '경과규정 장', '6,799', '시간 계산 필요'],
            ['dalam jangka waktu', '기간 내에', '4,948', '기간 추출'],
            ['paling lama', '최대', '8,043', '기간 추출'],
            ['paling lambat', '늦어도', '7,219', '기한 추출'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('4.4.2 경과 기간 표현', level=3)
    add_table(doc,
        ['표현', '기간', '건수'],
        [
            ['1 (satu) tahun / satu tahun', '1년', '7,050'],
            ['2 (dua) tahun / dua tahun', '2년', '3,769'],
            ['6 (enam) bulan / enam bulan', '6개월', '4,448'],
            ['30 (tiga puluh) hari', '30일', '6,576'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('추출 정규식', level=4)
    add_code_block(doc, '''# 경과 기간 추출
TRANSITION_PERIOD = r"(?:dalam jangka waktu|paling lama|paling lambat)\\s+"
                    r"(\\d+)\\s*\\(([^)]+)\\)\\s*(hari|bulan|tahun)"

# 예시: "paling lama 2 (dua) tahun"
# → 숫자: 2, 단위: tahun (년)
# → 시행일 + 2년 = 만료일''')

    doc.add_page_break()

    # =====================================================
    # 5. 그래프 데이터베이스 설계
    # =====================================================
    doc.add_heading('5. 그래프 데이터베이스 설계', level=1)

    doc.add_paragraph(
        '추출된 관계 정보를 그래프 데이터베이스에 저장하여, 법령 간 관계를 시각화하고 '
        '쿼리할 수 있도록 합니다.'
    )

    doc.add_heading('5.1 노드 설계', level=2)

    doc.add_heading('Peraturan (법령) 노드', level=3)
    add_table(doc,
        ['속성', '타입', '설명', '필수'],
        [
            ['slug', 'string', '고유 식별자', '✓'],
            ['jenis', 'enum', 'UU, PP, Perpres, Permen 등', '✓'],
            ['nomor', 'string', '법령 번호', '✓'],
            ['tahun', 'integer', '제정 연도', '✓'],
            ['tentang', 'string', '법령 제목', '✓'],
            ['status_bpk', 'enum', 'Berlaku / Tidak Berlaku', ''],
            ['status_leg', 'string', '법제처 원본 status', ''],
            ['tanggal_berlaku', 'date', '시행일', ''],
            ['tanggal_berakhir', 'date', '만료일 (경과규정)', ''],
            ['has_conditional', 'boolean', '조건부 유효 여부', ''],
            ['needs_review', 'boolean', '인간 검토 필요 여부', ''],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('5.2 엣지(관계) 설계', level=2)

    add_table(doc,
        ['관계', '방향', '의미', '추출 소스', '예상 건수'],
        [
            ['MENCABUT', '신법 → 구법', 'A가 B를 폐지함', 'status "Dicabut Oleh"', '3,681'],
            ['DICABUT_OLEH', '구법 → 신법', 'B가 A에 의해 폐지됨', 'MENCABUT의 역방향', '3,681'],
            ['MENGUBAH', '개정법 → 원법', 'A가 B를 개정함', 'tentang "Perubahan Atas"', '4,000'],
            ['DIUBAH_OLEH', '원법 → 개정법', 'B가 A에 의해 개정됨', 'MENGUBAH의 역방향', '4,000'],
            ['REFERENCES', '하위법 → 상위법', 'A가 B를 참조함', 'extracted_text', '25,000'],
            ['CONDITIONALLY_VALID', '신법 → 구법', 'B가 A 조건 하에 유효', 'sepanjang 패턴', '2,500'],
            ['TEMPORARILY_VALID', '신법 → 구법', 'B가 일시적으로 유효', '경과규정', '1,000'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('엣지 속성', level=3)
    add_table(doc,
        ['엣지', '속성', '타입', '설명'],
        [
            ['MENCABUT', 'scope', 'enum', 'FULL / PARTIAL / CONDITIONAL'],
            ['MENCABUT', 'pasal', 'string', '폐지 조항'],
            ['MENGUBAH', 'amendment_order', 'int', '개정 차수 (1, 2, 3...)'],
            ['CONDITIONALLY_VALID', 'condition_type', 'enum', 'TIDAK_BERTENTANGAN / BELUM_DIATUR'],
            ['CONDITIONALLY_VALID', 'condition_text', 'string', '원문 조건 텍스트'],
            ['TEMPORARILY_VALID', 'valid_until', 'date', '유효 만료일'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('5.3 추출 알고리즘', level=2)

    doc.add_heading('Step 1: status 필드에서 MENCABUT 관계 추출', level=3)
    add_code_block(doc, '''def extract_revocation_from_status(status_text):
    """
    법제처 status 필드에서 폐지 관계 추출

    예시 입력:
    "Tidak BerlakuDicabut Oleh :Undang-Undang Nomor 17 Tahun 2023 Tentang Kesehatan"

    출력:
    {
        "target_jenis": "Undang-Undang",
        "target_nomor": "17",
        "target_tahun": "2023",
        "edge_type": "DICABUT_OLEH"
    }
    """
    pattern = r"Dicabut Oleh\\s*:\\s*(Undang-Undang|Peraturan.*?)\\s+Nomor\\s+(\\d+)\\s+Tahun\\s+(\\d{4})"
    match = re.search(pattern, status_text)
    if match:
        return {
            "target_jenis": match.group(1),
            "target_nomor": match.group(2),
            "target_tahun": match.group(3)
        }
    return None''')

    doc.add_heading('Step 2: tentang에서 MENGUBAH 관계 추출', level=3)
    add_code_block(doc, '''def extract_amendment_from_title(tentang):
    """
    법령 제목에서 개정 관계 추출

    예시 입력:
    "Perubahan Kedua Atas Undang-Undang Nomor 11 Tahun 2008 Tentang ITE"

    출력:
    {
        "amendment_order": 2,  # Kedua = 2차
        "target_jenis": "Undang-Undang",
        "target_nomor": "11",
        "target_tahun": "2008",
        "edge_type": "MENGUBAH"
    }
    """
    order_map = {"": 1, "Kedua": 2, "Ketiga": 3, "Keempat": 4}
    pattern = r"Perubahan\\s+(Kedua|Ketiga|Keempat)?\\s*Atas.*?(Undang-Undang|Peraturan.*?)\\s+Nomor\\s+(\\d+)\\s+Tahun\\s+(\\d{4})"
    # ...''')

    doc.add_heading('Step 3: extracted_text에서 조건부 유효 플래그 설정', level=3)
    add_code_block(doc, '''def flag_conditional_validity(extracted_text):
    """
    조건부 유효 패턴 탐지 → needs_review = True
    """
    CONDITIONAL_PATTERNS = [
        r"sepanjang tidak bertentangan",
        r"tetap berlaku.*?sepanjang",
        r"dinyatakan masih.*?berlaku",
        r"sebagian.*?dicabut"
    ]

    for pattern in CONDITIONAL_PATTERNS:
        if re.search(pattern, extracted_text, re.IGNORECASE):
            return True  # 인간 검토 필요
    return False''')

    doc.add_page_break()

    # =====================================================
    # 6. 인간 개입 필요 케이스 상세
    # =====================================================
    doc.add_heading('6. 인간 개입 필요 케이스 상세', level=1)

    doc.add_heading('6.1 왜 인간 판단이 필요한가', level=2)

    doc.add_paragraph(
        '일부 법령의 현행성은 단순한 패턴 매칭으로 판단할 수 없습니다. '
        '이는 법률의 본질적 특성에서 기인합니다:'
    )

    add_table(doc,
        ['이유', '설명', '예시'],
        [
            ['조건의 해석', '법적 조건이 충족되었는지 판단 필요', '"상충하지 않는 한" → 무엇이 상충인가?'],
            ['맥락 의존성', '동일 표현도 맥락에 따라 의미 상이', '"tetap berlaku"가 무조건인지 조건부인지'],
            ['법률 전문 지식', '법률 용어의 정확한 해석 필요', '"Prinsip Syariah"의 범위'],
            ['사실 판단', '법 외적 사실 확인 필요', '경과 기간 동안 조정이 완료되었는가?'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('6.2 케이스별 상세 분석', level=2)

    doc.add_heading('케이스 A: "sepanjang tidak bertentangan" (상충하지 않는 한)', level=3)

    add_table(doc,
        ['항목', '내용'],
        [
            ['건수', '2,287건'],
            ['패턴', '신법 시행 시, 구법이 신법과 상충하지 않는 한 유효'],
            ['문제', '"상충"의 판단 기준이 명시되지 않음'],
            ['필요한 판단', '구법의 각 조항이 신법과 상충하는지 법률적 비교 분석'],
            ['자동화 불가 이유', '법률 텍스트의 의미론적 비교가 필요'],
            ['인간 작업', '법률 전문가가 두 법령을 비교하여 상충 조항 식별'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('케이스 B: "sebagian dicabut" (부분 폐지)', level=3)

    add_table(doc,
        ['항목', '내용'],
        [
            ['건수', '2,626건'],
            ['패턴', '법령의 일부 조항만 폐지됨'],
            ['문제', '어떤 조항(Pasal, Ayat)이 폐지되었는지 특정 필요'],
            ['필요한 판단', '폐지 범위 파악 및 남은 조항의 유효성 확인'],
            ['자동화 불가 이유', '조항 수준의 상세 파싱 및 맥락 이해 필요'],
            ['인간 작업', '폐지된 조항 목록 작성 및 DB 업데이트'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('케이스 C: DB 간 status 불일치', level=3)

    add_table(doc,
        ['항목', '내용'],
        [
            ['건수', '1,822건'],
            ['패턴', '법제처는 "Berlaku", BPK는 "Tidak Berlaku" (또는 반대)'],
            ['문제', '어느 DB가 정확한지 판단 필요'],
            ['필요한 판단', '최신 상태 확인 및 DB 오류 수정'],
            ['자동화 불가 이유', '외부 정보 확인 또는 원문 대조 필요'],
            ['인간 작업', '원문 확인 후 정확한 상태 결정'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('케이스 D: 경과규정 만료 판단', level=3)

    add_table(doc,
        ['항목', '내용'],
        [
            ['건수', '~1,000건 (시간 경과 시 증가)'],
            ['패턴', '"시행일로부터 2년간 유효" 등 시간 조건'],
            ['문제', '시행일 + 기간 계산 후 현재 시점과 비교'],
            ['필요한 판단', '만료 후 자동 폐지인지, 연장 가능인지 확인'],
            ['자동화 가능 부분', '날짜 계산은 자동화 가능'],
            ['인간 확인 필요', '만료 후 처리 방식이 명시되지 않은 경우'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('6.3 실제 사례 분석', level=2)

    doc.add_heading('사례 1: UU 17/2023 (건강법) - 복합 조건', level=3)

    p = doc.add_paragraph()
    p.add_run('원문 (KETENTUAN PERALIHAN):').bold = True
    doc.add_paragraph(
        '"Pada saat Undang-Undang ini mulai berlaku... dinyatakan masih tetap berlaku '
        'sepanjang tidak bertentangan dengan ketentuan dalam Undang-Undang ini."'
    )

    p = doc.add_paragraph()
    p.add_run('번역:').bold = True
    doc.add_paragraph(
        '"본 법률 시행 시... 본 법률의 규정과 상충하지 않는 한 여전히 유효하다고 선언한다."'
    )

    p = doc.add_paragraph()
    p.add_run('인간 판단 필요 사항:').bold = True
    doc.add_paragraph('1. 이전 건강 관련 법령(UU 36/2009 등)의 어떤 조항이 상충하는가?', style='List Bullet')
    doc.add_paragraph('2. 하위 법령(PP, Permen)들도 동일하게 적용되는가?', style='List Bullet')
    doc.add_paragraph('3. "상충"의 판단 주체와 절차는 무엇인가?', style='List Bullet')

    doc.add_heading('사례 2: PP 58/2021 - DB 불일치', level=3)

    add_table(doc,
        ['DB', 'status'],
        [
            ['법제처', 'Berlaku'],
            ['BPK', 'Tidak Berlaku'],
        ]
    )

    p = doc.add_paragraph()
    p.add_run('조사 필요 사항:').bold = True
    doc.add_paragraph('1. 최근 이 법령을 폐지하는 새 법령이 있는가?', style='List Bullet')
    doc.add_paragraph('2. 어느 DB가 업데이트 지연인가?', style='List Bullet')
    doc.add_paragraph('3. 공식 관보(Lembaran Negara) 확인', style='List Bullet')

    doc.add_page_break()

    # =====================================================
    # 7. 업무 분담 및 워크플로우
    # =====================================================
    doc.add_heading('7. 업무 분담 및 워크플로우', level=1)

    doc.add_heading('7.1 전체 워크플로우', level=2)

    add_table(doc,
        ['단계', '처리 주체', '입력', '출력', '예상 시간'],
        [
            ['1. 데이터 수집', '시스템', '법제처/BPK API', '원시 데이터', '자동'],
            ['2. 1차 분류', '시스템', '35,316건', 'A/B/C/D 카테고리', '수 분'],
            ['3. 관계 추출', '시스템', 'extracted_text', '그래프 엣지', '수 분'],
            ['4. 샘플 검증', '시스템+인간', 'B 카테고리', '품질 확인', '수 시간'],
            ['5. 전문가 검토', '인간', 'C/D 카테고리', '최종 판정', '수백 시간'],
            ['6. DB 업데이트', '시스템', '검토 결과', '최종 그래프', '자동'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('7.2 시스템 자동 처리 (단계 1-3)', level=2)

    doc.add_heading('입력 조건 → 출력 결과 매핑', level=3)
    add_table(doc,
        ['조건', '결과', '건수'],
        [
            ['법제처 "Berlaku" + BPK "Berlaku"', '자동: 유효 (VALID)', '~24,000'],
            ['status에 "Dicabut Oleh" 포함', '자동: 무효 (INVALID) + 관계 추출', '3,681'],
            ['tentang에 "Perubahan Atas" 포함', '자동: 개정 관계 추출', '3,278'],
            ['text에 "sepanjang tidak bertentangan"', '플래그: needs_review = true', '2,287'],
            ['법제처 ≠ BPK status', '플래그: needs_validation = true', '1,822'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('7.3 인간 검토 (단계 5)', level=2)

    doc.add_heading('우선순위 대기열', level=3)
    add_table(doc,
        ['순위', '케이스', '건수', '검토 시간/건', '총 예상 시간'],
        [
            ['P1', 'DB 간 status 불일치', '1,822', '5분', '152시간'],
            ['P2', 'sepanjang tidak bertentangan', '2,287', '15분', '572시간'],
            ['P3', 'sebagian dicabut (부분 폐지)', '800', '20분', '267시간'],
            ['P4', '복합 조건', '400', '30분', '200시간'],
            ['', '합계', '~5,300', '', '~1,200시간'],
        ]
    )
    doc.add_paragraph()

    p = doc.add_paragraph()
    p.add_run('참고: ').bold = True
    p.add_run('1,200시간 = 전담 인력 1명 기준 약 6개월, 3명 기준 약 2개월')

    doc.add_heading('7.4 검증 체크리스트', level=2)

    add_table(doc,
        ['ID', '검증 항목', '목표', '측정 방법'],
        [
            ['V-01', '자동 유효 판정 정확도', '≥99%', '샘플 400건 검증'],
            ['V-02', '자동 무효 판정 정확도', '≥98%', '샘플 100건 검증'],
            ['V-03', '인간 검토 필요 탐지율', '≥99%', '조건부 패턴 recall'],
            ['V-04', '관계 추출 정확도', '≥95%', '엣지 샘플 검증'],
            ['V-05', 'DB 불일치 해결율', '100%', '전수 조사'],
        ]
    )
    doc.add_paragraph()

    doc.add_page_break()

    # =====================================================
    # 8. 결론 및 권장사항
    # =====================================================
    doc.add_heading('8. 결론 및 권장사항', level=1)

    doc.add_heading('8.1 핵심 결론', level=2)

    p = doc.add_paragraph()
    p.add_run('1. 90%의 법령은 시스템이 자동으로 현행성을 판단할 수 있습니다.').bold = True
    doc.add_paragraph(
        '   - BPK와 법제처 status가 일치하는 경우 자동 확정\n'
        '   - "Dicabut Oleh" 패턴으로 폐지 관계 자동 추출\n'
        '   - 개정 관계는 제목에서 자동 추출'
    )

    p = doc.add_paragraph()
    p.add_run('2. 10%의 법령은 법률 전문가의 판단이 필수입니다.').bold = True
    doc.add_paragraph(
        '   - "sepanjang tidak bertentangan" 등 조건부 유효 표현\n'
        '   - 부분 폐지의 범위 결정\n'
        '   - DB 간 불일치 해결'
    )

    p = doc.add_paragraph()
    p.add_run('3. 그래프 데이터베이스로 법령 간 관계를 시각화할 수 있습니다.').bold = True
    doc.add_paragraph(
        '   - ~36,000개의 관계(엣지) 추출 가능\n'
        '   - 폐지 체인, 개정 이력 추적\n'
        '   - 상위법-하위법 계층 구조'
    )

    doc.add_heading('8.2 권장 실행 계획', level=2)

    add_table(doc,
        ['단계', '작업', '담당', '예상 기간'],
        [
            ['1', '자동 분류 시스템 개발', '개발팀', '2주'],
            ['2', '그래프 DB 구축', '개발팀', '1주'],
            ['3', 'P1 케이스 (DB 불일치) 처리', '법률팀', '2주'],
            ['4', 'P2 케이스 (조건부 유효) 처리', '법률팀', '4주'],
            ['5', '검증 및 품질 보증', '공동', '1주'],
            ['', '총 예상 기간', '', '~10주'],
        ]
    )
    doc.add_paragraph()

    doc.add_heading('8.3 기대 효과', level=2)

    add_table(doc,
        ['지표', '현재 (수동)', '자동화 후', '개선율'],
        [
            ['처리 시간', '법령당 30분', '법령당 1초 (90%)', '1,800배'],
            ['법률팀 업무량', '35,316건 전수', '3,500건만', '90% 감소'],
            ['관계 추적', '수동 검색', '그래프 쿼리', '실시간'],
            ['오류율', '인적 오류 발생', '일관된 규칙 적용', '최소화'],
        ]
    )
    doc.add_paragraph()

    doc.add_paragraph()
    doc.add_paragraph()

    # Footer
    footer = doc.add_paragraph('─' * 50)
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run('Report generated: 2025-12-26\n')
    footer.add_run('분석 파일: /docs/analysis/\n')
    footer.add_run('총 분석 법령: 35,316건 (법제처) + 34,549건 (BPK)')

    # Save
    output_path = 'docs/인도네시아_법령_현행성_심층분석_상세보고서.docx'
    doc.save(output_path)
    print(f"Saved: {output_path}")
    return output_path

if __name__ == '__main__':
    create_report()
