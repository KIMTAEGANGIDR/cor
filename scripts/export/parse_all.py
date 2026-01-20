#!/usr/bin/env python3
"""Batch parse all extracted documents into structured format."""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeRemainingColumn

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.structure_parser import StructureParser


def ensure_parse_columns(db_path: str = "data/peraturan.db") -> None:
    """Ensure parsing columns exist in database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(peraturan)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    new_cols = [
        ("parsed_json", "TEXT"),
        ("parsed_bab_count", "INTEGER DEFAULT 0"),
        ("parsed_pasal_count", "INTEGER DEFAULT 0"),
        ("parsed_ayat_count", "INTEGER DEFAULT 0"),
        ("parsed_huruf_count", "INTEGER DEFAULT 0"),
        ("parse_success", "INTEGER DEFAULT 0"),
    ]

    for col_name, col_type in new_cols:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE peraturan ADD COLUMN {col_name} {col_type}")
            print(f"Added column: {col_name}")

    conn.commit()
    conn.close()


def get_documents_to_parse(db_path: str = "data/peraturan.db") -> list[tuple[str, str, str]]:
    """Get documents that need parsing."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT slug, jenis, extracted_text
        FROM peraturan
        WHERE extraction_success = 1
            AND (parse_success IS NULL OR parse_success = 0)
            AND length(extracted_text) > 100
        ORDER BY tahun DESC
    """)

    results = cursor.fetchall()
    conn.close()
    return results


def save_parse_result(
    conn: sqlite3.Connection,
    slug: str,
    doc_dict: dict,
    bab_count: int,
    pasal_count: int,
    ayat_count: int,
    huruf_count: int,
) -> None:
    """Save parsing result to database."""
    cursor = conn.cursor()

    # Store JSON (without full text to save space)
    json_data = json.dumps(doc_dict, ensure_ascii=False)

    cursor.execute("""
        UPDATE peraturan SET
            parsed_json = ?,
            parsed_bab_count = ?,
            parsed_pasal_count = ?,
            parsed_ayat_count = ?,
            parsed_huruf_count = ?,
            parse_success = 1,
            updated_at = ?
        WHERE slug = ?
    """, (
        json_data,
        bab_count,
        pasal_count,
        ayat_count,
        huruf_count,
        datetime.now().isoformat(),
        slug,
    ))


def run_batch_parsing(
    db_path: str = "data/peraturan.db",
    batch_size: int = 100,
    limit: int = 0,
) -> dict:
    """Run batch parsing on all extracted documents."""

    ensure_parse_columns(db_path)

    docs = get_documents_to_parse(db_path)

    if limit > 0:
        docs = docs[:limit]

    total = len(docs)
    print(f"\n총 {total:,}개 문서 파싱 시작...\n")

    if total == 0:
        print("파싱할 문서가 없습니다.")
        return {}

    parser = StructureParser()

    stats = {
        "total": total,
        "success": 0,
        "failed": 0,
        "total_babs": 0,
        "total_pasals": 0,
        "total_ayats": 0,
        "total_hurufs": 0,
        "by_jenis": defaultdict(lambda: {"count": 0, "pasals": 0, "ayats": 0}),
        "start_time": datetime.now().isoformat(),
    }

    conn = sqlite3.connect(db_path)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("✓:{task.fields[success]} Pasal:{task.fields[pasals]}"),
        TimeRemainingColumn(),
    ) as progress:

        task = progress.add_task(
            "구조화 파싱 중",
            total=total,
            success=0,
            pasals=0,
        )

        for i, (slug, jenis, text) in enumerate(docs):
            try:
                # Parse document
                doc = parser.parse(text, slug)

                # Convert to dict (for JSON storage)
                doc_dict = doc.to_dict()

                # Save to database
                save_parse_result(
                    conn, slug, doc_dict,
                    len(doc.babs),
                    doc.total_pasal,
                    doc.total_ayat,
                    doc.total_huruf,
                )

                stats["success"] += 1
                stats["total_babs"] += len(doc.babs)
                stats["total_pasals"] += doc.total_pasal
                stats["total_ayats"] += doc.total_ayat
                stats["total_hurufs"] += doc.total_huruf

                # By jenis stats
                jenis_key = jenis or "UNKNOWN"
                stats["by_jenis"][jenis_key]["count"] += 1
                stats["by_jenis"][jenis_key]["pasals"] += doc.total_pasal
                stats["by_jenis"][jenis_key]["ayats"] += doc.total_ayat

            except Exception as e:
                stats["failed"] += 1

            progress.update(
                task,
                advance=1,
                success=stats["success"],
                pasals=stats["total_pasals"],
            )

            # Commit periodically
            if (i + 1) % batch_size == 0:
                conn.commit()

    conn.commit()
    conn.close()

    stats["end_time"] = datetime.now().isoformat()
    stats["by_jenis"] = dict(stats["by_jenis"])

    return stats


def generate_report(stats: dict, output_path: str = "docs/PARSING_REPORT.md") -> None:
    """Generate parsing report."""

    success_rate = (stats["success"] / stats["total"] * 100) if stats["total"] > 0 else 0
    avg_pasals = stats["total_pasals"] / stats["success"] if stats["success"] > 0 else 0
    avg_ayats = stats["total_ayats"] / stats["success"] if stats["success"] > 0 else 0

    report = f"""# 구조화 파싱 결과 보고서

> 파싱 완료일: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## 1. 요약

| 항목 | 값 |
|------|-----|
| **총 문서** | {stats['total']:,}개 |
| **파싱 성공** | {stats['success']:,}개 ({success_rate:.1f}%) |
| **파싱 실패** | {stats['failed']:,}개 |

## 2. 구조 통계

| 항목 | 총계 | 평균/문서 |
|------|------|-----------|
| **BAB (장)** | {stats['total_babs']:,}개 | {stats['total_babs']/stats['success']:.1f} |
| **Pasal (조)** | {stats['total_pasals']:,}개 | {avg_pasals:.1f} |
| **Ayat (항)** | {stats['total_ayats']:,}개 | {avg_ayats:.1f} |
| **Huruf (호)** | {stats['total_hurufs']:,}개 | {stats['total_hurufs']/stats['success']:.1f} |

## 3. 법령 유형별 결과

| 유형 | 문서 수 | 총 Pasal | 총 Ayat | 평균 Pasal |
|------|---------|----------|---------|------------|
"""

    for jenis, data in sorted(stats["by_jenis"].items(), key=lambda x: -x[1]["count"]):
        avg = data["pasals"] / data["count"] if data["count"] > 0 else 0
        report += f"| {jenis} | {data['count']:,} | {data['pasals']:,} | {data['ayats']:,} | {avg:.1f} |\n"

    report += f"""
## 4. 시간 정보

- 시작: {stats['start_time']}
- 종료: {stats['end_time']}

## 5. 데이터베이스 스키마

파싱 결과는 `peraturan` 테이블에 저장됨:

```sql
parsed_json TEXT,           -- 구조화된 JSON
parsed_bab_count INTEGER,   -- BAB 수
parsed_pasal_count INTEGER, -- Pasal 수
parsed_ayat_count INTEGER,  -- Ayat 수
parsed_huruf_count INTEGER, -- Huruf 수
parse_success INTEGER       -- 파싱 성공 여부
```

## 6. JSON 구조 예시

```json
{{
  "slug": "uu-no-11-tahun-2020",
  "babs": [
    {{
      "nomor": "I",
      "judul": "KETENTUAN UMUM",
      "pasals": [
        {{
          "nomor": 1,
          "text": "Dalam Undang-Undang ini...",
          "ayats": [
            {{
              "nomor": 1,
              "text": "...",
              "hurufs": [...]
            }}
          ]
        }}
      ]
    }}
  ],
  "stats": {{
    "total_pasal": 1220,
    "total_ayat": 2983,
    "total_huruf": 0
  }}
}}
```

---

*Generated: {datetime.now().strftime('%Y-%m-%d')}*
"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n보고서 저장: {output_path}")


def main():
    """Run batch parsing."""
    import argparse

    parser = argparse.ArgumentParser(description="Batch parse extracted documents")
    parser.add_argument("--limit", type=int, default=0, help="Limit documents")
    parser.add_argument("--batch-size", type=int, default=100, help="Commit batch size")
    args = parser.parse_args()

    stats = run_batch_parsing(limit=args.limit, batch_size=args.batch_size)

    if stats:
        # Save stats
        stats_path = "data/parsing_stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        print(f"통계 저장: {stats_path}")

        # Generate report
        generate_report(stats)

        # Summary
        print("\n" + "=" * 50)
        print("파싱 완료!")
        print("=" * 50)
        print(f"성공: {stats['success']:,}개")
        print(f"실패: {stats['failed']:,}개")
        print(f"총 Pasal: {stats['total_pasals']:,}개")
        print(f"총 Ayat: {stats['total_ayats']:,}개")


if __name__ == "__main__":
    main()
