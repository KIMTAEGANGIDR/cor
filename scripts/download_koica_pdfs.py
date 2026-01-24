#!/usr/bin/env python3
"""KOICA 대상 PDF 다운로드 스크립트

KOICA 대상 법종(PERMEN, PERBAN, PP, PERPRES, PENPRES, UU, UUDRT, PERPPU 등)의
PDF만 다운로드합니다. KEPPRES, INPRES, PERDA는 제외합니다.
"""

import asyncio
import sqlite3
import httpx
from pathlib import Path
from datetime import datetime
import sys

DB_PATH = '/home/tylor/cor/peraturan/data/db/peraturan.db'
PDF_DIR = Path('/home/tylor/peraturan_pdfs')  # 기존 PDF 저장 경로
LOG_FILE = '/home/tylor/cor/logs/download_koica_pdfs.log'

BATCH_SIZE = 20
RATE_LIMIT = 0.5

# KOICA 비대상 (제외할 법종)
EXCLUDE_JENIS = [
    'KEPUTUSAN PRESIDEN',
    'INSTRUKSI PRESIDEN',
    'PERATURAN DAERAH',
]


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    sys.stdout.flush()
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


async def download_pdf(client, url, slug):
    """PDF 다운로드"""
    try:
        # URL 정리
        url = url.strip().replace(' ', '%20')

        resp = await client.get(url, timeout=60, follow_redirects=True)

        if resp.status_code == 404:
            return None, "file_not_found"
        elif resp.status_code != 200:
            return None, f"http_{resp.status_code}"

        # Content-Type 확인
        content_type = resp.headers.get('content-type', '')
        if 'pdf' not in content_type.lower() and 'octet-stream' not in content_type.lower():
            if len(resp.content) < 1000:
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


async def download_peraturan():
    """peraturan 테이블에서 KOICA 대상 다운로드"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    cur = conn.cursor()

    # KOICA 대상만 (비대상 제외)
    exclude_condition = " AND ".join([f"jenis NOT LIKE '{j}%'" for j in EXCLUDE_JENIS])

    # 다운로드 대상 조회 (file_not_found 제외 - 이미 시도한 것)
    cur.execute(f'''
        SELECT COUNT(*) FROM peraturan
        WHERE pdf_url LIKE 'http%'
        AND (local_pdf_path IS NULL OR local_pdf_path = '')
        AND (download_error IS NULL OR download_error NOT IN ('file_not_found', 'not_pdf'))
        AND {exclude_condition}
    ''')
    total = cur.fetchone()[0]

    log(f"\n=== peraturan PDF 다운로드 ===")
    log(f"KOICA 대상 다운로드 대기: {total}건")

    if total == 0:
        log("다운로드 대상 없음")
        conn.close()
        return 0, 0

    PDF_DIR.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    failed = 0
    processed = 0

    async with httpx.AsyncClient() as client:
        while True:
            cur.execute(f'''
                SELECT slug, pdf_url FROM peraturan
                WHERE pdf_url LIKE 'http%'
                AND (local_pdf_path IS NULL OR local_pdf_path = '')
                AND (download_error IS NULL OR download_error NOT IN ('file_not_found', 'not_pdf'))
                AND {exclude_condition}
                LIMIT ?
            ''', (BATCH_SIZE,))
            rows = cur.fetchall()

            if not rows:
                break

            for slug, url in rows:
                filepath, error = await download_pdf(client, url, slug)

                if filepath:
                    cur.execute('''
                        UPDATE peraturan SET
                            local_pdf_path = ?,
                            download_error = NULL
                        WHERE slug = ?
                    ''', (filepath, slug))
                    downloaded += 1
                else:
                    cur.execute('''
                        UPDATE peraturan SET download_error = ?
                        WHERE slug = ?
                    ''', (error, slug))
                    failed += 1

                await asyncio.sleep(RATE_LIMIT)

            conn.commit()
            processed += len(rows)

            pct = (processed / total) * 100 if total > 0 else 0
            log(f"처리: {processed}/{total} ({pct:.1f}%) | 성공: {downloaded} | 실패: {failed}")

    conn.close()
    return downloaded, failed


async def download_translations():
    """translations 테이블 PDF 다운로드"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    cur = conn.cursor()

    cur.execute('''
        SELECT COUNT(*) FROM translations
        WHERE pdf_url IS NOT NULL AND pdf_url != ''
        AND (local_pdf_path IS NULL OR local_pdf_path = '')
    ''')
    total = cur.fetchone()[0]

    log(f"\n=== TERJEMAH PDF 다운로드 ===")
    log(f"다운로드 대기: {total}건")

    if total == 0:
        log("다운로드 대상 없음")
        conn.close()
        return 0, 0

    downloaded = 0
    failed = 0
    processed = 0

    async with httpx.AsyncClient() as client:
        while True:
            cur.execute('''
                SELECT id, pdf_url FROM translations
                WHERE pdf_url IS NOT NULL AND pdf_url != ''
                AND (local_pdf_path IS NULL OR local_pdf_path = '')
                LIMIT ?
            ''', (BATCH_SIZE,))
            rows = cur.fetchall()

            if not rows:
                break

            for tr_id, url in rows:
                # slug 생성 (tr- 제거)
                slug = tr_id.replace('tr-', '') + '_terjemah'

                filepath, error = await download_pdf(client, url, slug)

                if filepath:
                    cur.execute('''
                        UPDATE translations SET local_pdf_path = ?
                        WHERE id = ?
                    ''', (filepath, tr_id))
                    downloaded += 1
                else:
                    failed += 1

                await asyncio.sleep(RATE_LIMIT)

            conn.commit()
            processed += len(rows)

            pct = (processed / total) * 100 if total > 0 else 0
            log(f"처리: {processed}/{total} ({pct:.1f}%) | 성공: {downloaded} | 실패: {failed}")

    conn.close()
    return downloaded, failed


async def main():
    log("=" * 60)
    log("KOICA 대상 PDF 다운로드 시작")
    log(f"시작: {datetime.now().isoformat()}")
    log("=" * 60)

    # 1. peraturan 다운로드
    p_down, p_fail = await download_peraturan()

    # 2. translations 다운로드
    t_down, t_fail = await download_translations()

    log("\n" + "=" * 60)
    log("다운로드 완료")
    log(f"peraturan: 성공 {p_down}건, 실패 {p_fail}건")
    log(f"translations: 성공 {t_down}건, 실패 {t_fail}건")
    log(f"총계: 성공 {p_down + t_down}건, 실패 {p_fail + t_fail}건")
    log("=" * 60)


if __name__ == '__main__':
    asyncio.run(main())
