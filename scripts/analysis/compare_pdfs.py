#!/usr/bin/env python3
"""
PDF 비교 분석 스크립트
- 페이지 수 비교
- 텍스트 추출 및 길이 비교
"""

import sqlite3
import os
import fitz  # PyMuPDF
from pathlib import Path
import json

BASE_DIR = Path(__file__).parent.parent

def get_pdf_pairs(limit=100):
    """크기가 다른 PP PDF 쌍 가져오기"""
    bpk_db = BASE_DIR / "data/bpk/peraturan_bpk.db"
    leg_db = BASE_DIR / "data/peraturan.db"

    conn = sqlite3.connect(bpk_db)
    conn.execute(f"ATTACH DATABASE '{leg_db}' AS leg")

    query = """
    SELECT
        b.nomor, b.tahun, b.judul,
        b.pdf_path as bpk_pdf,
        l.local_pdf_path as leg_pdf
    FROM peraturan b
    INNER JOIN leg.peraturan l ON b.nomor = l.nomor AND b.tahun = l.tahun
    WHERE b.bentuk = 'Peraturan Pemerintah (PP)'
      AND l.jenis = 'PERATURAN PEMERINTAH'
      AND b.pdf_path IS NOT NULL AND b.pdf_path <> ''
      AND l.local_pdf_path IS NOT NULL AND l.local_pdf_path <> ''
    ORDER BY RANDOM()
    LIMIT ?
    """

    cursor = conn.execute(query, (limit * 2,))  # 더 많이 가져와서 필터링
    rows = cursor.fetchall()
    conn.close()

    # 파일이 존재하고 크기가 다른 것만 필터링
    valid_pairs = []
    for row in rows:
        nomor, tahun, judul, bpk_pdf, leg_pdf = row
        bpk_path = BASE_DIR / bpk_pdf
        leg_path = BASE_DIR / leg_pdf

        if bpk_path.exists() and leg_path.exists():
            bpk_size = bpk_path.stat().st_size
            leg_size = leg_path.stat().st_size

            if bpk_size != leg_size:  # 크기가 다른 것만
                valid_pairs.append({
                    'nomor': nomor,
                    'tahun': tahun,
                    'judul': judul[:50] + '...' if len(judul) > 50 else judul,
                    'bpk_pdf': str(bpk_path),
                    'leg_pdf': str(leg_path),
                    'bpk_size': bpk_size,
                    'leg_size': leg_size,
                })

                if len(valid_pairs) >= limit:
                    break

    return valid_pairs


def analyze_pdf(pdf_path):
    """PDF 분석: 페이지 수, 텍스트 길이"""
    try:
        doc = fitz.open(pdf_path)
        page_count = len(doc)

        # 텍스트 추출
        full_text = ""
        for page in doc:
            full_text += page.get_text()

        text_length = len(full_text)
        doc.close()

        return {
            'pages': page_count,
            'text_length': text_length,
            'error': None
        }
    except Exception as e:
        return {
            'pages': 0,
            'text_length': 0,
            'error': str(e)
        }


def compare_pdfs(pairs):
    """PDF 쌍 비교"""
    results = []

    for i, pair in enumerate(pairs):
        print(f"\r분석 중: {i+1}/{len(pairs)}", end="", flush=True)

        bpk_analysis = analyze_pdf(pair['bpk_pdf'])
        leg_analysis = analyze_pdf(pair['leg_pdf'])

        result = {
            'nomor': pair['nomor'],
            'tahun': pair['tahun'],
            'judul': pair['judul'],
            'bpk_size': pair['bpk_size'],
            'leg_size': pair['leg_size'],
            'size_diff': pair['bpk_size'] - pair['leg_size'],
            'bpk_pages': bpk_analysis['pages'],
            'leg_pages': leg_analysis['pages'],
            'page_diff': bpk_analysis['pages'] - leg_analysis['pages'],
            'bpk_text_len': bpk_analysis['text_length'],
            'leg_text_len': leg_analysis['text_length'],
            'text_diff': bpk_analysis['text_length'] - leg_analysis['text_length'],
            'bpk_error': bpk_analysis['error'],
            'leg_error': leg_analysis['error'],
        }

        # 차이 분류
        if result['page_diff'] > 0:
            result['page_winner'] = 'BPK'
        elif result['page_diff'] < 0:
            result['page_winner'] = 'LEG'
        else:
            result['page_winner'] = 'SAME'

        if result['text_diff'] > 0:
            result['text_winner'] = 'BPK'
        elif result['text_diff'] < 0:
            result['text_winner'] = 'LEG'
        else:
            result['text_winner'] = 'SAME'

        results.append(result)

    print()  # 줄바꿈
    return results


def summarize_results(results):
    """결과 요약"""
    total = len(results)

    # 페이지 수 비교
    same_pages = sum(1 for r in results if r['page_diff'] == 0)
    bpk_more_pages = sum(1 for r in results if r['page_diff'] > 0)
    leg_more_pages = sum(1 for r in results if r['page_diff'] < 0)

    # 텍스트 길이 비교
    same_text = sum(1 for r in results if abs(r['text_diff']) < 100)  # 100자 이하 차이는 동일로 간주
    bpk_more_text = sum(1 for r in results if r['text_diff'] >= 100)
    leg_more_text = sum(1 for r in results if r['text_diff'] <= -100)

    # 에러
    bpk_errors = sum(1 for r in results if r['bpk_error'])
    leg_errors = sum(1 for r in results if r['leg_error'])

    # 페이지 차이 통계
    page_diffs = [abs(r['page_diff']) for r in results if r['page_diff'] != 0]
    avg_page_diff = sum(page_diffs) / len(page_diffs) if page_diffs else 0
    max_page_diff = max(page_diffs) if page_diffs else 0

    # 이상 케이스 찾기 (페이지 수 크게 다른 것)
    anomalies = [r for r in results if abs(r['page_diff']) >= 5]

    summary = {
        'total': total,
        'pages': {
            'same': same_pages,
            'bpk_more': bpk_more_pages,
            'leg_more': leg_more_pages,
            'avg_diff': round(avg_page_diff, 1),
            'max_diff': max_page_diff,
        },
        'text': {
            'same': same_text,
            'bpk_more': bpk_more_text,
            'leg_more': leg_more_text,
        },
        'errors': {
            'bpk': bpk_errors,
            'leg': leg_errors,
        },
        'anomalies': anomalies,
    }

    return summary


def print_summary(summary):
    """결과 출력"""
    print("\n" + "="*70)
    print("  PDF 비교 분석 결과 (크기가 다른 PDF 100개 샘플)")
    print("="*70)

    print(f"\n총 분석: {summary['total']}개")

    print("\n■ 페이지 수 비교:")
    print(f"  - 동일: {summary['pages']['same']}개 ({summary['pages']['same']/summary['total']*100:.1f}%)")
    print(f"  - BPK가 더 많음: {summary['pages']['bpk_more']}개")
    print(f"  - 법제처가 더 많음: {summary['pages']['leg_more']}개")
    print(f"  - 평균 페이지 차이: {summary['pages']['avg_diff']}페이지")
    print(f"  - 최대 페이지 차이: {summary['pages']['max_diff']}페이지")

    print("\n■ 텍스트 추출량 비교:")
    print(f"  - 유사 (100자 이내): {summary['text']['same']}개 ({summary['text']['same']/summary['total']*100:.1f}%)")
    print(f"  - BPK가 더 많음: {summary['text']['bpk_more']}개")
    print(f"  - 법제처가 더 많음: {summary['text']['leg_more']}개")

    if summary['errors']['bpk'] or summary['errors']['leg']:
        print(f"\n■ 에러:")
        print(f"  - BPK PDF 에러: {summary['errors']['bpk']}개")
        print(f"  - 법제처 PDF 에러: {summary['errors']['leg']}개")

    if summary['anomalies']:
        print(f"\n■ 이상 케이스 (페이지 5개 이상 차이): {len(summary['anomalies'])}개")
        for a in summary['anomalies'][:10]:  # 최대 10개만 표시
            print(f"  - PP {a['nomor']}/{a['tahun']}: BPK {a['bpk_pages']}p vs 법제처 {a['leg_pages']}p (차이: {abs(a['page_diff'])}p)")


def main():
    print("PDF 비교 분석 시작...")
    print("-" * 50)

    # 1. PDF 쌍 가져오기
    print("1. 크기가 다른 PP PDF 100개 선택 중...")
    pairs = get_pdf_pairs(100)
    print(f"   → {len(pairs)}개 선택됨")

    # 2. PDF 비교
    print("\n2. PDF 분석 중...")
    results = compare_pdfs(pairs)

    # 3. 결과 요약
    print("\n3. 결과 요약 중...")
    summary = summarize_results(results)

    # 4. 결과 출력
    print_summary(summary)

    # 5. 상세 결과 저장
    output_path = BASE_DIR / "docs/pdf-comparison-results.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            'summary': summary,
            'details': results
        }, f, ensure_ascii=False, indent=2)
    print(f"\n상세 결과 저장: {output_path}")

    return summary, results


if __name__ == "__main__":
    main()
