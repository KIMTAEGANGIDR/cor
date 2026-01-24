#!/usr/bin/env python3
"""pengundangan 필드 업데이트 스크립트"""

import asyncio
import sqlite3
import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import re
import sys

DB_PATH = '/home/tylor/cor/peraturan/data/db/peraturan.db'
BATCH_SIZE = 50
RATE_LIMIT = 0.3  # seconds between requests

def normalize_date(date_str):
    """날짜 문자열을 ISO 형식으로 변환"""
    if not date_str:
        return None

    months = {
        'januari': '01', 'februari': '02', 'maret': '03', 'april': '04',
        'mei': '05', 'juni': '06', 'juli': '07', 'agustus': '08',
        'september': '09', 'oktober': '10', 'november': '11', 'desember': '12'
    }

    date_str = date_str.strip().lower()
    match = re.match(r'(\d{1,2})\s+(\w+)\s+(\d{4})', date_str)
    if match:
        day, month, year = match.groups()
        month_num = months.get(month)
        if month_num:
            return f"{year}-{month_num}-{day.zfill(2)}"

    return None

async def fetch_pengundangan(client, url):
    """페이지에서 pengundangan 정보 추출"""
    try:
        resp = await client.get(url, timeout=30)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')

        data = {}
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['th', 'td'])
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower()
                    value = cells[1].get_text(strip=True)

                    if 'tahun pengundangan' in label:
                        try:
                            data['tahun_pengundangan'] = int(value) if value else None
                        except:
                            data['tahun_pengundangan'] = None
                    elif 'nomor pengundangan' in label:
                        data['nomor_pengundangan'] = value if value else None
                    elif 'nomor tambahan' in label:
                        data['nomor_tambahan'] = value if value else None
                    elif 'tanggal pengundangan' in label:
                        data['tanggal_pengundangan'] = normalize_date(value)
                    elif 'pejabat pengundangan' in label:
                        data['pejabat_pengundangan'] = value if value else None

        return data if data else None
    except Exception as e:
        return None

async def process_batch(client, batch, conn):
    """배치 처리"""
    updated = 0
    errors = 0

    for slug, url in batch:
        data = await fetch_pengundangan(client, url)

        if data:
            cur = conn.cursor()
            cur.execute('''
                UPDATE peraturan SET
                    tahun_pengundangan = ?,
                    nomor_pengundangan = ?,
                    nomor_tambahan = ?,
                    tanggal_pengundangan = ?,
                    pejabat_pengundangan = ?
                WHERE slug = ?
            ''', (
                data.get('tahun_pengundangan'),
                data.get('nomor_pengundangan'),
                data.get('nomor_tambahan'),
                data.get('tanggal_pengundangan'),
                data.get('pejabat_pengundangan'),
                slug
            ))
            updated += 1
        else:
            errors += 1

        await asyncio.sleep(RATE_LIMIT)

    conn.commit()
    return updated, errors

async def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # NULL인 레코드 수
    cur.execute('SELECT COUNT(*) FROM peraturan WHERE tahun_pengundangan IS NULL')
    total = cur.fetchone()[0]

    print(f"=== pengundangan 필드 업데이트 ===")
    print(f"시작: {datetime.now().isoformat()}")
    print(f"대상: {total}개\n")

    if total == 0:
        print("업데이트 대상 없음")
        return

    total_updated = 0
    total_errors = 0
    processed = 0

    async with httpx.AsyncClient(follow_redirects=True) as client:
        while True:
            cur.execute('''
                SELECT slug, source_url FROM peraturan
                WHERE tahun_pengundangan IS NULL
                LIMIT ?
            ''', (BATCH_SIZE,))
            batch = cur.fetchall()

            if not batch:
                break

            updated, errors = await process_batch(client, batch, conn)
            total_updated += updated
            total_errors += errors
            processed += len(batch)

            # 진행상황 출력
            remaining = total - processed
            pct = (processed / total) * 100
            print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                  f"처리: {processed}/{total} ({pct:.1f}%) | "
                  f"성공: {total_updated} | 실패: {total_errors} | "
                  f"남은: {remaining}")
            sys.stdout.flush()

    print(f"\n=== 완료 ===")
    print(f"종료: {datetime.now().isoformat()}")
    print(f"총 업데이트: {total_updated}개")
    print(f"총 실패: {total_errors}개")

    # 최종 확인
    cur.execute("SELECT COUNT(*) FROM peraturan WHERE tahun_pengundangan IS NOT NULL")
    filled = cur.fetchone()[0]
    print(f"pengundangan 채워진 레코드: {filled}개")

    conn.close()

if __name__ == '__main__':
    asyncio.run(main())
