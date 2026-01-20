#!/usr/bin/env python3
"""
연도별 파싱 샘플 비교 웹페이지 생성
- 각 시대별 샘플 추출
- 원본 vs 전처리 결과 비교
- 들여쓰기 적용된 구조화 뷰
"""

import sqlite3
import json
import sys
from pathlib import Path
from html import escape

# 프로젝트 경로 추가
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.preprocessor import LawTextPreprocessor

DB_PATH = "/tmp/peraturan.db"
OUTPUT_PATH = PROJECT_DIR / "delivery" / "era_comparison.html"

# 시대 정의
ERAS = [
    {"name": "1945-1959", "start": 1945, "end": 1959, "label": "독립 초기", "color": "#8b4513"},
    {"name": "1960-1969", "start": 1960, "end": 1969, "label": "수카르노 시대", "color": "#a0522d"},
    {"name": "1970-1979", "start": 1970, "end": 1979, "label": "신질서 초기", "color": "#cd853f"},
    {"name": "1980-1989", "start": 1980, "end": 1989, "label": "신질서 중기", "color": "#daa520"},
    {"name": "1990-1999", "start": 1990, "end": 1999, "label": "민주화 이행기", "color": "#b8860b"},
    {"name": "2000-2009", "start": 2000, "end": 2009, "label": "개혁 시대", "color": "#2e8b57"},
    {"name": "2010-2019", "start": 2010, "end": 2019, "label": "전자정부", "color": "#20b2aa"},
    {"name": "2020-2025", "start": 2020, "end": 2025, "label": "최신", "color": "#4169e1"},
]


def get_samples_by_era():
    """각 시대별 샘플 2개씩 추출"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    samples = []

    for era in ERAS:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT slug, jenis, nomor, tahun, tentang, extracted_text
            FROM peraturan
            WHERE jenis = 'UNDANG-UNDANG'
              AND tahun BETWEEN ? AND ?
              AND extracted_text IS NOT NULL
              AND LENGTH(extracted_text) > 500
            ORDER BY RANDOM()
            LIMIT 2
        """, (era['start'], era['end']))

        for row in cursor.fetchall():
            samples.append({
                'era': era['name'],
                'era_label': era['label'],
                'era_color': era['color'],
                'slug': row['slug'],
                'nomor': row['nomor'],
                'tahun': row['tahun'],
                'tentang': row['tentang'],
                'text': row['extracted_text'][:8000]  # 앞부분만
            })

    conn.close()
    return samples


def apply_structure_formatting(text: str) -> str:
    """구조 요소에 들여쓰기 적용하여 HTML 생성"""
    import re

    lines = text.split('\n')
    html_parts = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_parts.append('<div class="blank-line"></div>')
            continue

        escaped = escape(stripped)

        # BAB (장)
        if re.match(r'^BAB\s+[IVXLCDM]+', stripped, re.IGNORECASE):
            html_parts.append(f'<div class="bab">{escaped}</div>')

        # BAB 제목 (대문자로 된 제목)
        elif re.match(r'^[A-Z\s]{10,}$', stripped) and 'UNDANG' not in stripped:
            html_parts.append(f'<div class="bab-title">{escaped}</div>')

        # Pasal
        elif re.match(r'^Pasal\s+\d+', stripped, re.IGNORECASE):
            html_parts.append(f'<div class="pasal">{escaped}</div>')

        # Ayat - (1), (2), ...
        elif re.match(r'^\(\d+\)', stripped):
            html_parts.append(f'<div class="ayat">{escaped}</div>')

        # Huruf - a., b., c., ...
        elif re.match(r'^[a-z]\.', stripped):
            html_parts.append(f'<div class="huruf">{escaped}</div>')

        # Angka - 1., 2., 3., ...
        elif re.match(r'^\d+\.', stripped):
            html_parts.append(f'<div class="angka">{escaped}</div>')

        # Menimbang/Mengingat
        elif re.match(r'^Menimbang|^Mengingat|^Menetapkan|^MEMUTUSKAN', stripped, re.IGNORECASE):
            html_parts.append(f'<div class="header-section">{escaped}</div>')

        # 일반 텍스트
        else:
            html_parts.append(f'<div class="text">{escaped}</div>')

    return '\n'.join(html_parts)


def generate_html(samples):
    """비교 HTML 페이지 생성"""

    preprocessor = LawTextPreprocessor()

    samples_data = []
    for s in samples:
        # 전처리 적용
        result = preprocessor.preprocess(s['text'])

        # 구조화 HTML 생성
        structured_html = apply_structure_formatting(result.cleaned)

        samples_data.append({
            'era': s['era'],
            'era_label': s['era_label'],
            'era_color': s['era_color'],
            'title': f"UU No. {s['nomor']} Tahun {s['tahun']}",
            'tentang': s['tentang'][:100],
            'original': s['text'][:5000],
            'cleaned': result.cleaned[:5000],
            'structured': structured_html,
            'changes': result.changes
        })

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>연도별 법령 파싱 비교</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Malgun Gothic', sans-serif;
            background: #0d1117;
            color: #c9d1d9;
        }}

        .header {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            padding: 20px;
            text-align: center;
            border-bottom: 2px solid #30363d;
        }}
        .header h1 {{ font-size: 22px; margin-bottom: 8px; }}
        .header p {{ color: #8b949e; font-size: 14px; }}

        .era-nav {{
            display: flex;
            gap: 8px;
            padding: 15px 20px;
            background: #161b22;
            overflow-x: auto;
            border-bottom: 1px solid #30363d;
        }}
        .era-btn {{
            padding: 10px 20px;
            background: #21262d;
            border: 2px solid #30363d;
            color: #8b949e;
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            white-space: nowrap;
            transition: all 0.2s;
        }}
        .era-btn:hover {{ background: #30363d; color: #fff; }}
        .era-btn.active {{ border-color: var(--era-color); color: #fff; background: #30363d; }}

        .sample-nav {{
            display: flex;
            gap: 5px;
            padding: 10px 20px;
            background: #0d1117;
            border-bottom: 1px solid #30363d;
        }}
        .sample-btn {{
            padding: 6px 14px;
            background: #21262d;
            border: 1px solid #30363d;
            color: #8b949e;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
        }}
        .sample-btn.active {{ background: #238636; border-color: #238636; color: #fff; }}

        .info-bar {{
            background: #161b22;
            padding: 12px 20px;
            border-bottom: 1px solid #30363d;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .info-bar .title {{ font-size: 15px; font-weight: bold; }}
        .info-bar .era-badge {{
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            color: #fff;
        }}

        .main {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            height: calc(100vh - 180px);
        }}

        .panel {{
            display: flex;
            flex-direction: column;
            border-right: 1px solid #30363d;
            overflow: hidden;
        }}
        .panel:last-child {{ border-right: none; }}

        .panel-head {{
            background: #21262d;
            padding: 10px 15px;
            border-bottom: 1px solid #30363d;
            font-size: 13px;
            font-weight: bold;
        }}
        .panel-head .badge {{
            float: right;
            padding: 2px 8px;
            border-radius: 8px;
            font-size: 10px;
        }}
        .badge.original {{ background: #da3633; }}
        .badge.cleaned {{ background: #238636; }}
        .badge.structured {{ background: #1f6feb; }}

        .panel-body {{
            flex: 1;
            overflow-y: auto;
            padding: 15px;
            font-size: 13px;
            line-height: 1.7;
        }}

        /* 원본 텍스트 */
        .raw-text {{
            font-family: 'Monaco', 'Menlo', monospace;
            white-space: pre-wrap;
            color: #ffa657;
        }}

        /* 정제된 텍스트 */
        .clean-text {{
            font-family: 'Monaco', 'Menlo', monospace;
            white-space: pre-wrap;
            color: #7ee787;
        }}

        /* 구조화된 뷰 */
        .structured-view {{ font-family: 'Noto Sans', sans-serif; }}
        .structured-view .bab {{
            font-size: 16px;
            font-weight: bold;
            color: #ff7b72;
            text-align: center;
            margin: 20px 0 5px 0;
            padding: 10px;
            background: #21262d;
            border-radius: 6px;
        }}
        .structured-view .bab-title {{
            font-size: 14px;
            font-weight: bold;
            color: #ffa657;
            text-align: center;
            margin-bottom: 15px;
        }}
        .structured-view .pasal {{
            font-size: 14px;
            font-weight: bold;
            color: #58a6ff;
            margin: 15px 0 8px 0;
            padding: 8px 12px;
            background: #161b22;
            border-left: 3px solid #58a6ff;
            border-radius: 4px;
        }}
        .structured-view .ayat {{
            margin-left: 20px;
            padding: 6px 12px;
            background: #0d1117;
            border-radius: 4px;
            margin-bottom: 4px;
        }}
        .structured-view .huruf {{
            margin-left: 45px;
            padding: 4px 10px;
            color: #d2a8ff;
        }}
        .structured-view .angka {{
            margin-left: 70px;
            padding: 4px 10px;
            color: #a5d6ff;
        }}
        .structured-view .header-section {{
            font-weight: bold;
            color: #7ee787;
            margin: 15px 0 8px 0;
            padding: 8px;
            background: #1a2e1a;
            border-radius: 4px;
        }}
        .structured-view .text {{
            padding: 2px 0;
        }}
        .structured-view .blank-line {{
            height: 10px;
        }}

        .changes-list {{
            background: #161b22;
            padding: 10px 15px;
            border-top: 1px solid #30363d;
            font-size: 11px;
            color: #8b949e;
            max-height: 100px;
            overflow-y: auto;
        }}
        .changes-list strong {{ color: #58a6ff; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 연도별 법령 파싱 비교</h1>
        <p>시대별 PDF 원본 → 전처리 → 구조화 비교</p>
    </div>

    <div class="era-nav" id="eraNav"></div>
    <div class="sample-nav" id="sampleNav"></div>

    <div class="info-bar" id="infoBar">
        <span class="title">시대를 선택하세요</span>
        <span class="era-badge" id="eraBadge"></span>
    </div>

    <div class="main">
        <div class="panel">
            <div class="panel-head">
                📄 원본 텍스트
                <span class="badge original">ORIGINAL</span>
            </div>
            <div class="panel-body raw-text" id="originalView"></div>
        </div>
        <div class="panel">
            <div class="panel-head">
                ✨ 전처리 결과
                <span class="badge cleaned">CLEANED</span>
            </div>
            <div class="panel-body clean-text" id="cleanedView"></div>
            <div class="changes-list" id="changesList"></div>
        </div>
        <div class="panel">
            <div class="panel-head">
                🏗️ 구조화 뷰
                <span class="badge structured">STRUCTURED</span>
            </div>
            <div class="panel-body structured-view" id="structuredView"></div>
        </div>
    </div>

    <script>
        const data = {json.dumps(samples_data, ensure_ascii=False)};
        const eras = {json.dumps(ERAS, ensure_ascii=False)};

        let currentEra = null;
        let currentSampleIdx = 0;

        function init() {{
            // Era buttons
            const eraNav = document.getElementById('eraNav');
            eras.forEach(era => {{
                const btn = document.createElement('button');
                btn.className = 'era-btn';
                btn.style.setProperty('--era-color', era.color);
                btn.innerHTML = `<strong>${{era.name}}</strong><br><small>${{era.label}}</small>`;
                btn.onclick = () => selectEra(era.name);
                eraNav.appendChild(btn);
            }});

            selectEra(eras[0].name);
        }}

        function selectEra(eraName) {{
            currentEra = eraName;
            currentSampleIdx = 0;

            // Update era buttons
            document.querySelectorAll('.era-btn').forEach((btn, i) => {{
                btn.classList.toggle('active', eras[i].name === eraName);
            }});

            // Get samples for this era
            const eraSamples = data.filter(d => d.era === eraName);

            // Update sample buttons
            const sampleNav = document.getElementById('sampleNav');
            sampleNav.innerHTML = '';
            eraSamples.forEach((s, i) => {{
                const btn = document.createElement('button');
                btn.className = 'sample-btn' + (i === 0 ? ' active' : '');
                btn.textContent = s.title;
                btn.onclick = () => selectSample(i);
                sampleNav.appendChild(btn);
            }});

            if (eraSamples.length > 0) {{
                showSample(eraSamples[0]);
            }}
        }}

        function selectSample(idx) {{
            currentSampleIdx = idx;
            const eraSamples = data.filter(d => d.era === currentEra);

            document.querySelectorAll('.sample-btn').forEach((btn, i) => {{
                btn.classList.toggle('active', i === idx);
            }});

            if (eraSamples[idx]) {{
                showSample(eraSamples[idx]);
            }}
        }}

        function showSample(sample) {{
            // Info bar
            document.getElementById('infoBar').innerHTML = `
                <span class="title">${{sample.title}} - ${{sample.tentang}}</span>
                <span class="era-badge" style="background: ${{sample.era_color}}">${{sample.era}} ${{sample.era_label}}</span>
            `;

            // Panels
            document.getElementById('originalView').textContent = sample.original;
            document.getElementById('cleanedView').textContent = sample.cleaned;
            document.getElementById('structuredView').innerHTML = sample.structured;

            // Changes
            const changesList = document.getElementById('changesList');
            if (sample.changes.length > 0) {{
                changesList.innerHTML = '<strong>적용된 변경:</strong> ' +
                    sample.changes.map(c => `<span>${{c}}</span>`).join(' | ');
            }} else {{
                changesList.innerHTML = '<strong>변경 없음</strong>';
            }}
        }}

        init();
    </script>
</body>
</html>'''

    return html


def main():
    print("샘플 추출 중...")
    samples = get_samples_by_era()
    print(f"{len(samples)}개 샘플 추출")

    print("HTML 생성 중...")
    html = generate_html(samples)

    print(f"저장 중: {OUTPUT_PATH}")
    OUTPUT_PATH.write_text(html, encoding='utf-8')
    print(f"✅ 완료: {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
