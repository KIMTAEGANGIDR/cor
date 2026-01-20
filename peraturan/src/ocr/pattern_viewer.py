"""
ILIS OCR Pipeline - Pattern Viewer (Gradio UI)

헤더 이미지와 OCR 결과를 확인하고 패턴을 분류하는 웹 UI
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional
import os

import gradio as gr
from paddleocr import PaddleOCR

from .database import OCRPipelineDB

# 환경변수로 모델 체크 비활성화
os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

# PaddleOCR 싱글톤
_ocr = None

def get_ocr():
    """PaddleOCR 인스턴스 (싱글톤)"""
    global _ocr
    if _ocr is None:
        _ocr = PaddleOCR(lang='id')
    return _ocr


class PatternViewer:
    """헤더 패턴 뷰어"""

    def __init__(self, db_path: Optional[str] = None):
        self.db = OCRPipelineDB(db_path)
        self.current_cluster_id = None
        self.current_doc_index = 0

    def get_clusters(self) -> list[dict]:
        """클러스터 목록 조회"""
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT c.id, c.name, c.description, c.document_count,
                       c.sample_documents, c.status, c.representative_text
                FROM clusters c
                ORDER BY c.document_count DESC
            """).fetchall()
        return [dict(row) for row in rows]

    def get_cluster_documents(self, cluster_id: int, limit: int = 100) -> list[dict]:
        """클러스터의 문서 목록"""
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT d.id, d.judul, d.jenis, d.nomor, d.tahun,
                       h.image_path, h.raw_text, h.ocr_confidence
                FROM documents d
                JOIN headers h ON d.id = h.document_id
                WHERE h.cluster_id = ?
                ORDER BY d.tahun DESC, d.nomor
                LIMIT ?
            """, (cluster_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_document_by_id(self, doc_id: str) -> Optional[dict]:
        """문서 정보 조회"""
        with self.db.connection() as conn:
            row = conn.execute("""
                SELECT d.id, d.judul, d.jenis, d.nomor, d.tahun, d.pdf_path,
                       h.image_path, h.raw_text, h.lines, h.ocr_confidence, h.cluster_id
                FROM documents d
                JOIN headers h ON d.id = h.document_id
                WHERE d.id = ?
            """, (doc_id,)).fetchone()
        return dict(row) if row else None

    def run_ocr(self, image_path: str) -> tuple[list[str], list[float], str]:
        """PaddleOCR 실행"""
        if not image_path or not Path(image_path).exists():
            return [], [], "이미지를 찾을 수 없습니다"

        try:
            ocr = get_ocr()
            result = ocr.predict(image_path)

            if result and result[0]:
                res = result[0]
                texts = res['rec_texts'] if hasattr(res, '__getitem__') else res.rec_texts
                scores = res['rec_scores'] if hasattr(res, '__getitem__') else res.rec_scores

                # 포맷팅된 결과
                formatted = []
                for i, (text, score) in enumerate(zip(texts, scores), 1):
                    formatted.append(f"{i:2}. [{score*100:5.1f}%] {text}")

                return texts, scores, "\n".join(formatted)
            return [], [], "텍스트를 인식하지 못했습니다"
        except Exception as e:
            return [], [], f"OCR 오류: {str(e)}"

    def save_pattern_classification(
        self,
        cluster_id: int,
        noise_lines: list[int],
        metadata_lines: dict[int, str],
        body_start: int,
        notes: str
    ) -> bool:
        """패턴 분류 저장"""
        try:
            with self.db.connection() as conn:
                # pattern_rules 테이블에 저장
                conn.execute("""
                    INSERT OR REPLACE INTO pattern_rules
                    (cluster_id, noise_lines, metadata_lines, body_start_line, notes, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'draft', datetime('now'))
                """, (
                    cluster_id,
                    json.dumps(noise_lines),
                    json.dumps(metadata_lines),
                    body_start,
                    notes
                ))

                # 클러스터 상태 업데이트
                conn.execute("""
                    UPDATE clusters SET status = 'classified' WHERE id = ?
                """, (cluster_id,))

            return True
        except Exception as e:
            print(f"저장 오류: {e}")
            return False


def create_viewer_ui(viewer: PatternViewer) -> gr.Blocks:
    """Gradio UI 생성"""

    with gr.Blocks(title="ILIS OCR Pattern Viewer") as app:

        gr.Markdown("# 📋 ILIS OCR 패턴 분석기")
        gr.Markdown("헤더 이미지를 확인하고 양식 패턴을 분류합니다")

        with gr.Tabs():
            # ===== 탭 1: 클러스터 브라우저 =====
            with gr.Tab("🗂️ 클러스터 브라우저"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 클러스터 목록")
                        cluster_list = gr.Dataframe(
                            headers=["ID", "이름", "문서수", "상태", "설명"],
                            datatype=["number", "str", "number", "str", "str"],
                            interactive=False,
                            max_height=400
                        )
                        refresh_btn = gr.Button("🔄 새로고침", variant="secondary")

                    with gr.Column(scale=2):
                        gr.Markdown("### 클러스터 상세")
                        selected_cluster = gr.Number(label="선택된 클러스터 ID", visible=False)
                        cluster_info = gr.Markdown("클러스터를 선택하세요")

                        with gr.Row():
                            doc_dropdown = gr.Dropdown(
                                label="문서 선택",
                                choices=[],
                                interactive=True
                            )
                            load_doc_btn = gr.Button("📄 문서 보기")

                with gr.Row():
                    with gr.Column():
                        header_image = gr.Image(label="헤더 이미지")
                    with gr.Column():
                        ocr_result = gr.Textbox(
                            label="OCR 결과 (PaddleOCR)",
                            lines=12,
                            interactive=False
                        )
                        run_ocr_btn = gr.Button("🔍 OCR 재실행", variant="primary")

            # ===== 탭 2: 패턴 분류 =====
            with gr.Tab("🏷️ 패턴 분류"):
                gr.Markdown("### 라인별 분류")
                gr.Markdown("각 라인을 **노이즈** / **메타데이터** / **본문**으로 분류하세요")

                with gr.Row():
                    with gr.Column():
                        classify_cluster_id = gr.Number(label="클러스터 ID")
                        line_classifications = gr.Dataframe(
                            headers=["라인", "텍스트", "분류", "메타 타입"],
                            datatype=["number", "str", "str", "str"],
                            interactive=True,
                            max_height=400
                        )

                    with gr.Column():
                        noise_lines_input = gr.Textbox(
                            label="노이즈 라인 (쉼표로 구분)",
                            placeholder="0, 5, 10"
                        )
                        body_start_input = gr.Number(
                            label="본문 시작 라인",
                            value=3
                        )
                        pattern_notes = gr.Textbox(
                            label="메모",
                            lines=3,
                            placeholder="이 패턴에 대한 설명..."
                        )
                        save_pattern_btn = gr.Button("💾 패턴 저장", variant="primary")
                        save_status = gr.Markdown("")

            # ===== 탭 3: 개별 문서 뷰어 =====
            with gr.Tab("📄 문서 뷰어"):
                with gr.Row():
                    doc_id_input = gr.Textbox(label="문서 ID", placeholder="예: uu-no-1-tahun-1945")
                    search_doc_btn = gr.Button("🔍 검색")

                with gr.Row():
                    with gr.Column():
                        single_header_image = gr.Image(label="헤더 이미지")
                    with gr.Column():
                        single_doc_info = gr.Markdown("문서 정보가 여기에 표시됩니다")
                        single_ocr_result = gr.Textbox(
                            label="OCR 결과",
                            lines=10,
                            interactive=False
                        )
                        single_ocr_btn = gr.Button("🔍 OCR 실행", variant="primary")

            # ===== 탭 4: 통계 =====
            with gr.Tab("📊 통계"):
                stats_md = gr.Markdown("로딩 중...")
                refresh_stats_btn = gr.Button("🔄 통계 새로고침")

        # ===== 이벤트 핸들러 =====

        def load_clusters():
            """클러스터 목록 로드"""
            clusters = viewer.get_clusters()
            data = [
                [c['id'], c['name'], c['document_count'], c['status'] or 'draft', c['description'] or '']
                for c in clusters
            ]
            return data

        def on_cluster_select(evt: gr.SelectData, data):
            """클러스터 선택 시"""
            if evt.index[0] is not None:
                row = data[evt.index[0]]
                cluster_id = row[0]

                # 문서 목록 가져오기
                docs = viewer.get_cluster_documents(cluster_id, limit=50)
                choices = [f"{d['id']} | {d['jenis']} {d['nomor']} ({d['tahun']})" for d in docs]

                info = f"""
### 클러스터 #{cluster_id}
- **문서 수**: {row[2]}개
- **상태**: {row[3]}
- **설명**: {row[4]}
                """

                return cluster_id, info, gr.update(choices=choices, value=choices[0] if choices else None)
            return None, "클러스터를 선택하세요", gr.update(choices=[])

        def load_document(doc_selection, cluster_id):
            """문서 로드"""
            if not doc_selection:
                return None, "문서를 선택하세요"

            doc_id = doc_selection.split(" | ")[0]
            doc = viewer.get_document_by_id(doc_id)

            if doc and doc['image_path'] and Path(doc['image_path']).exists():
                # 저장된 OCR 결과 표시
                ocr_text = doc.get('raw_text') or "OCR 결과 없음 - '🔍 OCR 재실행' 버튼을 클릭하세요"
                return doc['image_path'], ocr_text

            return None, "이미지를 찾을 수 없습니다"

        def run_ocr_on_image(doc_selection):
            """OCR 실행"""
            if not doc_selection:
                return "문서를 먼저 선택하세요"

            doc_id = doc_selection.split(" | ")[0]
            doc = viewer.get_document_by_id(doc_id)

            if doc and doc['image_path']:
                _, _, formatted = viewer.run_ocr(doc['image_path'])
                return formatted

            return "이미지를 찾을 수 없습니다"

        def search_single_doc(doc_id):
            """개별 문서 검색"""
            if not doc_id:
                return None, "문서 ID를 입력하세요", ""

            doc = viewer.get_document_by_id(doc_id.strip())

            if doc:
                info = f"""
### {doc['id']}
- **제목**: {doc.get('judul', 'N/A')}
- **유형**: {doc['jenis']}
- **번호/연도**: {doc['nomor']} / {doc['tahun']}
- **클러스터**: #{doc.get('cluster_id', 'N/A')}
- **OCR 신뢰도**: {doc.get('ocr_confidence', 0)*100:.1f}%
                """

                ocr_text = doc.get('raw_text') or "OCR 결과 없음"
                image_path = doc['image_path'] if doc.get('image_path') and Path(doc['image_path']).exists() else None

                return image_path, info, ocr_text

            return None, "문서를 찾을 수 없습니다", ""

        def run_single_ocr(doc_id):
            """개별 문서 OCR 실행"""
            doc = viewer.get_document_by_id(doc_id.strip()) if doc_id else None
            if doc and doc.get('image_path'):
                _, _, formatted = viewer.run_ocr(doc['image_path'])
                return formatted
            return "문서를 먼저 검색하세요"

        def load_stats():
            """통계 로드"""
            with viewer.db.connection() as conn:
                total_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                total_headers = conn.execute("SELECT COUNT(*) FROM headers").fetchone()[0]
                ocr_done = conn.execute("SELECT COUNT(*) FROM headers WHERE raw_text IS NOT NULL").fetchone()[0]
                total_clusters = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
                classified = conn.execute("SELECT COUNT(*) FROM clusters WHERE status = 'classified'").fetchone()[0]

                # 법령 유형별 통계
                jenis_stats = conn.execute("""
                    SELECT jenis, COUNT(*) as cnt FROM documents GROUP BY jenis ORDER BY cnt DESC
                """).fetchall()

            jenis_table = "\n".join([f"| {j[0]} | {j[1]:,} |" for j in jenis_stats])

            return f"""
## 📊 파이프라인 통계

### 전체 현황
| 항목 | 수량 |
|------|------|
| 총 문서 | {total_docs:,} |
| 헤더 추출 | {total_headers:,} |
| OCR 완료 | {ocr_done:,} |
| 클러스터 | {total_clusters} |
| 분류 완료 | {classified} |

### 법령 유형별
| 유형 | 문서 수 |
|------|--------|
{jenis_table}

### 진행률
- 헤더 추출: **{total_headers/total_docs*100:.1f}%**
- OCR 처리: **{ocr_done/total_headers*100:.1f}%** (헤더 기준)
- 패턴 분류: **{classified}/{total_clusters}** 클러스터
            """

        def save_pattern(cluster_id, noise_lines_str, body_start, notes):
            """패턴 저장"""
            try:
                noise_lines = [int(x.strip()) for x in noise_lines_str.split(",") if x.strip()]
                success = viewer.save_pattern_classification(
                    int(cluster_id),
                    noise_lines,
                    {},  # metadata_lines - 추후 구현
                    int(body_start),
                    notes
                )
                if success:
                    return "✅ 패턴이 저장되었습니다!"
                return "❌ 저장 실패"
            except Exception as e:
                return f"❌ 오류: {str(e)}"

        # 이벤트 연결
        refresh_btn.click(load_clusters, outputs=[cluster_list])
        cluster_list.select(
            on_cluster_select,
            inputs=[cluster_list],
            outputs=[selected_cluster, cluster_info, doc_dropdown]
        )
        load_doc_btn.click(
            load_document,
            inputs=[doc_dropdown, selected_cluster],
            outputs=[header_image, ocr_result]
        )
        run_ocr_btn.click(
            run_ocr_on_image,
            inputs=[doc_dropdown],
            outputs=[ocr_result]
        )

        search_doc_btn.click(
            search_single_doc,
            inputs=[doc_id_input],
            outputs=[single_header_image, single_doc_info, single_ocr_result]
        )
        single_ocr_btn.click(
            run_single_ocr,
            inputs=[doc_id_input],
            outputs=[single_ocr_result]
        )

        refresh_stats_btn.click(load_stats, outputs=[stats_md])

        save_pattern_btn.click(
            save_pattern,
            inputs=[classify_cluster_id, noise_lines_input, body_start_input, pattern_notes],
            outputs=[save_status]
        )

        # 초기 로드
        app.load(load_clusters, outputs=[cluster_list])
        app.load(load_stats, outputs=[stats_md])

    return app


def launch_viewer(db_path: Optional[str] = None, share: bool = False, port: int = 7860):
    """패턴 뷰어 실행

    Args:
        db_path: OCR 파이프라인 DB 경로
        share: Gradio 공유 링크 생성 여부
        port: 포트 번호
    """
    viewer = PatternViewer(db_path)
    app = create_viewer_ui(viewer)
    app.launch(share=share, server_port=port)


# CLI 실행
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ILIS OCR Pattern Viewer")
    parser.add_argument("--port", "-p", type=int, default=7860, help="Server port")
    parser.add_argument("--share", "-s", action="store_true", help="Create public link")

    args = parser.parse_args()

    print("🚀 ILIS OCR Pattern Viewer 시작...")
    print(f"   포트: {args.port}")
    launch_viewer(share=args.share, port=args.port)
