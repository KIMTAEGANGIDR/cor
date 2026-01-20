"""
ILIS OCR Pipeline - Triple Column Viewer

왼쪽: PDF 원본 | 중간: PyMuPDF 텍스트 블록 (스타일 정보) | 오른쪽: OCR 결과
"""

import os
import tempfile
from pathlib import Path
from typing import Optional

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


class TripleViewer:
    """PDF | PyMuPDF 텍스트 | OCR 3열 비교 뷰어"""

    def __init__(self):
        self.temp_dir = tempfile.mkdtemp()
        self.current_pdf = None
        self.current_pdf_path = None
        self.page_count = 0

    def load_pdf(self, pdf_file) -> tuple:
        """PDF 로드"""
        if pdf_file is None:
            return None, "PDF를 업로드하세요", "", "", 1, 1

        try:
            pdf_path = pdf_file.name if hasattr(pdf_file, 'name') else pdf_file
            self.current_pdf = fitz.open(pdf_path)
            self.current_pdf_path = pdf_path
            self.page_count = len(self.current_pdf)

            # 첫 페이지 렌더링
            first_img = self._render_page(0)

            # 첫 페이지 분석
            xml_html, ocr_html = self._analyze_page(0)

            info = f"📕 **{Path(pdf_path).name}** | {self.page_count}페이지"

            return first_img, info, xml_html, ocr_html, 1, self.page_count

        except Exception as e:
            return None, f"❌ 오류: {str(e)}", "", "", 1, 1

    def _render_page(self, page_num: int, dpi: int = 150) -> Optional[str]:
        """PDF 페이지 → 이미지"""
        if self.current_pdf is None or page_num >= self.page_count:
            return None

        page = self.current_pdf[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)

        output_path = os.path.join(self.temp_dir, f"page_{page_num}.png")
        pix.save(output_path)
        return output_path

    def _analyze_page(self, page_num: int) -> tuple[str, str]:
        """페이지 분석: PyMuPDF 텍스트 블록 + OCR"""
        if self.current_pdf is None:
            return "", ""

        # PyMuPDF 텍스트 블록 추출
        xml_html = self._extract_text_blocks(page_num)

        # OCR 실행
        page_img = self._render_page(page_num, dpi=200)
        ocr_html = self._run_ocr(page_img) if page_img else ""

        return xml_html, ocr_html

    def _extract_text_blocks(self, page_num: int) -> str:
        """PyMuPDF로 텍스트 블록 추출 (스타일 정보 포함)"""
        page = self.current_pdf[page_num]

        # dict 형식으로 추출 (폰트, 크기, 색상 정보 포함)
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        html_parts = []
        html_parts.append('<div style="font-family: monospace; font-size: 12px; line-height: 1.4;">')
        html_parts.append(f'<div style="background: #e3f2fd; padding: 8px; margin-bottom: 10px; border-radius: 4px;">')
        html_parts.append(f'<b>📄 페이지 {page_num + 1}</b> | ')
        html_parts.append(f'크기: {page.rect.width:.0f} x {page.rect.height:.0f}')
        html_parts.append('</div>')

        block_num = 0
        for block in blocks.get("blocks", []):
            if block.get("type") == 0:  # 텍스트 블록
                block_num += 1
                bbox = block.get("bbox", [0, 0, 0, 0])

                html_parts.append(f'<div style="border: 1px solid #ddd; margin: 5px 0; padding: 8px; border-radius: 4px; background: #fafafa;">')
                html_parts.append(f'<div style="color: #666; font-size: 10px; margin-bottom: 5px;">')
                html_parts.append(f'Block #{block_num} | y={bbox[1]:.0f}~{bbox[3]:.0f}')
                html_parts.append('</div>')

                for line in block.get("lines", []):
                    line_bbox = line.get("bbox", [0, 0, 0, 0])

                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if not text:
                            continue

                        font = span.get("font", "unknown")
                        size = span.get("size", 12)
                        flags = span.get("flags", 0)
                        color = span.get("color", 0)

                        # 플래그 해석
                        is_bold = flags & 2 ** 4  # bit 4 = bold
                        is_italic = flags & 2 ** 1  # bit 1 = italic
                        is_mono = flags & 2 ** 3  # bit 3 = monospace

                        # 스타일 태그 생성
                        style_tags = []
                        if is_bold:
                            style_tags.append("B")
                        if is_italic:
                            style_tags.append("I")
                        if is_mono:
                            style_tags.append("M")

                        # 크기 기반 스타일
                        if size >= 14:
                            style_tags.append("H")  # Heading
                        elif size <= 9:
                            style_tags.append("S")  # Small

                        # 들여쓰기 계산 (x 좌표 기반)
                        indent = int((span.get("bbox", [0])[0] - bbox[0]) / 20)
                        indent_str = "&nbsp;" * (indent * 4)

                        # HTML 스타일
                        css_styles = [f"font-size: {min(size, 16):.0f}px"]
                        if is_bold:
                            css_styles.append("font-weight: bold")
                        if is_italic:
                            css_styles.append("font-style: italic")
                        if is_mono:
                            css_styles.append("font-family: monospace")

                        # 색상 변환 (정수 → hex)
                        if color != 0:
                            hex_color = f"#{color:06x}"
                            css_styles.append(f"color: {hex_color}")

                        style_str = "; ".join(css_styles)
                        tags_str = f'<span style="background: #ffeb3b; font-size: 9px; padding: 1px 3px; border-radius: 2px; margin-right: 4px;">{",".join(style_tags)}</span>' if style_tags else ""

                        # 폰트 정보
                        font_info = f'<span style="color: #999; font-size: 9px;">[{font[:15]} {size:.0f}pt]</span>'

                        html_parts.append(f'<div style="margin: 2px 0;">')
                        html_parts.append(f'{indent_str}{tags_str}<span style="{style_str}">{self._escape_html(text)}</span> {font_info}')
                        html_parts.append('</div>')

                html_parts.append('</div>')

        html_parts.append('</div>')
        return "\n".join(html_parts)

    def _run_ocr(self, image_path: str) -> str:
        """PaddleOCR 실행"""
        if not image_path or not Path(image_path).exists():
            return "<p>이미지를 찾을 수 없습니다</p>"

        try:
            ocr = get_ocr()
            result = ocr.predict(image_path)

            if not result or not result[0]:
                return "<p>텍스트 인식 실패</p>"

            res = result[0]
            texts = res['rec_texts']
            scores = res['rec_scores']
            boxes = res.get('dt_polys', [])

            html_parts = []
            html_parts.append('<div style="font-family: monospace; font-size: 12px; line-height: 1.6;">')

            # 통계
            avg_score = sum(scores) / len(scores) if scores else 0
            html_parts.append(f'<div style="background: #e8f5e9; padding: 8px; margin-bottom: 10px; border-radius: 4px;">')
            html_parts.append(f'<b>🔍 OCR 결과</b> | {len(texts)}줄 | 평균 신뢰도: {avg_score*100:.1f}%')
            html_parts.append('</div>')

            for i, (text, score) in enumerate(zip(texts, scores), 1):
                # 신뢰도에 따른 색상
                if score >= 0.95:
                    bg_color = "#e8f5e9"  # 녹색
                    score_color = "#2e7d32"
                elif score >= 0.8:
                    bg_color = "#fff8e1"  # 노란색
                    score_color = "#f57f17"
                else:
                    bg_color = "#ffebee"  # 빨간색
                    score_color = "#c62828"

                # 신뢰도 바
                bar_width = int(score * 100)
                bar = f'<div style="width: {bar_width}px; height: 4px; background: {score_color}; border-radius: 2px; display: inline-block;"></div>'

                # 텍스트 분류
                category = self._classify_text(text)

                html_parts.append(f'<div style="background: {bg_color}; padding: 6px 10px; margin: 3px 0; border-radius: 4px; border-left: 3px solid {score_color};">')
                html_parts.append(f'<div style="display: flex; justify-content: space-between; align-items: center;">')
                html_parts.append(f'<span style="color: #666; font-size: 10px;">#{i}</span>')
                html_parts.append(f'<span style="color: {score_color}; font-size: 10px; font-weight: bold;">{score*100:.0f}%</span>')
                html_parts.append('</div>')
                html_parts.append(f'<div style="margin: 4px 0;"><b>{self._escape_html(text)}</b></div>')
                html_parts.append(f'<div style="color: #666; font-size: 10px;">{category}</div>')
                html_parts.append('</div>')

            # 전체 텍스트
            html_parts.append('<div style="margin-top: 15px; padding: 10px; background: #f5f5f5; border-radius: 4px;">')
            html_parts.append('<b>📋 전체 텍스트</b><hr style="margin: 5px 0;">')
            html_parts.append(f'<div style="white-space: pre-wrap;">{self._escape_html(chr(10).join(texts))}</div>')
            html_parts.append('</div>')

            html_parts.append('</div>')
            return "\n".join(html_parts)

        except Exception as e:
            return f"<p style='color: red;'>OCR 오류: {str(e)}</p>"

    def _classify_text(self, text: str) -> str:
        """텍스트 자동 분류"""
        t = text.upper().strip()

        if any(x in t for x in ['WWW.', '.GO.ID', 'SALINAN', 'SK NO']):
            return "🗑️ 노이즈"
        if t in ['PRESIDEN', 'REPUBLIK INDONESIA']:
            return "🗑️ 노이즈"
        if t.isdigit() and len(t) <= 3:
            return "🔢 페이지번호"

        if any(x in t for x in ['UNDANG-UNDANG', 'PERATURAN']):
            return "📋 법령유형"
        if 'NOMOR' in t and 'TAHUN' in t:
            return "📋 번호/연도"

        if t == 'TENTANG':
            return "▶️ 본문시작"
        if 'DENGAN RAHMAT' in t:
            return "📄 서두"
        if t.startswith('MENIMBANG'):
            return "📄 고려사항"
        if t.startswith('MENGINGAT'):
            return "📄 법적근거"
        if t.startswith('PASAL '):
            return "📄 조항"
        if t.startswith('BAB '):
            return "📄 장"

        return "📝 본문"

    def _escape_html(self, text: str) -> str:
        """HTML 이스케이프"""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

    def change_page(self, page_num: int) -> tuple:
        """페이지 변경"""
        if self.current_pdf is None:
            return None, "", ""

        page_num = max(1, min(page_num, self.page_count))
        page_idx = page_num - 1

        img = self._render_page(page_idx)
        xml_html, ocr_html = self._analyze_page(page_idx)

        return img, xml_html, ocr_html


def create_app():
    """Gradio 앱 생성"""
    viewer = TripleViewer()

    # 커스텀 CSS
    custom_css = """
    .triple-column {
        min-height: 600px;
    }
    .gradio-container {
        max-width: 100% !important;
    }
    """

    with gr.Blocks(title="PDF vs XML vs OCR", css=custom_css) as app:
        gr.Markdown("# 📊 PDF | PyMuPDF | OCR 3열 비교 뷰어")

        # 상단: 파일 업로드 + 페이지 네비게이션
        with gr.Row():
            pdf_upload = gr.File(
                label="📁 PDF 업로드",
                file_types=[".pdf"],
                type="filepath",
                scale=2
            )
            with gr.Column(scale=1):
                pdf_info = gr.Markdown("PDF를 업로드하세요")
                with gr.Row():
                    prev_btn = gr.Button("◀ 이전", size="sm")
                    page_num = gr.Number(label="페이지", value=1, minimum=1, precision=0, scale=1)
                    page_max = gr.Number(label="/전체", value=1, interactive=False, precision=0, scale=1)
                    next_btn = gr.Button("다음 ▶", size="sm")

        # 메인: 3열 레이아웃
        with gr.Row(equal_height=True):
            with gr.Column(scale=1):
                gr.Markdown("### 📕 PDF 원본")
                pdf_image = gr.Image(label="", type="filepath", height=700)

            with gr.Column(scale=1):
                gr.Markdown("### 📝 PyMuPDF 텍스트 블록")
                xml_output = gr.HTML(label="", elem_classes=["triple-column"])

            with gr.Column(scale=1):
                gr.Markdown("### 🔍 PaddleOCR 결과")
                ocr_output = gr.HTML(label="", elem_classes=["triple-column"])

        # === 이벤트 핸들러 ===
        def on_upload(pdf_file):
            img, info, xml_html, ocr_html, page, max_p = viewer.load_pdf(pdf_file)
            return img, info, xml_html, ocr_html, page, max_p

        def on_page_change(page):
            img, xml_html, ocr_html = viewer.change_page(int(page))
            return img, xml_html, ocr_html

        def on_prev(page, max_p):
            new_page = max(1, int(page) - 1)
            img, xml_html, ocr_html = viewer.change_page(new_page)
            return img, xml_html, ocr_html, new_page

        def on_next(page, max_p):
            new_page = min(int(max_p), int(page) + 1)
            img, xml_html, ocr_html = viewer.change_page(new_page)
            return img, xml_html, ocr_html, new_page

        # 이벤트 연결
        pdf_upload.change(
            on_upload,
            inputs=[pdf_upload],
            outputs=[pdf_image, pdf_info, xml_output, ocr_output, page_num, page_max]
        )

        page_num.submit(
            on_page_change,
            inputs=[page_num],
            outputs=[pdf_image, xml_output, ocr_output]
        )

        prev_btn.click(
            on_prev,
            inputs=[page_num, page_max],
            outputs=[pdf_image, xml_output, ocr_output, page_num]
        )

        next_btn.click(
            on_next,
            inputs=[page_num, page_max],
            outputs=[pdf_image, xml_output, ocr_output, page_num]
        )

    return app


def launch(port: int = 7864, share: bool = False):
    """뷰어 실행"""
    app = create_app()
    app.launch(server_port=port, share=share)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=7864)
    parser.add_argument("--share", "-s", action="store_true")
    args = parser.parse_args()

    print("🚀 Triple Viewer (PDF | XML | OCR) 시작...")
    print(f"   포트: {args.port}")
    launch(port=args.port, share=args.share)
