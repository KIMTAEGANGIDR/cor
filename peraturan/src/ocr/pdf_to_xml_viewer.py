"""
ILIS OCR Pipeline - PDF to XML Viewer

PDF 업로드 → OCR → 구조 파싱 → Akoma Ntoso XML
"""

import os
import re
import tempfile
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
import xml.etree.ElementTree as ET
from xml.dom import minidom

import fitz  # PyMuPDF
import gradio as gr
from paddleocr import PaddleOCR

os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

# PaddleOCR 싱글톤
_ocr = None

def get_ocr():
    global _ocr
    if _ocr is None:
        print("🔄 PaddleOCR 초기화 중...")
        _ocr = PaddleOCR(lang='id')
        print("✅ PaddleOCR 준비 완료")
    return _ocr


@dataclass
class ParsedLaw:
    """파싱된 법령 데이터"""
    # 메타데이터
    law_type: str = ""           # UNDANG-UNDANG, PERATURAN PEMERINTAH 등
    number: str = ""             # 번호
    year: str = ""               # 연도
    title: str = ""              # 제목 (TENTANG 이후)

    # 구조
    preamble: str = ""           # 서두 (DENGAN RAHMAT...)
    considerations: list = field(default_factory=list)  # Menimbang
    legal_basis: list = field(default_factory=list)     # Mengingat
    decides: str = ""            # Memutuskan/Menetapkan
    articles: list = field(default_factory=list)        # Pasal들

    # 원본
    raw_lines: list = field(default_factory=list)
    noise_lines: list = field(default_factory=list)


class LawParser:
    """인도네시아 법령 파서"""

    # 법령 유형 패턴
    LAW_TYPES = [
        "UNDANG-UNDANG",
        "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG",
        "PERATURAN PEMERINTAH",
        "PERATURAN PRESIDEN",
        "PERATURAN MENTERI",
        "KEPUTUSAN PRESIDEN",
    ]

    # 노이즈 패턴
    NOISE_PATTERNS = [
        r"^SALINAN$",
        r"^PRESIDEN$",
        r"^REPUBLIK INDONESIA$",
        r"www\..+\.go\.id",
        r"^SK\s*No",
        r"^\d+$",  # 페이지 번호
    ]

    def parse(self, lines: list[str]) -> ParsedLaw:
        """텍스트 라인들을 파싱"""
        result = ParsedLaw(raw_lines=lines)

        # 노이즈 제거
        clean_lines = []
        for line in lines:
            line = line.strip()
            is_noise = any(re.search(p, line, re.IGNORECASE) for p in self.NOISE_PATTERNS)
            if is_noise:
                result.noise_lines.append(line)
            else:
                clean_lines.append(line)

        # 법령 유형 찾기
        for i, line in enumerate(clean_lines):
            for law_type in self.LAW_TYPES:
                if law_type in line.upper():
                    result.law_type = law_type
                    break
            if result.law_type:
                break

        # 번호/연도 찾기
        for line in clean_lines:
            match = re.search(r"NOMOR\s+(\d+)\s+TAHUN\s+(\d{4})", line, re.IGNORECASE)
            if match:
                result.number = match.group(1)
                result.year = match.group(2)
                break

        # 제목 찾기 (TENTANG 이후)
        tentang_idx = -1
        for i, line in enumerate(clean_lines):
            if line.upper() == "TENTANG":
                tentang_idx = i
                break

        if tentang_idx >= 0 and tentang_idx + 1 < len(clean_lines):
            # TENTANG 다음 줄이 제목
            title_lines = []
            for j in range(tentang_idx + 1, len(clean_lines)):
                line = clean_lines[j]
                # DENGAN RAHMAT이 나오면 제목 끝
                if "DENGAN RAHMAT" in line.upper():
                    break
                title_lines.append(line)
            result.title = " ".join(title_lines)

        # 서두 (DENGAN RAHMAT TUHAN YANG MAHA ESA)
        for line in clean_lines:
            if "DENGAN RAHMAT TUHAN" in line.upper():
                result.preamble = line
                break

        # Menimbang, Mengingat, Pasal 등 파싱
        current_section = None
        current_content = []

        for line in clean_lines:
            upper = line.upper()

            if upper.startswith("MENIMBANG"):
                if current_section and current_content:
                    self._save_section(result, current_section, current_content)
                current_section = "menimbang"
                current_content = [line]
            elif upper.startswith("MENGINGAT"):
                if current_section and current_content:
                    self._save_section(result, current_section, current_content)
                current_section = "mengingat"
                current_content = [line]
            elif upper.startswith("MEMUTUSKAN") or upper.startswith("MENETAPKAN"):
                if current_section and current_content:
                    self._save_section(result, current_section, current_content)
                current_section = "memutuskan"
                current_content = [line]
            elif re.match(r"^PASAL\s+\d+", upper):
                if current_section and current_content:
                    self._save_section(result, current_section, current_content)
                current_section = "pasal"
                current_content = [line]
            elif current_section:
                current_content.append(line)

        # 마지막 섹션 저장
        if current_section and current_content:
            self._save_section(result, current_section, current_content)

        return result

    def _save_section(self, result: ParsedLaw, section: str, content: list):
        """섹션 내용 저장"""
        text = "\n".join(content)
        if section == "menimbang":
            result.considerations.append(text)
        elif section == "mengingat":
            result.legal_basis.append(text)
        elif section == "memutuskan":
            result.decides = text
        elif section == "pasal":
            result.articles.append(text)


class AkomaNtosoGenerator:
    """Akoma Ntoso XML 생성기"""

    def generate(self, parsed: ParsedLaw) -> str:
        """파싱된 법령을 Akoma Ntoso XML로 변환"""

        # 루트 요소
        akomantoso = ET.Element("akomaNtoso")
        akomantoso.set("xmlns", "http://docs.oasis-open.org/legaldocml/ns/akn/3.0")

        # act 요소
        act = ET.SubElement(akomantoso, "act")
        act.set("name", self._get_act_name(parsed.law_type))

        # meta 섹션
        meta = ET.SubElement(act, "meta")
        self._add_identification(meta, parsed)

        # preamble 섹션
        if parsed.preamble or parsed.considerations or parsed.legal_basis:
            preamble = ET.SubElement(act, "preamble")

            if parsed.preamble:
                formula = ET.SubElement(preamble, "formula")
                formula.set("name", "enactingFormula")
                p = ET.SubElement(formula, "p")
                p.text = parsed.preamble

            if parsed.considerations:
                recitals = ET.SubElement(preamble, "recitals")
                recitals.set("eId", "recs")
                for i, cons in enumerate(parsed.considerations, 1):
                    recital = ET.SubElement(recitals, "recital")
                    recital.set("eId", f"recs__rec_{i}")
                    p = ET.SubElement(recital, "p")
                    p.text = cons

            if parsed.legal_basis:
                citations = ET.SubElement(preamble, "citations")
                citations.set("eId", "cits")
                for i, basis in enumerate(parsed.legal_basis, 1):
                    citation = ET.SubElement(citations, "citation")
                    citation.set("eId", f"cits__cit_{i}")
                    p = ET.SubElement(citation, "p")
                    p.text = basis

        # body 섹션
        body = ET.SubElement(act, "body")

        for i, article_text in enumerate(parsed.articles, 1):
            article = ET.SubElement(body, "article")
            article.set("eId", f"art_{i}")

            # Pasal 번호 추출
            match = re.match(r"^(PASAL\s+\d+)", article_text, re.IGNORECASE)
            if match:
                num = ET.SubElement(article, "num")
                num.text = match.group(1)

            # 내용
            content = ET.SubElement(article, "content")
            p = ET.SubElement(content, "p")
            # Pasal 번호 이후 내용
            article_content = re.sub(r"^PASAL\s+\d+\.?\s*", "", article_text, flags=re.IGNORECASE)
            p.text = article_content.strip() if article_content.strip() else article_text

        # XML 포맷팅
        xml_str = ET.tostring(akomantoso, encoding="unicode")
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent="  ")

    def _get_act_name(self, law_type: str) -> str:
        """법령 유형에 따른 act name"""
        type_map = {
            "UNDANG-UNDANG": "act",
            "PERATURAN PEMERINTAH": "regulation",
            "PERATURAN PRESIDEN": "presidentialRegulation",
            "PERATURAN MENTERI": "ministerialRegulation",
        }
        for key, value in type_map.items():
            if key in law_type:
                return value
        return "act"

    def _add_identification(self, meta: ET.Element, parsed: ParsedLaw):
        """identification 메타데이터 추가"""
        identification = ET.SubElement(meta, "identification")
        identification.set("source", "#source")

        # FRBRWork
        work = ET.SubElement(identification, "FRBRWork")

        uri = f"/id/{self._get_act_name(parsed.law_type)}/{parsed.year}/{parsed.number}"

        this = ET.SubElement(work, "FRBRthis")
        this.set("value", uri)

        uri_el = ET.SubElement(work, "FRBRuri")
        uri_el.set("value", uri)

        date = ET.SubElement(work, "FRBRdate")
        date.set("date", parsed.year)
        date.set("name", "enacted")

        author = ET.SubElement(work, "FRBRauthor")
        author.set("href", "#source")

        country = ET.SubElement(work, "FRBRcountry")
        country.set("value", "id")

        number = ET.SubElement(work, "FRBRnumber")
        number.set("value", parsed.number)


class PDFToXMLViewer:
    """PDF to XML 뷰어"""

    def __init__(self):
        self.temp_dir = tempfile.mkdtemp()
        self.current_pdf = None
        self.page_count = 0
        self.parser = LawParser()
        self.xml_generator = AkomaNtosoGenerator()

    def load_pdf(self, pdf_file) -> tuple:
        if pdf_file is None:
            return None, "PDF 파일을 업로드하세요", "", "", 1

        try:
            pdf_path = pdf_file.name if hasattr(pdf_file, 'name') else pdf_file
            self.current_pdf = fitz.open(pdf_path)
            self.page_count = len(self.current_pdf)

            first_page_img = self._render_page(0)

            info = f"""
### 📕 {Path(pdf_path).name}
- 페이지: {self.page_count}
- 크기: {self.current_pdf[0].rect.width:.0f} x {self.current_pdf[0].rect.height:.0f}
            """

            return first_page_img, info, "", "", self.page_count

        except Exception as e:
            return None, f"❌ 오류: {str(e)}", "", "", 1

    def _render_page(self, page_num: int, dpi: int = 150) -> Optional[str]:
        if self.current_pdf is None or page_num >= self.page_count:
            return None

        page = self.current_pdf[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)

        output_path = os.path.join(self.temp_dir, f"page_{page_num}.png")
        pix.save(output_path)
        return output_path

    def get_page(self, page_num: int) -> Optional[str]:
        if self.current_pdf is None:
            return None
        if page_num < 1 or page_num > self.page_count:
            return None
        return self._render_page(page_num - 1)

    def process_page(self, page_num: int) -> tuple[str, str, str]:
        """페이지 OCR → 파싱 → XML"""
        if self.current_pdf is None:
            return "PDF를 업로드하세요", "", ""

        # OCR
        page_img = self._render_page(page_num - 1, dpi=200)
        if not page_img:
            return "렌더링 실패", "", ""

        try:
            ocr = get_ocr()
            result = ocr.predict(page_img)

            if not result or not result[0]:
                return "텍스트 인식 실패", "", ""

            res = result[0]
            texts = res['rec_texts']
            scores = res['rec_scores']

            # 파싱
            parsed = self.parser.parse(texts)

            # 메타데이터 요약
            meta_summary = self._format_metadata(parsed, scores)

            # XML 생성
            xml_output = self.xml_generator.generate(parsed)

            # OCR 원본
            ocr_lines = []
            for i, (text, score) in enumerate(zip(texts, scores), 1):
                ocr_lines.append(f"{i:2}. [{score*100:5.1f}%] {text}")
            ocr_output = "\n".join(ocr_lines)

            return meta_summary, ocr_output, xml_output

        except Exception as e:
            return f"오류: {str(e)}", "", ""

    def _format_metadata(self, parsed: ParsedLaw, scores: list) -> str:
        """메타데이터 포맷팅"""
        avg_conf = sum(scores) / len(scores) if scores else 0

        lines = [
            "## 📋 파싱 결과",
            "",
            "### 메타데이터",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| 법령유형 | **{parsed.law_type or 'N/A'}** |",
            f"| 번호 | **{parsed.number or 'N/A'}** |",
            f"| 연도 | **{parsed.year or 'N/A'}** |",
            f"| 제목 | {parsed.title[:50]}{'...' if len(parsed.title) > 50 else ''} |",
            "",
            "### 구조 분석",
            f"| 섹션 | 수 |",
            f"|------|-----|",
            f"| 노이즈 (제거됨) | {len(parsed.noise_lines)} |",
            f"| Menimbang (고려) | {len(parsed.considerations)} |",
            f"| Mengingat (근거) | {len(parsed.legal_basis)} |",
            f"| Pasal (조항) | {len(parsed.articles)} |",
            "",
            f"### 품질",
            f"- OCR 신뢰도: **{avg_conf*100:.1f}%**",
            f"- 인식 라인: {len(scores)}개",
        ]

        if parsed.noise_lines:
            lines.append("")
            lines.append("### 🗑️ 제거된 노이즈")
            for noise in parsed.noise_lines[:5]:
                lines.append(f"- `{noise}`")

        return "\n".join(lines)


def create_app():
    """Gradio 앱"""
    viewer = PDFToXMLViewer()

    with gr.Blocks(title="PDF → Akoma Ntoso XML") as app:
        gr.Markdown("# 📜 PDF → Akoma Ntoso XML 변환기")
        gr.Markdown("인도네시아 법령 PDF를 업로드하면 OCR → 구조 파싱 → XML 변환")

        # 상단: 파일 업로드 + 메타데이터
        with gr.Row():
            with gr.Column(scale=1):
                pdf_upload = gr.File(
                    label="📁 PDF 업로드",
                    file_types=[".pdf"],
                    type="filepath"
                )
                pdf_info = gr.Markdown("PDF를 업로드하세요")

                with gr.Row():
                    page_num = gr.Number(label="페이지", value=1, minimum=1, precision=0)
                    max_page = gr.Number(label="전체", value=1, interactive=False, precision=0)

                with gr.Row():
                    prev_btn = gr.Button("◀ 이전", size="sm")
                    next_btn = gr.Button("다음 ▶", size="sm")

                process_btn = gr.Button("🔄 OCR + 파싱 + XML 변환", variant="primary")

            with gr.Column(scale=2):
                meta_output = gr.Markdown("파싱 결과가 여기에 표시됩니다")

        # 중간: PDF | OCR | XML
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 📕 PDF")
                pdf_image = gr.Image(label="", type="filepath")

            with gr.Column(scale=1):
                gr.Markdown("### 🔍 OCR 원본")
                ocr_output = gr.Textbox(label="", lines=20, max_lines=30, interactive=False)

            with gr.Column(scale=1):
                gr.Markdown("### 📄 Akoma Ntoso XML")
                xml_output = gr.Textbox(label="", lines=20, max_lines=30, interactive=False)

        # === 이벤트 ===
        def on_upload(pdf_file):
            img, info, meta, ocr, xml = viewer.load_pdf(pdf_file)
            max_p = viewer.page_count
            return img, info, meta, ocr, xml, 1, max_p

        def on_prev(page, max_p):
            new_page = max(1, int(page) - 1)
            img = viewer.get_page(new_page)
            return img, new_page

        def on_next(page, max_p):
            new_page = min(int(max_p), int(page) + 1)
            img = viewer.get_page(new_page)
            return img, new_page

        def on_page_change(page):
            return viewer.get_page(int(page))

        def on_process(page):
            meta, ocr, xml = viewer.process_page(int(page))
            return meta, ocr, xml

        pdf_upload.change(
            on_upload,
            inputs=[pdf_upload],
            outputs=[pdf_image, pdf_info, meta_output, ocr_output, xml_output, page_num, max_page]
        )

        prev_btn.click(on_prev, inputs=[page_num, max_page], outputs=[pdf_image, page_num])
        next_btn.click(on_next, inputs=[page_num, max_page], outputs=[pdf_image, page_num])
        page_num.change(on_page_change, inputs=[page_num], outputs=[pdf_image])
        process_btn.click(on_process, inputs=[page_num], outputs=[meta_output, ocr_output, xml_output])

    return app


def launch(port: int = 7863, share: bool = False):
    app = create_app()
    app.launch(server_port=port, share=share)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=7863)
    parser.add_argument("--share", "-s", action="store_true")
    args = parser.parse_args()

    print("🚀 PDF → XML Viewer 시작...")
    print(f"   포트: {args.port}")
    launch(port=args.port, share=args.share)
