"""
ILIS OCR Pipeline - PDF Parser Viewer

PDF 파일 업로드 → 페이지별 OCR 파싱
좌측: PDF 페이지 | 우측: OCR 결과
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


class PDFParserViewer:
    """PDF 업로드 → 페이지별 OCR 파싱 뷰어"""

    def __init__(self):
        self.temp_dir = tempfile.mkdtemp()
        self.current_pdf = None
        self.current_pdf_path = None
        self.page_count = 0

    def load_pdf(self, pdf_file) -> tuple[Optional[str], str, int]:
        """PDF 로드"""
        if pdf_file is None:
            return None, "PDF 파일을 업로드하세요", 1

        try:
            # Gradio가 전달하는 파일 경로
            pdf_path = pdf_file.name if hasattr(pdf_file, 'name') else pdf_file

            self.current_pdf = fitz.open(pdf_path)
            self.current_pdf_path = pdf_path
            self.page_count = len(self.current_pdf)

            # 첫 페이지 이미지
            first_page_img = self._render_page(0)

            info = f"""
### 📕 PDF 정보
- **파일명**: {Path(pdf_path).name}
- **페이지 수**: {self.page_count}
- **크기**: {self.current_pdf[0].rect.width:.0f} x {self.current_pdf[0].rect.height:.0f}
            """

            return first_page_img, info, self.page_count

        except Exception as e:
            return None, f"❌ PDF 로드 오류: {str(e)}", 1

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

    def get_page(self, page_num: int) -> tuple[Optional[str], str]:
        """특정 페이지 가져오기"""
        if self.current_pdf is None:
            return None, "PDF를 먼저 업로드하세요"

        if page_num < 1 or page_num > self.page_count:
            return None, f"페이지 범위: 1 ~ {self.page_count}"

        page_img = self._render_page(page_num - 1)
        return page_img, f"📄 페이지 {page_num} / {self.page_count}"

    def run_ocr(self, page_num: int) -> str:
        """페이지 OCR 실행"""
        if self.current_pdf is None:
            return "PDF를 먼저 업로드하세요"

        page_img = self._render_page(page_num - 1, dpi=200)  # OCR용 고해상도
        if not page_img:
            return "페이지 렌더링 실패"

        try:
            ocr = get_ocr()
            result = ocr.predict(page_img)

            if not result or not result[0]:
                return "텍스트를 인식하지 못했습니다"

            res = result[0]
            texts = res['rec_texts']
            scores = res['rec_scores']

            lines = []
            lines.append("=" * 55)
            lines.append(f"📝 페이지 {page_num} OCR 결과")
            lines.append("=" * 55)
            lines.append("")

            for i, (text, score) in enumerate(zip(texts, scores), 1):
                category = self._classify_text(text)
                bar = '█' * int(score * 10) + '░' * (10 - int(score * 10))
                lines.append(f"{i:2}. [{bar}] {score*100:5.1f}% | {category}")
                lines.append(f"    {text}")
                lines.append("")

            lines.append("-" * 55)
            avg = sum(scores) / len(scores) if scores else 0
            lines.append(f"✅ {len(texts)}개 라인 | 평균 신뢰도: {avg*100:.1f}%")
            lines.append("")
            lines.append("📋 전체 텍스트:")
            lines.append("-" * 55)
            lines.append("\n".join(texts))

            return "\n".join(lines)

        except Exception as e:
            return f"OCR 오류: {str(e)}"

    def _classify_text(self, text: str) -> str:
        """텍스트 자동 분류"""
        t = text.upper().strip()

        # 노이즈
        if any(x in t for x in ['WWW.', '.GO.ID', 'SALINAN', 'SK NO', 'DITJEN']):
            return "🗑️ 노이즈"
        if t in ['PRESIDEN', 'REPUBLIK INDONESIA', 'INDONESIA']:
            return "🗑️ 노이즈"
        if t.isdigit() and len(t) <= 3:
            return "🗑️ 페이지번호"

        # 메타데이터
        if any(x in t for x in ['UNDANG-UNDANG', 'PERATURAN PEMERINTAH', 'PERATURAN PRESIDEN', 'PERATURAN MENTERI']):
            return "📋 법령유형"
        if 'NOMOR' in t and 'TAHUN' in t:
            return "📋 번호/연도"

        # 본문 구조
        if t == 'TENTANG':
            return "▶️ 본문시작"
        if 'DENGAN RAHMAT TUHAN' in t:
            return "📄 서두"
        if t.startswith('MENIMBANG'):
            return "📄 고려사항"
        if t.startswith('MENGINGAT'):
            return "📄 법적근거"
        if t.startswith('MEMUTUSKAN'):
            return "📄 결정"
        if t.startswith('MENETAPKAN'):
            return "📄 제정"
        if t.startswith('BAB '):
            return "📄 장(BAB)"
        if t.startswith('PASAL '):
            return "📄 조(PASAL)"
        if t.startswith('BAGIAN '):
            return "📄 절(BAGIAN)"
        if t.startswith('PARAGRAF '):
            return "📄 관(PARAGRAF)"

        # 일반 본문
        return "📄 본문"


def create_app():
    """Gradio 앱 생성"""
    viewer = PDFParserViewer()

    with gr.Blocks(title="PDF OCR Parser") as app:
        gr.Markdown("# 📄 PDF OCR 파서")
        gr.Markdown("PDF 파일을 업로드하면 페이지별로 OCR 파싱합니다")

        with gr.Row():
            with gr.Column(scale=1):
                pdf_upload = gr.File(
                    label="📁 PDF 파일 업로드",
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

                ocr_btn = gr.Button("🔍 OCR 실행", variant="primary")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 📕 PDF 페이지")
                pdf_image = gr.Image(label="", type="filepath")

            with gr.Column(scale=1):
                gr.Markdown("### 🔍 OCR 결과")
                ocr_output = gr.Textbox(
                    label="",
                    lines=30,
                    max_lines=50,
                    interactive=False
                )

        # === 이벤트 핸들러 ===

        def on_upload(pdf_file):
            img, info, total = viewer.load_pdf(pdf_file)
            return img, info, 1, total

        def on_page_change(page):
            img, status = viewer.get_page(int(page))
            return img

        def on_prev(page, max_p):
            new_page = max(1, int(page) - 1)
            img, _ = viewer.get_page(new_page)
            return img, new_page

        def on_next(page, max_p):
            new_page = min(int(max_p), int(page) + 1)
            img, _ = viewer.get_page(new_page)
            return img, new_page

        def on_ocr(page):
            return viewer.run_ocr(int(page))

        # 이벤트 연결
        pdf_upload.change(
            on_upload,
            inputs=[pdf_upload],
            outputs=[pdf_image, pdf_info, page_num, max_page]
        )

        page_num.change(on_page_change, inputs=[page_num], outputs=[pdf_image])
        prev_btn.click(on_prev, inputs=[page_num, max_page], outputs=[pdf_image, page_num])
        next_btn.click(on_next, inputs=[page_num, max_page], outputs=[pdf_image, page_num])
        ocr_btn.click(on_ocr, inputs=[page_num], outputs=[ocr_output])

    return app


def launch(port: int = 7862, share: bool = False):
    """뷰어 실행"""
    app = create_app()
    app.launch(server_port=port, share=share)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=7862)
    parser.add_argument("--share", "-s", action="store_true")
    args = parser.parse_args()

    print("🚀 PDF OCR Parser 시작...")
    print(f"   포트: {args.port}")
    launch(port=args.port, share=args.share)
