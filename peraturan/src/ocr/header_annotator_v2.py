"""
ILIS OCR Pipeline - Header Annotator V2 (HuggingFace Style)

고급 기능:
- 바운딩 박스 직접 드래그해서 그리기
- 영역별 색상 구분 (노이즈/메타/본문)
- 실시간 미리보기
- 규칙 저장
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional
from PIL import Image
import numpy as np

import gradio as gr
from gradio_image_annotation import image_annotator

os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

DB_PATH = "peraturan/data/ocr_pipeline.db"

# 레이블 정의
LABELS = ["noise", "metadata", "body_start"]
LABEL_COLORS = ["#ff4444", "#4444ff", "#44ff44"]  # 빨강, 파랑, 녹색
LABEL_NAMES_KR = {
    "noise": "🗑️ 노이즈 (제거할 영역)",
    "metadata": "📋 메타데이터 (법령유형, 번호 등)",
    "body_start": "▶️ 본문 시작점"
}


class HeaderAnnotatorV2:
    """고급 헤더 영역 주석 도구"""

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
                   (SELECT COUNT(*) FROM pattern_rules pr
                    WHERE pr.cluster_id = c.id AND pr.is_active = 1) as has_rule
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

    def get_header_image_and_ocr(self, header_id: int) -> tuple[Optional[str], Optional[str], list]:
        """헤더 이미지와 OCR 텍스트, 자동 생성 박스"""
        conn = self.get_connection()
        row = conn.execute("""
            SELECT h.*, d.jenis, d.nomor, d.tahun, d.tentang
            FROM headers h
            JOIN documents d ON h.document_id = d.id
            WHERE h.id = ?
        """, (header_id,)).fetchone()
        conn.close()

        if not row:
            return None, None, []

        image_path = row["image_path"]
        raw_text = row["raw_text"] or ""

        # OCR 라인 기반으로 초기 박스 생성
        boxes = []
        if raw_text and image_path and Path(image_path).exists():
            img = Image.open(image_path)
            img_width, img_height = img.size
            lines = [l.strip() for l in raw_text.strip().split('\n') if l.strip()]

            line_height = img_height / max(len(lines), 1)

            for i, line in enumerate(lines[:12]):  # 최대 12줄
                y1 = int(i * line_height)
                y2 = int((i + 1) * line_height)

                # 자동 분류
                label = self._auto_classify(line, i)

                boxes.append({
                    "xmin": 5,
                    "ymin": y1,
                    "xmax": img_width - 5,
                    "ymax": y2,
                    "label": label,
                    "color": LABEL_COLORS[LABELS.index(label)] if label in LABELS else "#999999"
                })

        return image_path, raw_text, boxes

    def _auto_classify(self, text: str, line_idx: int) -> str:
        """라인 자동 분류"""
        t = text.upper().strip()

        # 명확한 노이즈
        if t in ['SALINAN', 'PRESIDEN', 'REPUBLIK INDONESIA', 'INDONESIA', 'REPUBLIK']:
            return "noise"
        if 'WWW.' in t or '.GO.ID' in t:
            return "noise"

        # 메타데이터
        if any(x in t for x in ['UNDANG-UNDANG', 'PERATURAN', 'LEMBARAN NEGARA', 'NOMOR', 'TAHUN']):
            return "metadata"

        # 본문 시작
        if 'DENGAN RAHMAT' in t or 'TENTANG' in t:
            return "body_start"

        # 기본: 상위 라인은 노이즈
        if line_idx < 3:
            return "noise"
        elif line_idx < 6:
            return "metadata"
        else:
            return "body_start"

    def save_boxes_as_rule(self, cluster_id: int, boxes: list, notes: str = "") -> bool:
        """박스 정보를 규칙으로 저장"""
        conn = self.get_connection()
        try:
            # 박스를 y좌표 기준으로 정렬하고 라인 번호 추출
            sorted_boxes = sorted(boxes, key=lambda b: b.get('ymin', 0))

            noise_lines = []
            metadata_lines = []
            body_start = None

            for i, box in enumerate(sorted_boxes):
                label = box.get('label', 'unknown')
                if label == 'noise':
                    noise_lines.append(i)
                elif label == 'metadata':
                    metadata_lines.append(i)
                elif label == 'body_start' and body_start is None:
                    body_start = i

            if body_start is None:
                body_start = len(sorted_boxes)

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
                notes or "Annotated with bounding boxes"
            ))

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
    annotator = HeaderAnnotatorV2()

    with gr.Blocks(title="헤더 영역 주석 도구 V2", theme=gr.themes.Soft()) as app:
        gr.Markdown("# 🎨 헤더 영역 주석 도구 V2")
        gr.Markdown("""
        **사용법:**
        1. 클러스터와 헤더를 선택하세요
        2. 이미지에서 **드래그**해서 영역을 그리세요
        3. 레이블을 선택하고 **규칙 저장**

        | 색상 | 의미 |
        |------|------|
        | 🔴 빨강 | 노이즈 (제거) |
        | 🔵 파랑 | 메타데이터 |
        | 🟢 녹색 | 본문 시작 |
        """)

        # 상태
        current_cluster_id = gr.State(None)
        current_header_id = gr.State(None)

        # ===== 상단: 선택 영역 =====
        with gr.Row():
            cluster_dropdown = gr.Dropdown(
                label="📁 클러스터",
                choices=[],
                scale=2
            )
            header_dropdown = gr.Dropdown(
                label="📄 헤더",
                choices=[],
                scale=3
            )
            refresh_btn = gr.Button("🔄", scale=1)

        # ===== 메인: 어노테이터 =====
        with gr.Row():
            with gr.Column(scale=3):
                # 이미지 어노테이터
                annotator_component = image_annotator(
                    label="헤더 이미지 - 드래그해서 영역을 그리세요",
                    label_list=LABELS,
                    label_colors=LABEL_COLORS,
                    boxes_alpha=0.4,
                    box_thickness=3,
                    height=600,
                    show_label=True,
                    interactive=True
                )

            with gr.Column(scale=1):
                gr.Markdown("### 📋 레이블 설명")
                for label, color in zip(LABELS, LABEL_COLORS):
                    name_kr = LABEL_NAMES_KR.get(label, label)
                    gr.HTML(f"""
                        <div style="padding: 10px; margin: 5px 0;
                                    border-left: 5px solid {color};
                                    background: {color}22;">
                            <b>{name_kr}</b>
                        </div>
                    """)

                gr.Markdown("---")
                gr.Markdown("### 📝 OCR 텍스트")
                ocr_text_display = gr.Textbox(
                    label="",
                    lines=15,
                    interactive=False,
                    show_label=False
                )

        # ===== 하단: 저장 =====
        with gr.Row():
            notes_input = gr.Textbox(
                label="📋 메모",
                placeholder="이 클러스터의 특징...",
                scale=3
            )
            save_btn = gr.Button("💾 규칙 저장", variant="primary", scale=1)

        save_status = gr.Markdown("")

        # ===== 이벤트 =====

        def load_clusters():
            clusters = annotator.get_clusters()
            choices = []
            for c in clusters:
                icon = "✅" if c['has_rule'] else "⏳"
                label = f"{icon} #{c['id']} - {c['description']} ({c['document_count']}개)"
                choices.append((label, c['id']))
            return gr.update(choices=choices)

        def on_cluster_select(cluster_id):
            if not cluster_id:
                return gr.update(choices=[]), cluster_id

            headers = annotator.get_headers_for_cluster(cluster_id)
            choices = [
                (f"{h['jenis']} {h['nomor']} ({h['tahun']})", h['id'])
                for h in headers
            ]
            return gr.update(choices=choices), cluster_id

        def on_header_select(header_id):
            if not header_id:
                return None, "", header_id

            image_path, raw_text, boxes = annotator.get_header_image_and_ocr(header_id)

            if not image_path or not Path(image_path).exists():
                return None, "이미지 없음", header_id

            # 이미지를 numpy 배열로 로드
            img = np.array(Image.open(image_path).convert("RGB"))

            # image_annotator 형식으로 변환
            annotator_value = {
                "image": img,
                "boxes": boxes
            }

            # OCR 텍스트 포맷팅
            lines = raw_text.split('\n') if raw_text else []
            formatted_text = "\n".join([f"{i+1}. {l.strip()}" for i, l in enumerate(lines) if l.strip()])

            return annotator_value, formatted_text, header_id

        def save_rule(cluster_id, annotator_value, notes):
            if not cluster_id:
                return "❌ 클러스터를 선택하세요"

            if not annotator_value or 'boxes' not in annotator_value:
                return "❌ 박스를 그려주세요"

            boxes = annotator_value.get('boxes', [])
            if not boxes:
                return "❌ 영역을 지정해주세요"

            success = annotator.save_boxes_as_rule(cluster_id, boxes, notes)

            if success:
                box_summary = {}
                for b in boxes:
                    label = b.get('label', 'unknown')
                    box_summary[label] = box_summary.get(label, 0) + 1
                return f"✅ 저장 완료! {box_summary}"
            return "❌ 저장 실패"

        # 이벤트 연결
        app.load(load_clusters, outputs=[cluster_dropdown])
        refresh_btn.click(load_clusters, outputs=[cluster_dropdown])

        cluster_dropdown.change(
            on_cluster_select,
            inputs=[cluster_dropdown],
            outputs=[header_dropdown, current_cluster_id]
        )

        header_dropdown.change(
            on_header_select,
            inputs=[header_dropdown],
            outputs=[annotator_component, ocr_text_display, current_header_id]
        )

        save_btn.click(
            save_rule,
            inputs=[current_cluster_id, annotator_component, notes_input],
            outputs=[save_status]
        )

    return app


def launch(port: int = 7867, share: bool = False):
    app = create_app()
    app.launch(server_port=port, share=share)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=7867)
    parser.add_argument("--share", "-s", action="store_true")
    args = parser.parse_args()

    print("🎨 헤더 영역 주석 도구 V2 시작...")
    print(f"   URL: http://localhost:{args.port}")
    launch(port=args.port, share=args.share)
