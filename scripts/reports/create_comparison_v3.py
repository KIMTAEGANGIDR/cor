#!/usr/bin/env python3
"""
PDF 원본 vs HTML 파싱 결과 비교 시스템 v3
"""

import sqlite3
import json
from pathlib import Path
from html import escape

DB_PATH = "/tmp/peraturan.db"
PROJECT_DIR = Path(__file__).parent.parent
OUTPUT_PATH = PROJECT_DIR / "delivery" / "comparison_v3.html"

def get_sample_laws():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    cursor.execute("""
        SELECT slug, jenis, nomor, tahun, tentang,
               local_pdf_path, extracted_text, parsed_json,
               parsed_bab_count, parsed_pasal_count
        FROM peraturan
        WHERE jenis = 'UNDANG-UNDANG'
          AND parsed_json IS NOT NULL
          AND LENGTH(parsed_json) > 100
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
    """Convert parsed JSON to HTML - using actual field names"""
    if not parsed:
        return "<p class='error'>파싱 데이터 없음</p>"

    html_parts = []

    # Title from slug
    slug = parsed.get('slug', '')
    if slug:
        html_parts.append(f'<h1 class="doc-title">{escape(slug.upper().replace("-", " "))}</h1>')

    # BABs
    babs = parsed.get('babs', [])
    for bab in babs:
        nomor = bab.get('nomor', '')
        judul = bab.get('judul', '')

        html_parts.append(f'<div class="bab">')
        html_parts.append(f'<h2 class="bab-header">BAB {escape(str(nomor))}</h2>')
        if judul:
            html_parts.append(f'<h3 class="bab-title">{escape(judul)}</h3>')

        # Pasals in BAB
        for pasal in bab.get('pasals', []):
            html_parts.append(render_pasal(pasal))

        html_parts.append('</div>')

    # Standalone Pasals
    pasals = parsed.get('pasals', [])
    if pasals and not babs:
        html_parts.append('<div class="pasals-container">')
        for pasal in pasals:
            html_parts.append(render_pasal(pasal))
        html_parts.append('</div>')

    if not html_parts:
        html_parts.append(f'<pre>{escape(json.dumps(parsed, indent=2, ensure_ascii=False)[:5000])}</pre>')

    return '\n'.join(html_parts)

def render_pasal(pasal: dict) -> str:
    """Render a single Pasal"""
    parts = []
    nomor = pasal.get('nomor', '')
    text = pasal.get('text', '')
    ayats = pasal.get('ayats', [])

    parts.append(f'<div class="pasal">')
    parts.append(f'<h4 class="pasal-header">Pasal {escape(str(nomor))}</h4>')

    # Main text
    if text:
        # Preserve line breaks
        text_html = escape(text).replace('\n', '<br>\n')
        parts.append(f'<div class="pasal-text">{text_html}</div>')

    # Ayats
    for ayat in ayats:
        ayat_nomor = ayat.get('nomor', '')
        ayat_text = ayat.get('text', '')

        parts.append(f'<div class="ayat">')
        parts.append(f'<span class="ayat-num">({ayat_nomor})</span>')
        if ayat_text:
            ayat_html = escape(ayat_text).replace('\n', '<br>\n')
            parts.append(f'<span class="ayat-text">{ayat_html}</span>')
        parts.append('</div>')

    parts.append('</div>')
    return '\n'.join(parts)

def generate_html(laws):
    laws_data = []
    for law in laws:
        pdf_path = str(PROJECT_DIR / law['pdf_path']) if law['pdf_path'] else ''
        parsed_html = parsed_to_html(law['parsed'])

        laws_data.append({
            'slug': law['slug'],
            'title': f"UU No. {law['nomor']} Tahun {law['tahun']}",
            'tentang': law['tentang'][:80],
            'stats': f"BAB: {law['bab_count']}, Pasal: {law['pasal_count']}",
            'pdf_path': pdf_path,
            'parsed_html': parsed_html,
            'extracted_text': law['extracted_text'][:30000]
        })

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>PDF vs 파싱 비교 v3</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: system-ui, sans-serif; background: #0d1117; color: #c9d1d9; }}

        .header {{
            background: linear-gradient(90deg, #238636, #1f6feb);
            padding: 15px 20px;
            position: sticky; top: 0; z-index: 100;
        }}
        .header h1 {{ font-size: 18px; color: white; }}

        .tabs {{
            display: flex; gap: 8px; padding: 12px 20px;
            background: #161b22; border-bottom: 1px solid #30363d;
            overflow-x: auto;
        }}
        .tab {{
            padding: 8px 16px; background: #21262d; border: 1px solid #30363d;
            color: #8b949e; border-radius: 6px; cursor: pointer; font-size: 13px;
            white-space: nowrap;
        }}
        .tab:hover {{ background: #30363d; color: #c9d1d9; }}
        .tab.active {{ background: #238636; border-color: #238636; color: white; }}

        .info {{
            background: #161b22; padding: 10px 20px;
            border-bottom: 1px solid #30363d; font-size: 14px;
        }}
        .info .name {{ color: #58a6ff; font-weight: bold; }}
        .info .detail {{ color: #8b949e; margin-left: 15px; }}

        .main {{ display: grid; grid-template-columns: 1fr 1fr; height: calc(100vh - 110px); }}

        .panel {{ display: flex; flex-direction: column; border-right: 1px solid #30363d; }}
        .panel:last-child {{ border-right: none; }}

        .panel-head {{
            background: #21262d; padding: 10px 15px;
            border-bottom: 1px solid #30363d;
            display: flex; justify-content: space-between; align-items: center;
        }}
        .panel-head .badge {{
            background: #da3633; padding: 2px 8px; border-radius: 10px;
            font-size: 11px; color: white;
        }}
        .panel-head .badge.blue {{ background: #1f6feb; }}

        .panel-body {{ flex: 1; overflow-y: auto; padding: 15px; }}

        iframe {{ width: 100%; height: 100%; border: none; background: white; }}

        /* Parsed content styles */
        .parsed {{ font-size: 14px; line-height: 1.7; }}
        .parsed .doc-title {{
            font-size: 16px; color: #58a6ff; text-align: center;
            padding-bottom: 15px; margin-bottom: 20px; border-bottom: 1px solid #30363d;
        }}
        .parsed .bab {{
            margin: 20px 0; padding: 15px;
            background: #161b22; border-radius: 8px; border-left: 3px solid #da3633;
        }}
        .parsed .bab-header {{ color: #da3633; font-size: 15px; text-align: center; }}
        .parsed .bab-title {{ color: #f0883e; font-size: 14px; text-align: center; margin-bottom: 15px; }}
        .parsed .pasal {{
            margin: 12px 0; padding: 12px;
            background: #0d1117; border-radius: 6px; border: 1px solid #30363d;
        }}
        .parsed .pasal-header {{ color: #58a6ff; font-size: 14px; margin-bottom: 8px; }}
        .parsed .pasal-text {{
            color: #c9d1d9; padding: 10px;
            background: #161b22; border-radius: 4px;
            line-height: 1.8;
        }}
        .parsed .ayat {{
            margin: 8px 0 8px 20px; padding: 8px 12px;
            background: #21262d; border-radius: 4px;
        }}
        .parsed .ayat-num {{ color: #7ee787; font-weight: bold; margin-right: 8px; }}
        .parsed .ayat-text {{ color: #c9d1d9; }}
        .parsed .error {{ color: #f85149; padding: 20px; text-align: center; }}

        .raw-text {{
            font-family: monospace; font-size: 13px;
            white-space: pre-wrap; color: #f0883e; line-height: 1.6;
        }}

        .view-btns {{ display: flex; gap: 5px; }}
        .view-btns button {{
            padding: 4px 10px; background: #30363d; border: none;
            color: #8b949e; border-radius: 4px; cursor: pointer; font-size: 11px;
        }}
        .view-btns button.active {{ background: #1f6feb; color: white; }}
    </style>
</head>
<body>
    <div class="header"><h1>📄 PDF 원본 vs 파싱 결과 비교</h1></div>
    <div class="tabs" id="tabs"></div>
    <div class="info" id="info"><span class="name">법령 선택</span></div>

    <div class="main">
        <div class="panel">
            <div class="panel-head">
                <span>📑 PDF 원본</span>
                <span class="badge">PDF</span>
            </div>
            <div class="panel-body"><iframe id="pdf"></iframe></div>
        </div>
        <div class="panel">
            <div class="panel-head">
                <span>🔖 파싱 결과</span>
                <div class="view-btns">
                    <button id="btnHtml" class="active" onclick="setView('html')">HTML</button>
                    <button id="btnRaw" onclick="setView('raw')">원본</button>
                </div>
            </div>
            <div class="panel-body" id="content"></div>
        </div>
    </div>

    <script>
        const data = {json.dumps(laws_data, ensure_ascii=False)};
        let cur = null, view = 'html';

        function init() {{
            const tabs = document.getElementById('tabs');
            data.forEach((d, i) => {{
                const t = document.createElement('button');
                t.className = 'tab';
                t.textContent = d.title;
                t.onclick = () => select(i);
                tabs.appendChild(t);
            }});
            if (data.length) select(0);
        }}

        function select(i) {{
            cur = data[i];
            document.querySelectorAll('.tab').forEach((t, j) => t.classList.toggle('active', i === j));
            document.getElementById('info').innerHTML =
                `<span class="name">${{cur.title}}</span><span class="detail">${{cur.tentang}}</span><span class="detail">${{cur.stats}}</span>`;
            document.getElementById('pdf').src = 'file://' + cur.pdf_path;
            render();
        }}

        function setView(v) {{
            view = v;
            document.getElementById('btnHtml').classList.toggle('active', v === 'html');
            document.getElementById('btnRaw').classList.toggle('active', v === 'raw');
            render();
        }}

        function render() {{
            if (!cur) return;
            const el = document.getElementById('content');
            if (view === 'html') {{
                el.className = 'panel-body parsed';
                el.innerHTML = cur.parsed_html;
            }} else {{
                el.className = 'panel-body raw-text';
                el.textContent = cur.extracted_text;
            }}
        }}

        init();
    </script>
</body>
</html>'''

    return html

def main():
    print("로드 중...")
    laws = get_sample_laws()
    print(f"{len(laws)}개 법령")

    html = generate_html(laws)
    OUTPUT_PATH.write_text(html, encoding='utf-8')
    print(f"✅ 저장: {OUTPUT_PATH}")

if __name__ == '__main__':
    main()
