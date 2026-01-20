#!/usr/bin/env python3
"""
PDF 원본 vs 구조화 뷰 비교 웹페이지
- 왼쪽: PDF 원본 뷰어
- 오른쪽: 전처리 + 구조화된 뷰
- 연도별 샘플
"""

import sqlite3
import json
import sys
from pathlib import Path
from html import escape
import re

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.preprocessor import LawTextPreprocessor

DB_PATH = "/tmp/peraturan.db"
OUTPUT_PATH = PROJECT_DIR / "delivery" / "pdf_vs_structure.html"

ERAS = [
    {"name": "1950s", "start": 1945, "end": 1959, "label": "독립 초기"},
    {"name": "1960s", "start": 1960, "end": 1969, "label": "수카르노"},
    {"name": "1970s", "start": 1970, "end": 1979, "label": "신질서 초기"},
    {"name": "1980s", "start": 1980, "end": 1989, "label": "신질서 중기"},
    {"name": "1990s", "start": 1990, "end": 1999, "label": "민주화"},
    {"name": "2000s", "start": 2000, "end": 2009, "label": "개혁"},
    {"name": "2010s", "start": 2010, "end": 2019, "label": "전자정부"},
    {"name": "2020s", "start": 2020, "end": 2025, "label": "최신"},
]


def get_samples():
    """각 시대별 샘플 1개씩"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    samples = []

    for era in ERAS:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT slug, nomor, tahun, tentang, local_pdf_path, extracted_text
            FROM peraturan
            WHERE jenis = 'UNDANG-UNDANG'
              AND tahun BETWEEN ? AND ?
              AND extracted_text IS NOT NULL
              AND local_pdf_path IS NOT NULL
              AND LENGTH(extracted_text) > 1000
            ORDER BY tahun DESC
            LIMIT 1
        """, (era['start'], era['end']))

        row = cursor.fetchone()
        if row:
            samples.append({
                'era': era['name'],
                'era_label': era['label'],
                'slug': row['slug'],
                'nomor': row['nomor'],
                'tahun': row['tahun'],
                'tentang': row['tentang'],
                'pdf_path': str(PROJECT_DIR / row['local_pdf_path']),
                'text': row['extracted_text']
            })

    conn.close()
    return samples


def structure_text(text: str) -> str:
    """텍스트를 구조화된 HTML로 변환"""
    preprocessor = LawTextPreprocessor()
    result = preprocessor.preprocess(text)
    cleaned = result.cleaned

    lines = cleaned.split('\n')
    html_parts = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_parts.append('<div class="blank"></div>')
            continue

        escaped = escape(stripped)

        # UNDANG-UNDANG 제목
        if re.match(r'^UNDANG-UNDANG', stripped):
            html_parts.append(f'<div class="title">{escaped}</div>')

        # BAB
        elif re.match(r'^BAB\s+[IVXLCDM]+', stripped, re.IGNORECASE):
            html_parts.append(f'<div class="bab">{escaped}</div>')

        # BAB 제목 (전부 대문자)
        elif re.match(r'^[A-Z\s,]{5,}$', stripped) and len(stripped) < 100:
            html_parts.append(f'<div class="bab-title">{escaped}</div>')

        # Pasal (아라비아 숫자 또는 로마자)
        elif re.match(r'^Pasal\s+(\d+|[IVXLCDM]+)', stripped, re.IGNORECASE):
            html_parts.append(f'<div class="pasal">{escaped}</div>')

        # Ayat (1), (2)
        elif re.match(r'^\(\d+\)', stripped):
            html_parts.append(f'<div class="ayat"><span class="num">{escaped[:3]}</span>{escaped[3:]}</div>')

        # Huruf a., b.
        elif re.match(r'^[a-z]\.', stripped):
            html_parts.append(f'<div class="huruf"><span class="num">{escaped[:2]}</span>{escaped[2:]}</div>')

        # Sub-angka 6a., 6b.
        elif re.match(r'^\d+[a-z]\.', stripped):
            match = re.match(r'^(\d+[a-z]\.)', stripped)
            num = match.group(1)
            rest = stripped[len(num):]
            html_parts.append(f'<div class="angka sub"><span class="num">{num}</span>{escape(rest)}</div>')

        # Angka 1., 2.
        elif re.match(r'^\d+\.\s', stripped):
            match = re.match(r'^(\d+\.)', stripped)
            num = match.group(1)
            rest = stripped[len(num):]
            html_parts.append(f'<div class="angka"><span class="num">{num}</span>{escape(rest)}</div>')

        # Menimbang, Mengingat
        elif re.match(r'^(Menimbang|Mengingat|Menetapkan|MEMUTUSKAN)', stripped, re.IGNORECASE):
            html_parts.append(f'<div class="section-header">{escaped}</div>')

        # 일반
        else:
            html_parts.append(f'<div class="text">{escaped}</div>')

    return '\n'.join(html_parts)


def generate_html(samples):
    samples_data = []
    for s in samples:
        structured = structure_text(s['text'][:15000])
        samples_data.append({
            'era': s['era'],
            'era_label': s['era_label'],
            'title': f"UU No. {s['nomor']} Tahun {s['tahun']}",
            'tentang': s['tentang'][:80],
            'pdf_path': s['pdf_path'],
            'structured': structured
        })

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>PDF vs 구조화 비교</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: system-ui, sans-serif; background: #0d1117; color: #c9d1d9; }}

        .header {{
            background: linear-gradient(90deg, #1f6feb, #238636);
            padding: 15px 25px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .header h1 {{ font-size: 18px; color: white; }}

        .era-tabs {{
            display: flex;
            background: #161b22;
            border-bottom: 1px solid #30363d;
            overflow-x: auto;
        }}
        .era-tab {{
            padding: 12px 20px;
            background: none;
            border: none;
            border-bottom: 3px solid transparent;
            color: #8b949e;
            cursor: pointer;
            font-size: 13px;
            white-space: nowrap;
        }}
        .era-tab:hover {{ color: #c9d1d9; background: #21262d; }}
        .era-tab.active {{
            color: #58a6ff;
            border-bottom-color: #58a6ff;
            background: #21262d;
        }}

        .info {{
            background: #161b22;
            padding: 10px 25px;
            border-bottom: 1px solid #30363d;
            font-size: 14px;
        }}
        .info .law-title {{ color: #58a6ff; font-weight: bold; }}
        .info .law-desc {{ color: #8b949e; margin-left: 15px; }}

        .main {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            height: calc(100vh - 120px);
        }}

        .panel {{
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        .panel:first-child {{ border-right: 2px solid #30363d; }}

        .panel-header {{
            background: #21262d;
            padding: 10px 20px;
            font-weight: bold;
            font-size: 14px;
            border-bottom: 1px solid #30363d;
            display: flex;
            justify-content: space-between;
        }}
        .panel-header .tag {{
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: normal;
        }}
        .tag.pdf {{ background: #da3633; }}
        .tag.struct {{ background: #238636; }}

        .panel-body {{
            flex: 1;
            overflow: auto;
        }}

        iframe {{
            width: 100%;
            height: 100%;
            border: none;
            background: white;
        }}

        .structured {{
            padding: 20px;
            font-size: 14px;
            line-height: 1.8;
        }}

        .structured .title {{
            font-size: 16px;
            font-weight: bold;
            color: #fff;
            text-align: center;
            padding: 15px;
            background: #21262d;
            border-radius: 8px;
            margin-bottom: 20px;
        }}

        .structured .bab {{
            font-size: 15px;
            font-weight: bold;
            color: #ff7b72;
            text-align: center;
            margin: 25px 0 5px 0;
            padding: 12px;
            background: linear-gradient(90deg, #21262d, #2d1f1f, #21262d);
            border-radius: 6px;
        }}

        .structured .bab-title {{
            font-size: 13px;
            font-weight: bold;
            color: #ffa657;
            text-align: center;
            margin-bottom: 15px;
        }}

        .structured .pasal {{
            font-weight: bold;
            color: #58a6ff;
            margin: 20px 0 10px 0;
            padding: 10px 15px;
            background: #161b22;
            border-left: 4px solid #58a6ff;
            border-radius: 0 6px 6px 0;
        }}

        .structured .ayat {{
            margin: 8px 0 8px 25px;
            padding: 8px 15px;
            background: #0d1117;
            border-radius: 6px;
            border-left: 2px solid #30363d;
        }}
        .structured .ayat .num {{
            color: #7ee787;
            font-weight: bold;
            margin-right: 8px;
        }}

        .structured .huruf {{
            margin: 4px 0 4px 50px;
            padding: 6px 12px;
            color: #d2a8ff;
        }}
        .structured .huruf .num {{
            font-weight: bold;
            margin-right: 5px;
        }}

        .structured .angka {{
            margin: 4px 0 4px 75px;
            padding: 5px 10px;
            color: #a5d6ff;
            font-size: 13px;
        }}
        .structured .angka .num {{
            font-weight: bold;
            margin-right: 5px;
        }}

        .structured .section-header {{
            font-weight: bold;
            color: #7ee787;
            margin: 20px 0 10px 0;
            padding: 10px 15px;
            background: #0d2818;
            border-radius: 6px;
        }}

        .structured .text {{
            padding: 3px 0;
            color: #c9d1d9;
        }}

        .structured .blank {{
            height: 8px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📄 PDF 원본 vs 🏗️ 구조화 뷰 비교</h1>
        <span style="color: rgba(255,255,255,0.7); font-size: 13px;">연도별 파싱 품질 검증</span>
    </div>

    <div class="era-tabs" id="eraTabs"></div>

    <div class="info" id="info">
        <span class="law-title">법령을 선택하세요</span>
    </div>

    <div class="main">
        <div class="panel">
            <div class="panel-header">
                📄 PDF 원본
                <span class="tag pdf">PDF</span>
            </div>
            <div class="panel-body">
                <iframe id="pdfFrame"></iframe>
            </div>
        </div>
        <div class="panel">
            <div class="panel-header">
                🏗️ 구조화 뷰 (전처리 적용)
                <span class="tag struct">STRUCTURED</span>
            </div>
            <div class="panel-body structured" id="structuredView"></div>
        </div>
    </div>

    <script>
        const data = {json.dumps(samples_data, ensure_ascii=False)};

        function init() {{
            const tabs = document.getElementById('eraTabs');
            data.forEach((d, i) => {{
                const tab = document.createElement('button');
                tab.className = 'era-tab' + (i === 0 ? ' active' : '');
                tab.innerHTML = `${{d.era}}<br><small>${{d.era_label}}</small>`;
                tab.onclick = () => select(i);
                tabs.appendChild(tab);
            }});
            select(0);
        }}

        function select(idx) {{
            const d = data[idx];

            document.querySelectorAll('.era-tab').forEach((t, i) => {{
                t.classList.toggle('active', i === idx);
            }});

            document.getElementById('info').innerHTML = `
                <span class="law-title">${{d.title}}</span>
                <span class="law-desc">${{d.tentang}}</span>
            `;

            document.getElementById('pdfFrame').src = 'file://' + d.pdf_path;
            document.getElementById('structuredView').innerHTML = d.structured;
        }}

        init();
    </script>
</body>
</html>'''

    return html


def main():
    print("샘플 추출 중...")
    samples = get_samples()
    print(f"{len(samples)}개 샘플")

    print("HTML 생성 중...")
    html = generate_html(samples)

    OUTPUT_PATH.write_text(html, encoding='utf-8')
    print(f"✅ 완료: {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
