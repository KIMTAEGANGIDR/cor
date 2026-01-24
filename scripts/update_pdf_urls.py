#!/usr/bin/env python3
"""Update PDF URLs for documents that don't have them."""

import asyncio
import sqlite3
import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import sys

DB_PATH = "/home/tylor/cor/peraturan/data/db/peraturan.db"
BASE_URL = "https://peraturan.go.id"

async def extract_pdf_url(html: str) -> str | None:
    """Extract PDF URL from HTML."""
    soup = BeautifulSoup(html, "lxml")
    
    # Method 1: Direct PDF links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/files/" in href and href.endswith(".pdf"):
            if href.startswith("/"):
                return f"{BASE_URL}{href}"
            elif href.startswith("http"):
                return href
            return f"{BASE_URL}/{href}"
    
    # Method 2: meta tags
    for meta in soup.find_all("meta", {"name": "article:tag"}):
        content = meta.get("content", "")
        if content.endswith(".pdf") and "/files/" in content:
            # Clean up URL (remove spaces)
            content = content.replace(" ", "")
            if content.startswith("http"):
                return content
            if content.startswith("/"):
                return f"{BASE_URL}{content}"
    
    return None

async def update_pdf_urls(batch_size: int = 100, delay: float = 0.3):
    """Update PDF URLs for all documents that don't have them."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Count total
    cursor = conn.execute("""
        SELECT COUNT(*) FROM peraturan 
        WHERE pdf_url IS NULL OR pdf_url = ''
    """)
    total = cursor.fetchone()[0]
    
    print(f"[{datetime.now()}] 총 {total}개 문서 처리 시작")
    
    updated = 0
    no_pdf = 0
    failed = 0
    processed = 0
    
    async with httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=5)) as client:
        while True:
            # Get batch
            cursor = conn.execute("""
                SELECT slug FROM peraturan 
                WHERE pdf_url IS NULL OR pdf_url = ''
                ORDER BY tahun DESC
                LIMIT ?
            """, (batch_size,))
            slugs = [row['slug'] for row in cursor.fetchall()]
            
            if not slugs:
                break
            
            for slug in slugs:
                url = f"{BASE_URL}/id/{slug}"
                try:
                    resp = await client.get(url, follow_redirects=True)
                    if resp.status_code != 200:
                        failed += 1
                        continue
                    
                    pdf_url = await extract_pdf_url(resp.text)
                    
                    if pdf_url:
                        conn.execute("""
                            UPDATE peraturan SET pdf_url = ? WHERE slug = ?
                        """, (pdf_url, slug))
                        conn.commit()
                        updated += 1
                    else:
                        # Mark as checked (set empty string to distinguish)
                        conn.execute("""
                            UPDATE peraturan SET pdf_url = 'NO_PDF' WHERE slug = ?
                        """, (slug,))
                        conn.commit()
                        no_pdf += 1
                    
                    await asyncio.sleep(delay)
                    
                except Exception as e:
                    failed += 1
                    print(f"Error {slug}: {e}", file=sys.stderr)
                
                processed += 1
                if processed % 100 == 0:
                    print(f"[{datetime.now()}] 진행: {processed}/{total}, 업데이트: {updated}, PDF없음: {no_pdf}, 실패: {failed}")
    
    conn.close()
    
    print(f"\n[{datetime.now()}] === 완료 ===")
    print(f"총 처리: {processed}")
    print(f"PDF URL 업데이트: {updated}")
    print(f"PDF 없음: {no_pdf}")
    print(f"실패: {failed}")

if __name__ == "__main__":
    asyncio.run(update_pdf_urls())
