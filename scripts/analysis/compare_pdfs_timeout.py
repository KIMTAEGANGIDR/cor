#!/usr/bin/env python3
"""
전체 PDF 비교 분석 (타임아웃 포함)
- 각 파일 5초 타임아웃
- 느린 파일 스킵
"""

import sqlite3
import fitz
from pathlib import Path
import json
from datetime import datetime
from collections import defaultdict
import statistics
import signal
import sys

BASE_DIR = Path(__file__).parent.parent
TIMEOUT_SEC = 5

class TimeoutError(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutError("Timeout")

LAW_TYPES = {
    'UU': ("bentuk LIKE 'Undang-undang (UU)%'", "jenis = 'UNDANG-UNDANG'"),
    'PP': ("bentuk = 'Peraturan Pemerintah (PP)'", "jenis = 'PERATURAN PEMERINTAH'"),
    'Perpres': ("bentuk = 'Peraturan Presiden (Perpres)'", "jenis = 'PERATURAN PRESIDEN'"),
    'Perpu': ("bentuk = 'Peraturan Pemerintah Pengganti Undang-Undang (Perpu)'", "jenis = 'PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG'"),
}

def get_pairs():
    bpk_db = BASE_DIR / "data/bpk/peraturan_bpk.db"
    leg_db = BASE_DIR / "data/peraturan.db"
    conn = sqlite3.connect(bpk_db)
    conn.execute(f"ATTACH DATABASE '{leg_db}' AS leg")

    pairs = []
    for law_type, (bpk_w, leg_w) in LAW_TYPES.items():
        q = f"""SELECT '{law_type}', b.nomor, b.tahun, b.pdf_path, l.local_pdf_path
                FROM peraturan b
                INNER JOIN leg.peraturan l ON b.nomor = l.nomor AND b.tahun = l.tahun
                WHERE {bpk_w} AND {leg_w}
                  AND b.pdf_path IS NOT NULL AND b.pdf_path <> ''
                  AND l.local_pdf_path IS NOT NULL AND l.local_pdf_path <> ''"""
        for row in conn.execute(q).fetchall():
            lt, nom, thn, bp, lp = row
            bpk_path = BASE_DIR / bp
            leg_path = BASE_DIR / lp
            if bpk_path.exists() and leg_path.exists():
                pairs.append((lt, nom, thn, str(bpk_path), str(leg_path),
                             bpk_path.stat().st_size, leg_path.stat().st_size))
        print(f"  {law_type}: {sum(1 for p in pairs if p[0]==law_type)}개", flush=True)
    conn.close()
    return pairs


def analyze_pdf_with_timeout(path):
    """타임아웃 포함 PDF 분석"""
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(TIMEOUT_SEC)
    try:
        doc = fitz.open(path)
        pages = len(doc)
        text = sum(len(p.get_text()) for p in doc)
        doc.close()
        signal.alarm(0)
        return pages, text, None
    except TimeoutError:
        signal.alarm(0)
        return 0, 0, "TIMEOUT"
    except Exception as e:
        signal.alarm(0)
        return 0, 0, str(e)[:30]


def main():
    start = datetime.now()
    print(f"전체 PDF 비교 분석: {start.strftime('%H:%M:%S')}", flush=True)
    print(f"타임아웃: {TIMEOUT_SEC}초/파일", flush=True)
    print("=" * 60, flush=True)

    print("\n1. PDF 쌍 추출...", flush=True)
    pairs = get_pairs()
    total = len(pairs)
    print(f"   총 {total:,}개", flush=True)

    print("\n2. PDF 분석 중...", flush=True)
    results = []
    timeouts = 0

    for i, (lt, nom, thn, bp, lp, bs, ls) in enumerate(pairs):
        if i % 50 == 0:
            print(f"   {i:,}/{total:,} ({i*100//total}%) [타임아웃: {timeouts}]", flush=True)

        bp_pg, bp_tx, bp_err = analyze_pdf_with_timeout(bp)
        lp_pg, lp_tx, lp_err = analyze_pdf_with_timeout(lp)

        if bp_err == "TIMEOUT" or lp_err == "TIMEOUT":
            timeouts += 1

        cat = 'BPK_ERROR' if bp_err else 'LEG_ERROR' if lp_err else \
              'IDENTICAL' if bs == ls else \
              'SAME_PAGES' if bp_pg == lp_pg else \
              'BPK_MORE' if bp_pg > lp_pg else 'LEG_MORE'

        results.append({
            'law_type': lt, 'nomor': nom, 'tahun': thn,
            'bpk_size': bs, 'leg_size': ls,
            'bpk_pages': bp_pg, 'leg_pages': lp_pg,
            'bpk_text': bp_tx, 'leg_text': lp_tx,
            'bpk_err': bp_err, 'leg_err': lp_err,
            'category': cat, 'page_diff': bp_pg - lp_pg
        })

    print(f"   {total:,}/{total:,} (100%) [타임아웃: {timeouts}]", flush=True)

    # 통계 계산
    print("\n3. 통계 계산...", flush=True)

    by_type = defaultdict(list)
    for r in results:
        by_type[r['law_type']].append(r)

    stats = {}
    for lt in LAW_TYPES.keys():
        rs = by_type[lt]
        valid = [r for r in rs if not r['bpk_err'] and not r['leg_err']]
        diffs = [abs(r['page_diff']) for r in valid if r['page_diff'] != 0]

        stats[lt] = {
            'total': len(rs),
            'identical': sum(1 for r in valid if r['bpk_size'] == r['leg_size']),
            'same_pages': sum(1 for r in valid if r['page_diff'] == 0),
            'bpk_more': sum(1 for r in valid if r['page_diff'] > 0),
            'leg_more': sum(1 for r in valid if r['page_diff'] < 0),
            'bpk_err': sum(1 for r in rs if r['bpk_err']),
            'leg_err': sum(1 for r in rs if r['leg_err']),
            'timeouts': sum(1 for r in rs if r['bpk_err'] == 'TIMEOUT' or r['leg_err'] == 'TIMEOUT'),
            'avg_diff': round(statistics.mean(diffs), 1) if diffs else 0,
            'max_diff': max(diffs) if diffs else 0,
            'total_bpk_mb': round(sum(r['bpk_size'] for r in valid) / 1024**2, 1),
            'total_leg_mb': round(sum(r['leg_size'] for r in valid) / 1024**2, 1),
        }

    # 전체
    all_valid = [r for r in results if not r['bpk_err'] and not r['leg_err']]
    all_diffs = [abs(r['page_diff']) for r in all_valid if r['page_diff'] != 0]
    stats['TOTAL'] = {
        'total': len(results),
        'identical': sum(1 for r in all_valid if r['bpk_size'] == r['leg_size']),
        'same_pages': sum(1 for r in all_valid if r['page_diff'] == 0),
        'bpk_more': sum(1 for r in all_valid if r['page_diff'] > 0),
        'leg_more': sum(1 for r in all_valid if r['page_diff'] < 0),
        'bpk_err': sum(1 for r in results if r['bpk_err']),
        'leg_err': sum(1 for r in results if r['leg_err']),
        'timeouts': timeouts,
        'avg_diff': round(statistics.mean(all_diffs), 1) if all_diffs else 0,
        'max_diff': max(all_diffs) if all_diffs else 0,
        'total_bpk_mb': round(sum(r['bpk_size'] for r in all_valid) / 1024**2, 1),
        'total_leg_mb': round(sum(r['leg_size'] for r in all_valid) / 1024**2, 1),
    }

    # 이상치
    anomalies = sorted([r for r in all_valid if abs(r['page_diff']) >= 10],
                       key=lambda x: abs(x['page_diff']), reverse=True)[:30]
    stats['anomalies'] = anomalies

    # 손상된 파일
    stats['corrupted'] = [
        {'law_type': r['law_type'], 'nomor': r['nomor'], 'tahun': r['tahun'],
         'bpk_err': r['bpk_err'], 'leg_err': r['leg_err']}
        for r in results if r['bpk_err'] or r['leg_err']
    ]

    # 출력
    print("\n" + "=" * 60, flush=True)
    print("  분석 결과", flush=True)
    print("=" * 60, flush=True)

    print(f"\n■ 법령 유형별", flush=True)
    print("-" * 60, flush=True)
    print(f"{'유형':<8} {'총수':>6} {'동일':>6} {'BPK+':>6} {'법제처+':>7} {'에러':>5}", flush=True)
    print("-" * 60, flush=True)

    for lt in LAW_TYPES.keys():
        s = stats[lt]
        print(f"{lt:<8} {s['total']:>6,} {s['same_pages']:>6,} {s['bpk_more']:>6,} {s['leg_more']:>7,} {s['bpk_err']+s['leg_err']:>5,}", flush=True)

    t = stats['TOTAL']
    print("-" * 60, flush=True)
    print(f"{'합계':<8} {t['total']:>6,} {t['same_pages']:>6,} {t['bpk_more']:>6,} {t['leg_more']:>7,} {t['bpk_err']+t['leg_err']:>5,}", flush=True)

    print(f"\n■ 전체 통계", flush=True)
    print(f"  파일 완전 동일: {t['identical']:,}개 ({t['identical']*100/t['total']:.1f}%)", flush=True)
    print(f"  페이지 수 동일: {t['same_pages']:,}개 ({t['same_pages']*100/t['total']:.1f}%)", flush=True)
    print(f"  BPK 페이지 더 많음: {t['bpk_more']:,}개 ({t['bpk_more']*100/t['total']:.1f}%)", flush=True)
    print(f"  법제처 페이지 더 많음: {t['leg_more']:,}개 ({t['leg_more']*100/t['total']:.1f}%)", flush=True)
    print(f"  평균 페이지 차이: {t['avg_diff']}p", flush=True)
    print(f"  최대 페이지 차이: {t['max_diff']}p", flush=True)
    print(f"  타임아웃: {t['timeouts']:,}개", flush=True)
    print(f"  에러 총합: {t['bpk_err']+t['leg_err']:,}개", flush=True)

    print(f"\n■ 용량", flush=True)
    print(f"  BPK: {t['total_bpk_mb']:,.0f} MB", flush=True)
    print(f"  법제처: {t['total_leg_mb']:,.0f} MB", flush=True)

    if anomalies:
        print(f"\n■ 이상 케이스 TOP 10", flush=True)
        for r in anomalies[:10]:
            print(f"  {r['law_type']} {r['nomor']}/{r['tahun']}: BPK {r['bpk_pages']}p vs 법제처 {r['leg_pages']}p", flush=True)

    # 저장
    out = BASE_DIR / "docs/pdf-full-comparison-results.json"
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'generated': datetime.now().isoformat(), 'stats': stats}, f, ensure_ascii=False, indent=2, default=str)

    end = datetime.now()
    dur = (end-start).total_seconds()
    print(f"\n완료! ({dur:.0f}초 = {dur/60:.1f}분)", flush=True)
    print(f"저장: {out}", flush=True)


if __name__ == "__main__":
    main()
