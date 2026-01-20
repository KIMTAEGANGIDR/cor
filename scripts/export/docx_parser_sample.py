#!/usr/bin/env python3
"""
DOCX → XML 파싱 샘플 스크립트

인도네시아 법령 분석 보고서(.docx)를 파싱하여 구조화된 데이터로 변환합니다.

DOCX 파일 구조:
    .docx (ZIP 압축 파일)
    ├── [Content_Types].xml
    ├── _rels/.rels
    ├── word/
    │   ├── document.xml      ← 본문 (핵심)
    │   ├── styles.xml        ← 스타일 정의
    │   ├── numbering.xml     ← 번호 매기기
    │   └── _rels/document.xml.rels
    └── docProps/
        ├── core.xml          ← 메타데이터 (제목, 저자 등)
        └── app.xml

핵심 XML 요소 (OOXML WordprocessingML):
    <w:document>
        <w:body>
            <w:p>              ← 단락 (Paragraph)
                <w:pPr>        ← 단락 속성
                    <w:pStyle> ← 스타일 (Heading1, Heading2 등)
                </w:pPr>
                <w:r>          ← Run (텍스트 조각)
                    <w:t>      ← 텍스트
                </w:r>
            </w:p>
            <w:tbl>            ← 테이블
                <w:tr>         ← 행
                    <w:tc>     ← 셀
            </w:tbl>
        </w:body>
    </w:document>

Usage:
    python docx_parser_sample.py <input.docx> [--output json|markdown|structure]
"""

import zipfile
import json
import re
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path


# ============================================================
# OOXML 네임스페이스 정의
# ============================================================
NAMESPACES = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'cp': 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'dcterms': 'http://purl.org/dc/terms/',
}


# ============================================================
# 데이터 모델
# ============================================================
@dataclass
class TableCell:
    """테이블 셀"""
    text: str
    row: int
    col: int


@dataclass
class Table:
    """테이블"""
    rows: list[list[str]] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)

    def to_dict(self):
        return {
            'headers': self.headers,
            'rows': self.rows
        }


@dataclass
class Paragraph:
    """단락"""
    text: str
    style: Optional[str] = None
    level: int = 0  # 0=본문, 1=Heading1, 2=Heading2...

    def to_dict(self):
        return asdict(self)


@dataclass
class Section:
    """섹션 (Heading 기준 분할)"""
    title: str
    level: int
    paragraphs: list[Paragraph] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    subsections: list['Section'] = field(default_factory=list)

    def to_dict(self):
        return {
            'title': self.title,
            'level': self.level,
            'paragraphs': [p.to_dict() for p in self.paragraphs],
            'tables': [t.to_dict() for t in self.tables],
            'subsections': [s.to_dict() for s in self.subsections]
        }


@dataclass
class Document:
    """문서 전체"""
    title: str = ""
    metadata: dict = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)

    def to_dict(self):
        return {
            'title': self.title,
            'metadata': self.metadata,
            'sections': [s.to_dict() for s in self.sections]
        }

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ============================================================
# DOCX 파서
# ============================================================
class DocxParser:
    """DOCX 파일 파서"""

    def __init__(self, docx_path: str):
        self.docx_path = Path(docx_path)
        self.ns = NAMESPACES

    def _get_text_from_element(self, element) -> str:
        """요소에서 모든 텍스트 추출"""
        texts = []
        for t in element.findall('.//w:t', self.ns):
            if t.text:
                texts.append(t.text)
        return ''.join(texts)

    def _get_style(self, paragraph) -> tuple[Optional[str], int]:
        """단락 스타일 및 레벨 추출"""
        pPr = paragraph.find('w:pPr', self.ns)
        if pPr is None:
            return None, 0

        style_elem = pPr.find('w:pStyle', self.ns)
        if style_elem is None:
            return None, 0

        style = style_elem.get(f'{{{self.ns["w"]}}}val')

        # Heading 레벨 추출
        level = 0
        if style:
            match = re.match(r'Heading(\d+)', style)
            if match:
                level = int(match.group(1))

        return style, level

    def _parse_table(self, table_elem) -> Table:
        """테이블 파싱"""
        table = Table()

        for row_idx, tr in enumerate(table_elem.findall('.//w:tr', self.ns)):
            row = []
            for tc in tr.findall('.//w:tc', self.ns):
                cell_text = self._get_text_from_element(tc)
                row.append(cell_text)

            if row_idx == 0:
                table.headers = row
            else:
                table.rows.append(row)

        return table

    def _parse_metadata(self, zf: zipfile.ZipFile) -> dict:
        """메타데이터 파싱 (docProps/core.xml)"""
        metadata = {}

        try:
            with zf.open('docProps/core.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()

                # 제목
                title = root.find('.//dc:title', self.ns)
                if title is not None and title.text:
                    metadata['title'] = title.text

                # 저자
                creator = root.find('.//dc:creator', self.ns)
                if creator is not None and creator.text:
                    metadata['creator'] = creator.text

                # 생성일
                created = root.find('.//dcterms:created', self.ns)
                if created is not None and created.text:
                    metadata['created'] = created.text

                # 수정일
                modified = root.find('.//dcterms:modified', self.ns)
                if modified is not None and modified.text:
                    metadata['modified'] = modified.text

        except Exception as e:
            print(f"메타데이터 파싱 실패: {e}")

        return metadata

    def parse(self) -> Document:
        """DOCX 파일 파싱"""
        doc = Document()

        with zipfile.ZipFile(self.docx_path, 'r') as zf:
            # 메타데이터 파싱
            doc.metadata = self._parse_metadata(zf)

            # document.xml 파싱
            with zf.open('word/document.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()

            body = root.find('.//w:body', self.ns)
            if body is None:
                return doc

            # 섹션 스택 (레벨별 현재 섹션)
            # 레벨 0은 doc.sections에 직접 추가되는 최상위
            section_stack: dict[int, Section] = {}
            current_section: Optional[Section] = None

            for element in body:
                tag = element.tag.split('}')[-1]

                if tag == 'p':  # 단락
                    text = self._get_text_from_element(element)
                    style, level = self._get_style(element)

                    if level > 0:  # Heading
                        # 새 섹션 생성
                        new_section = Section(title=text, level=level)

                        if level == 1:
                            # Heading1은 최상위 섹션
                            doc.sections.append(new_section)
                            # 문서 제목 설정 (첫 Heading1)
                            if not doc.title:
                                doc.title = text
                        else:
                            # Heading2 이상은 부모 섹션의 subsection으로 추가
                            parent_level = level - 1
                            while parent_level >= 1 and parent_level not in section_stack:
                                parent_level -= 1

                            if parent_level >= 1 and parent_level in section_stack:
                                section_stack[parent_level].subsections.append(new_section)
                            elif doc.sections:
                                # 부모가 없으면 최상위 섹션의 subsection으로
                                doc.sections[-1].subsections.append(new_section)

                        section_stack[level] = new_section
                        current_section = new_section

                        # 더 높은 레벨의 이전 섹션 정리
                        for lvl in list(section_stack.keys()):
                            if lvl > level:
                                del section_stack[lvl]

                    elif text.strip():  # 일반 단락
                        para = Paragraph(text=text, style=style, level=0)

                        if current_section is not None:
                            current_section.paragraphs.append(para)

                elif tag == 'tbl':  # 테이블
                    table = self._parse_table(element)

                    if current_section is not None:
                        current_section.tables.append(table)

        return doc


# ============================================================
# 출력 포맷터
# ============================================================
def format_as_structure(doc: Document) -> str:
    """문서 구조 출력"""
    lines = []
    lines.append(f"# 문서: {doc.title}")
    lines.append(f"# 메타데이터: {doc.metadata}")
    lines.append("")

    def print_section(section: Section, indent=0):
        prefix = "  " * indent
        lines.append(f"{prefix}{'#' * section.level} {section.title}")

        if section.paragraphs:
            lines.append(f"{prefix}  - 단락: {len(section.paragraphs)}개")
        if section.tables:
            lines.append(f"{prefix}  - 테이블: {len(section.tables)}개")

        for sub in section.subsections:
            print_section(sub, indent + 1)

    for section in doc.sections:
        print_section(section)

    return '\n'.join(lines)


def format_as_markdown(doc: Document) -> str:
    """마크다운 출력"""
    lines = []

    def print_section(section: Section):
        lines.append(f"{'#' * section.level} {section.title}")
        lines.append("")

        for para in section.paragraphs:
            lines.append(para.text)
            lines.append("")

        for table in section.tables:
            if table.headers:
                lines.append("| " + " | ".join(table.headers) + " |")
                lines.append("| " + " | ".join(["---"] * len(table.headers)) + " |")
            for row in table.rows:
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")

        for sub in section.subsections:
            print_section(sub)

    for section in doc.sections:
        print_section(section)

    return '\n'.join(lines)


# ============================================================
# 특화된 파서: 법령 분석 보고서
# ============================================================
class LawReportParser(DocxParser):
    """법령 분석 보고서 특화 파서"""

    def extract_statistics(self) -> dict:
        """통계 데이터 추출"""
        doc = self.parse()
        stats = {
            'title': doc.title,
            'sections': [],
            'tables': [],
            'key_numbers': {}
        }

        def extract_from_section(section: Section):
            stats['sections'].append({
                'title': section.title,
                'level': section.level,
                'paragraph_count': len(section.paragraphs),
                'table_count': len(section.tables)
            })

            # 테이블 데이터 추출
            for table in section.tables:
                if table.headers:
                    stats['tables'].append({
                        'section': section.title,
                        'headers': table.headers,
                        'row_count': len(table.rows)
                    })

            # 숫자 데이터 추출 (예: "35,316건", "90%")
            for para in section.paragraphs:
                numbers = re.findall(r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(건|개|%)', para.text)
                for num, unit in numbers:
                    key = f"{section.title}_{num}{unit}"
                    stats['key_numbers'][key] = {
                        'value': num.replace(',', ''),
                        'unit': unit,
                        'context': para.text[:100]
                    }

            for sub in section.subsections:
                extract_from_section(sub)

        for section in doc.sections:
            extract_from_section(section)

        return stats

    def extract_graph_schema(self) -> dict:
        """그래프 스키마 섹션 추출"""
        doc = self.parse()
        schema = {
            'nodes': [],
            'edges': []
        }

        def find_schema_section(section: Section):
            if '스키마' in section.title or 'schema' in section.title.lower():
                # 노드/엣지 테이블 찾기
                for table in section.tables:
                    if any('노드' in h or 'node' in h.lower() for h in table.headers):
                        schema['nodes'].extend(table.rows)
                    elif any('관계' in h or 'edge' in h.lower() for h in table.headers):
                        schema['edges'].extend(table.rows)

            for sub in section.subsections:
                find_schema_section(sub)

        for section in doc.sections:
            find_schema_section(section)

        return schema


# ============================================================
# 메인
# ============================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description='DOCX → XML 파싱 샘플')
    parser.add_argument('input', help='입력 DOCX 파일')
    parser.add_argument('--output', '-o', choices=['json', 'markdown', 'structure', 'stats'],
                       default='structure', help='출력 형식')

    args = parser.parse_args()

    if args.output == 'stats':
        # 법령 보고서 특화 파서
        report_parser = LawReportParser(args.input)
        stats = report_parser.extract_statistics()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        # 일반 파서
        docx_parser = DocxParser(args.input)
        doc = docx_parser.parse()

        if args.output == 'json':
            print(doc.to_json())
        elif args.output == 'markdown':
            print(format_as_markdown(doc))
        else:  # structure
            print(format_as_structure(doc))


if __name__ == '__main__':
    main()
