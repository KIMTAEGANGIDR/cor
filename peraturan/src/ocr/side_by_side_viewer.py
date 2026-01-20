"""
ILIS OCR Pipeline - Side-by-Side Viewer

좌측: PDF 원본 | 우측: OCR 파싱 결과
"""

import os
import tempfile
from pathlib import Path
from typing import Optional
import sqlite3

import fitz  # PyMuPDF
import gradio as gr
from paddleocr import PaddleOCR

os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

# PaddleOCR 싱글톤
_ocr = None

def get_ocr():
    global _ocr
    if _ocr is None:
        print("PaddleOCR 초기화 중...")
        _ocr = PaddleOCR(lang='id')
    return _ocr


class SideBySideViewer:
    """PDF vs OCR 비교 뷰어"""

    def __init__(self, db_path: str = "peraturan/data/ocr_pipeline.db"):
        self.db_path = db_path
        self.temp_dir = tempfile.mkdtemp()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_clusters(self) -> list[tuple]:
        """클러스터 목록"""
        conn = self.get_connection()
        rows = conn.execute("""
            SELECT id, name, description, document_count
            FROM clusters ORDER BY document_count DESC
        """).fetchall()
        conn.close()
        return [(f"#{r['id']} - {r['description']} ({r['document_count']}개)", r['id']) for r in rows]

    def get_documents_by_cluster(self, cluster_id: int) -> list[tuple]:
        """클러스터 내 문서 목록"""
        conn = self.get_connection()
        rows = conn.execute("""
            SELECT d.id, d.jenis, d.nomor, d.tahun, d.pdf_path
            FROM documents d
            JOIN headers h ON d.id = h.document_id
            WHERE h.cluster_id = ?
            ORDER BY d.tahun DESC, d.nomor
            LIMIT 50
        """, (cluster_id,)).fetchall()
        conn.close()
        return [(f"{r['id']} | {r['jenis']} {r['nomor']} ({r['tahun']})", r['id']) for r in rows]

    def get_document(self, doc_id: str) -> Optional[dict]:
        """문서 정보"""
        conn = self.get_connection()
        row = conn.execute("""
            SELECT d.id, d.jenis, d.nomor, d.tahun, d.tentang, d.pdf_path,
                   h.image_path, h.raw_text, h.cluster_id
            FROM documents d
            LEFT JOIN headers h ON d.id = h.document_id
            WHERE d.id = ?
        """, (doc_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def pdf_to_image(self, pdf_path: str, page_num: int = 0, dpi: int = 150) -> Optional[str]:
        """PDF 페이지를 이미지로 변환"""
        if not pdf_path or not Path(pdf_path).exists():
            return None

        try:
            doc = fitz.open(pdf_path)
            if page_num >= len(doc):
                page_num = 0

            page = doc[page_num]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)

            output_path = os.path.join(self.temp_dir, f"page_{page_num}.png")
            pix.save(output_path)
            doc.close()

            return output_path
        except Exception as e:
            print(f"PDF 변환 오류: {e}")
            return None

    def run_ocr(self, image_path: str) -> str:
        """OCR 실행 및 포맷팅"""
        if not image_path or not Path(image_path).exists():
            return "이미지를 찾을 수 없습니다"

        try:
            ocr = get_ocr()
            result = ocr.predict(image_path)

            if result and result[0]:
                res = result[0]
                texts = res['rec_texts']
                scores = res['rec_scores']

                lines = []
                lines.append("=" * 50)
                lines.append("📝 OCR 파싱 결과 (PaddleOCR)")
                lines.append("=" * 50)
                lines.append("")

                for i, (text, score) in enumerate(zip(texts, scores), 1):
                    # 분류 추정
                    category = self._guess_category(text, i)
                    bar = '█' * int(score * 10) + '░' * (10 - int(score * 10))
                    lines.append(f"{i:2}. [{bar}] {score*100:5.1f}%")
                    lines.append(f"    {category}: {text}")
                    lines.append("")

                lines.append("-" * 50)
                avg = sum(scores) / len(scores) if scores else 0
                lines.append(f"✅ 총 {len(texts)}개 라인 | 평균 신뢰도: {avg*100:.1f}%")

                return "\n".join(lines)

            return "텍스트를 인식하지 못했습니다"
        except Exception as e:
            return f"OCR 오류: {str(e)}"

    def _guess_category(self, text: str, line_num: int) -> str:
        """텍스트 분류 추정"""
        text_upper = text.upper()

        # 노이즈 패턴
        if any(x in text_upper for x in ['WWW.', '.GO.ID', 'SALINAN']):
            return "🗑️ 노이즈"
        if text_upper in ['PRESIDEN', 'REPUBLIK INDONESIA']:
            return "🗑️ 노이즈"

        # 메타데이터 패턴
        if 'UNDANG-UNDANG' in text_upper or 'PERATURAN' in text_upper:
            return "📋 메타-법령유형"
        if 'NOMOR' in text_upper and 'TAHUN' in text_upper:
            return "📋 메타-번호/연도"

        # 본문 시작
        if text_upper == 'TENTANG':
            return "▶️ 본문시작"

        # 본문
        if 'DENGAN RAHMAT' in text_upper:
            return "📄 본문-서두"
        if 'MENIMBANG' in text_upper:
            return "📄 본문-고려"
        if 'MENGINGAT' in text_upper:
            return "📄 본문-근거"
        if 'MEMUTUSKAN' in text_upper:
            return "📄 본문-결정"
        if text_upper.startswith('PASAL'):
            return "📄 본문-조항"

        return "📄 본문"


def create_app():
    """Gradio 앱 생성"""
    viewer = SideBySideViewer()

    with gr.Blocks(title="PDF vs OCR 비교 뷰어") as app:
        gr.Markdown("# 📑 PDF vs OCR 비교 뷰어")
        gr.Markdown("좌측: PDF 원본 | 우측: OCR 파싱 결과")

        with gr.Row():
            with gr.Column(scale=1):
                cluster_dropdown = gr.Dropdown(
                    label="클러스터 선택",
                    choices=viewer.get_clusters(),
                    type="index"
                )
                doc_dropdown = gr.Dropdown(
                    label="문서 선택",
                    choices=[],
                    type="value"
                )
                page_slider = gr.Slider(
                    label="페이지",
                    minimum=1,
                    maximum=10,
                    step=1,
                    value=1
                )
                load_btn = gr.Button("📄 불러오기", variant="primary")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 📕 PDF 원본")
                pdf_image = gr.Image(label="PDF 페이지", type="filepath")

            with gr.Column(scale=1):
                gr.Markdown("### 🔍 OCR 파싱 결과")
                ocr_output = gr.Textbox(
                    label="",
                    lines=25,
                    max_lines=40,
                    interactive=False
                )

        with gr.Row():
            doc_info = gr.Markdown("문서를 선택하세요")

        # === 이벤트 핸들러 ===

        def on_cluster_change(cluster_idx):
            if cluster_idx is None:
                return gr.update(choices=[])
            clusters = viewer.get_clusters()
            cluster_id = clusters[cluster_idx][1]
            docs = viewer.get_documents_by_cluster(cluster_id)
            return gr.update(choices=[d[0] for d in docs], value=docs[0][0] if docs else None)

        def on_load(doc_selection, page_num):
            if not doc_selection:
                return None, "문서를 선택하세요", "문서를 선택하세요"

            doc_id = doc_selection.split(" | ")[0]
            doc = viewer.get_document(doc_id)

            if not doc:
                return None, "문서를 찾을 수 없습니다", "문서를 찾을 수 없습니다"

            # 문서 정보
            info = f"""
### 📋 문서 정보
- **ID**: {doc['id']}
- **유형**: {doc['jenis']}
- **번호/연도**: {doc['nomor']} / {doc['tahun']}
- **제목**: {doc.get('tentang', 'N/A')}
- **클러스터**: #{doc.get('cluster_id', 'N/A')}
            """

            # PDF → 이미지
            pdf_path = doc.get('pdf_path')
            page_img = viewer.pdf_to_image(pdf_path, page_num - 1) if pdf_path else None

            # OCR 실행 (헤더 이미지 또는 PDF 첫 페이지)
            if page_num == 1 and doc.get('image_path') and Path(doc['image_path']).exists():
                # 헤더 이미지로 OCR
                ocr_result = viewer.run_ocr(doc['image_path'])
            elif page_img:
                # PDF 페이지로 OCR
                ocr_result = viewer.run_ocr(page_img)
            else:
                ocr_result = "이미지를 찾을 수 없습니다"

            return page_img, ocr_result, info

        cluster_dropdown.change(on_cluster_change, inputs=[cluster_dropdown], outputs=[doc_dropdown])
        load_btn.click(on_load, inputs=[doc_dropdown, page_slider], outputs=[pdf_image, ocr_output, doc_info])

    return app


def launch(port: int = 7861, share: bool = False):
    """뷰어 실행"""
    app = create_app()
    app.launch(server_port=port, share=share)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=7861)
    parser.add_argument("--share", "-s", action="store_true")
    args = parser.parse_args()

    print("🚀 Side-by-Side Viewer 시작...")
    print(f"   포트: {args.port}")
    launch(port=args.port, share=args.share)
