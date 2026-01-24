#!/usr/bin/env python3
"""PERPPU PDF 재다운로드 스크립트

URL이 있지만 다운로드 실패한 PERPPU 문서들의 실제 PDF URL을
사이트에서 추출하여 다운로드합니다.
"""

import sqlite3
import httpx
import asyncio
import re
from pathlib import Path
from urllib.parse import quote
import time

DB_PATH = "/home/tylor/cor/peraturan/data/db/peraturan.db"
PDF_DIR = Path("/home/tylor/peraturan_pdfs")

async def get_actual_pdf_url(client: httpx.AsyncClient, slug: str) -> str | None:
    """사이트에서 실제 PDF URL 추출"""
    url = f"https://peraturan.go.id/id/{slug}"
    try:
        resp = await client.get(url, timeout=30)
        if resp.status_code != 200:
            return None

        # meta 태그에서 PDF URL 추출
        matches = re.findall(r'https://peraturan\.go\.id/files/[^"\'<>]+\.pdf', resp.text, re.IGNORECASE)
        if matches:
            # 첫 번째 PDF URL 반환
            return matches[0]
        return None
    except Exception as e:
        print(f"  Error fetching {slug}: {e}")
        return None

async def download_pdf(client: httpx.AsyncClient, url: str, slug: str) -> bool:
    """PDF 다운로드"""
    try:
        # URL 인코딩 (공백 등 처리)
        encoded_url = url.replace(' ', '%20')

        resp = await client.get(encoded_url, timeout=60, follow_redirects=True)
        if resp.status_code != 200:
            print(f"  {slug}: HTTP {resp.status_code}")
            return False

        # Content-Type 확인
        content_type = resp.headers.get('content-type', '')
        if 'pdf' not in content_type.lower() and 'octet-stream' not in content_type.lower():
            print(f"  {slug}: Not PDF ({content_type})")
            return False

        # 파일 저장
        pdf_path = PDF_DIR / f"{slug}.pdf"
        pdf_path.write_bytes(resp.content)
        print(f"  {slug}: Downloaded ({len(resp.content):,} bytes)")
        return True
    except Exception as e:
        print(f"  {slug}: Error - {e}")
        return False

async def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 대상 목록 조회
    cur.execute("""
        SELECT slug, pdf_url
        FROM peraturan
        WHERE jenis = 'PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG'
        AND pdf_url != 'NO_PDF'
        AND pdf_status = 'no_pdf'
        ORDER BY tahun DESC
    """)
    targets = cur.fetchall()
    print(f"재다운로드 대상: {len(targets)}건\n")

    success = 0
    failed = 0

    async with httpx.AsyncClient() as client:
        for i, (slug, old_url) in enumerate(targets, 1):
            print(f"[{i}/{len(targets)}] {slug}")

            # 1. 사이트에서 실제 URL 추출
            actual_url = await get_actual_pdf_url(client, slug)
            if not actual_url:
                print(f"  URL not found, trying original: {old_url}")
                actual_url = old_url
            else:
                if actual_url != old_url:
                    print(f"  Found new URL: {actual_url}")

            # 2. 다운로드 시도
            if await download_pdf(client, actual_url, slug):
                # DB 업데이트
                cur.execute("""
                    UPDATE peraturan
                    SET pdf_status = 'downloaded',
                        pdf_url = ?
                    WHERE slug = ?
                """, (actual_url, slug))
                conn.commit()
                success += 1
            else:
                failed += 1

            # Rate limiting
            await asyncio.sleep(0.5)

    conn.close()

    print(f"\n{'='*50}")
    print(f"완료: 성공 {success}건, 실패 {failed}건")
    print(f"{'='*50}")

if __name__ == "__main__":
    asyncio.run(main())
