#!/usr/bin/env python3
"""TERJEMAH(공식 번역본) 크롤링 스크립트

CSV 파일에 있는 번역본 항목들을 크롤링하여 translations 테이블에 저장합니다.
"""

import asyncio
import csv
import sqlite3
import re
from pathlib import Path
from datetime import datetime
import httpx
from bs4 import BeautifulSoup


# 경로 설정
BASE_DIR = Path("/home/tylor/cor")
DB_PATH = BASE_DIR / "data" / "peraturan.db"
CSV_PATH = BASE_DIR / "reports" / "koica_missing_items_terjemah.csv"
LOG_FILE = BASE_DIR / "logs" / "crawl_terjemah_direct.log"


def log(msg: str):
    """로그 출력 및 파일 저장"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def extract_translation_info(html: str, slug: str) -> dict | None:
    """HTML에서 번역 정보 추출"""
    soup = BeautifulSoup(html, "lxml")

    result = {"slug": slug}

    # 제목 추출
    title_tag = soup.find("h1") or soup.find("h2")
    if title_tag:
        result["translated_title"] = title_tag.get_text(strip=True)

    # 원본 제목 (Indonesian)
    for tag in soup.find_all(["p", "div", "span"]):
        text = tag.get_text(strip=True)
        if "tentang" in text.lower():
            result["original_title"] = text
            break

    # PDF URL 추출
    pdf_matches = re.findall(
        r'https://peraturan\.go\.id/files[^"\'<>\s]+\.pdf',
        html, re.IGNORECASE
    )
    if pdf_matches:
        result["pdf_url"] = pdf_matches[0]

    return result


async def crawl_translation(client: httpx.AsyncClient, url: str, slug: str) -> dict | None:
    """단일 번역본 URL 크롤링"""
    try:
        resp = await client.get(url, timeout=30, follow_redirects=True)
        if resp.status_code != 200:
            log(f"  {slug}: HTTP {resp.status_code}")
            return None

        return extract_translation_info(resp.text, slug)
    except Exception as e:
        log(f"  {slug}: Error - {e}")
        return None


def save_translation(conn: sqlite3.Connection, data: dict, csv_row: dict) -> bool:
    """번역본 DB에 저장"""
    cur = conn.cursor()

    # 기존 레코드 확인
    translation_id = f"tr-{data['slug']}"
    cur.execute("SELECT id FROM translations WHERE id = ?", (translation_id,))
    if cur.fetchone():
        log(f"  이미 존재: {translation_id}")
        return False

    # 원본 slug 추출 (번역 slug에서)
    original_slug = data["slug"]

    # translated_title이 없으면 slug에서 생성
    translated_title = data.get("translated_title") or f"Translation of {original_slug}"

    try:
        cur.execute("""
            INSERT INTO translations (id, original_slug, original_title, translated_title,
                                      language, pdf_url, source_url, created_at)
            VALUES (?, ?, ?, ?, 'en', ?, ?, datetime('now'))
        """, (
            translation_id,
            original_slug,
            data.get("original_title", ""),
            translated_title,
            csv_row.get("pdf_url", ""),
            csv_row.get("translation_url", "")
        ))
        conn.commit()
        return True
    except sqlite3.IntegrityError as e:
        log(f"  DB 오류: {e}")
        return False


async def main():
    log("\n" + "="*60)
    log("TERJEMAH 크롤링 시작")
    log("="*60)

    if not CSV_PATH.exists():
        log(f"CSV 파일 없음: {CSV_PATH}")
        return

    # CSV 읽기
    with open(CSV_PATH, "r") as f:
        reader = csv.DictReader(f)
        items = list(reader)

    log(f"전체 번역본: {len(items)}건")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # DB에 없는 항목만 필터링
    to_crawl = []
    for item in items:
        slug = item.get("slug", "")
        if not slug:
            continue

        translation_id = f"tr-{slug}"
        cur.execute("SELECT id FROM translations WHERE id = ?", (translation_id,))
        if not cur.fetchone():
            to_crawl.append(item)

    log(f"DB 누락: {len(to_crawl)}건\n")

    if not to_crawl:
        log("크롤링할 항목 없음")
        conn.close()
        return

    success = 0
    failed = 0

    async with httpx.AsyncClient() as client:
        for i, item in enumerate(to_crawl, 1):
            slug = item["slug"]
            url = item.get("translation_url", "")

            if not url:
                url = f"https://peraturan.go.id/terjemahresmi/{slug}"

            log(f"[{i}/{len(to_crawl)}] {slug}")

            data = await crawl_translation(client, url, slug)
            if data:
                if save_translation(conn, data, item):
                    success += 1
                    log(f"  -> 저장 완료")
                else:
                    failed += 1
            else:
                # 크롤링 실패해도 CSV 정보로 저장 시도
                data = {"slug": slug}
                if save_translation(conn, data, item):
                    success += 1
                    log(f"  -> CSV 정보로 저장")
                else:
                    failed += 1

            # Rate limiting
            await asyncio.sleep(1.0)

    conn.close()

    log(f"\n{'='*60}")
    log(f"완료: 성공 {success}건, 실패 {failed}건")
    log(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
