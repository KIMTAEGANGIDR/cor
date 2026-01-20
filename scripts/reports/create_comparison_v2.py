#!/usr/bin/env python3
"""
PDF 원본 vs HTML 파싱 결과 비교 시스템
- 왼쪽: PDF 원본 뷰어
- 오른쪽: 파싱된 구조를 HTML로 렌더링 (줄바꿈 확인용)
"""

import sqlite3
import json
import re
from pathlib import Path
from html import escape

DB_PATH = "/tmp/peraturan.db"
PROJECT_DIR = Path(__file__).parent.parent
PDF_DIR = PROJECT_DIR / "data" / "pdfs" / "uu"
OUTPUT_PATH = PROJECT_DIR / "delivery" / "comparison_v2.html"

def get_sample_laws():
    """Get 10 sample laws with parsed data"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    cursor.execute("""
        SELECT slug, jenis, nomor, tahun, tentang,
               local_pdf_path, extracted_text, parsed_json,
               parsed_bab_count, parsed_pasal_count
        FROM peraturan
        WHERE jenis = 'UNDANG-UNDANG'
          AND extracted_text IS NOT NULL
          AND parsed_json IS NOT NULL
          AND parsed_pasal_count > 3
        ORDER BY tahun DESC
        LIMIT 10
    """)

    laws = []
    for row in cursor.fetchall():
        try:
            parsed = json.loads(row['parsed_json']) if row['parsed_json'] else {}
        except:
            parsed = {}

        laws.append({
            'slug': row['slug'],
            'nomor': row['nomor'],
            'tahun': row['tahun'],
            'tentang': row['tentang'],
            'pdf_path': row['local_pdf_path'],
            'extracted_text': row['extracted_text'] or '',
            'parsed': parsed,
            'bab_count': row['parsed_bab_count'],
            'pasal_count': row['parsed_pasal_count']
        })

    conn.close()
    return laws

def parsed_to_html(parsed: dict) -> str:
    """Convert parsed JSON structure to formatted HTML"""
    if not parsed:
        return "<p>파싱 데이터 없음</p>"

    html_parts = []

    # Title
    if parsed.get('title'):
        html_parts.append(f'<h1 class="doc-title">{escape(parsed["title"])}</h1>')

    # Preamble
    if parsed.get('preamble'):
        html_parts.append(f'<div class="preamble">{escape(parsed["preamble"])}</div>')

    # Considering (Menimbang)
    if parsed.get('considering'):
        html_parts.append('<div class="considering">')
        html_parts.append('<h3>Menimbang:</h3>')
        for item in parsed.get('considering', []):
            html_parts.append(f'<p class="considering-item">{escape(str(item))}</p>')
        html_parts.append('</div>')

    # Recalling (Mengingat)
    if parsed.get('recalling'):
        html_parts.append('<div class="recalling">')
        html_parts.append('<h3>Mengingat:</h3>')
        for item in parsed.get('recalling', []):
            html_parts.append(f'<p class="recalling-item">{escape(str(item))}</p>')
        html_parts.append('</div>')

    # BABs (Chapters)
    for bab in parsed.get('babs', []):
        bab_num = bab.get('number', '')
        bab_title = bab.get('title', '')
        html_parts.append(f'<div class="bab">')
        html_parts.append(f'<h2 class="bab-header">BAB {escape(bab_num)}</h2>')
        if bab_title:
            html_parts.append(f'<h3 class="bab-title">{escape(bab_title)}</h3>')

        # Pasals (Articles) in BAB
        for pasal in bab.get('pasals', []):
            html_parts.append(render_pasal(pasal))

        html_parts.append('</div>')

    # Standalone Pasals (not in BAB)
    for pasal in parsed.get('pasals', []):
        html_parts.append(render_pasal(pasal))

    # Penjelasan
    if parsed.get('penjelasan'):
        html_parts.append('<div class="penjelasan">')
        html_parts.append('<h2>PENJELASAN</h2>')
        penjelasan = parsed['penjelasan']
        if isinstance(penjelasan, dict):
            if penjelasan.get('umum'):
                html_parts.append(f'<div class="penjelasan-umum"><h3>I. UMUM</h3><p>{escape(str(penjelasan["umum"]))}</p></div>')
            if penjelasan.get('pasal_demi_pasal'):
                html_parts.append('<div class="penjelasan-pasal"><h3>II. PASAL DEMI PASAL</h3>')
                for p in penjelasan.get('pasal_demi_pasal', []):
                    html_parts.append(f'<p>{escape(str(p))}</p>')
                html_parts.append('</div>')
        else:
            html_parts.append(f'<p>{escape(str(penjelasan))}</p>')
        html_parts.append('</div>')

    return '\n'.join(html_parts)

def render_pasal(pasal: dict) -> str:
    """Render a single Pasal to HTML"""
    parts = []
    pasal_num = pasal.get('number', '')
    parts.append(f'<div class="pasal">')
    parts.append(f'<h4 class="pasal-header">Pasal {escape(str(pasal_num))}</h4>')

    # Ayats
    ayats = pasal.get('ayats', [])
    if ayats:
        for ayat in ayats:
            parts.append(render_ayat(ayat))

    # Direct content (if no ayats)
    if pasal.get('content'):
        content = pasal['content']
        if isinstance(content, list):
            for c in content:
                parts.append(f'<p class="pasal-content">{escape(str(c))}</p>')
        else:
            parts.append(f'<p class="pasal-content">{escape(str(content))}</p>')

    parts.append('</div>')
    return '\n'.join(parts)

def render_ayat(ayat: dict) -> str:
    """Render a single Ayat to HTML"""
    parts = []
    ayat_num = ayat.get('number', '')
    parts.append(f'<div class="ayat">')
    parts.append(f'<span class="ayat-num">({ayat_num})</span>')

    if ayat.get('content'):
        content = ayat['content']
        if isinstance(content, list):
            for c in content:
                parts.append(f'<span class="ayat-content">{escape(str(c))}</span>')
        else:
            parts.append(f'<span class="ayat-content">{escape(str(content))}</span>')

    # Hurufs (points)
    for huruf in ayat.get('hurufs', []):
        huruf_letter = huruf.get('letter', '')
        huruf_content = huruf.get('content', '')
        parts.append(f'<div class="huruf"><span class="huruf-letter">{escape(huruf_letter)}.</span> {escape(str(huruf_content))}</div>')

    parts.append('</div>')
    return '\n'.join(parts)

def generate_html(laws):
    """Generate the comparison HTML page"""

    laws_data = []
    for law in laws:
        # Convert PDF path to absolute path for embedding
        pdf_abs_path = str(PROJECT_DIR / law['pdf_path']) if law['pdf_path'] else ''

        # Generate HTML from parsed structure
        parsed_html = parsed_to_html(law['parsed'])

        laws_data.append({
            'slug': law['slug'],
            'title': f"UU No. {law['nomor']} Tahun {law['tahun']}",
            'tentang': law['tentang'],
            'stats': f"BAB: {law['bab_count']}, Pasal: {law['pasal_count']}",
            'pdf_path': pdf_abs_path,
            'parsed_html': parsed_html,
            'extracted_text': law['extracted_text'][:20000]
        })

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PDF vs 파싱 결과 비교</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f0f1a;
            color: #eee;
            min-height: 100vh;
        }}
        .header {{
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }}
        .header h1 {{ font-size: 20px; }}
        .header .subtitle {{ font-size: 14px; opacity: 0.8; }}

        .law-tabs {{
            display: flex;
            gap: 5px;
            padding: 10px 20px;
            background: #1a1a2e;
            overflow-x: auto;
            border-bottom: 1px solid #333;
        }}
        .law-tab {{
            padding: 8px 16px;
            background: #252540;
            border: none;
            color: #aaa;
            border-radius: 6px;
            cursor: pointer;
            white-space: nowrap;
            font-size: 13px;
        }}
        .law-tab:hover {{ background: #353560; color: #fff; }}
        .law-tab.active {{ background: #4a7dff; color: #fff; }}

        .info-bar {{
            background: #1a1a2e;
            padding: 10px 20px;
            border-bottom: 1px solid #333;
            font-size: 14px;
        }}
        .info-bar .title {{ color: #4a7dff; font-weight: bold; }}
        .info-bar .stats {{ color: #7ee787; margin-left: 20px; }}

        .comparison {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            height: calc(100vh - 130px);
        }}

        .panel {{
            display: flex;
            flex-direction: column;
            border-right: 1px solid #333;
        }}
        .panel:last-child {{ border-right: none; }}

        .panel-header {{
            background: #252540;
            padding: 10px 15px;
            font-weight: bold;
            font-size: 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #333;
        }}
        .panel-header .badge {{
            background: #e94560;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
        }}
        .panel-header .badge.xml {{ background: #4a7dff; }}

        .panel-content {{
            flex: 1;
            overflow-y: auto;
            padding: 20px;
        }}

        /* PDF iframe */
        .pdf-frame {{
            width: 100%;
            height: 100%;
            border: none;
            background: #fff;
        }}

        /* Parsed HTML Styles */
        .parsed-content {{
            font-family: 'Noto Sans', sans-serif;
            font-size: 14px;
            line-height: 1.8;
            color: #e0e0e0;
        }}
        .parsed-content .doc-title {{
            font-size: 18px;
            color: #fff;
            text-align: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #4a7dff;
        }}
        .parsed-content .preamble {{
            background: #1a2a4a;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            white-space: pre-wrap;
        }}
        .parsed-content .considering,
        .parsed-content .recalling {{
            background: #1a2a3a;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 15px;
        }}
        .parsed-content .considering h3,
        .parsed-content .recalling h3 {{
            color: #7ee787;
            margin-bottom: 10px;
        }}
        .parsed-content .considering-item,
        .parsed-content .recalling-item {{
            margin-left: 20px;
            margin-bottom: 8px;
            padding-left: 15px;
            border-left: 3px solid #4a7dff;
        }}
        .parsed-content .bab {{
            margin: 25px 0;
            padding: 15px;
            background: #151525;
            border-radius: 8px;
            border-left: 4px solid #e94560;
        }}
        .parsed-content .bab-header {{
            color: #e94560;
            font-size: 16px;
            text-align: center;
        }}
        .parsed-content .bab-title {{
            color: #ffa657;
            font-size: 14px;
            text-align: center;
            margin-bottom: 15px;
        }}
        .parsed-content .pasal {{
            margin: 15px 0;
            padding: 12px;
            background: #1a1a30;
            border-radius: 6px;
        }}
        .parsed-content .pasal-header {{
            color: #4a7dff;
            font-size: 15px;
            margin-bottom: 10px;
        }}
        .parsed-content .pasal-content {{
            margin-left: 20px;
            white-space: pre-wrap;
        }}
        .parsed-content .ayat {{
            margin: 8px 0 8px 20px;
            padding: 8px;
            background: #202040;
            border-radius: 4px;
        }}
        .parsed-content .ayat-num {{
            color: #7ee787;
            font-weight: bold;
            margin-right: 10px;
        }}
        .parsed-content .ayat-content {{
            white-space: pre-wrap;
        }}
        .parsed-content .huruf {{
            margin: 5px 0 5px 40px;
            padding: 5px 10px;
            background: #252550;
            border-radius: 4px;
        }}
        .parsed-content .huruf-letter {{
            color: #ffa657;
            font-weight: bold;
        }}
        .parsed-content .penjelasan {{
            margin-top: 30px;
            padding: 20px;
            background: #1a2a2a;
            border-radius: 8px;
            border-left: 4px solid #7ee787;
        }}
        .parsed-content .penjelasan h2 {{
            color: #7ee787;
            margin-bottom: 15px;
        }}
        .parsed-content .penjelasan h3 {{
            color: #ffa657;
            margin: 15px 0 10px 0;
        }}

        /* Extracted text view */
        .extracted-text {{
            font-family: monospace;
            font-size: 13px;
            white-space: pre-wrap;
            line-height: 1.6;
            color: #ffa657;
        }}

        .view-toggle {{
            display: flex;
            gap: 5px;
        }}
        .view-toggle button {{
            padding: 5px 12px;
            background: #353560;
            border: none;
            color: #aaa;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
        }}
        .view-toggle button.active {{
            background: #4a7dff;
            color: #fff;
        }}

        ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
        ::-webkit-scrollbar-track {{ background: #1a1a2e; }}
        ::-webkit-scrollbar-thumb {{ background: #4a4a6a; border-radius: 5px; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>📄 PDF vs 파싱 결과 비교</h1>
            <div class="subtitle">줄바꿈 및 구조 파싱 검증용</div>
        </div>
    </div>

    <div class="law-tabs" id="lawTabs"></div>

    <div class="info-bar" id="infoBar">
        <span class="title">법령을 선택하세요</span>
    </div>

    <div class="comparison">
        <div class="panel">
            <div class="panel-header">
                <span>📑 PDF 원본</span>
                <span class="badge">PDF</span>
            </div>
            <div class="panel-content" id="pdfPanel">
                <iframe class="pdf-frame" id="pdfFrame"></iframe>
            </div>
        </div>

        <div class="panel">
            <div class="panel-header">
                <span>🔖 파싱 결과 (HTML)</span>
                <div class="view-toggle">
                    <button id="btnHtml" class="active" onclick="setView('html')">구조화</button>
                    <button id="btnText" onclick="setView('text')">원본텍스트</button>
                </div>
            </div>
            <div class="panel-content" id="parsedPanel">
                <div class="parsed-content" id="parsedContent"></div>
            </div>
        </div>
    </div>

    <script>
        const lawsData = {json.dumps(laws_data, ensure_ascii=False)};

        let currentLaw = null;
        let currentView = 'html';

        function init() {{
            const tabs = document.getElementById('lawTabs');
            lawsData.forEach((law, index) => {{
                const tab = document.createElement('button');
                tab.className = 'law-tab';
                tab.textContent = law.title;
                tab.onclick = () => selectLaw(index);
                tabs.appendChild(tab);
            }});

            if (lawsData.length > 0) {{
                selectLaw(0);
            }}
        }}

        function selectLaw(index) {{
            currentLaw = lawsData[index];

            // Update tabs
            document.querySelectorAll('.law-tab').forEach((tab, i) => {{
                tab.classList.toggle('active', i === index);
            }});

            // Update info bar
            document.getElementById('infoBar').innerHTML = `
                <span class="title">${{currentLaw.title}}</span>
                <span class="stats">${{currentLaw.tentang}}</span>
                <span class="stats" style="margin-left:20px;">${{currentLaw.stats}}</span>
            `;

            // Load PDF
            const pdfFrame = document.getElementById('pdfFrame');
            pdfFrame.src = 'file://' + currentLaw.pdf_path;

            // Update parsed content
            updateParsedContent();
        }}

        function setView(view) {{
            currentView = view;
            document.getElementById('btnHtml').classList.toggle('active', view === 'html');
            document.getElementById('btnText').classList.toggle('active', view === 'text');
            updateParsedContent();
        }}

        function updateParsedContent() {{
            if (!currentLaw) return;

            const container = document.getElementById('parsedContent');
            if (currentView === 'html') {{
                container.className = 'parsed-content';
                container.innerHTML = currentLaw.parsed_html;
            }} else {{
                container.className = 'extracted-text';
                container.textContent = currentLaw.extracted_text;
            }}
        }}

        init();
    </script>
</body>
</html>
'''

    return html

def main():
    print("법령 데이터 로드 중...")
    laws = get_sample_laws()
    print(f"{len(laws)}개 법령 로드됨")

    print("HTML 생성 중...")
    html = generate_html(laws)

    print(f"저장 중: {OUTPUT_PATH}")
    OUTPUT_PATH.write_text(html, encoding='utf-8')

    print(f"\n✅ 완료: {OUTPUT_PATH}")
    print(f"   파일 크기: {OUTPUT_PATH.stat().st_size / 1024:.1f} KB")

if __name__ == '__main__':
    main()
