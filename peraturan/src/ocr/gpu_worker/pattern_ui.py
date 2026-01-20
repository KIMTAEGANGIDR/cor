#!/usr/bin/env python3
"""
ILIS Pattern Analyzer - Web UI
패턴 분석기 웹 인터페이스 (Gradio)

Usage:
    pip install gradio
    python3 pattern_ui.py
    # http://192.168.0.113:7860
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional
import base64

try:
    import gradio as gr
except ImportError:
    print("Installing gradio...")
    import subprocess
    subprocess.run(["pip3", "install", "gradio"], check=True)
    import gradio as gr

# Config
DB_PATH = Path("/mnt/workspace/images/ocr_pipeline.db")
HEADERS_DIR = Path("/mnt/workspace/images/headers")


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_clusters():
    """클러스터 목록 조회"""
    conn = get_db()
    rows = conn.execute("""
        SELECT id, name, document_count, description, status
        FROM clusters
        ORDER BY document_count DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cluster_samples(cluster_id: int):
    """클러스터 샘플 조회"""
    conn = get_db()
    rows = conn.execute("""
        SELECT h.id, h.document_id, h.image_path, h.raw_text, h.lines, h.ocr_confidence
        FROM headers h
        WHERE h.cluster_id = ?
        LIMIT 5
    """, (cluster_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pattern_rule(cluster_id: int):
    """패턴 룰 조회"""
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM pattern_rules
        WHERE cluster_id = ? AND is_active = 1
    """, (cluster_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def save_pattern_rule(cluster_id: int, noise_lines: str, metadata_lines: str,
                      body_start: int, metadata_mapping: str, notes: str):
    """패턴 룰 저장"""
    conn = get_db()

    # 기존 룰 비활성화
    conn.execute("""
        UPDATE pattern_rules SET is_active = 0 WHERE cluster_id = ?
    """, (cluster_id,))

    # 새 룰 삽입
    noise = [int(x.strip()) for x in noise_lines.split(',') if x.strip().isdigit()]
    meta = [int(x.strip()) for x in metadata_lines.split(',') if x.strip().isdigit()]

    try:
        mapping = json.loads(metadata_mapping) if metadata_mapping.strip() else {}
    except:
        mapping = {}

    conn.execute("""
        INSERT INTO pattern_rules (cluster_id, noise_lines, metadata_lines,
                                   body_start_line, metadata_mapping, notes,
                                   is_active, version)
        VALUES (?, ?, ?, ?, ?, ?, 1, 1)
    """, (
        cluster_id,
        json.dumps(noise),
        json.dumps(meta),
        body_start,
        json.dumps(mapping),
        notes
    ))

    # 클러스터 상태 업데이트
    conn.execute("""
        UPDATE clusters SET status = 'pending_validation' WHERE id = ?
    """, (cluster_id,))

    conn.commit()
    conn.close()
    return "저장 완료!"


def approve_pattern(cluster_id: int):
    """패턴 승인"""
    conn = get_db()
    conn.execute("UPDATE clusters SET status = 'approved' WHERE id = ?", (cluster_id,))
    conn.commit()
    conn.close()
    return "승인됨!"


def load_image_base64(image_path: str) -> Optional[str]:
    """이미지를 base64로 로드"""
    path = Path(image_path)
    if not path.exists():
        # 상대 경로 시도
        path = HEADERS_DIR / image_path.split('/headers/')[-1] if '/headers/' in image_path else path

    if path.exists():
        with open(path, 'rb') as f:
            return f"data:image/jpeg;base64,{base64.b64encode(f.read()).decode()}"
    return None


# Gradio UI
def create_ui():
    with gr.Blocks(title="ILIS Pattern Analyzer") as app:
        gr.Markdown("# ILIS 패턴 분석기")
        gr.Markdown("클러스터별 헤더 패턴을 분석하고 룰을 정의합니다.")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("## 클러스터 목록")
                cluster_dropdown = gr.Dropdown(
                    label="클러스터 선택",
                    choices=[],
                    interactive=True
                )
                refresh_btn = gr.Button("새로고침")

                cluster_info = gr.Markdown("클러스터를 선택하세요")

            with gr.Column(scale=2):
                gr.Markdown("## 샘플 헤더")
                sample_gallery = gr.Gallery(label="헤더 이미지", columns=2, height=300)
                sample_text = gr.Textbox(label="OCR 텍스트 (라인별)", lines=10, interactive=False)

        gr.Markdown("---")
        gr.Markdown("## 패턴 룰 정의")

        with gr.Row():
            with gr.Column():
                noise_input = gr.Textbox(
                    label="노이즈 라인 (쉼표 구분)",
                    placeholder="0, 5, 10",
                    info="제거할 라인 번호들"
                )
                metadata_input = gr.Textbox(
                    label="메타데이터 라인 (쉼표 구분)",
                    placeholder="1, 2, 3",
                    info="법령 유형, 번호, 연도 등"
                )
                body_start = gr.Number(
                    label="본문 시작 라인",
                    value=4,
                    precision=0
                )

            with gr.Column():
                metadata_mapping = gr.Textbox(
                    label="메타데이터 매핑 (JSON)",
                    placeholder='{"1": "law_type", "2": "law_number"}',
                    lines=3
                )
                notes = gr.Textbox(
                    label="메모",
                    placeholder="이 패턴에 대한 설명...",
                    lines=3
                )

        with gr.Row():
            save_btn = gr.Button("저장", variant="primary")
            approve_btn = gr.Button("승인", variant="secondary")
            status_text = gr.Textbox(label="상태", interactive=False)

        # State
        current_cluster = gr.State(value=None)

        # Functions
        def refresh_clusters():
            clusters = get_clusters()
            choices = [f"{c['id']}: {c['name']} ({c['document_count']}개) - {c['status']}"
                      for c in clusters]
            return gr.Dropdown(choices=choices)

        def on_cluster_select(selection):
            if not selection:
                return None, "클러스터를 선택하세요", [], "", "", "", 4, "", ""

            cluster_id = int(selection.split(':')[0])
            clusters = get_clusters()
            cluster = next((c for c in clusters if c['id'] == cluster_id), None)

            if not cluster:
                return None, "클러스터를 찾을 수 없음", [], "", "", "", 4, "", ""

            # 샘플 조회
            samples = get_cluster_samples(cluster_id)
            images = []
            text_lines = []

            for s in samples:
                img = load_image_base64(s['image_path'])
                if img:
                    images.append(img)

                if s['raw_text']:
                    text_lines.append(f"=== {s['document_id']} ===")
                    for i, line in enumerate(s['raw_text'].split('\n')):
                        text_lines.append(f"[{i}] {line}")
                    text_lines.append("")

            # 기존 패턴 룰 로드
            rule = get_pattern_rule(cluster_id)
            noise = ','.join(map(str, json.loads(rule['noise_lines']))) if rule and rule['noise_lines'] else ""
            meta = ','.join(map(str, json.loads(rule['metadata_lines']))) if rule and rule['metadata_lines'] else ""
            body = rule['body_start_line'] if rule else 4
            mapping = rule['metadata_mapping'] if rule else ""
            notes_val = rule['notes'] if rule else ""

            info = f"""
### {cluster['name']}
- 문서 수: **{cluster['document_count']}**
- 상태: **{cluster['status']}**
- 설명: {cluster['description'] or '-'}
"""

            return (cluster_id, info, images, '\n'.join(text_lines),
                    noise, meta, body, mapping, notes_val)

        def on_save(cluster_id, noise, meta, body, mapping, notes_val):
            if cluster_id is None:
                return "클러스터를 먼저 선택하세요"
            return save_pattern_rule(cluster_id, noise, meta, int(body), mapping, notes_val)

        def on_approve(cluster_id):
            if cluster_id is None:
                return "클러스터를 먼저 선택하세요"
            return approve_pattern(cluster_id)

        # Events
        refresh_btn.click(refresh_clusters, outputs=cluster_dropdown)
        app.load(refresh_clusters, outputs=cluster_dropdown)

        cluster_dropdown.change(
            on_cluster_select,
            inputs=[cluster_dropdown],
            outputs=[current_cluster, cluster_info, sample_gallery, sample_text,
                    noise_input, metadata_input, body_start, metadata_mapping, notes]
        )

        save_btn.click(
            on_save,
            inputs=[current_cluster, noise_input, metadata_input, body_start,
                   metadata_mapping, notes],
            outputs=[status_text]
        )

        approve_btn.click(
            on_approve,
            inputs=[current_cluster],
            outputs=[status_text]
        )

    return app


if __name__ == "__main__":
    print("Starting ILIS Pattern Analyzer...")
    print("Access at: http://192.168.0.113:7860")

    app = create_ui()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False
    )
