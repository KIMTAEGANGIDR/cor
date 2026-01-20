#!/usr/bin/env python3
"""
"상충하지 않는 한" (sepanjang tidak bertentangan) 문구 추출 스크립트
"""

import sqlite3
import re
import json
from pathlib import Path

DB_PATH = "/tmp/peraturan.db"

def extract_conflict_context(text: str) -> list[dict]:
    """
    "sepanjang tidak bertentangan" 문구와 그 맥락을 추출
    Returns: list of {subject, condition, full_sentence}
    """
    results = []

    # 문장 단위로 분리 (마침표, 세미콜론 기준)
    sentences = re.split(r'[.;]', text)

    for sentence in sentences:
        if 'sepanjang tidak bertentangan' not in sentence.lower():
            continue

        # 문장 정리
        clean = ' '.join(sentence.split())
        if len(clean) < 20:
            continue

        # 패턴 1: "X ... sepanjang tidak bertentangan dengan Y"
        # 패턴 2: "... tetap berlaku sepanjang tidak bertentangan dengan ..."

        # dengan 뒤에 오는 상충 기준 추출
        match = re.search(
            r'sepanjang tidak bertentangan dengan\s+(.+?)(?:\.|;|$)',
            clean, re.IGNORECASE
        )

        if match:
            condition = match.group(1).strip()

            # 주어 추출 (앞부분)
            before_match = clean[:match.start()]

            # "tetap berlaku" 앞의 주어 찾기
            subject_match = re.search(
                r'(?:^|,\s*)([^,]+?)\s+(?:dinyatakan\s+)?(?:masih\s+)?(?:tetap\s+)?berlaku',
                before_match, re.IGNORECASE
            )

            if subject_match:
                subject = subject_match.group(1).strip()
            else:
                # 대체: 문장 앞부분 사용
                subject = before_match[-100:].strip() if len(before_match) > 100 else before_match.strip()

            results.append({
                'subject': subject[:200],  # 상충하지 않는 대상
                'condition': condition[:200],  # 상충 기준
                'full_sentence': clean[:500]
            })

    return results


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    cursor.execute("""
        SELECT slug, jenis, nomor, tahun, tentang, extracted_text
        FROM peraturan
        WHERE extracted_text LIKE '%sepanjang tidak bertentangan%'
        ORDER BY tahun DESC, jenis
    """)

    all_results = []

    for row in cursor.fetchall():
        if not row['extracted_text']:
            continue

        contexts = extract_conflict_context(row['extracted_text'])

        for ctx in contexts:
            all_results.append({
                'law_id': row['slug'],
                'jenis': row['jenis'],
                'nomor': row['nomor'],
                'tahun': row['tahun'],
                'tentang': row['tentang'][:100],
                'subject': ctx['subject'],
                'condition': ctx['condition'],
                'full_sentence': ctx['full_sentence']
            })

    conn.close()

    # 중복 제거 및 100개 선택
    seen = set()
    unique_results = []

    for r in all_results:
        # 유사한 조건으로 그룹화
        key = (r['condition'][:50], r['subject'][:30])
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

        if len(unique_results) >= 100:
            break

    # 결과 출력
    print("=" * 120)
    print("'상충하지 않는 한' (sepanjang tidak bertentangan) 분석 결과")
    print("=" * 120)
    print(f"총 {len(all_results)}개 문구 발견, {len(unique_results)}개 고유 패턴 추출\n")

    # 마크다운 테이블 생성
    print("| # | 법령 | 상충하지 않는 대상 | 상충 기준 |")
    print("|---|------|-------------------|----------|")

    for i, r in enumerate(unique_results, 1):
        law = f"{r['jenis']} {r['nomor']}/{r['tahun']}"
        subject = r['subject'][:60].replace('|', '/').replace('\n', ' ')
        condition = r['condition'][:80].replace('|', '/').replace('\n', ' ')
        print(f"| {i} | {law} | {subject} | {condition} |")

    # JSON으로도 저장
    output_path = Path(__file__).parent.parent / "delivery" / "conflict_clauses.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            'total_found': len(all_results),
            'unique_patterns': len(unique_results),
            'results': unique_results
        }, f, ensure_ascii=False, indent=2)

    print(f"\n상세 결과: {output_path}")


if __name__ == '__main__':
    main()
