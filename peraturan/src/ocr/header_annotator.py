"""
ILIS OCR Pipeline - Header Annotator (Human-in-the-loop)

목적: 헤더 이미지에서 영역을 직접 지정
- 노이즈 영역 (빨강)
- 메타데이터 영역 (파랑)
- 본문 시작점 (녹색)

사용자가 직접 드래그해서 영역을 그리고 규칙으로 저장
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

import gradio as gr

os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

DB_PATH = "peraturan/data/ocr_pipeline.db"

# 영역 타입별 색상
COLORS = {
    "noise": (255, 100, 100, 128),      # 빨강 (반투명)
    "metadata": (100, 100, 255, 128),   # 파랑 (반투명)
    "body": (100, 255, 100, 128),       # 녹색 (반투명)
}

COLORS_SOLID = {
    "noise": "#ff6464",
    "metadata": "#6464ff",
    "body": "#64ff64",
}


class HeaderAnnotator:
    """헤더 영역 주석 도구"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_clusters(self) -> list[dict]:
        """클러스터 목록"""
        conn = self.get_connection()
        rows = conn.execute("""
            SELECT c.id, c.name, c.description, c.document_count, c.status,
                   (SELECT COUNT(*) FROM pattern_rules pr WHERE pr.cluster_id = c.id AND pr.is_active = 1) as has_rule
            FROM clusters c
            WHERE c.document_count > 0
            ORDER BY c.document_count DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_headers_for_cluster(self, cluster_id: int, limit: int = 50) -> list[dict]:
        """클러스터의 헤더 목록"""
        conn = self.get_connection()
        rows = conn.execute("""
            SELECT h.id, h.document_id, h.image_path, h.raw_text,
                   d.jenis, d.nomor, d.tahun, d.tentang
            FROM headers h
            JOIN documents d ON h.document_id = d.id
            WHERE h.cluster_id = ? AND h.image_path IS NOT NULL
            ORDER BY d.tahun DESC
            LIMIT ?
        """, (cluster_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_header_with_boxes(self, header_id: int) -> tuple[Optional[str], list, Optional[str]]:
        """
        헤더 이미지와 OCR 바운딩 박스 반환

        Returns:
            (image_path, annotations, raw_text)
        """
        conn = self.get_connection()
        row = conn.execute("""
            SELECT h.*, d.jenis, d.nomor, d.tahun, d.tentang
            FROM headers h
            JOIN documents d ON h.document_id = d.id
            WHERE h.id = ?
        """, (header_id,)).fetchone()
        conn.close()

        if not row:
            return None, [], None

        image_path = row["image_path"]
        raw_text = row["raw_text"] or ""

        # OCR 텍스트를 라인별로 나눠서 가상의 영역 생성
        # (실제 바운딩 박스가 없으므로 라인 기반으로 추정)
        annotations = []
        if raw_text:
            lines = raw_text.strip().split('\n')
            img = Image.open(image_path)
            img_width, img_height = img.size

            line_height = img_height / max(len(lines), 1)

            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue

                # 라인별 영역 추정 (y 좌표 기준)
                y1 = int(i * line_height)
                y2 = int((i + 1) * line_height)

                # 기본 분류 (자동)
                label = self._auto_classify_line(line, i)

                annotations.append({
                    "line_num": i + 1,
                    "text": line[:50],
                    "y1": y1,
                    "y2": y2,
                    "label": label
                })

        return image_path, annotations, raw_text

    def _auto_classify_line(self, text: str, line_idx: int) -> str:
        """라인 자동 분류"""
        t = text.upper().strip()

        # 노이즈 패턴
        if t in ['SALINAN', 'PRESIDEN', 'REPUBLIK INDONESIA', 'INDONESIA', 'REPUBLIK']:
            return "noise"
        if 'WWW.' in t or '.GO.ID' in t:
            return "noise"
        if t.isdigit() and len(t) <= 3:
            return "noise"

        # 메타데이터 패턴
        if any(x in t for x in ['UNDANG-UNDANG', 'PERATURAN', 'LEMBARAN NEGARA']):
            return "metadata"
        if 'NOMOR' in t or 'TAHUN' in t:
            return "metadata"
        if t == 'TENTANG':
            return "metadata"

        # 본문 시작 패턴
        if 'DENGAN RAHMAT' in t:
            return "body"

        # 기본값: 상위 4줄은 노이즈 가능성
        if line_idx < 4:
            return "noise"

        return "body"

    def create_annotated_image(self, image_path: str, annotations: list) -> Image.Image:
        """주석이 그려진 이미지 생성"""
        img = Image.open(image_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        img_width = img.size[0]

        for ann in annotations:
            y1 = ann["y1"]
            y2 = ann["y2"]
            label = ann["label"]
            color = COLORS.get(label, (200, 200, 200, 64))

            # 영역 채우기
            draw.rectangle([0, y1, img_width, y2], fill=color)

            # 라인 번호와 텍스트
            text = f"{ann['line_num']}. {ann['text'][:30]}"
            draw.text((5, y1 + 2), text, fill=(0, 0, 0, 255))

        # 오버레이 합성
        result = Image.alpha_composite(img, overlay)
        return result.convert("RGB")

    def save_annotation_rule(self, cluster_id: int, noise_lines: list[int],
                             metadata_lines: list[int], body_start: int,
                             notes: str = "") -> bool:
        """주석 기반 규칙 저장"""
        conn = self.get_connection()
        try:
            # 기존 규칙 비활성화
            conn.execute("""
                UPDATE pattern_rules SET is_active = 0 WHERE cluster_id = ?
            """, (cluster_id,))

            # 새 규칙 저장
            conn.execute("""
                INSERT INTO pattern_rules
                (cluster_id, noise_lines, metadata_lines, body_start_line, notes, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (
                cluster_id,
                json.dumps(noise_lines),
                json.dumps(metadata_lines),
                body_start,
                notes
            ))

            # 클러스터 상태 업데이트
            conn.execute("""
                UPDATE clusters SET status = 'annotated' WHERE id = ?
            """, (cluster_id,))

            conn.commit()
            return True
        except Exception as e:
            print(f"저장 오류: {e}")
            return False
        finally:
            conn.close()


def create_app():
    """Gradio 앱 생성"""
    annotator = HeaderAnnotator()

    with gr.Blocks(title="헤더 영역 주석 도구", theme=gr.themes.Soft()) as app:
        gr.Markdown("# 🎨 헤더 영역 주석 도구")
        gr.Markdown("헤더 이미지에서 **노이즈/메타/본문** 영역을 직접 지정하세요")

        # 상태
        current_cluster_id = gr.State(None)
        current_header_id = gr.State(None)
        current_annotations = gr.State([])

        # ===== 상단: 클러스터/헤더 선택 =====
        with gr.Row():
            cluster_dropdown = gr.Dropdown(
                label="📁 클러스터",
                choices=[],
                scale=2
            )
            header_dropdown = gr.Dropdown(
                label="📄 헤더 선택",
                choices=[],
                scale=3
            )
            refresh_btn = gr.Button("🔄", scale=1)

        # ===== 메인: 이미지 + 라인 목록 =====
        with gr.Row(equal_height=True):
            # 왼쪽: 주석 이미지
            with gr.Column(scale=2):
                gr.Markdown("### 📸 헤더 이미지 (영역 표시)")
                annotated_image = gr.Image(
                    label="",
                    height=500,
                    interactive=False
                )

                # 범례
                with gr.Row():
                    gr.HTML("""
                        <div style="display: flex; gap: 20px; font-size: 14px;">
                            <span style="color: #ff6464;">🔴 노이즈</span>
                            <span style="color: #6464ff;">🔵 메타데이터</span>
                            <span style="color: #64ff64;">🟢 본문</span>
                        </div>
                    """)

            # 오른쪽: 라인별 분류
            with gr.Column(scale=1):
                gr.Markdown("### 📝 라인별 분류")
                gr.Markdown("클릭해서 분류를 변경하세요")

                lines_html = gr.HTML("<p>헤더를 선택하세요</p>")

                gr.Markdown("---")
                gr.Markdown("### ✏️ 수동 지정")

                body_start_slider = gr.Slider(
                    minimum=1,
                    maximum=15,
                    step=1,
                    value=5,
                    label="본문 시작 라인"
                )

                noise_checkboxes = gr.CheckboxGroup(
                    choices=[f"Line {i}" for i in range(1, 11)],
                    label="🗑️ 노이즈로 지정할 라인",
                    value=["Line 1", "Line 2"]
                )

        # ===== 하단: 저장 =====
        with gr.Row():
            notes_input = gr.Textbox(
                label="📋 메모 (선택)",
                placeholder="이 클러스터의 특징을 메모하세요...",
                scale=3
            )
            save_btn = gr.Button("💾 규칙 저장", variant="primary", scale=1)

        save_status = gr.Markdown("")

        # ===== 이벤트 핸들러 =====

        def load_clusters():
            """클러스터 목록 로드"""
            clusters = annotator.get_clusters()
            choices = []
            for c in clusters:
                status_icon = "✅" if c['has_rule'] else "⏳"
                label = f"{status_icon} #{c['id']} - {c['description']} ({c['document_count']}개)"
                choices.append((label, c['id']))
            return gr.update(choices=choices)

        def on_cluster_select(cluster_id):
            """클러스터 선택"""
            if not cluster_id:
                return gr.update(choices=[]), None

            headers = annotator.get_headers_for_cluster(cluster_id)
            choices = [
                (f"{h['jenis']} {h['nomor']} ({h['tahun']})", h['id'])
                for h in headers
            ]
            return gr.update(choices=choices), cluster_id

        def on_header_select(header_id, cluster_id):
            """헤더 선택"""
            if not header_id:
                return None, "<p>헤더를 선택하세요</p>", [], 5, ["Line 1", "Line 2"]

            image_path, annotations, raw_text = annotator.get_header_with_boxes(header_id)

            if not image_path or not Path(image_path).exists():
                return None, "<p>이미지를 찾을 수 없습니다</p>", [], 5, ["Line 1", "Line 2"]

            # 주석 이미지 생성
            annotated_img = annotator.create_annotated_image(image_path, annotations)

            # 라인 목록 HTML
            lines_html_content = create_lines_html(annotations)

            # 기본값 설정
            noise_lines = [f"Line {a['line_num']}" for a in annotations if a['label'] == 'noise']
            body_start = next((a['line_num'] for a in annotations if a['label'] == 'body'), 5)

            return annotated_img, lines_html_content, annotations, body_start, noise_lines

        def create_lines_html(annotations: list) -> str:
            """라인 목록 HTML 생성"""
            if not annotations:
                return "<p>OCR 데이터 없음</p>"

            html = ['<div style="font-size: 13px; max-height: 400px; overflow-y: auto;">']

            for ann in annotations:
                label = ann['label']
                color = COLORS_SOLID.get(label, "#999")
                icon = {"noise": "🗑️", "metadata": "📋", "body": "📝"}.get(label, "❓")

                html.append(f'''
                <div style="padding: 8px; margin: 4px 0; border-left: 4px solid {color};
                            background: {color}22; border-radius: 4px;">
                    <div style="display: flex; justify-content: space-between;">
                        <span><b>Line {ann['line_num']}</b>: {ann['text'][:35]}...</span>
                        <span>{icon}</span>
                    </div>
                </div>
                ''')

            html.append('</div>')
            return '\n'.join(html)

        def update_preview(annotations, body_start, noise_lines, header_id):
            """미리보기 업데이트"""
            if not header_id or not annotations:
                return None

            # 노이즈 라인 번호 추출
            noise_nums = [int(l.split()[-1]) for l in noise_lines]

            # annotations 업데이트
            for ann in annotations:
                line_num = ann['line_num']
                if line_num in noise_nums:
                    ann['label'] = 'noise'
                elif line_num < body_start:
                    ann['label'] = 'metadata'
                else:
                    ann['label'] = 'body'

            # 이미지 다시 생성
            image_path, _, _ = annotator.get_header_with_boxes(header_id)
            if image_path and Path(image_path).exists():
                return annotator.create_annotated_image(image_path, annotations)
            return None

        def save_rule(cluster_id, annotations, body_start, noise_lines, notes):
            """규칙 저장"""
            if not cluster_id:
                return "❌ 클러스터를 선택하세요"

            # 노이즈 라인 번호 (0-indexed)
            noise_nums = [int(l.split()[-1]) - 1 for l in noise_lines]

            # 메타데이터 라인 (노이즈와 본문 사이)
            meta_nums = [i for i in range(body_start - 1) if i not in noise_nums]

            success = annotator.save_annotation_rule(
                cluster_id=cluster_id,
                noise_lines=noise_nums,
                metadata_lines=meta_nums,
                body_start=body_start - 1,  # 0-indexed
                notes=notes or f"Manual annotation"
            )

            if success:
                return f"✅ 클러스터 #{cluster_id} 규칙 저장됨! (노이즈: Line {[n+1 for n in noise_nums]}, 본문: Line {body_start}+)"
            return "❌ 저장 실패"

        # ===== 이벤트 연결 =====

        app.load(load_clusters, outputs=[cluster_dropdown])
        refresh_btn.click(load_clusters, outputs=[cluster_dropdown])

        cluster_dropdown.change(
            on_cluster_select,
            inputs=[cluster_dropdown],
            outputs=[header_dropdown, current_cluster_id]
        )

        header_dropdown.change(
            on_header_select,
            inputs=[header_dropdown, current_cluster_id],
            outputs=[annotated_image, lines_html, current_annotations, body_start_slider, noise_checkboxes]
        )

        # 슬라이더/체크박스 변경 시 미리보기 업데이트
        body_start_slider.change(
            update_preview,
            inputs=[current_annotations, body_start_slider, noise_checkboxes, header_dropdown],
            outputs=[annotated_image]
        )

        noise_checkboxes.change(
            update_preview,
            inputs=[current_annotations, body_start_slider, noise_checkboxes, header_dropdown],
            outputs=[annotated_image]
        )

        save_btn.click(
            save_rule,
            inputs=[current_cluster_id, current_annotations, body_start_slider, noise_checkboxes, notes_input],
            outputs=[save_status]
        )

    return app


def launch(port: int = 7866, share: bool = False):
    """앱 실행"""
    app = create_app()
    app.launch(server_port=port, share=share)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=7866)
    parser.add_argument("--share", "-s", action="store_true")
    args = parser.parse_args()

    print("🎨 헤더 영역 주석 도구 시작...")
    print(f"   포트: {args.port}")
    print(f"   URL: http://localhost:{args.port}")
    launch(port=args.port, share=args.share)
