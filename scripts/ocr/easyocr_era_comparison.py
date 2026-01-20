#!/usr/bin/env python3
"""
EasyOCR 연대별 파이프라인 테스트 + 비교 웹페이지 생성

파이프라인:
1. PDF → 이미지 변환 (pdf2image)
2. EasyOCR로 텍스트 추출
3. 구조화된 HTML로 변환
4. PDF 원본 vs OCR 결과 비교 웹페이지

연대: 1950s ~ 2020s (8개 시대)
"""

import sys
import time
import json
import sqlite3
from pathlib import Path
from html import escape
import re

# 프로젝트 경로
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

DB_PATH = "/tmp/peraturan.db"
OUTPUT_DIR = PROJECT_DIR / "delivery"
OUTPUT_DIR.mkdir(exist_ok=True)

# 연대 정의
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


def get_era_samples():
    """각 연대별 샘플 1개씩 가져오기"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    samples = []

    for era in ERAS:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT slug, nomor, tahun, tentang, local_pdf_path
            FROM peraturan
            WHERE jenis = 'UNDANG-UNDANG'
              AND tahun BETWEEN ? AND ?
              AND local_pdf_path IS NOT NULL
            ORDER BY tahun DESC
            LIMIT 1
        """, (era['start'], era['end']))

        row = cursor.fetchone()
        if row:
            pdf_path = PROJECT_DIR / row['local_pdf_path']
            if pdf_path.exists():
                samples.append({
                    'era': era['name'],
                    'era_label': era['label'],
                    'slug': row['slug'],
                    'nomor': row['nomor'],
                    'tahun': row['tahun'],
                    'tentang': row['tentang'],
                    'pdf_path': str(pdf_path),
                })
                print(f"  ✓ {era['name']}: UU {row['nomor']}/{row['tahun']}")
            else:
                print(f"  ✗ {era['name']}: PDF 없음")
        else:
            print(f"  ✗ {era['name']}: 데이터 없음")

    conn.close()
    return samples


def pdf_to_images(pdf_path, dpi=150, max_pages=3):
    """PDF → 이미지 변환"""
    from pdf2image import convert_from_path

    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        first_page=1,
        last_page=max_pages
    )
    return images


def ocr_images(images, reader):
    """EasyOCR로 이미지에서 텍스트 추출"""
    import numpy as np

    all_texts = []

    for i, img in enumerate(images):
        img_np = np.array(img)
        results = reader.readtext(img_np)

        # 결과를 y좌표로 정렬 (위에서 아래로)
        results.sort(key=lambda x: (x[0][0][1], x[0][0][0]))

        page_texts = []
        for (bbox, text, confidence) in results:
            if confidence > 0.3:  # 신뢰도 30% 이상만
                page_texts.append({
                    'text': text,
                    'confidence': confidence,
                    'y': bbox[0][1],  # y좌표
                    'x': bbox[0][0],  # x좌표
                })

        all_texts.append(page_texts)

    return all_texts


def structure_ocr_text(ocr_results):
    """OCR 결과를 구조화된 HTML로 변환"""
    html_parts = []

    for page_idx, page_texts in enumerate(ocr_results):
        if page_idx > 0:
            html_parts.append('<div class="page-break">--- 페이지 {} ---</div>'.format(page_idx + 1))

        for item in page_texts:
            text = item['text'].strip()
            if not text:
                continue

            escaped = escape(text)
            conf = item['confidence']
            conf_class = 'high' if conf > 0.9 else 'medium' if conf > 0.7 else 'low'

            # 구조 감지
            if re.match(r'^UNDANG-UNDANG', text, re.IGNORECASE):
                html_parts.append(f'<div class="title">{escaped}</div>')
            elif re.match(r'^BAB\s+[IVXLCDM]+', text, re.IGNORECASE):
                html_parts.append(f'<div class="bab">{escaped}</div>')
            elif re.match(r'^Pasal\s+\d+', text, re.IGNORECASE):
                html_parts.append(f'<div class="pasal">{escaped}</div>')
            elif re.match(r'^\(\d+\)', text):
                html_parts.append(f'<div class="ayat conf-{conf_class}">{escaped}</div>')
            elif re.match(r'^[a-z]\.', text):
                html_parts.append(f'<div class="huruf conf-{conf_class}">{escaped}</div>')
            elif re.match(r'^(Menimbang|Mengingat|Menetapkan|MEMUTUSKAN)', text, re.IGNORECASE):
                html_parts.append(f'<div class="section-header">{escaped}</div>')
            elif re.match(r'^PRESIDEN', text, re.IGNORECASE):
                html_parts.append(f'<div class="header">{escaped}</div>')
            elif re.match(r'^REPUBLIK\s+INDONESIA', text, re.IGNORECASE):
                html_parts.append(f'<div class="header">{escaped}</div>')
            else:
                html_parts.append(f'<div class="text conf-{conf_class}">{escaped}</div>')

    return '\n'.join(html_parts)


def generate_comparison_html(samples_data):
    """비교 웹페이지 생성"""
    html = '''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>PDF vs EasyOCR 비교 (연대별)</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, sans-serif; background: #0d1117; color: #c9d1d9; }

        .header {
            background: linear-gradient(90deg, #238636, #1f6feb);
            padding: 15px 25px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { font-size: 18px; color: white; }
        .header .info { color: rgba(255,255,255,0.8); font-size: 13px; }

        .era-tabs {
            display: flex;
            background: #161b22;
            border-bottom: 1px solid #30363d;
            overflow-x: auto;
        }
        .era-tab {
            padding: 12px 20px;
            background: none;
            border: none;
            border-bottom: 3px solid transparent;
            color: #8b949e;
            cursor: pointer;
            font-size: 13px;
            white-space: nowrap;
        }
        .era-tab:hover { color: #c9d1d9; background: #21262d; }
        .era-tab.active {
            color: #58a6ff;
            border-bottom-color: #58a6ff;
            background: #21262d;
        }

        .law-info {
            background: #161b22;
            padding: 10px 25px;
            border-bottom: 1px solid #30363d;
            font-size: 14px;
        }
        .law-info .title { color: #58a6ff; font-weight: bold; }
        .law-info .desc { color: #8b949e; margin-left: 15px; }
        .law-info .stats { float: right; color: #7ee787; }

        .main {
            display: grid;
            grid-template-columns: 1fr 1fr;
            height: calc(100vh - 130px);
        }

        .panel {
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .panel:first-child { border-right: 2px solid #30363d; }

        .panel-header {
            background: #21262d;
            padding: 10px 20px;
            font-weight: bold;
            font-size: 14px;
            border-bottom: 1px solid #30363d;
            display: flex;
            justify-content: space-between;
        }
        .panel-header .tag {
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: normal;
        }
        .tag.pdf { background: #da3633; }
        .tag.ocr { background: #238636; }

        .panel-body {
            flex: 1;
            overflow: auto;
        }

        iframe {
            width: 100%;
            height: 100%;
            border: none;
            background: white;
        }

        .ocr-result {
            padding: 20px;
            font-size: 14px;
            line-height: 1.8;
        }

        .ocr-result .title {
            font-size: 16px;
            font-weight: bold;
            color: #fff;
            text-align: center;
            padding: 15px;
            background: #21262d;
            border-radius: 8px;
            margin-bottom: 20px;
        }

        .ocr-result .header {
            font-weight: bold;
            color: #8b949e;
            text-align: center;
            margin: 5px 0;
        }

        .ocr-result .bab {
            font-size: 15px;
            font-weight: bold;
            color: #ff7b72;
            text-align: center;
            margin: 25px 0 5px 0;
            padding: 12px;
            background: linear-gradient(90deg, #21262d, #2d1f1f, #21262d);
            border-radius: 6px;
        }

        .ocr-result .pasal {
            font-weight: bold;
            color: #58a6ff;
            margin: 20px 0 10px 0;
            padding: 10px 15px;
            background: #161b22;
            border-left: 4px solid #58a6ff;
            border-radius: 0 6px 6px 0;
        }

        .ocr-result .ayat {
            margin: 8px 0 8px 25px;
            padding: 8px 15px;
            background: #0d1117;
            border-radius: 6px;
            border-left: 2px solid #30363d;
        }

        .ocr-result .huruf {
            margin: 4px 0 4px 50px;
            padding: 6px 12px;
            color: #d2a8ff;
        }

        .ocr-result .section-header {
            font-weight: bold;
            color: #7ee787;
            margin: 20px 0 10px 0;
            padding: 10px 15px;
            background: #0d2818;
            border-radius: 6px;
        }

        .ocr-result .text {
            padding: 3px 0;
            color: #c9d1d9;
        }

        .ocr-result .page-break {
            text-align: center;
            color: #484f58;
            margin: 20px 0;
            padding: 10px;
            border-top: 1px dashed #30363d;
            border-bottom: 1px dashed #30363d;
        }

        /* 신뢰도 표시 */
        .conf-high { }
        .conf-medium { opacity: 0.85; }
        .conf-low { opacity: 0.7; background: rgba(218, 54, 51, 0.1); }
    </style>
</head>
<body>
    <div class="header">
        <h1>PDF 원본 vs EasyOCR 결과 비교</h1>
        <span class="info">연대별 OCR 품질 검증 | Pipeline: PDF → Image → EasyOCR → Structured</span>
    </div>

    <div class="era-tabs" id="eraTabs"></div>

    <div class="law-info" id="lawInfo">
        <span class="title">법령을 선택하세요</span>
    </div>

    <div class="main">
        <div class="panel">
            <div class="panel-header">
                PDF 원본
                <span class="tag pdf">ORIGINAL</span>
            </div>
            <div class="panel-body">
                <iframe id="pdfFrame"></iframe>
            </div>
        </div>
        <div class="panel">
            <div class="panel-header">
                EasyOCR 결과 (구조화)
                <span class="tag ocr">OCR</span>
            </div>
            <div class="panel-body ocr-result" id="ocrResult"></div>
        </div>
    </div>

    <script>
        const data = ''' + json.dumps(samples_data, ensure_ascii=False) + ''';

        function init() {
            const tabs = document.getElementById('eraTabs');
            data.forEach((d, i) => {
                const tab = document.createElement('button');
                tab.className = 'era-tab' + (i === 0 ? ' active' : '');
                tab.innerHTML = `${d.era}<br><small>${d.era_label}</small>`;
                tab.onclick = () => select(i);
                tabs.appendChild(tab);
            });
            select(0);
        }

        function select(idx) {
            const d = data[idx];

            document.querySelectorAll('.era-tab').forEach((t, i) => {
                t.classList.toggle('active', i === idx);
            });

            document.getElementById('lawInfo').innerHTML = `
                <span class="title">UU No. ${d.nomor} Tahun ${d.tahun}</span>
                <span class="desc">${d.tentang.substring(0, 60)}...</span>
                <span class="stats">OCR 시간: ${d.ocr_time}초</span>
            `;

            document.getElementById('pdfFrame').src = 'file://' + d.pdf_path;
            document.getElementById('ocrResult').innerHTML = d.ocr_html;
        }

        init();
    </script>
</body>
</html>'''
    return html


def main():
    print("=" * 60)
    print("EasyOCR 연대별 파이프라인 테스트")
    print("=" * 60)

    # 1. 샘플 가져오기
    print("\n[1] 연대별 샘플 선택...")
    samples = get_era_samples()
    print(f"  총 {len(samples)}개 샘플")

    if not samples:
        print("  샘플이 없습니다!")
        return

    # 2. EasyOCR 초기화
    print("\n[2] EasyOCR 초기화...")
    import easyocr
    start = time.time()
    reader = easyocr.Reader(['en', 'id'], gpu=False)  # 영어 + 인도네시아어
    print(f"  ✓ 초기화 완료: {time.time() - start:.1f}초")

    # 3. 각 샘플 처리
    print("\n[3] OCR 처리...")
    results = []

    for sample in samples:
        print(f"\n  처리 중: {sample['era']} - UU {sample['nomor']}/{sample['tahun']}")

        # PDF → 이미지
        start = time.time()
        images = pdf_to_images(sample['pdf_path'], dpi=150, max_pages=3)
        img_time = time.time() - start
        print(f"    - 이미지 변환: {len(images)}페이지, {img_time:.1f}초")

        # OCR
        start = time.time()
        ocr_results = ocr_images(images, reader)
        ocr_time = time.time() - start
        total_texts = sum(len(p) for p in ocr_results)
        print(f"    - OCR: {total_texts}개 텍스트, {ocr_time:.1f}초")

        # 구조화
        ocr_html = structure_ocr_text(ocr_results)

        results.append({
            'era': sample['era'],
            'era_label': sample['era_label'],
            'nomor': sample['nomor'],
            'tahun': sample['tahun'],
            'tentang': sample['tentang'],
            'pdf_path': sample['pdf_path'],
            'ocr_time': f"{ocr_time:.1f}",
            'ocr_html': ocr_html,
        })

    # 4. HTML 생성
    print("\n[4] 비교 웹페이지 생성...")
    html = generate_comparison_html(results)
    output_path = OUTPUT_DIR / "easyocr_comparison.html"
    output_path.write_text(html, encoding='utf-8')
    print(f"  ✓ 저장: {output_path}")

    print("\n" + "=" * 60)
    print("완료!")
    print("=" * 60)


if __name__ == '__main__':
    main()
