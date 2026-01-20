#!/usr/bin/env python3
"""
DB 경로 마이그레이션 스크립트
로컬 경로를 서버 경로로 변환

Usage:
    python migrate_paths.py
"""

import sqlite3
import re
from pathlib import Path

# Config
DB_PATH = Path("/mnt/workspace/images/ocr_pipeline.db")
SERVER_HEADERS_DIR = "/mnt/workspace/images/headers"

# Local path pattern
LOCAL_PATTERN = r'^.*/peraturan/data/headers/'


def migrate():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # Check current paths
    cursor.execute("SELECT image_path FROM headers LIMIT 1")
    sample = cursor.fetchone()

    if sample:
        print(f"Sample path: {sample[0]}")

        if sample[0].startswith('/mnt/workspace'):
            print("Paths already migrated!")
            conn.close()
            return

    # Update paths
    print("Migrating paths...")

    cursor.execute("""
        UPDATE headers
        SET image_path = REPLACE(
            image_path,
            SUBSTR(image_path, 1, INSTR(image_path, '/headers/') - 1),
            '/mnt/workspace/images'
        )
        WHERE image_path LIKE '%/headers/%'
    """)

    affected = cursor.rowcount
    conn.commit()

    print(f"Updated {affected} records")

    # Verify
    cursor.execute("SELECT image_path FROM headers LIMIT 3")
    for row in cursor.fetchall():
        print(f"  -> {row[0]}")

    conn.close()
    print("Done!")


if __name__ == '__main__':
    migrate()
