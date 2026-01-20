"""
Akoma Ntoso XML 생성기

인도네시아 법령을 Akoma Ntoso 3.0 XML 형식으로 변환

Akoma Ntoso: 법률 문서를 위한 국제 XML 표준
- OASIS 표준 (LegalDocML)
- UN, EU 등에서 채택

구조:
- akomaNtoso (루트)
  - act (법률)
    - meta (메타데이터)
    - body (본문)
      - chapter (BAB)
        - article (Pasal)
          - paragraph (Ayat)
            - point (Huruf)
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET
from xml.dom import minidom

from .structure_parser import ParsedDocument, Bab, Pasal, Ayat, Huruf


# Akoma Ntoso 3.0 네임스페이스
AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
AKN_PREFIX = "{" + AKN_NS + "}"

# 인도네시아 법령 유형 매핑
JENIS_TO_AKN_TYPE = {
    "UNDANG-UNDANG": "act",
    "UU": "act",
    "PERATURAN PEMERINTAH": "act",
    "PP": "act",
    "PERATURAN PRESIDEN": "act",
    "PERPRES": "act",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "act",
    "PERPPU": "act",
    "PERATURAN MENTERI": "act",
    "PERMEN": "act",
    "PERATURAN BADAN/LEMBAGA": "act",
    "PERBAN": "act",
    "KEPUTUSAN PRESIDEN": "doc",
    "KEPPRES": "doc",
    "INSTRUKSI PRESIDEN": "doc",
    "INPRES": "doc",
}

# 법령 유형 약어
JENIS_ABBREV = {
    "UNDANG-UNDANG": "uu",
    "PERATURAN PEMERINTAH": "pp",
    "PERATURAN PRESIDEN": "perpres",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "perppu",
    "PERATURAN MENTERI": "permen",
    "PERATURAN BADAN/LEMBAGA": "perban",
    "KEPUTUSAN PRESIDEN": "keppres",
    "INSTRUKSI PRESIDEN": "inpres",
}


@dataclass
class AknMetadata:
    """Akoma Ntoso 메타데이터"""
    slug: str
    jenis: str
    nomor: str
    tahun: int
    tentang: str
    tanggal_penetapan: Optional[str] = None
    tanggal_pengundangan: Optional[str] = None
    tempat_penetapan: Optional[str] = None
    pemrakarsa: Optional[str] = None
    status: Optional[str] = None


class AkomaNtosoGenerator:
    """
    Akoma Ntoso XML 생성기

    사용법:
        generator = AkomaNtosoGenerator()
        xml_str = generator.generate(parsed_doc, metadata)
        generator.save(xml_str, output_path)
    """

    def __init__(self, country: str = "id", language: str = "ind"):
        """
        Args:
            country: 국가 코드 (ISO 3166-1 alpha-2)
            language: 언어 코드 (ISO 639-2)
        """
        self.country = country
        self.language = language

    def generate(
        self,
        parsed_doc: ParsedDocument,
        metadata: AknMetadata,
    ) -> str:
        """
        파싱된 문서를 Akoma Ntoso XML로 변환

        Args:
            parsed_doc: 구조 파싱된 문서
            metadata: 메타데이터

        Returns:
            XML 문자열
        """
        # 루트 요소
        root = ET.Element("akomaNtoso", xmlns=AKN_NS)

        # 문서 유형 결정
        doc_type = JENIS_TO_AKN_TYPE.get(metadata.jenis.upper(), "act")

        # act/doc 요소
        doc_elem = ET.SubElement(root, doc_type, name=metadata.slug)

        # 메타 섹션
        self._add_meta(doc_elem, metadata)

        # 본문 섹션
        self._add_body(doc_elem, parsed_doc, metadata)

        # XML 문자열로 변환 (예쁘게 포맷팅)
        return self._prettify(root)

    def _add_meta(self, parent: ET.Element, metadata: AknMetadata) -> None:
        """메타데이터 섹션 추가"""
        meta = ET.SubElement(parent, "meta")

        # identification
        identification = ET.SubElement(meta, "identification", source="#source")

        # FRBRWork
        work = ET.SubElement(identification, "FRBRWork")
        work_uri = self._generate_uri(metadata, "work")
        ET.SubElement(work, "FRBRthis", value=work_uri)
        ET.SubElement(work, "FRBRuri", value=work_uri)
        ET.SubElement(work, "FRBRdate", date=str(metadata.tahun), name="enactment")
        ET.SubElement(work, "FRBRauthor", href="#author")
        ET.SubElement(work, "FRBRcountry", value=self.country.upper())

        # FRBRExpression
        expr = ET.SubElement(identification, "FRBRExpression")
        expr_uri = self._generate_uri(metadata, "expression")
        ET.SubElement(expr, "FRBRthis", value=expr_uri)
        ET.SubElement(expr, "FRBRuri", value=expr_uri)
        if metadata.tanggal_pengundangan:
            ET.SubElement(expr, "FRBRdate", date=metadata.tanggal_pengundangan, name="publication")
        ET.SubElement(expr, "FRBRauthor", href="#author")
        ET.SubElement(expr, "FRBRlanguage", language=self.language)

        # FRBRManifestation
        manif = ET.SubElement(identification, "FRBRManifestation")
        manif_uri = self._generate_uri(metadata, "manifestation")
        ET.SubElement(manif, "FRBRthis", value=manif_uri)
        ET.SubElement(manif, "FRBRuri", value=manif_uri)
        ET.SubElement(manif, "FRBRdate", date=datetime.now().strftime("%Y-%m-%d"), name="generation")
        ET.SubElement(manif, "FRBRauthor", href="#generator")

        # publication
        if metadata.tanggal_pengundangan:
            ET.SubElement(
                meta, "publication",
                date=metadata.tanggal_pengundangan,
                name="Lembaran Negara",
                showAs="Lembaran Negara Republik Indonesia"
            )

        # lifecycle
        lifecycle = ET.SubElement(meta, "lifecycle", source="#source")
        if metadata.tanggal_penetapan:
            ET.SubElement(
                lifecycle, "eventRef",
                date=metadata.tanggal_penetapan,
                type="generation",
                source="#source"
            )

        # references
        references = ET.SubElement(meta, "references", source="#source")
        ET.SubElement(
            references, "TLCOrganization",
            eId="source",
            href="/ontology/organization/id/peraturan-go-id",
            showAs="peraturan.go.id"
        )
        if metadata.pemrakarsa:
            ET.SubElement(
                references, "TLCOrganization",
                eId="author",
                href=f"/ontology/organization/id/{self._slugify(metadata.pemrakarsa)}",
                showAs=metadata.pemrakarsa
            )
        ET.SubElement(
            references, "TLCOrganization",
            eId="generator",
            href="/ontology/organization/ilis",
            showAs="ILIS OCR Pipeline"
        )

    def _add_body(
        self,
        parent: ET.Element,
        parsed_doc: ParsedDocument,
        metadata: AknMetadata
    ) -> None:
        """본문 섹션 추가"""
        body = ET.SubElement(parent, "body")

        # 제목
        preface = ET.SubElement(parent, "preface")
        doc_title = ET.SubElement(preface, "docTitle")
        doc_title.text = f"{metadata.jenis} REPUBLIK INDONESIA NOMOR {metadata.nomor} TAHUN {metadata.tahun}"

        long_title = ET.SubElement(preface, "longTitle")
        p = ET.SubElement(long_title, "p")
        p.text = f"TENTANG {metadata.tentang}"

        # BAB이 있는 경우
        if parsed_doc.babs:
            for bab in parsed_doc.babs:
                self._add_chapter(body, bab)

        # BAB 없이 Pasal만 있는 경우
        if parsed_doc.pasals:
            for pasal in parsed_doc.pasals:
                self._add_article(body, pasal)

    def _add_chapter(self, parent: ET.Element, bab: Bab) -> None:
        """장(BAB) 추가"""
        chapter = ET.SubElement(
            parent, "chapter",
            eId=f"chp_{bab.nomor}"
        )

        # 장 번호
        num = ET.SubElement(chapter, "num")
        num.text = f"BAB {bab.nomor}"

        # 장 제목
        heading = ET.SubElement(chapter, "heading")
        heading.text = bab.judul

        # 조(Pasal) 추가
        for pasal in bab.pasals:
            self._add_article(chapter, pasal)

    def _add_article(self, parent: ET.Element, pasal: Pasal) -> None:
        """조(Pasal) 추가"""
        article = ET.SubElement(
            parent, "article",
            eId=f"art_{pasal.nomor}"
        )

        # 조 번호
        num = ET.SubElement(article, "num")
        num.text = f"Pasal {pasal.nomor}"

        # 항(Ayat)이 있는 경우
        if pasal.ayats:
            for ayat in pasal.ayats:
                self._add_paragraph(article, ayat, pasal.nomor)
        else:
            # 항 없이 본문만 있는 경우
            content = ET.SubElement(article, "content")
            p = ET.SubElement(content, "p")
            p.text = self._clean_text(pasal.text)

    def _add_paragraph(self, parent: ET.Element, ayat: Ayat, pasal_num: int) -> None:
        """항(Ayat) 추가"""
        # ayat.nomor가 0이면 암시적 항
        if ayat.nomor == 0:
            content = ET.SubElement(parent, "content")
            p = ET.SubElement(content, "p")
            p.text = self._clean_text(ayat.text)
            return

        paragraph = ET.SubElement(
            parent, "paragraph",
            eId=f"art_{pasal_num}__para_{ayat.nomor}"
        )

        # 항 번호
        num = ET.SubElement(paragraph, "num")
        num.text = f"({ayat.nomor})"

        # 호(Huruf)가 있는 경우
        if ayat.hurufs:
            content = ET.SubElement(paragraph, "content")
            # 항 본문 (호 이전 텍스트)
            intro_text = self._extract_intro(ayat.text)
            if intro_text:
                intro = ET.SubElement(content, "intro")
                p = ET.SubElement(intro, "p")
                p.text = intro_text

            # 호 목록
            block_list = ET.SubElement(content, "blockList")
            for huruf in ayat.hurufs:
                self._add_point(block_list, huruf, pasal_num, ayat.nomor)
        else:
            content = ET.SubElement(paragraph, "content")
            p = ET.SubElement(content, "p")
            p.text = self._clean_text(ayat.text)

    def _add_point(
        self,
        parent: ET.Element,
        huruf: Huruf,
        pasal_num: int,
        ayat_num: int
    ) -> None:
        """호(Huruf) 추가"""
        item = ET.SubElement(
            parent, "item",
            eId=f"art_{pasal_num}__para_{ayat_num}__point_{huruf.huruf}"
        )

        # 호 번호
        num = ET.SubElement(item, "num")
        num.text = f"{huruf.huruf}."

        # 호 내용
        p = ET.SubElement(item, "p")
        p.text = self._clean_text(huruf.text)

        # 목(Angka)이 있는 경우
        if huruf.angkas:
            sub_list = ET.SubElement(item, "blockList")
            for angka in huruf.angkas:
                sub_item = ET.SubElement(
                    sub_list, "item",
                    eId=f"art_{pasal_num}__para_{ayat_num}__point_{huruf.huruf}__subpoint_{angka.nomor}"
                )
                sub_num = ET.SubElement(sub_item, "num")
                sub_num.text = f"{angka.nomor}."
                sub_p = ET.SubElement(sub_item, "p")
                sub_p.text = self._clean_text(angka.text)

    def _generate_uri(self, metadata: AknMetadata, level: str) -> str:
        """FRBR URI 생성"""
        jenis_abbrev = JENIS_ABBREV.get(metadata.jenis.upper(), metadata.jenis.lower())
        base = f"/akn/{self.country}/{jenis_abbrev}/{metadata.tahun}/{metadata.nomor}"

        if level == "work":
            return base
        elif level == "expression":
            return f"{base}/{self.language}@"
        elif level == "manifestation":
            return f"{base}/{self.language}@/main.xml"
        return base

    def _slugify(self, text: str) -> str:
        """텍스트를 slug로 변환"""
        text = text.lower()
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[\s_-]+', '-', text)
        return text.strip('-')

    def _clean_text(self, text: str) -> str:
        """텍스트 정리"""
        if not text:
            return ""
        # 연속 공백 제거
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _extract_intro(self, text: str) -> str:
        """항 본문에서 호 이전 텍스트 추출"""
        # 첫 번째 a. 또는 1. 이전 텍스트
        match = re.search(r'^(.+?)(?=[a-z]\.|^\d+\.)', text, re.MULTILINE | re.DOTALL)
        if match:
            return self._clean_text(match.group(1))
        return ""

    def _prettify(self, elem: ET.Element) -> str:
        """XML 예쁘게 포맷팅"""
        rough_string = ET.tostring(elem, encoding='unicode')
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ", encoding=None)

    def save(self, xml_content: str, output_path: Path) -> None:
        """XML 파일 저장"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_content)


def generate_akn(
    parsed_doc: ParsedDocument,
    metadata: dict,
    output_path: Optional[Path] = None,
) -> str:
    """
    편의 함수: Akoma Ntoso XML 생성

    Args:
        parsed_doc: 구조 파싱된 문서
        metadata: 메타데이터 딕셔너리
        output_path: 저장 경로 (선택)

    Returns:
        XML 문자열
    """
    akn_metadata = AknMetadata(
        slug=metadata.get("slug", parsed_doc.slug),
        jenis=metadata.get("jenis", ""),
        nomor=metadata.get("nomor", ""),
        tahun=metadata.get("tahun", 0),
        tentang=metadata.get("tentang", ""),
        tanggal_penetapan=metadata.get("tanggal_penetapan"),
        tanggal_pengundangan=metadata.get("tanggal_pengundangan"),
        tempat_penetapan=metadata.get("tempat_penetapan"),
        pemrakarsa=metadata.get("pemrakarsa"),
        status=metadata.get("status"),
    )

    generator = AkomaNtosoGenerator()
    xml_content = generator.generate(parsed_doc, akn_metadata)

    if output_path:
        generator.save(xml_content, output_path)

    return xml_content


# CLI 테스트용
if __name__ == "__main__":
    from .structure_parser import ParsedDocument, Bab, Pasal, Ayat, Huruf

    print("=== Akoma Ntoso XML 생성 테스트 ===\n")

    # 테스트 문서 생성
    test_doc = ParsedDocument(
        slug="uu-no-12-tahun-2011",
        babs=[
            Bab(
                nomor="I",
                judul="KETENTUAN UMUM",
                pasals=[
                    Pasal(
                        nomor=1,
                        text="Dalam Undang-Undang ini yang dimaksud dengan:",
                        ayats=[
                            Ayat(
                                nomor=1,
                                text="Pembentukan Peraturan adalah...",
                                hurufs=[]
                            ),
                        ]
                    ),
                ]
            ),
        ],
        pasals=[],
        total_pasal=1,
        total_ayat=1,
        total_huruf=0,
    )

    test_metadata = AknMetadata(
        slug="uu-no-12-tahun-2011",
        jenis="UNDANG-UNDANG",
        nomor="12",
        tahun=2011,
        tentang="PEMBENTUKAN PERATURAN PERUNDANG-UNDANGAN",
        tanggal_penetapan="2011-08-12",
        tanggal_pengundangan="2011-08-12",
        pemrakarsa="DPR",
    )

    generator = AkomaNtosoGenerator()
    xml_output = generator.generate(test_doc, test_metadata)

    print(xml_output[:2000])
    print("\n... (truncated)")
