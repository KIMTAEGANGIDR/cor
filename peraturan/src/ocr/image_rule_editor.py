"""
ILIS OCR Pipeline - Image Noise Rule Editor

클러스터별 이미지 노이즈 규칙 편집 UI
- 크롭 비율 설정
- 마스킹 영역 설정
- 전처리 옵션 설정
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional

import gradio as gr

DB_PATH = "peraturan/data/ocr_pipeline.db"


class ImageRuleEditor:
    """이미지 노이즈 규칙 편집기"""

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
            SELECT c.id, c.name, c.description, c.document_count,
                   (SELECT COUNT(*) FROM image_noise_rules r WHERE r.cluster_id = c.id AND r.is_active = 1) as has_rule
            FROM clusters c
            WHERE c.document_count > 0
            ORDER BY c.document_count DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_rule(self, cluster_id: int) -> dict | None:
        """클러스터별 규칙 조회"""
        conn = self.get_connection()
        row = conn.execute("""
            SELECT * FROM image_noise_rules
            WHERE cluster_id = ? AND is_active = 1
            ORDER BY id DESC LIMIT 1
        """, (cluster_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def save_rule(
        self,
        cluster_id: int,
        header_crop: float,
        footer_crop: float,
        left_crop: float,
        right_crop: float,
        grayscale: bool,
        denoise: bool,
        deskew: bool,
        binarize: bool,
        binarize_threshold: int,
        description: str,
    ) -> bool:
        """규칙 저장"""
        conn = self.get_connection()
        try:
            # 기존 규칙 비활성화
            conn.execute("""
                UPDATE image_noise_rules SET is_active = 0 WHERE cluster_id = ?
            """, (cluster_id,))

            # 새 규칙 저장
            conn.execute("""
                INSERT INTO image_noise_rules
                (cluster_id, header_crop_ratio, footer_crop_ratio,
                 left_crop_ratio, right_crop_ratio,
                 grayscale, denoise, deskew, binarize, binarize_threshold,
                 description, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                cluster_id, header_crop, footer_crop, left_crop, right_crop,
                int(grayscale), int(denoise), int(deskew), int(binarize), binarize_threshold,
                description,
            ))
            conn.commit()
            return True
        except Exception as e:
            print(f"저장 오류: {e}")
            return False
        finally:
            conn.close()

    def get_sample_images(self, cluster_id: int, limit: int = 5) -> list[str]:
        """클러스터의 샘플 이미지 경로"""
        conn = self.get_connection()
        rows = conn.execute("""
            SELECT h.image_path
            FROM headers h
            WHERE h.cluster_id = ? AND h.image_path IS NOT NULL
            LIMIT ?
        """, (cluster_id, limit)).fetchall()
        conn.close()
        return [r["image_path"] for r in rows if Path(r["image_path"]).exists()]

    def get_stats(self) -> dict:
        """통계"""
        conn = self.get_connection()

        # 클러스터 수
        row = conn.execute("SELECT COUNT(*) FROM clusters WHERE document_count > 0").fetchone()
        total_clusters = row[0]

        # 규칙이 있는 클러스터 수
        row = conn.execute("""
            SELECT COUNT(DISTINCT cluster_id) FROM image_noise_rules WHERE is_active = 1
        """).fetchone()
        clusters_with_rules = row[0]

        # 규칙별 문서 수
        rows = conn.execute("""
            SELECT r.cluster_id, c.document_count
            FROM image_noise_rules r
            JOIN clusters c ON r.cluster_id = c.id
            WHERE r.is_active = 1
        """).fetchall()
        covered_documents = sum(r["document_count"] for r in rows)

        # 전체 문서 수
        row = conn.execute("""
            SELECT SUM(document_count) FROM clusters WHERE document_count > 0
        """).fetchone()
        total_documents = row[0] or 0

        conn.close()

        return {
            "total_clusters": total_clusters,
            "clusters_with_rules": clusters_with_rules,
            "coverage_ratio": clusters_with_rules / total_clusters if total_clusters > 0 else 0,
            "covered_documents": covered_documents,
            "total_documents": total_documents,
            "document_coverage": covered_documents / total_documents if total_documents > 0 else 0,
        }


def create_app():
    """Gradio 앱 생성"""
    editor = ImageRuleEditor()

    with gr.Blocks(title="이미지 노이즈 규칙 편집기", theme=gr.themes.Soft()) as app:
        gr.Markdown("# 🖼️ 이미지 노이즈 규칙 편집기")
        gr.Markdown("클러스터별 이미지 전처리 규칙을 설정합니다")

        # 상태
        current_cluster_id = gr.State(None)

        # ========== 통계 ==========
        with gr.Row():
            stats_html = gr.HTML("")

        # ========== 클러스터 선택 ==========
        with gr.Row():
            cluster_dropdown = gr.Dropdown(
                label="📁 클러스터 선택",
                choices=[],
                scale=4
            )
            refresh_btn = gr.Button("🔄 새로고침", scale=1)

        # ========== 메인 편집 영역 ==========
        with gr.Row():
            # 왼쪽: 샘플 이미지
            with gr.Column(scale=1):
                gr.Markdown("### 📸 샘플 이미지")
                sample_gallery = gr.Gallery(
                    label="",
                    columns=2,
                    rows=2,
                    height=400,
                    object_fit="contain",
                )

            # 오른쪽: 규칙 설정
            with gr.Column(scale=1):
                gr.Markdown("### ⚙️ 노이즈 제거 규칙")

                with gr.Group():
                    gr.Markdown("#### 📐 크롭 설정")
                    header_crop = gr.Slider(
                        minimum=0, maximum=0.5, step=0.01, value=0,
                        label="상단 크롭 비율 (헤더 제거)"
                    )
                    footer_crop = gr.Slider(
                        minimum=0, maximum=0.3, step=0.01, value=0,
                        label="하단 크롭 비율 (푸터 제거)"
                    )
                    with gr.Row():
                        left_crop = gr.Slider(
                            minimum=0, maximum=0.15, step=0.01, value=0,
                            label="좌측 크롭"
                        )
                        right_crop = gr.Slider(
                            minimum=0, maximum=0.15, step=0.01, value=0,
                            label="우측 크롭"
                        )

                with gr.Group():
                    gr.Markdown("#### 🔧 전처리 옵션")
                    with gr.Row():
                        grayscale = gr.Checkbox(label="그레이스케일", value=False)
                        denoise = gr.Checkbox(label="노이즈 제거", value=False)
                    with gr.Row():
                        deskew = gr.Checkbox(label="기울기 보정", value=False)
                        binarize = gr.Checkbox(label="이진화", value=False)
                    binarize_threshold = gr.Slider(
                        minimum=50, maximum=200, step=5, value=127,
                        label="이진화 임계값",
                        visible=False
                    )

                rule_description = gr.Textbox(
                    label="규칙 설명",
                    placeholder="예: 1990년대 PP 문서용 규칙"
                )

                with gr.Row():
                    save_btn = gr.Button("💾 규칙 저장", variant="primary", scale=2)
                    status_msg = gr.Markdown("")

        # ========== 이벤트 핸들러 ==========

        def load_stats():
            """통계 로드"""
            stats = editor.get_stats()
            return f"""
            <div style="display: flex; gap: 20px; justify-content: center; padding: 10px;">
                <div style="text-align: center; padding: 10px 20px; background: #e3f2fd; border-radius: 8px;">
                    <div style="font-size: 24px; font-weight: bold;">{stats['clusters_with_rules']}/{stats['total_clusters']}</div>
                    <div style="font-size: 12px; color: #666;">규칙 정의된 클러스터</div>
                </div>
                <div style="text-align: center; padding: 10px 20px; background: #e8f5e9; border-radius: 8px;">
                    <div style="font-size: 24px; font-weight: bold;">{stats['document_coverage']:.1%}</div>
                    <div style="font-size: 12px; color: #666;">문서 커버리지</div>
                </div>
                <div style="text-align: center; padding: 10px 20px; background: #fff3e0; border-radius: 8px;">
                    <div style="font-size: 24px; font-weight: bold;">{stats['covered_documents']:,}</div>
                    <div style="font-size: 12px; color: #666;">규칙 적용 문서</div>
                </div>
            </div>
            """

        def load_clusters():
            """클러스터 목록 로드"""
            clusters = editor.get_clusters()
            choices = []
            for c in clusters:
                rule_badge = "✅" if c["has_rule"] else "❌"
                label = f"{rule_badge} #{c['id']} - {c['description'] or '(이름 없음)'} ({c['document_count']}개)"
                choices.append((label, c["id"]))
            return gr.update(choices=choices), load_stats()

        def on_cluster_select(cluster_id):
            """클러스터 선택 시"""
            if not cluster_id:
                return (None, [], 0, 0, 0, 0, False, False, False, False, 127, "")

            # 샘플 이미지
            images = editor.get_sample_images(cluster_id)

            # 기존 규칙
            rule = editor.get_rule(cluster_id)

            if rule:
                return (
                    cluster_id,
                    images,
                    rule.get("header_crop_ratio") or 0,
                    rule.get("footer_crop_ratio") or 0,
                    rule.get("left_crop_ratio") or 0,
                    rule.get("right_crop_ratio") or 0,
                    bool(rule.get("grayscale")),
                    bool(rule.get("denoise")),
                    bool(rule.get("deskew")),
                    bool(rule.get("binarize")),
                    rule.get("binarize_threshold") or 127,
                    rule.get("description") or "",
                )
            else:
                return (cluster_id, images, 0, 0, 0, 0, False, False, False, False, 127, "")

        def on_binarize_change(binarize):
            """이진화 체크박스 변경 시"""
            return gr.update(visible=binarize)

        def save_rule(
            cluster_id, header_crop, footer_crop, left_crop, right_crop,
            grayscale, denoise, deskew, binarize, binarize_threshold, description
        ):
            """규칙 저장"""
            if not cluster_id:
                return "❌ 클러스터를 선택하세요"

            success = editor.save_rule(
                cluster_id=cluster_id,
                header_crop=header_crop,
                footer_crop=footer_crop,
                left_crop=left_crop,
                right_crop=right_crop,
                grayscale=grayscale,
                denoise=denoise,
                deskew=deskew,
                binarize=binarize,
                binarize_threshold=binarize_threshold,
                description=description,
            )

            if success:
                return f"✅ 클러스터 #{cluster_id} 규칙 저장됨"
            return "❌ 저장 실패"

        # ========== 이벤트 연결 ==========

        app.load(load_clusters, outputs=[cluster_dropdown, stats_html])
        refresh_btn.click(load_clusters, outputs=[cluster_dropdown, stats_html])

        cluster_dropdown.change(
            on_cluster_select,
            inputs=[cluster_dropdown],
            outputs=[
                current_cluster_id, sample_gallery,
                header_crop, footer_crop, left_crop, right_crop,
                grayscale, denoise, deskew, binarize, binarize_threshold,
                rule_description
            ]
        )

        binarize.change(on_binarize_change, inputs=[binarize], outputs=[binarize_threshold])

        save_btn.click(
            save_rule,
            inputs=[
                current_cluster_id, header_crop, footer_crop, left_crop, right_crop,
                grayscale, denoise, deskew, binarize, binarize_threshold, rule_description
            ],
            outputs=[status_msg]
        ).then(
            load_clusters,
            outputs=[cluster_dropdown, stats_html]
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

    print("🚀 이미지 노이즈 규칙 편집기 시작...")
    print(f"   포트: {args.port}")
    launch(port=args.port, share=args.share)
