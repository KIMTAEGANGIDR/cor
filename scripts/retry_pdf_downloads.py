#!/usr/bin/env python3
"""PDF 다운로드 재시도 스크립트"""

import asyncio
import sqlite3
import httpx
from pathlib import Path
from datetime import datetime
import sys

DB_PATH = '/home/tylor/cor/peraturan/data/db/peraturan.db'
PDF_DIR = Path('/home/tylor/cor/peraturan/data/pdfs')
BATCH_SIZE = 50
RATE_LIMIT = 0.3

async def download_pdf(client, url, slug):
    """PDF 다운로드"""
    try:
        resp = await client.get(url, timeout=60, follow_redirects=True)

        if resp.status_code == 404:
            return None, "file_not_found"
        elif resp.status_code != 200:
            return None, f"http_{resp.status_code}"

        # Content-Type 확인
        content_type = resp.headers.get('content-type', '')
        if 'pdf' not in content_type.lower() and len(resp.content) < 1000:
            return None, "not_pdf"

        # 파일 저장
        filename = f"{slug}.pdf"
        filepath = PDF_DIR / filename
        filepath.write_bytes(resp.content)

        return str(filepath), None
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception as e:
        return None, str(e)[:50]

async def main():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    cur = conn.cursor()

    # 404 에러는 스킵 (이미 시도한 것)
    # 전체 대기 수 (file_not_found 제외)
    cur.execute('''
        SELECT COUNT(*) FROM peraturan
        WHERE pdf_url LIKE 'http%' AND local_pdf_path IS NULL
        AND (download_error IS NULL OR download_error NOT IN ('file_not_found', 'not_pdf'))
    ''')
    total = cur.fetchone()[0]

    print(f"=== PDF 다운로드 재시도 ===")
    print(f"시작: {datetime.now().isoformat()}")
    print(f"대상: {total}개\n")
    sys.stdout.flush()

    if total == 0:
        print("다운로드 대상 없음")
        return

    PDF_DIR.mkdir(parents=True, exist_ok=True)

    total_downloaded = 0
    total_failed = 0
    processed = 0

    async with httpx.AsyncClient() as client:
        while True:
            cur.execute('''
                SELECT slug, pdf_url FROM peraturan
                WHERE pdf_url LIKE 'http%' AND local_pdf_path IS NULL
                AND (download_error IS NULL OR download_error NOT IN ('file_not_found', 'not_pdf'))
                LIMIT ?
            ''', (BATCH_SIZE,))
            rows = cur.fetchall()

            if not rows:
                break

            batch_downloaded = 0
            batch_failed = 0

            for slug, url in rows:
                filepath, error = await download_pdf(client, url, slug)

                if filepath:
                    cur.execute('''
                        UPDATE peraturan SET
                            local_pdf_path = ?,
                            pdf_status = 'downloaded',
                            download_error = NULL
                        WHERE slug = ?
                    ''', (filepath, slug))
                    batch_downloaded += 1
                else:
                    cur.execute('''
                        UPDATE peraturan SET download_error = ?
                        WHERE slug = ?
                    ''', (error, slug))
                    batch_failed += 1

                await asyncio.sleep(RATE_LIMIT)

            conn.commit()

            total_downloaded += batch_downloaded
            total_failed += batch_failed
            processed += len(rows)

            remaining = total - processed
            pct = (processed / total) * 100
            print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                  f"처리: {processed}/{total} ({pct:.1f}%) | "
                  f"성공: {total_downloaded} | 실패: {total_failed}")
            sys.stdout.flush()

    print(f"\n=== 완료 ===")
    print(f"종료: {datetime.now().isoformat()}")
    print(f"총 다운로드: {total_downloaded}개")
    print(f"총 실패: {total_failed}개")

    # 최종 확인
    cur.execute("SELECT COUNT(*) FROM peraturan WHERE local_pdf_path IS NOT NULL")
    downloaded = cur.fetchone()[0]
    print(f"전체 다운로드 완료: {downloaded}개")

    conn.close()

if __name__ == '__main__':
    asyncio.run(main())
