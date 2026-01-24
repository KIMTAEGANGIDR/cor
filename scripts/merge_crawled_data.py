#!/usr/bin/env python3
"""크롤링 데이터 병합 스크립트

새로 크롤링한 데이터를 메인 DB에 병합합니다.
"""

import sqlite3
from datetime import datetime

# DB 경로
NEW_DB = "/home/tylor/cor/data/peraturan.db"
MAIN_DB = "/home/tylor/cor/peraturan/data/db/peraturan.db"


def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")


def merge_peraturan():
    """peraturan 테이블 병합"""
    new_conn = sqlite3.connect(NEW_DB)
    main_conn = sqlite3.connect(MAIN_DB)

    new_cur = new_conn.cursor()
    main_cur = main_conn.cursor()

    # 새 DB에서 데이터 가져오기
    new_cur.execute("SELECT * FROM peraturan")
    columns = [desc[0] for desc in new_cur.description]
    rows = new_cur.fetchall()

    log(f"새 peraturan 데이터: {len(rows)}건")

    inserted = 0
    updated = 0
    skipped = 0

    for row in rows:
        data = dict(zip(columns, row))
        slug = data["slug"]

        # 기존 데이터 확인
        main_cur.execute("SELECT slug FROM peraturan WHERE slug = ?", (slug,))
        existing = main_cur.fetchone()

        if existing:
            # 업데이트 (pdf_url 등)
            if data.get("pdf_url") and data["pdf_url"] != "NO_PDF":
                main_cur.execute("""
                    UPDATE peraturan SET pdf_url = ?, updated_at = datetime('now')
                    WHERE slug = ? AND (pdf_url IS NULL OR pdf_url = 'NO_PDF')
                """, (data["pdf_url"], slug))
                if main_cur.rowcount > 0:
                    updated += 1
                else:
                    skipped += 1
            else:
                skipped += 1
        else:
            # 삽입
            cols = [c for c in columns if c in data and data[c] is not None]
            placeholders = ["?" for _ in cols]
            values = [data[c] for c in cols]

            try:
                main_cur.execute(f"""
                    INSERT INTO peraturan ({', '.join(cols)})
                    VALUES ({', '.join(placeholders)})
                """, values)
                inserted += 1
            except sqlite3.IntegrityError as e:
                log(f"  삽입 실패 {slug}: {e}")
                skipped += 1

    main_conn.commit()
    log(f"peraturan 병합 완료: 삽입 {inserted}, 업데이트 {updated}, 스킵 {skipped}")

    new_conn.close()
    main_conn.close()


def merge_translations():
    """translations 테이블 병합"""
    new_conn = sqlite3.connect(NEW_DB)
    main_conn = sqlite3.connect(MAIN_DB)

    new_cur = new_conn.cursor()
    main_cur = main_conn.cursor()

    # 새 DB에서 데이터 가져오기
    new_cur.execute("SELECT * FROM translations")
    columns = [desc[0] for desc in new_cur.description]
    rows = new_cur.fetchall()

    log(f"새 translations 데이터: {len(rows)}건")

    inserted = 0
    skipped = 0

    for row in rows:
        data = dict(zip(columns, row))
        tr_id = data["id"]

        # 기존 데이터 확인
        main_cur.execute("SELECT id FROM translations WHERE id = ?", (tr_id,))
        if main_cur.fetchone():
            skipped += 1
            continue

        # 삽입
        cols = [c for c in columns if c in data and data[c] is not None]
        placeholders = ["?" for _ in cols]
        values = [data[c] for c in cols]

        try:
            main_cur.execute(f"""
                INSERT INTO translations ({', '.join(cols)})
                VALUES ({', '.join(placeholders)})
            """, values)
            inserted += 1
        except sqlite3.IntegrityError as e:
            log(f"  삽입 실패 {tr_id}: {e}")
            skipped += 1

    main_conn.commit()
    log(f"translations 병합 완료: 삽입 {inserted}, 스킵 {skipped}")

    new_conn.close()
    main_conn.close()


def main():
    log("=" * 60)
    log("크롤링 데이터 병합 시작")
    log("=" * 60)

    merge_peraturan()
    merge_translations()

    log("=" * 60)
    log("병합 완료")
    log("=" * 60)


if __name__ == "__main__":
    main()
