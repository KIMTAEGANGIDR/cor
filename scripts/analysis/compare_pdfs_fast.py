#!/usr/bin/env python3
"""
전체 PDF 비교 분석 스크립트 (최적화 버전)
- 멀티프로세싱 사용
- 자주 진행상황 출력
"""

import sqlite3
import os
import fitz  # PyMuPDF
from pathlib import Path
import json
from datetime import datetime
from collections import defaultdict
import statistics
from multiprocessing import Pool, cpu_count
import sys

BASE_DIR = Path(__file__).parent.parent

# 법령 유형 매핑
LAW_TYPES = {
    'UU': {
        'bpk_where': "bentuk LIKE 'Undang-undang (UU)%'",
        'leg_where': "jenis = 'UNDANG-UNDANG'",
    },
    'PP': {
        'bpk_where': "bentuk = 'Peraturan Pemerintah (PP)'",
        'leg_where': "jenis = 'PERATURAN PEMERINTAH'",
    },
    'Perpres': {
        'bpk_where': "bentuk = 'Peraturan Presiden (Perpres)'",
        'leg_where': "jenis = 'PERATURAN PRESIDEN'",
    },
    'Perpu': {
        'bpk_where': "bentuk = 'Peraturan Pemerintah Pengganti Undang-Undang (Perpu)'",
        'leg_where': "jenis = 'PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG'",
    },
}


def get_all_pdf_pairs():
    """모든 법령 유형의 PDF 쌍 가져오기"""
    bpk_db = BASE_DIR / "data/bpk/peraturan_bpk.db"
    leg_db = BASE_DIR / "data/peraturan.db"

    conn = sqlite3.connect(bpk_db)
    conn.execute(f"ATTACH DATABASE '{leg_db}' AS leg")

    all_pairs = []

    for law_type, config in LAW_TYPES.items():
        query = f"""
        SELECT
            '{law_type}' as law_type,
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

        count = 0
        for row in rows:
            law_type, nomor, tahun, judul, bpk_pdf, leg_pdf = row
            bpk_path = BASE_DIR / bpk_pdf
            leg_path = BASE_DIR / leg_pdf

            if bpk_path.exists() and leg_path.exists():
                all_pairs.append({
                    'law_type': law_type,
                    'nomor': nomor,
                    'tahun': tahun,
                    'judul': (judul[:80] if judul else ''),
                    'bpk_pdf': str(bpk_path),
                    'leg_pdf': str(leg_path),
                    'bpk_size': bpk_path.stat().st_size,
                    'leg_size': leg_path.stat().st_size,
                })
                count += 1

        print(f"  {law_type}: {count}개", flush=True)

    conn.close()
    return all_pairs


def analyze_single_pdf(pdf_path):
    """단일 PDF 분석"""
    try:
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        doc.close()
        return page_count, len(full_text), None
    except Exception as e:
        return 0, 0, str(e)[:50]


def analyze_pair(pair):
    """PDF 쌍 분석 (멀티프로세싱용)"""
    bpk_pages, bpk_text, bpk_err = analyze_single_pdf(pair['bpk_pdf'])
    leg_pages, leg_text, leg_err = analyze_single_pdf(pair['leg_pdf'])

    result = {
        'law_type': pair['law_type'],
        'nomor': pair['nomor'],
        'tahun': pair['tahun'],
        'judul': pair['judul'],
        'bpk_size': pair['bpk_size'],
        'leg_size': pair['leg_size'],
        'size_same': pair['bpk_size'] == pair['leg_size'],
        'bpk_pages': bpk_pages,
        'leg_pages': leg_pages,
        'page_diff': bpk_pages - leg_pages,
        'bpk_text_len': bpk_text,
        'leg_text_len': leg_text,
        'text_diff': bpk_text - leg_text,
        'bpk_error': bpk_err,
        'leg_error': leg_err,
    }

    # 분류
    if bpk_err:
        result['category'] = 'BPK_ERROR'
    elif leg_err:
        result['category'] = 'LEG_ERROR'
    elif result['size_same']:
        result['category'] = 'IDENTICAL'
    elif result['page_diff'] == 0:
        if abs(result['text_diff']) < 100:
            result['category'] = 'SAME_CONTENT'
        elif result['text_diff'] > 0:
            result['category'] = 'BPK_BETTER_OCR'
        else:
            result['category'] = 'LEG_BETTER_OCR'
    elif result['page_diff'] > 0:
        result['category'] = 'BPK_MORE_PAGES'
    else:
        result['category'] = 'LEG_MORE_PAGES'

    return result


def calculate_statistics(results):
    """통계 계산"""
    if not results:
        return {}

    categories = defaultdict(int)
    for r in results:
        categories[r['category']] += 1

    valid = [r for r in results if not r['bpk_error'] and not r['leg_error']]

    abs_page_diffs = [abs(r['page_diff']) for r in valid if r['page_diff'] != 0]

    stats = {
        'total': len(results),
        'valid': len(valid),
        'categories': dict(categories),
        'size': {
            'identical': sum(1 for r in valid if r['size_same']),
            'bpk_larger': sum(1 for r in valid if r['bpk_size'] > r['leg_size']),
            'leg_larger': sum(1 for r in valid if r['leg_size'] > r['bpk_size']),
            'total_bpk_mb': round(sum(r['bpk_size'] for r in valid) / 1024 / 1024, 1),
            'total_leg_mb': round(sum(r['leg_size'] for r in valid) / 1024 / 1024, 1),
        },
        'pages': {
            'same': sum(1 for r in valid if r['page_diff'] == 0),
            'bpk_more': sum(1 for r in valid if r['page_diff'] > 0),
            'leg_more': sum(1 for r in valid if r['page_diff'] < 0),
            'avg_diff': round(statistics.mean(abs_page_diffs), 1) if abs_page_diffs else 0,
            'median_diff': statistics.median(abs_page_diffs) if abs_page_diffs else 0,
            'max_diff': max(abs_page_diffs) if abs_page_diffs else 0,
        },
        'text': {
            'similar': sum(1 for r in valid if abs(r['text_diff']) < 100),
            'bpk_more': sum(1 for r in valid if r['text_diff'] >= 100),
            'leg_more': sum(1 for r in valid if r['text_diff'] <= -100),
        },
        'errors': {
            'bpk': sum(1 for r in results if r['bpk_error']),
            'leg': sum(1 for r in results if r['leg_error']),
        },
    }

    # 이상치
    stats['anomalies'] = sorted(
        [r for r in valid if abs(r['page_diff']) >= 10],
        key=lambda x: abs(x['page_diff']),
        reverse=True
    )[:30]

    # 손상된 파일
    stats['corrupted_leg'] = [
        {'nomor': r['nomor'], 'tahun': r['tahun'], 'error': r['leg_error']}
        for r in results if r['leg_error']
    ]

    return stats


def main():
    start_time = datetime.now()
    print(f"전체 PDF 비교 분석 시작: {start_time.strftime('%H:%M:%S')}", flush=True)
    print("=" * 70, flush=True)

    # 1. PDF 쌍 가져오기
    print("\n1. PDF 쌍 추출 중...", flush=True)
    all_pairs = get_all_pdf_pairs()
    print(f"   총 {len(all_pairs):,}개 PDF 쌍", flush=True)

    # 2. PDF 분석 (멀티프로세싱)
    print(f"\n2. PDF 분석 중... (CPU: {cpu_count()}코어)", flush=True)

    results = []
    batch_size = 100
    total = len(all_pairs)

    # 배치로 처리하면서 진행상황 출력
    with Pool(processes=min(4, cpu_count())) as pool:
        for i in range(0, total, batch_size):
            batch = all_pairs[i:i+batch_size]
            batch_results = pool.map(analyze_pair, batch)
            results.extend(batch_results)
            pct = (i + len(batch)) * 100 // total
            print(f"   진행: {i + len(batch):,}/{total:,} ({pct}%)", flush=True)

    # 3. 법령 유형별 통계
    print("\n3. 통계 계산 중...", flush=True)

    by_type = defaultdict(list)
    for r in results:
        by_type[r['law_type']].append(r)

    all_stats = {}
    for law_type in LAW_TYPES.keys():
        all_stats[law_type] = calculate_statistics(by_type[law_type])
        print(f"   {law_type}: 완료", flush=True)

    # 4. 전체 통계
    all_stats['TOTAL'] = calculate_statistics(results)

    # 5. 결과 출력
    print("\n" + "=" * 70, flush=True)
    print("  분석 결과 요약", flush=True)
    print("=" * 70, flush=True)

    print(f"\n총 분석: {len(results):,}개 PDF 쌍", flush=True)

    print("\n■ 법령 유형별 요약", flush=True)
    print("-" * 70, flush=True)
    print(f"{'유형':<8} {'총수':>7} {'동일':>7} {'BPK우세':>8} {'법제처우세':>9} {'에러':>6}", flush=True)
    print("-" * 70, flush=True)

    for law_type in LAW_TYPES.keys():
        s = all_stats[law_type]
        if s:
            print(f"{law_type:<8} {s['total']:>7,} {s['pages']['same']:>7,} "
                  f"{s['pages']['bpk_more']:>8,} {s['pages']['leg_more']:>9,} "
                  f"{s['errors']['leg']:>6,}", flush=True)

    # 전체
    t = all_stats['TOTAL']
    print("-" * 70, flush=True)
    print(f"{'합계':<8} {t['total']:>7,} {t['pages']['same']:>7,} "
          f"{t['pages']['bpk_more']:>8,} {t['pages']['leg_more']:>9,} "
          f"{t['errors']['leg']:>6,}", flush=True)

    print(f"\n■ 전체 통계", flush=True)
    print(f"  - 파일 완전 동일: {t['size']['identical']:,}개 ({t['size']['identical']*100/t['total']:.1f}%)", flush=True)
    print(f"  - 페이지 수 동일: {t['pages']['same']:,}개 ({t['pages']['same']*100/t['total']:.1f}%)", flush=True)
    print(f"  - BPK 페이지 더 많음: {t['pages']['bpk_more']:,}개 ({t['pages']['bpk_more']*100/t['total']:.1f}%)", flush=True)
    print(f"  - 법제처 페이지 더 많음: {t['pages']['leg_more']:,}개 ({t['pages']['leg_more']*100/t['total']:.1f}%)", flush=True)
    print(f"  - 평균 페이지 차이: {t['pages']['avg_diff']}p", flush=True)
    print(f"  - 최대 페이지 차이: {t['pages']['max_diff']}p", flush=True)
    print(f"  - 법제처 PDF 손상: {t['errors']['leg']:,}개", flush=True)

    print(f"\n■ 용량 비교", flush=True)
    print(f"  - BPK 총 용량: {t['size']['total_bpk_mb']:,.0f} MB", flush=True)
    print(f"  - 법제처 총 용량: {t['size']['total_leg_mb']:,.0f} MB", flush=True)

    # 6. 결과 저장
    output_path = BASE_DIR / "docs/pdf-full-comparison-results.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'statistics': all_stats,
        }, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n결과 저장: {output_path}", flush=True)

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    print(f"\n완료! (소요시간: {duration:.0f}초 = {duration/60:.1f}분)", flush=True)

    return all_stats


if __name__ == "__main__":
    main()
