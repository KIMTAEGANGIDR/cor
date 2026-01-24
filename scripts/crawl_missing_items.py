#!/usr/bin/env python3
"""KOICA 누락 항목 크롤링 스크립트

CSV 파일에 있는 누락 항목들을 직접 크롤링합니다.
- UUDRT: 177건
- PERPPU: 15건 (DB에 없는 것들)
- UUDS: 1건
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
REPORTS_DIR = BASE_DIR / "reports"
LOG_FILE = BASE_DIR / "logs" / "crawl_missing.log"

# CSV 파일들
CSV_FILES = {
    "uudrt": REPORTS_DIR / "koica_missing_items_uudrt.csv",
    "perppu": REPORTS_DIR / "koica_missing_items_perppu.csv",
}


def log(msg: str):
    """로그 출력 및 파일 저장"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def extract_metadata(html: str, slug: str) -> dict | None:
    """HTML에서 메타데이터 추출"""
    soup = BeautifulSoup(html, "lxml")

    result = {"slug": slug}

    # 제목 추출
    title_tag = soup.find("h1") or soup.find("h2")
    if title_tag:
        result["judul"] = title_tag.get_text(strip=True)

    # 메타 태그에서 정보 추출
    for meta in soup.find_all("meta"):
        name = meta.get("name", "").lower()
        content = meta.get("content", "")
        if name == "description":
            result["tentang"] = content

    # PDF URL 추출
    pdf_matches = re.findall(
        r'https://peraturan\.go\.id/files/[^"\'<>\s]+\.pdf',
        html, re.IGNORECASE
    )
    if pdf_matches:
        result["pdf_url"] = pdf_matches[0]
    else:
        result["pdf_url"] = "NO_PDF"

    # 테이블에서 상세 정보 추출
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 2:
            key = tds[0].get_text(strip=True).lower()
            val = tds[1].get_text(strip=True)

            if "nomor" in key:
                result["nomor"] = val
            elif "tahun" in key:
                try:
                    result["tahun"] = int(val)
                except:
                    pass
            elif "jenis" in key or "tipe" in key:
                result["jenis"] = val
            elif "tentang" in key:
                result["tentang"] = val
            elif "status" in key:
                result["status"] = val
            elif "tempat" in key and "penetapan" in key:
                result["tempat_penetapan"] = val
            elif "tanggal" in key and "penetapan" in key:
                result["tanggal_penetapan"] = val
            elif "tanggal" in key and "pengundangan" in key:
                result["tanggal_pengundangan"] = val
            elif "tanggal" in key and "berlaku" in key:
                result["tanggal_berlaku"] = val
            elif "sumber" in key:
                result["sumber"] = val

    # 필수 필드 추출 실패 시 None
    if "nomor" not in result and "tahun" not in result:
        # slug에서 추출 시도
        match = re.match(r"(\w+)-no-(\d+)-tahun-(\d+)", slug)
        if match:
            result["jenis"] = match.group(1).upper().replace("-", " ")
            result["nomor"] = match.group(2)
            result["tahun"] = int(match.group(3))

    return result


async def crawl_url(client: httpx.AsyncClient, url: str, slug: str) -> dict | None:
    """단일 URL 크롤링"""
    try:
        resp = await client.get(url, timeout=30, follow_redirects=True)
        if resp.status_code != 200:
            log(f"  {slug}: HTTP {resp.status_code}")
            return None

        return extract_metadata(resp.text, slug)
    except Exception as e:
        log(f"  {slug}: Error - {e}")
        return None


def save_to_db(conn: sqlite3.Connection, data: dict):
    """DB에 저장 (upsert)"""
    cur = conn.cursor()

    # 기존 레코드 확인 (slug가 primary key)
    cur.execute("SELECT slug FROM peraturan WHERE slug = ?", (data["slug"],))
    existing = cur.fetchone()

    # 컬럼 매핑 (DB 스키마에 맞춤)
    column_map = {
        "tentang": "tentang",
        "jenis": "jenis",
        "nomor": "nomor",
        "tahun": "tahun",
        "status": "status",
        "pdf_url": "pdf_url",
        "tempat_penetapan": "tempat_penetapan",
        "tanggal_penetapan": "tanggal_penetapan",
        "tanggal_pengundangan": "tanggal_pengundangan",
    }

    if existing:
        # 업데이트
        fields = []
        values = []
        for key, col in column_map.items():
            if key in data:
                fields.append(f"{col} = ?")
                values.append(data[key])

        if fields:
            values.append(data["slug"])
            cur.execute(f"""
                UPDATE peraturan SET {', '.join(fields)}, updated_at = datetime('now')
                WHERE slug = ?
            """, values)
    else:
        # 삽입 - 필수 필드 확인
        if "nomor" not in data or "tahun" not in data or "jenis" not in data:
            log(f"  필수 필드 누락: {data}")
            return False

        # source_url 추가
        data["source_url"] = f"https://peraturan.go.id/id/{data['slug']}"

        columns = ["slug", "source_url"]
        placeholders = ["?", "?"]
        values = [data["slug"], data["source_url"]]

        for key, col in column_map.items():
            if key in data:
                columns.append(col)
                placeholders.append("?")
                values.append(data[key])

        try:
            cur.execute(f"""
                INSERT INTO peraturan ({', '.join(columns)})
                VALUES ({', '.join(placeholders)})
            """, values)
        except sqlite3.IntegrityError as e:
            log(f"  DB 오류: {e}")
            return False

    conn.commit()
    return True


async def crawl_from_csv(csv_path: Path, jenis: str):
    """CSV 파일의 항목들 크롤링"""
    if not csv_path.exists():
        log(f"CSV 파일 없음: {csv_path}")
        return

    # CSV 읽기
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        items = list(reader)

    # DB에 없는 항목만 필터링
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    to_crawl = []
    for item in items:
        slug = item.get("slug", "")
        if not slug:
            continue

        cur.execute("SELECT slug FROM peraturan WHERE slug = ?", (slug,))
        if not cur.fetchone():
            to_crawl.append(item)

    log(f"\n{'='*60}")
    log(f"{jenis.upper()} 크롤링 시작")
    log(f"전체: {len(items)}건, DB 누락: {len(to_crawl)}건")
    log(f"{'='*60}\n")

    if not to_crawl:
        log("크롤링할 항목 없음")
        conn.close()
        return

    success = 0
    failed = 0

    async with httpx.AsyncClient() as client:
        for i, item in enumerate(to_crawl, 1):
            slug = item["slug"]
            url = item.get("url") or f"https://peraturan.go.id/id/{slug}"

            log(f"[{i}/{len(to_crawl)}] {slug}")

            data = await crawl_url(client, url, slug)
            if data:
                # jenis 강제 설정
                if jenis == "uudrt":
                    data["jenis"] = "UNDANG-UNDANG DARURAT"
                elif jenis == "perppu":
                    data["jenis"] = "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG"

                if save_to_db(conn, data):
                    success += 1
                    pdf_info = data.get('pdf_url', 'NO_PDF')
                    if len(pdf_info) > 50:
                        pdf_info = pdf_info[:50] + "..."
                    log(f"  -> 저장 완료 (PDF: {pdf_info})")
                else:
                    failed += 1
                    log(f"  -> 저장 실패")
            else:
                failed += 1

            # Rate limiting
            await asyncio.sleep(1.5)

    conn.close()

    log(f"\n{jenis.upper()} 완료: 성공 {success}건, 실패 {failed}건\n")


async def crawl_uuds():
    """UUDS (임시헌법) 크롤링"""
    log("\n" + "="*60)
    log("UUDS 크롤링 시작")
    log("="*60 + "\n")

    # UUDS slug 추정
    slug = "uuds-no-1-tahun-1950"
    url = f"https://peraturan.go.id/id/{slug}"

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 이미 있는지 확인
    cur.execute("SELECT slug FROM peraturan WHERE slug LIKE 'uuds%'")
    if cur.fetchone():
        log("UUDS 이미 존재")
        conn.close()
        return

    async with httpx.AsyncClient() as client:
        log(f"[1/1] {slug}")
        data = await crawl_url(client, url, slug)

        if data:
            data["jenis"] = "UNDANG-UNDANG DASAR SEMENTARA"
            if save_to_db(conn, data):
                log(f"  -> 저장 완료")
            else:
                log(f"  -> 저장 실패")
        else:
            log("  -> 크롤링 실패")

    conn.close()


async def main():
    log("\n" + "="*60)
    log("KOICA 누락 항목 크롤링 시작")
    log("="*60)

    # 1. UUDRT 크롤링
    await crawl_from_csv(CSV_FILES["uudrt"], "uudrt")

    # 2. PERPPU 누락분 크롤링 (DB에 없는 것만)
    await crawl_from_csv(CSV_FILES["perppu"], "perppu")

    # 3. UUDS 크롤링
    await crawl_uuds()

    log("\n" + "="*60)
    log("모든 크롤링 완료")
    log("="*60)


if __name__ == "__main__":
    asyncio.run(main())
