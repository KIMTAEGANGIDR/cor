#!/usr/bin/env python3
"""
PDF vs XML 비교 웹페이지 생성기
"""

import sqlite3
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from html import escape

DB_PATH = "/tmp/peraturan.db"
PROJECT_DIR = Path(__file__).parent.parent
XML_DIR = PROJECT_DIR / "delivery" / "akoma-ntoso" / "uu"
OUTPUT_PATH = PROJECT_DIR / "delivery" / "comparison.html"

def get_sample_laws():
    """Get 10 sample laws with good data"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    cursor.execute("""
        SELECT slug, jenis, nomor, tahun, tentang,
               extracted_text, parsed_json,
               parsed_bab_count, parsed_pasal_count
        FROM peraturan
        WHERE jenis = 'UNDANG-UNDANG'
          AND extracted_text IS NOT NULL
          AND parsed_pasal_count > 5
        ORDER BY tahun DESC
        LIMIT 10
    """)

    laws = []
    for row in cursor.fetchall():
        laws.append({
            'slug': row['slug'],
            'jenis': row['jenis'],
            'nomor': row['nomor'],
            'tahun': row['tahun'],
            'tentang': row['tentang'],
            'extracted_text': row['extracted_text'] or '',
            'parsed_json': row['parsed_json'] or '{}',
            'bab_count': row['parsed_bab_count'],
            'pasal_count': row['parsed_pasal_count']
        })

    conn.close()
    return laws

def load_xml_content(slug):
    """Load XML file content"""
    xml_path = XML_DIR / f"{slug}.xml"
    if xml_path.exists():
        return xml_path.read_text(encoding='utf-8')
    return None

def format_xml_preview(xml_content):
    """Format XML for display with syntax highlighting"""
    if not xml_content:
        return "<p>XML 파일 없음</p>"

    # Truncate if too long
    if len(xml_content) > 50000:
        xml_content = xml_content[:50000] + "\n... (truncated)"

    return escape(xml_content)

def generate_html(laws):
    """Generate comparison HTML page"""

    laws_data = []
    for law in laws:
        xml_content = load_xml_content(law['slug'])
        laws_data.append({
            'slug': law['slug'],
            'title': f"UU No. {law['nomor']} Tahun {law['tahun']}",
            'tentang': law['tentang'],
            'stats': f"BAB: {law['bab_count']}, Pasal: {law['pasal_count']}",
            'pdf_text': law['extracted_text'][:30000] if law['extracted_text'] else '',
            'xml_content': xml_content[:30000] if xml_content else '',
            'has_xml': xml_content is not None
        })

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PDF vs XML 비교 시스템</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            text-align: center;
        }}
        .header h1 {{ font-size: 24px; margin-bottom: 10px; }}
        .header p {{ opacity: 0.9; }}

        .container {{ max-width: 1800px; margin: 0 auto; padding: 20px; }}

        .law-selector {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 20px;
            justify-content: center;
        }}
        .law-btn {{
            padding: 10px 20px;
            background: #16213e;
            border: 2px solid #0f3460;
            color: #eee;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s;
        }}
        .law-btn:hover {{ background: #0f3460; }}
        .law-btn.active {{
            background: #e94560;
            border-color: #e94560;
        }}

        .comparison {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }}

        .panel {{
            background: #16213e;
            border-radius: 12px;
            overflow: hidden;
        }}
        .panel-header {{
            background: #0f3460;
            padding: 15px 20px;
            font-weight: bold;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .panel-header .badge {{
            background: #e94560;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
        }}
        .panel-content {{
            padding: 20px;
            height: 70vh;
            overflow-y: auto;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 13px;
            line-height: 1.6;
            white-space: pre-wrap;
            word-break: break-word;
        }}
        .panel-content.xml {{
            color: #7ee787;
        }}
        .panel-content.pdf {{
            color: #ffa657;
        }}

        .info-bar {{
            background: #0f3460;
            padding: 15px 20px;
            margin-bottom: 20px;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .info-bar .title {{ font-size: 18px; font-weight: bold; }}
        .info-bar .stats {{ color: #7ee787; }}

        .search-box {{
            margin-bottom: 20px;
            display: flex;
            gap: 10px;
        }}
        .search-box input {{
            flex: 1;
            padding: 12px 20px;
            border: none;
            border-radius: 8px;
            background: #16213e;
            color: #eee;
            font-size: 14px;
        }}
        .search-box button {{
            padding: 12px 24px;
            background: #e94560;
            border: none;
            border-radius: 8px;
            color: white;
            cursor: pointer;
        }}

        ::-webkit-scrollbar {{ width: 8px; }}
        ::-webkit-scrollbar-track {{ background: #1a1a2e; }}
        ::-webkit-scrollbar-thumb {{ background: #0f3460; border-radius: 4px; }}

        .highlight {{ background: yellow; color: black; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📄 PDF vs XML 비교 시스템</h1>
        <p>인도네시아 법령 원본 텍스트와 Akoma Ntoso XML 파싱 결과 비교</p>
    </div>

    <div class="container">
        <div class="law-selector" id="lawSelector">
            <!-- Generated by JS -->
        </div>

        <div class="search-box">
            <input type="text" id="searchInput" placeholder="검색어 입력 (예: Pasal 1, BAB I, ketentuan)">
            <button onclick="highlightSearch()">검색</button>
            <button onclick="clearSearch()">초기화</button>
        </div>

        <div class="info-bar" id="infoBar">
            <span class="title">법령을 선택하세요</span>
            <span class="stats"></span>
        </div>

        <div class="comparison">
            <div class="panel">
                <div class="panel-header">
                    <span>📑 PDF 추출 텍스트 (원본)</span>
                    <span class="badge">PDF</span>
                </div>
                <div class="panel-content pdf" id="pdfContent">
                    좌측 버튼에서 법령을 선택하세요.
                </div>
            </div>

            <div class="panel">
                <div class="panel-header">
                    <span>🔖 Akoma Ntoso XML (파싱)</span>
                    <span class="badge">XML</span>
                </div>
                <div class="panel-content xml" id="xmlContent">
                    파싱된 XML 구조가 여기에 표시됩니다.
                </div>
            </div>
        </div>
    </div>

    <script>
        const lawsData = {json.dumps(laws_data, ensure_ascii=False, indent=2)};

        let currentLaw = null;

        function init() {{
            const selector = document.getElementById('lawSelector');
            lawsData.forEach((law, index) => {{
                const btn = document.createElement('button');
                btn.className = 'law-btn';
                btn.textContent = law.title;
                btn.onclick = () => selectLaw(index);
                selector.appendChild(btn);
            }});

            // Select first law by default
            if (lawsData.length > 0) {{
                selectLaw(0);
            }}
        }}

        function selectLaw(index) {{
            currentLaw = lawsData[index];

            // Update buttons
            document.querySelectorAll('.law-btn').forEach((btn, i) => {{
                btn.classList.toggle('active', i === index);
            }});

            // Update info bar
            const infoBar = document.getElementById('infoBar');
            infoBar.innerHTML = `
                <span class="title">${{currentLaw.title}} - ${{currentLaw.tentang}}</span>
                <span class="stats">${{currentLaw.stats}}</span>
            `;

            // Update content
            document.getElementById('pdfContent').textContent = currentLaw.pdf_text || '(텍스트 없음)';
            document.getElementById('xmlContent').textContent = currentLaw.xml_content || '(XML 파일 없음)';
        }}

        function highlightSearch() {{
            const query = document.getElementById('searchInput').value.trim();
            if (!query) return;

            const regex = new RegExp(`(${{escapeRegex(query)}})`, 'gi');

            ['pdfContent', 'xmlContent'].forEach(id => {{
                const el = document.getElementById(id);
                const text = currentLaw ? (id === 'pdfContent' ? currentLaw.pdf_text : currentLaw.xml_content) : '';
                el.innerHTML = escapeHtml(text).replace(regex, '<span class="highlight">$1</span>');
            }});
        }}

        function clearSearch() {{
            document.getElementById('searchInput').value = '';
            if (currentLaw) {{
                document.getElementById('pdfContent').textContent = currentLaw.pdf_text || '';
                document.getElementById('xmlContent').textContent = currentLaw.xml_content || '';
            }}
        }}

        function escapeRegex(str) {{
            return str.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
        }}

        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
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
