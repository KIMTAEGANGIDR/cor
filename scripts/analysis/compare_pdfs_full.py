#!/usr/bin/env python3
"""
전체 PDF 비교 분석 스크립트
- 모든 법령 유형 (UU, PP, Perpres, Perpu) 분석
- 페이지 수, 텍스트 추출량, 파일 크기 비교
- 상세 통계 산출
"""

import sqlite3
import os
import fitz  # PyMuPDF
from pathlib import Path
import json
from datetime import datetime
from collections import defaultdict
import statistics

BASE_DIR = Path(__file__).parent.parent

# 법령 유형 매핑
LAW_TYPES = {
    'UU': {
        'bpk_where': "bentuk LIKE 'Undang-undang (UU)%'",
        'leg_where': "jenis = 'UNDANG-UNDANG'",
        'name_ko': '법률',
        'name_id': 'Undang-Undang'
    },
    'PP': {
        'bpk_where': "bentuk = 'Peraturan Pemerintah (PP)'",
        'leg_where': "jenis = 'PERATURAN PEMERINTAH'",
        'name_ko': '정부령',
        'name_id': 'Peraturan Pemerintah'
    },
    'Perpres': {
        'bpk_where': "bentuk = 'Peraturan Presiden (Perpres)'",
        'leg_where': "jenis = 'PERATURAN PRESIDEN'",
        'name_ko': '대통령령',
        'name_id': 'Peraturan Presiden'
    },
    'Perpu': {
        'bpk_where': "bentuk = 'Peraturan Pemerintah Pengganti Undang-Undang (Perpu)'",
        'leg_where': "jenis = 'PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG'",
        'name_ko': '긴급법률',
        'name_id': 'Peraturan Pemerintah Pengganti UU'
    },
}


def get_all_pdf_pairs():
    """모든 법령 유형의 PDF 쌍 가져오기"""
    bpk_db = BASE_DIR / "data/bpk/peraturan_bpk.db"
    leg_db = BASE_DIR / "data/peraturan.db"

    conn = sqlite3.connect(bpk_db)
    conn.execute(f"ATTACH DATABASE '{leg_db}' AS leg")

    all_pairs = {}

    for law_type, config in LAW_TYPES.items():
        query = f"""
        SELECT
            b.nomor, b.tahun, b.judul,
            b.pdf_path as bpk_pdf,
            l.local_pdf_path as leg_pdf
        FROM peraturan b
        INNER JOIN leg.peraturan l ON b.nomor = l.nomor AND b.tahun = l.tahun
        WHERE {config['bpk_where']}
          AND {config['leg_where']}
          AND b.pdf_path IS NOT NULL AND b.pdf_path <> ''
          AND l.local_pdf_path IS NOT NULL AND l.local_pdf_path <> ''
        """

        cursor = conn.execute(query)
        rows = cursor.fetchall()

        pairs = []
        for row in rows:
            nomor, tahun, judul, bpk_pdf, leg_pdf = row
            bpk_path = BASE_DIR / bpk_pdf
            leg_path = BASE_DIR / leg_pdf

            if bpk_path.exists() and leg_path.exists():
                pairs.append({
                    'law_type': law_type,
                    'nomor': nomor,
                    'tahun': tahun,
                    'judul': judul[:80] if judul else '',
                    'bpk_pdf': str(bpk_path),
                    'leg_pdf': str(leg_path),
                    'bpk_size': bpk_path.stat().st_size,
                    'leg_size': leg_path.stat().st_size,
                })

        all_pairs[law_type] = pairs
        print(f"  {law_type}: {len(pairs)}개 PDF 쌍")

    conn.close()
    return all_pairs


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
            'error': str(e)[:100]
        }


def compare_all_pdfs(all_pairs):
    """모든 PDF 쌍 비교"""
    all_results = {}
    total_count = sum(len(pairs) for pairs in all_pairs.values())
    current = 0

    for law_type, pairs in all_pairs.items():
        results = []

        for pair in pairs:
            current += 1
            if current % 50 == 0:
                print(f"\r  분석 중: {current}/{total_count} ({current*100//total_count}%)", end="", flush=True)

            bpk_analysis = analyze_pdf(pair['bpk_pdf'])
            leg_analysis = analyze_pdf(pair['leg_pdf'])

            result = {
                'law_type': law_type,
                'nomor': pair['nomor'],
                'tahun': pair['tahun'],
                'judul': pair['judul'],
                'bpk_size': pair['bpk_size'],
                'leg_size': pair['leg_size'],
                'size_diff': pair['bpk_size'] - pair['leg_size'],
                'size_same': pair['bpk_size'] == pair['leg_size'],
                'bpk_pages': bpk_analysis['pages'],
                'leg_pages': leg_analysis['pages'],
                'page_diff': bpk_analysis['pages'] - leg_analysis['pages'],
                'bpk_text_len': bpk_analysis['text_length'],
                'leg_text_len': leg_analysis['text_length'],
                'text_diff': bpk_analysis['text_length'] - leg_analysis['text_length'],
                'bpk_error': bpk_analysis['error'],
                'leg_error': leg_analysis['error'],
            }

            # 분류
            result['category'] = categorize_result(result)

            results.append(result)

        all_results[law_type] = results

    print()
    return all_results


def categorize_result(r):
    """결과 분류"""
    if r['bpk_error']:
        return 'BPK_ERROR'
    if r['leg_error']:
        return 'LEG_ERROR'
    if r['size_same']:
        return 'IDENTICAL'
    if r['page_diff'] == 0:
        if abs(r['text_diff']) < 100:
            return 'SAME_CONTENT'  # 페이지 같고 텍스트 유사
        elif r['text_diff'] > 0:
            return 'BPK_BETTER_OCR'  # 페이지 같지만 BPK 텍스트 더 많음
        else:
            return 'LEG_BETTER_OCR'
    elif r['page_diff'] > 0:
        return 'BPK_MORE_PAGES'
    else:
        return 'LEG_MORE_PAGES'


def calculate_statistics(results):
    """상세 통계 계산"""
    if not results:
        return {}

    # 분류별 카운트
    categories = defaultdict(int)
    for r in results:
        categories[r['category']] += 1

    # 페이지 차이 통계 (에러 제외)
    valid_results = [r for r in results if not r['bpk_error'] and not r['leg_error']]

    page_diffs = [r['page_diff'] for r in valid_results]
    abs_page_diffs = [abs(d) for d in page_diffs if d != 0]

    text_diffs = [r['text_diff'] for r in valid_results]
    abs_text_diffs = [abs(d) for d in text_diffs if d != 0]

    size_diffs = [r['size_diff'] for r in valid_results]

    stats = {
        'total': len(results),
        'valid': len(valid_results),
        'categories': dict(categories),

        # 파일 크기 비교
        'size': {
            'identical': sum(1 for r in valid_results if r['size_same']),
            'bpk_larger': sum(1 for r in valid_results if r['size_diff'] > 0),
            'leg_larger': sum(1 for r in valid_results if r['size_diff'] < 0),
            'total_bpk_mb': sum(r['bpk_size'] for r in valid_results) / 1024 / 1024,
            'total_leg_mb': sum(r['leg_size'] for r in valid_results) / 1024 / 1024,
        },

        # 페이지 수 비교
        'pages': {
            'same': sum(1 for r in valid_results if r['page_diff'] == 0),
            'bpk_more': sum(1 for r in valid_results if r['page_diff'] > 0),
            'leg_more': sum(1 for r in valid_results if r['page_diff'] < 0),
        },

        # 텍스트 비교
        'text': {
            'similar': sum(1 for r in valid_results if abs(r['text_diff']) < 100),
            'bpk_more': sum(1 for r in valid_results if r['text_diff'] >= 100),
            'leg_more': sum(1 for r in valid_results if r['text_diff'] <= -100),
        },

        # 에러
        'errors': {
            'bpk': sum(1 for r in results if r['bpk_error']),
            'leg': sum(1 for r in results if r['leg_error']),
        },
    }

    # 페이지 차이 통계
    if abs_page_diffs:
        stats['pages']['avg_diff'] = round(statistics.mean(abs_page_diffs), 1)
        stats['pages']['median_diff'] = statistics.median(abs_page_diffs)
        stats['pages']['max_diff'] = max(abs_page_diffs)
        stats['pages']['stdev'] = round(statistics.stdev(abs_page_diffs), 1) if len(abs_page_diffs) > 1 else 0
    else:
        stats['pages']['avg_diff'] = 0
        stats['pages']['median_diff'] = 0
        stats['pages']['max_diff'] = 0
        stats['pages']['stdev'] = 0

    # 텍스트 차이 통계
    if abs_text_diffs:
        stats['text']['avg_diff'] = round(statistics.mean(abs_text_diffs), 0)
        stats['text']['median_diff'] = round(statistics.median(abs_text_diffs), 0)
        stats['text']['max_diff'] = max(abs_text_diffs)
    else:
        stats['text']['avg_diff'] = 0
        stats['text']['median_diff'] = 0
        stats['text']['max_diff'] = 0

    # 연도별 분석
    year_stats = defaultdict(lambda: {'bpk_win': 0, 'leg_win': 0, 'same': 0})
    for r in valid_results:
        year = r['tahun']
        if r['page_diff'] > 0:
            year_stats[year]['bpk_win'] += 1
        elif r['page_diff'] < 0:
            year_stats[year]['leg_win'] += 1
        else:
            year_stats[year]['same'] += 1

    stats['by_year'] = dict(year_stats)

    # 이상치 (페이지 10개 이상 차이)
    anomalies = [r for r in valid_results if abs(r['page_diff']) >= 10]
    stats['anomalies'] = sorted(anomalies, key=lambda x: abs(x['page_diff']), reverse=True)[:20]

    # 손상된 PDF 목록
    stats['corrupted'] = {
        'bpk': [r for r in results if r['bpk_error']],
        'leg': [r for r in results if r['leg_error']],
    }

    return stats


def print_summary(all_stats):
    """요약 출력"""
    print("\n" + "=" * 80)
    print("  전체 PDF 비교 분석 결과")
    print("=" * 80)

    total_items = sum(s['total'] for s in all_stats.values())
    print(f"\n총 분석: {total_items:,}개 PDF 쌍\n")

    # 법령 유형별 요약
    print("■ 법령 유형별 요약")
    print("-" * 80)
    print(f"{'유형':<10} {'총수':>8} {'동일':>8} {'BPK우세':>8} {'법제처우세':>8} {'BPK에러':>8} {'법제처에러':>8}")
    print("-" * 80)

    for law_type, stats in all_stats.items():
        if stats:
            print(f"{law_type:<10} "
                  f"{stats['total']:>8,} "
                  f"{stats['pages']['same']:>8,} "
                  f"{stats['pages']['bpk_more']:>8,} "
                  f"{stats['pages']['leg_more']:>8,} "
                  f"{stats['errors']['bpk']:>8,} "
                  f"{stats['errors']['leg']:>8,}")

    # 전체 통계
    print("\n■ 전체 통계")
    print("-" * 80)

    totals = {
        'identical': sum(s['size']['identical'] for s in all_stats.values() if s),
        'same_pages': sum(s['pages']['same'] for s in all_stats.values() if s),
        'bpk_more': sum(s['pages']['bpk_more'] for s in all_stats.values() if s),
        'leg_more': sum(s['pages']['leg_more'] for s in all_stats.values() if s),
        'bpk_err': sum(s['errors']['bpk'] for s in all_stats.values() if s),
        'leg_err': sum(s['errors']['leg'] for s in all_stats.values() if s),
    }

    print(f"  파일 완전 동일: {totals['identical']:,}개 ({totals['identical']*100/total_items:.1f}%)")
    print(f"  페이지 수 동일: {totals['same_pages']:,}개 ({totals['same_pages']*100/total_items:.1f}%)")
    print(f"  BPK 페이지 더 많음: {totals['bpk_more']:,}개 ({totals['bpk_more']*100/total_items:.1f}%)")
    print(f"  법제처 페이지 더 많음: {totals['leg_more']:,}개 ({totals['leg_more']*100/total_items:.1f}%)")
    print(f"  BPK PDF 에러: {totals['bpk_err']:,}개")
    print(f"  법제처 PDF 에러: {totals['leg_err']:,}개")


def main():
    start_time = datetime.now()
    print(f"전체 PDF 비교 분석 시작: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. PDF 쌍 가져오기
    print("\n1. PDF 쌍 추출 중...")
    all_pairs = get_all_pdf_pairs()
    total_pairs = sum(len(pairs) for pairs in all_pairs.values())
    print(f"   총 {total_pairs:,}개 PDF 쌍")

    # 2. PDF 분석
    print("\n2. PDF 내용 분석 중...")
    all_results = compare_all_pdfs(all_pairs)

    # 3. 통계 계산
    print("\n3. 통계 계산 중...")
    all_stats = {}
    for law_type, results in all_results.items():
        all_stats[law_type] = calculate_statistics(results)
        print(f"  {law_type}: 완료")

    # 4. 결과 출력
    print_summary(all_stats)

    # 5. 결과 저장
    output_path = BASE_DIR / "docs/pdf-full-comparison-results.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        # results에서 anomalies만 저장 (전체 결과는 너무 큼)
        save_data = {
            'generated_at': datetime.now().isoformat(),
            'statistics': all_stats,
        }
        json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n결과 저장: {output_path}")

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    print(f"\n완료: {end_time.strftime('%Y-%m-%d %H:%M:%S')} (소요시간: {duration:.0f}초)")

    return all_stats


if __name__ == "__main__":
    main()
