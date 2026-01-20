"""
ILIS OCR Pipeline - Header Pattern Analyzer

목적: 클러스터별 헤더 패턴 분석 + 노이즈 패턴 발견
- 클러스터 내 헤더들을 비교
- 공통 노이즈 패턴 식별 (상단/하단)
- 패턴 규칙 정의 및 저장
"""

import html
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Optional

import gradio as gr

os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

DB_PATH = "peraturan/data/ocr_pipeline.db"


class PatternAnalyzer:
    """클러스터별 헤더 패턴 분석기"""

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
            SELECT id, name, description, document_count, status
            FROM clusters
            WHERE document_count > 0
            ORDER BY document_count DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_cluster_headers(self, cluster_id: int, limit: int = 20) -> list[dict]:
        """클러스터 내 헤더 목록"""
        conn = self.get_connection()
        rows = conn.execute("""
            SELECT h.id, h.document_id, h.image_path, h.raw_text, h.lines,
                   d.jenis, d.nomor, d.tahun
            FROM headers h
            JOIN documents d ON h.document_id = d.id
            WHERE h.cluster_id = ? AND h.image_path IS NOT NULL
            ORDER BY d.tahun DESC
            LIMIT ?
        """, (cluster_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_header_detail(self, header_id: int) -> Optional[dict]:
        """헤더 상세 정보"""
        conn = self.get_connection()
        row = conn.execute("""
            SELECT h.*, d.jenis, d.nomor, d.tahun, d.tentang
            FROM headers h
            JOIN documents d ON h.document_id = d.id
            WHERE h.id = ?
        """, (header_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def analyze_cluster_patterns(self, cluster_id: int) -> dict:
        """클러스터 공통 패턴 분석"""
        headers = self.get_cluster_headers(cluster_id, limit=50)

        if not headers:
            return {"error": "헤더가 없습니다"}

        # 라인별 텍스트 수집
        all_lines_by_position = {}  # position -> [texts]
        line_counts = Counter()

        for h in headers:
            raw_text = h.get('raw_text', '')
            if not raw_text:
                continue

            lines = raw_text.strip().split('\n')
            for i, line in enumerate(lines):
                line = line.strip()
                if line:
                    if i not in all_lines_by_position:
                        all_lines_by_position[i] = []
                    all_lines_by_position[i].append(line)
                    line_counts[line] += 1

        # 공통 노이즈 패턴 (상위 라인에서 반복되는 것)
        noise_candidates = []
        meta_candidates = []

        # 상단 3줄 분석 (보통 노이즈)
        for pos in range(min(3, len(all_lines_by_position))):
            if pos in all_lines_by_position:
                texts = all_lines_by_position[pos]
                most_common = Counter(texts).most_common(3)
                for text, count in most_common:
                    ratio = count / len(headers)
                    if ratio > 0.3:  # 30% 이상 등장
                        noise_candidates.append({
                            "position": pos,
                            "text": text,
                            "count": count,
                            "ratio": ratio,
                            "type": self._classify_text(text)
                        })

        # 전체에서 자주 등장하는 패턴
        for text, count in line_counts.most_common(20):
            ratio = count / len(headers)
            if ratio > 0.2:
                classification = self._classify_text(text)
                if classification.startswith("🗑"):
                    noise_candidates.append({
                        "text": text,
                        "count": count,
                        "ratio": ratio,
                        "type": classification
                    })
                elif classification.startswith("📋"):
                    meta_candidates.append({
                        "text": text,
                        "count": count,
                        "ratio": ratio,
                        "type": classification
                    })

        return {
            "total_headers": len(headers),
            "noise_patterns": noise_candidates[:10],
            "meta_patterns": meta_candidates[:10],
            "line_positions": len(all_lines_by_position)
        }

    def _classify_text(self, text: str) -> str:
        """텍스트 자동 분류"""
        t = text.upper().strip()

        # 확실한 노이즈
        if t in ['SALINAN', 'PRESIDEN', 'REPUBLIK INDONESIA', 'INDONESIA']:
            return "🗑️ 노이즈-고정텍스트"
        if 'WWW.' in t or '.GO.ID' in t:
            return "🗑️ 노이즈-URL"
        if t.isdigit() and len(t) <= 3:
            return "🗑️ 노이즈-페이지번호"
        if 'SK NO' in t or 'DITJEN' in t:
            return "🗑️ 노이즈-관인"

        # 메타데이터
        if any(x in t for x in ['UNDANG-UNDANG', 'PERATURAN PEMERINTAH',
                                 'PERATURAN PRESIDEN', 'PERATURAN MENTERI']):
            return "📋 메타-법령유형"
        if 'NOMOR' in t and 'TAHUN' in t:
            return "📋 메타-번호연도"
        if t == 'TENTANG':
            return "📋 메타-제목구분"
        if 'DENGAN RAHMAT' in t:
            return "▶️ 본문-서두"

        return "📝 미분류"

    def get_existing_noise_patterns(self, cluster_id: int = None) -> list[dict]:
        """기존 노이즈 패턴 조회"""
        conn = self.get_connection()
        if cluster_id:
            rows = conn.execute("""
                SELECT * FROM noise_patterns
                WHERE (scope = 'global' OR cluster_id = ?) AND is_active = 1
            """, (cluster_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM noise_patterns WHERE is_active = 1
            """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def save_noise_pattern(self, pattern: str, pattern_type: str,
                          description: str, cluster_id: int = None) -> bool:
        """노이즈 패턴 저장"""
        conn = self.get_connection()
        try:
            scope = 'cluster_specific' if cluster_id else 'global'
            conn.execute("""
                INSERT INTO noise_patterns (pattern, pattern_type, description, scope, cluster_id)
                VALUES (?, ?, ?, ?, ?)
            """, (pattern, pattern_type, description, scope, cluster_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"저장 오류: {e}")
            return False
        finally:
            conn.close()

    def save_pattern_rule(self, cluster_id: int, noise_lines: list[int],
                         metadata_lines: list[int], body_start: int,
                         notes: str = "") -> bool:
        """패턴 규칙 저장"""
        conn = self.get_connection()
        try:
            # 기존 규칙 비활성화
            conn.execute("""
                UPDATE pattern_rules SET is_active = 0 WHERE cluster_id = ?
            """, (cluster_id,))

            # 새 규칙 저장
            conn.execute("""
                INSERT INTO pattern_rules
                (cluster_id, noise_lines, metadata_lines, body_start_line, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (cluster_id, json.dumps(noise_lines), json.dumps(metadata_lines),
                  body_start, notes))

            # 클러스터 상태 업데이트
            conn.execute("""
                UPDATE clusters SET status = 'pending_validation' WHERE id = ?
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
    analyzer = PatternAnalyzer()

    with gr.Blocks(title="헤더 패턴 분석기") as app:
        gr.Markdown("# 🔍 헤더 패턴 분석기")
        gr.Markdown("클러스터별 헤더를 비교하고 **노이즈 패턴**을 발견합니다")

        # 상태 저장
        current_cluster = gr.State(None)
        current_headers = gr.State([])
        selected_header_id = gr.State(None)

        # ========== 상단: 클러스터 선택 ==========
        with gr.Row():
            cluster_dropdown = gr.Dropdown(
                label="📁 클러스터 선택",
                choices=[],
                scale=3
            )
            refresh_btn = gr.Button("🔄", scale=1)
            save_rule_btn = gr.Button("💾 규칙 저장", variant="primary", scale=1)

        # ========== 클러스터 통계 ==========
        cluster_stats = gr.Markdown("클러스터를 선택하세요")

        # ========== 헤더 썸네일 갤러리 ==========
        gr.Markdown("### 📸 헤더 이미지 비교")
        header_gallery = gr.Gallery(
            label="",
            columns=5,
            rows=2,
            height=250,
            object_fit="contain",
            allow_preview=False
        )

        # ========== 메인 분석 영역 ==========
        with gr.Row(equal_height=True):
            # 왼쪽: 선택한 헤더 이미지
            with gr.Column(scale=1):
                gr.Markdown("### 📄 선택한 헤더")
                header_image = gr.Image(label="", height=400)
                header_info = gr.Markdown("헤더를 선택하세요")

            # 오른쪽: OCR 라인 + 태깅
            with gr.Column(scale=1):
                gr.Markdown("### 📝 OCR 라인 분석")

                # 라인별 체크박스 (노이즈 여부)
                line_analysis = gr.HTML("<p>헤더를 선택하면 라인 분석이 표시됩니다</p>")

        # ========== 하단: 공통 패턴 분석 ==========
        gr.Markdown("---")
        gr.Markdown("### 📊 클러스터 공통 패턴")

        with gr.Row():
            with gr.Column():
                gr.Markdown("#### 🗑️ 노이즈 패턴 후보")
                noise_patterns_html = gr.HTML("<p>클러스터를 선택하세요</p>")

            with gr.Column():
                gr.Markdown("#### 📋 메타데이터 패턴")
                meta_patterns_html = gr.HTML("<p>클러스터를 선택하세요</p>")

        # ========== 노이즈 패턴 추가 ==========
        gr.Markdown("---")
        with gr.Row():
            new_pattern = gr.Textbox(label="새 노이즈 패턴 (정규식)", scale=3)
            pattern_desc = gr.Textbox(label="설명", scale=2)
            add_pattern_btn = gr.Button("➕ 추가", scale=1)

        pattern_status = gr.Markdown("")

        # ========== 이벤트 핸들러 ==========

        def load_clusters():
            """클러스터 목록 로드"""
            clusters = analyzer.get_clusters()
            choices = [
                (f"#{c['id']} - {c['description']} ({c['document_count']}개)", c['id'])
                for c in clusters
            ]
            return gr.update(choices=choices)

        def on_cluster_select(cluster_choice):
            """클러스터 선택 시"""
            if not cluster_choice:
                return (None, [], None, "", [],
                        "<p>클러스터를 선택하세요</p>",
                        "<p>클러스터를 선택하세요</p>")

            cluster_id = cluster_choice
            headers = analyzer.get_cluster_headers(cluster_id, limit=20)

            # 갤러리용 이미지 (경로만)
            gallery_images = []
            for h in headers:
                img_path = h.get('image_path')
                if img_path and Path(img_path).exists():
                    gallery_images.append(img_path)

            print(f"[DEBUG] 클러스터 {cluster_id}: {len(gallery_images)}개 이미지 로드")

            # 클러스터 통계
            stats = f"**{len(headers)}개 헤더** | 클러스터 #{cluster_id}"

            # 공통 패턴 분석
            patterns = analyzer.analyze_cluster_patterns(cluster_id)

            noise_html = format_patterns_html(patterns.get('noise_patterns', []), "noise")
            meta_html = format_patterns_html(patterns.get('meta_patterns', []), "meta")

            return (cluster_id, headers, None, stats, gallery_images, noise_html, meta_html)

        def on_gallery_select(headers, evt: gr.SelectData):
            """갤러리에서 헤더 선택"""
            if not headers or evt.index >= len(headers):
                return None, "", "<p>선택 오류</p>"

            header = headers[evt.index]
            header_id = header['id']

            # 상세 정보 로드
            detail = analyzer.get_header_detail(header_id)
            if not detail:
                return None, "", "<p>헤더를 찾을 수 없습니다</p>"

            # 이미지
            img_path = detail.get('image_path')

            # 헤더 정보
            info = f"""
**{detail['jenis']} {detail['nomor']} ({detail['tahun']})**

{detail.get('tentang', '')[:100]}...
            """

            # 라인 분석 HTML
            raw_text = detail.get('raw_text', '')
            lines = raw_text.strip().split('\n') if raw_text else []

            line_html = format_lines_html(lines, analyzer)

            return img_path, info, line_html

        def format_patterns_html(patterns: list, ptype: str) -> str:
            """패턴 목록 HTML 포맷팅"""
            if not patterns:
                return "<p style='color: #666;'>패턴 없음</p>"

            html_parts = ['<div style="font-size: 13px;">']
            for p in patterns:
                ratio_pct = p.get('ratio', 0) * 100
                count = p.get('count', 0)
                text = p.get('text', '')[:50]
                ptype_label = p.get('type', '미분류')

                if ptype == "noise":
                    bg_color = "#ffebee"
                    border_color = "#ef5350"
                else:
                    bg_color = "#e3f2fd"
                    border_color = "#2196f3"

                # HTML escape to prevent XSS from OCR text
                safe_text = html.escape(text)
                safe_label = html.escape(ptype_label)
                html_parts.append(f'''
                <div style="background: {bg_color}; border-left: 3px solid {border_color};
                            padding: 8px; margin: 5px 0; border-radius: 4px;">
                    <div style="display: flex; justify-content: space-between;">
                        <span style="font-weight: bold;">{safe_text}</span>
                        <span style="color: #666; font-size: 11px;">{ratio_pct:.0f}% ({count}개)</span>
                    </div>
                    <div style="color: #666; font-size: 11px; margin-top: 3px;">{safe_label}</div>
                </div>
                ''')
            html_parts.append('</div>')
            return '\n'.join(html_parts)

        def format_lines_html(lines: list, analyzer: PatternAnalyzer) -> str:
            """라인별 분석 HTML"""
            if not lines:
                return "<p>OCR 텍스트가 없습니다</p>"

            html_parts = ['<div style="font-size: 13px; max-height: 350px; overflow-y: auto;">']

            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue

                classification = analyzer._classify_text(line)

                # 분류에 따른 색상
                if "노이즈" in classification:
                    bg_color = "#ffebee"
                    border_color = "#ef5350"
                    icon = "🗑️"
                elif "메타" in classification:
                    bg_color = "#e3f2fd"
                    border_color = "#2196f3"
                    icon = "📋"
                elif "본문" in classification:
                    bg_color = "#e8f5e9"
                    border_color = "#4caf50"
                    icon = "▶️"
                else:
                    bg_color = "#f5f5f5"
                    border_color = "#9e9e9e"
                    icon = "❓"

                # HTML escape to prevent XSS from OCR text
                safe_line = html.escape(line[:60])
                ellipsis = '...' if len(line) > 60 else ''
                safe_classification = html.escape(classification.split("-")[-1] if "-" in classification else classification)
                html_parts.append(f'''
                <div style="background: {bg_color}; border-left: 4px solid {border_color};
                            padding: 6px 10px; margin: 4px 0; border-radius: 4px;
                            display: flex; align-items: center; gap: 10px;">
                    <span style="color: #666; font-size: 11px; min-width: 25px;">#{i+1}</span>
                    <span style="flex: 1; word-break: break-all;">{safe_line}{ellipsis}</span>
                    <span style="font-size: 11px; white-space: nowrap;">{icon} {safe_classification}</span>
                </div>
                ''')

            html_parts.append('</div>')
            return '\n'.join(html_parts)

        def add_noise_pattern(pattern, desc, cluster_id):
            """노이즈 패턴 추가"""
            if not pattern:
                return "❌ 패턴을 입력하세요"

            success = analyzer.save_noise_pattern(
                pattern=pattern,
                pattern_type='regex',
                description=desc or pattern[:30],
                cluster_id=cluster_id
            )

            if success:
                return f"✅ 패턴 추가됨: `{pattern}`"
            return "❌ 저장 실패"

        def save_current_rule(cluster_id):
            """현재 규칙 저장 (간단 버전)"""
            if not cluster_id:
                return "❌ 클러스터를 선택하세요"

            # 기본 노이즈 라인 (상위 3줄)
            success = analyzer.save_pattern_rule(
                cluster_id=cluster_id,
                noise_lines=[0, 1, 2],
                metadata_lines=[3, 4, 5],
                body_start=6,
                notes="자동 생성된 기본 규칙"
            )

            if success:
                return f"✅ 클러스터 #{cluster_id} 규칙 저장됨"
            return "❌ 저장 실패"

        # ========== 이벤트 연결 ==========

        # 초기 로드
        app.load(load_clusters, outputs=[cluster_dropdown])
        refresh_btn.click(load_clusters, outputs=[cluster_dropdown])

        # 클러스터 선택
        cluster_dropdown.change(
            on_cluster_select,
            inputs=[cluster_dropdown],
            outputs=[current_cluster, current_headers, selected_header_id,
                     cluster_stats, header_gallery, noise_patterns_html, meta_patterns_html]
        )

        # 갤러리 선택
        header_gallery.select(
            on_gallery_select,
            inputs=[current_headers],
            outputs=[header_image, header_info, line_analysis]
        )

        # 패턴 추가
        add_pattern_btn.click(
            add_noise_pattern,
            inputs=[new_pattern, pattern_desc, current_cluster],
            outputs=[pattern_status]
        )

        # 규칙 저장
        save_rule_btn.click(
            save_current_rule,
            inputs=[current_cluster],
            outputs=[pattern_status]
        )

    return app


def launch(port: int = 7865, share: bool = False):
    """앱 실행"""
    app = create_app()
    app.launch(server_port=port, share=share)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=7865)
    parser.add_argument("--share", "-s", action="store_true")
    args = parser.parse_args()

    print("🚀 헤더 패턴 분석기 시작...")
    print(f"   포트: {args.port}")
    launch(port=args.port, share=args.share)
