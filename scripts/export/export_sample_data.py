#!/usr/bin/env python3
"""
샘플 사이트용 데이터 익스포트 스크립트

생성되는 파일:
1. metadata.db - 경량화된 SQLite (본문 제외)
2. laws.json - 법령 메타데이터 JSON
3. neo4j/nodes.csv - Neo4j 노드 임포트용
4. neo4j/relationships.csv - Neo4j 관계 임포트용
5. stats.json - 통계 정보
"""

import sqlite3
import json
import csv
import re
import os
from datetime import datetime
from pathlib import Path

# 경로 설정
BASE_DIR = Path(__file__).parent.parent
SOURCE_DB = BASE_DIR / "data" / "peraturan.db"
OUTPUT_DIR = BASE_DIR / "delivery" / "sample-site-data"
NEO4J_DIR = OUTPUT_DIR / "neo4j"

def ensure_dirs():
    """출력 디렉토리 생성"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    NEO4J_DIR.mkdir(parents=True, exist_ok=True)

def extract_relationships_from_status(status: str) -> list:
    """
    status 필드에서 법령 관계 추출

    실제 데이터 형식:
    - "Tidak BerlakuDicabut Oleh :Peraturan Arsip Nasional Nomor 10 Tahun 2016"
    - "Tidak BerlakuDiubah Oleh :PP Nomor 12 Tahun 2020"
    """
    relationships = []
    if not status:
        return relationships

    # 폐지 관계 추출 - 실제 포맷에 맞게 수정
    # "Dicabut Oleh :" 또는 "Dicabut Oleh:" 형태
    dicabut_pattern = r"Dicabut Oleh\s*:\s*(.+?)\s*(?:Nomor|No\.?)\s*(\d+)\s*Tahun\s*(\d+)"
    matches = re.findall(dicabut_pattern, status, re.IGNORECASE)
    for match in matches:
        jenis_full, nomor, tahun = match
        jenis = normalize_jenis(jenis_full.strip())
        relationships.append({
            "type": "DICABUT_OLEH",
            "target_jenis": jenis,
            "target_nomor": nomor,
            "target_tahun": int(tahun)
        })

    # 개정 관계 추출
    diubah_pattern = r"Diubah Oleh\s*:\s*(.+?)\s*(?:Nomor|No\.?)\s*(\d+)\s*Tahun\s*(\d+)"
    matches = re.findall(diubah_pattern, status, re.IGNORECASE)
    for match in matches:
        jenis_full, nomor, tahun = match
        jenis = normalize_jenis(jenis_full.strip())
        relationships.append({
            "type": "DIUBAH_OLEH",
            "target_jenis": jenis,
            "target_nomor": nomor,
            "target_tahun": int(tahun)
        })

    return relationships

def normalize_jenis(jenis_raw: str) -> str:
    """법령 유형 정규화 (긴 이름 → 약어)"""
    jenis = jenis_raw.upper()

    # 매핑 테이블
    mappings = {
        "UNDANG-UNDANG": "UU",
        "UNDANG UNDANG": "UU",
        "PERATURAN PEMERINTAH": "PP",
        "PERATURAN PRESIDEN": "PERPRES",
        "KEPUTUSAN PRESIDEN": "KEPPRES",
        "INSTRUKSI PRESIDEN": "INPRES",
        "PERPPU": "PERPPU",
        "PERATURAN MENTERI": "PERMEN",
    }

    for full, abbr in mappings.items():
        if full in jenis:
            return abbr

    # 기타 Peraturan 처리
    if "PERATURAN" in jenis:
        # "PERATURAN ARSIP NASIONAL" → "PERATURAN ARSIP NASIONAL"
        return jenis_raw.title()

    return jenis_raw

def determine_validity_status(status: str) -> str:
    """현행성 상태 결정"""
    if not status:
        return "UNKNOWN"

    status_lower = status.lower()
    if "tidak berlaku" in status_lower or "dicabut" in status_lower:
        return "DICABUT"
    elif "berlaku" in status_lower:
        return "BERLAKU"
    else:
        return "UNKNOWN"

def create_slug(jenis: str, nomor: str, tahun: int) -> str:
    """법령 슬러그 생성"""
    jenis_map = {
        "UNDANG-UNDANG": "uu",
        "PERATURAN PEMERINTAH": "pp",
        "PERATURAN PRESIDEN": "perpres",
        "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "perppu",
        "PERATURAN MENTERI": "permen",
        "KEPUTUSAN PRESIDEN": "keppres",
    }
    prefix = jenis_map.get(jenis, jenis.lower().replace(" ", "-")[:10])
    return f"{prefix}-no-{nomor}-tahun-{tahun}"

def export_metadata_db():
    """경량화된 메타데이터 DB 생성 (본문 제외)"""
    print("1. 메타데이터 DB 익스포트...")

    source_conn = sqlite3.connect(SOURCE_DB)
    source_conn.row_factory = sqlite3.Row

    output_db = OUTPUT_DIR / "metadata.db"
    if output_db.exists():
        output_db.unlink()

    target_conn = sqlite3.connect(output_db)

    # 테이블 생성
    target_conn.execute("""
        CREATE TABLE peraturan (
            id INTEGER PRIMARY KEY,
            slug TEXT UNIQUE,
            jenis TEXT,
            nomor TEXT,
            tahun INTEGER,
            judul TEXT,
            status TEXT,
            status_berlaku TEXT,
            tanggal_penetapan TEXT,
            pemrakarsa TEXT,
            pdf_url TEXT,
            has_pdf INTEGER,
            has_text INTEGER,
            pasal_count INTEGER
        )
    """)

    target_conn.execute("""
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY,
            source_slug TEXT,
            target_slug TEXT,
            relationship_type TEXT,
            source TEXT DEFAULT 'metadata'
        )
    """)

    # 인덱스 생성
    target_conn.execute("CREATE INDEX idx_jenis ON peraturan(jenis)")
    target_conn.execute("CREATE INDEX idx_tahun ON peraturan(tahun)")
    target_conn.execute("CREATE INDEX idx_status ON peraturan(status_berlaku)")
    target_conn.execute("CREATE INDEX idx_rel_source ON relationships(source_slug)")
    target_conn.execute("CREATE INDEX idx_rel_target ON relationships(target_slug)")

    # 데이터 복사
    cursor = source_conn.execute("""
        SELECT slug, jenis, nomor, tahun, tentang, status,
               tanggal_penetapan, pemrakarsa, pdf_url,
               local_pdf_path, extracted_text, pasal_count
        FROM peraturan
    """)

    laws = []
    relationships = []

    for row in cursor:
        slug = row['slug']
        status = row['status'] or ""

        # 현행성 상태 결정
        status_berlaku = determine_validity_status(status)

        # 관계 추출
        rels = extract_relationships_from_status(status)
        for rel in rels:
            target_slug = create_slug(
                rel['target_jenis'],
                rel['target_nomor'],
                rel['target_tahun']
            )
            relationships.append({
                "source_slug": slug,
                "target_slug": target_slug,
                "type": rel['type']
            })

        # 메타데이터 저장
        has_pdf = 1 if row['local_pdf_path'] else 0
        has_text = 1 if row['extracted_text'] else 0

        target_conn.execute("""
            INSERT INTO peraturan
            (slug, jenis, nomor, tahun, judul, status, status_berlaku,
             tanggal_penetapan, pemrakarsa, pdf_url, has_pdf, has_text, pasal_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            slug, row['jenis'], row['nomor'], row['tahun'], row['tentang'],
            status, status_berlaku, row['tanggal_penetapan'], row['pemrakarsa'],
            row['pdf_url'], has_pdf, has_text, row['pasal_count'] or 0
        ))

        laws.append({
            "slug": slug,
            "jenis": row['jenis'],
            "nomor": row['nomor'],
            "tahun": row['tahun'],
            "judul": row['tentang'],
            "status": status,
            "status_berlaku": status_berlaku,
            "has_pdf": has_pdf,
            "has_text": has_text
        })

    # 관계 저장
    for rel in relationships:
        target_conn.execute("""
            INSERT INTO relationships (source_slug, target_slug, relationship_type)
            VALUES (?, ?, ?)
        """, (rel['source_slug'], rel['target_slug'], rel['type']))

    target_conn.commit()

    # 통계
    count = target_conn.execute("SELECT COUNT(*) FROM peraturan").fetchone()[0]
    rel_count = target_conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]

    target_conn.close()
    source_conn.close()

    print(f"   ✅ metadata.db: {count:,}건 법령, {rel_count:,}건 관계")

    return laws, relationships

def export_json(laws: list):
    """JSON 익스포트"""
    print("2. JSON 익스포트...")

    output_file = OUTPUT_DIR / "laws.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "exported_at": datetime.now().isoformat(),
            "count": len(laws),
            "laws": laws
        }, f, ensure_ascii=False, indent=2)

    print(f"   ✅ laws.json: {len(laws):,}건")

def export_neo4j_csv(laws: list, relationships: list):
    """Neo4j 임포트용 CSV 생성"""
    print("3. Neo4j CSV 익스포트...")

    # 노드 CSV
    nodes_file = NEO4J_DIR / "nodes.csv"
    with open(nodes_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'slug:ID', 'jenis', 'nomor', 'tahun:int', 'judul',
            'status_berlaku', 'has_pdf:boolean', ':LABEL'
        ])
        for law in laws:
            writer.writerow([
                law['slug'],
                law['jenis'],
                law['nomor'],
                law['tahun'],
                law['judul'][:200] if law['judul'] else "",  # 제목 길이 제한
                law['status_berlaku'],
                'true' if law['has_pdf'] else 'false',
                'Peraturan'
            ])

    # 관계 CSV
    rels_file = NEO4J_DIR / "relationships.csv"
    with open(rels_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([':START_ID', ':END_ID', ':TYPE', 'source'])
        for rel in relationships:
            writer.writerow([
                rel['source_slug'],
                rel['target_slug'],
                rel['type'],
                'metadata'
            ])

    print(f"   ✅ nodes.csv: {len(laws):,}건")
    print(f"   ✅ relationships.csv: {len(relationships):,}건")

def export_stats(laws: list, relationships: list):
    """통계 정보 익스포트"""
    print("4. 통계 정보 생성...")

    # 유형별 통계
    jenis_stats = {}
    status_stats = {"BERLAKU": 0, "DICABUT": 0, "UNKNOWN": 0}
    year_stats = {}

    for law in laws:
        # 유형별
        jenis = law['jenis']
        if jenis not in jenis_stats:
            jenis_stats[jenis] = {"total": 0, "has_pdf": 0, "has_text": 0}
        jenis_stats[jenis]["total"] += 1
        jenis_stats[jenis]["has_pdf"] += law['has_pdf']
        jenis_stats[jenis]["has_text"] += law['has_text']

        # 상태별
        status_stats[law['status_berlaku']] += 1

        # 연도별
        year = law['tahun']
        if year:
            if year not in year_stats:
                year_stats[year] = 0
            year_stats[year] += 1

    # 관계 유형별
    rel_type_stats = {}
    for rel in relationships:
        rel_type = rel['type']
        if rel_type not in rel_type_stats:
            rel_type_stats[rel_type] = 0
        rel_type_stats[rel_type] += 1

    stats = {
        "exported_at": datetime.now().isoformat(),
        "summary": {
            "total_laws": len(laws),
            "total_relationships": len(relationships),
            "pdf_available": sum(1 for l in laws if l['has_pdf']),
            "text_available": sum(1 for l in laws if l['has_text'])
        },
        "by_jenis": jenis_stats,
        "by_status": status_stats,
        "by_year": dict(sorted(year_stats.items(), reverse=True)[:20]),
        "by_relationship_type": rel_type_stats
    }

    output_file = OUTPUT_DIR / "stats.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(f"   ✅ stats.json")
    print(f"      - 총 법령: {stats['summary']['total_laws']:,}건")
    print(f"      - 총 관계: {stats['summary']['total_relationships']:,}건")
    print(f"      - PDF 있음: {stats['summary']['pdf_available']:,}건")
    print(f"      - 현행: {status_stats['BERLAKU']:,}건, 폐지: {status_stats['DICABUT']:,}건")

def create_readme():
    """README 생성"""
    print("5. README 생성...")

    readme = """# 샘플 사이트용 데이터

## 파일 구조

```
sample-site-data/
├── metadata.db          # SQLite 메타데이터 (본문 제외, 경량화)
├── laws.json            # 법령 목록 JSON
├── stats.json           # 통계 정보
└── neo4j/
    ├── nodes.csv        # Neo4j 노드 임포트용
    └── relationships.csv # Neo4j 관계 임포트용
```

## metadata.db 스키마

### peraturan 테이블
| 컬럼 | 타입 | 설명 |
|-----|------|------|
| slug | TEXT | 고유 식별자 (uu-no-6-tahun-2023) |
| jenis | TEXT | 법령 유형 (UNDANG-UNDANG, PP, ...) |
| nomor | TEXT | 법령 번호 |
| tahun | INTEGER | 연도 |
| judul | TEXT | 제목 |
| status | TEXT | 원본 상태 문자열 |
| status_berlaku | TEXT | 현행성 (BERLAKU, DICABUT, UNKNOWN) |
| has_pdf | INTEGER | PDF 보유 여부 (0/1) |
| has_text | INTEGER | 본문 텍스트 보유 여부 (0/1) |
| pasal_count | INTEGER | 조항 수 |

### relationships 테이블
| 컬럼 | 타입 | 설명 |
|-----|------|------|
| source_slug | TEXT | 출발 법령 |
| target_slug | TEXT | 도착 법령 |
| relationship_type | TEXT | 관계 유형 (DICABUT_OLEH, DIUBAH_OLEH) |

## Neo4j 임포트

```bash
# 노드 임포트
neo4j-admin import --nodes=neo4j/nodes.csv --relationships=neo4j/relationships.csv

# 또는 Cypher LOAD CSV
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
CREATE (:Peraturan {
    slug: row.`slug:ID`,
    jenis: row.jenis,
    nomor: row.nomor,
    tahun: toInteger(row.`tahun:int`),
    judul: row.judul,
    status: row.status_berlaku
});

LOAD CSV WITH HEADERS FROM 'file:///relationships.csv' AS row
MATCH (source:Peraturan {slug: row.`:START_ID`})
MATCH (target:Peraturan {slug: row.`:END_ID`})
CREATE (source)-[:DICABUT_OLEH]->(target);
```

## 사용 예시

### Python SQLite
```python
import sqlite3

conn = sqlite3.connect('metadata.db')
cursor = conn.cursor()

# 현행 법률 목록
cursor.execute('''
    SELECT slug, judul FROM peraturan
    WHERE jenis = 'UNDANG-UNDANG' AND status_berlaku = 'BERLAKU'
    ORDER BY tahun DESC
''')

# 폐지 관계 조회
cursor.execute('''
    SELECT r.source_slug, r.target_slug
    FROM relationships r
    WHERE r.relationship_type = 'DICABUT_OLEH'
''')
```

### Neo4j Cypher
```cypher
// 특정 법령을 폐지한 법령 찾기
MATCH (old:Peraturan)-[:DICABUT_OLEH]->(new:Peraturan)
WHERE old.slug = 'uu-no-13-tahun-2003'
RETURN new.slug, new.judul

// 법령 관계 체인
MATCH path = (p:Peraturan)-[:DICABUT_OLEH*1..3]->(target:Peraturan)
WHERE p.jenis = 'UNDANG-UNDANG'
RETURN path
```

---
생성일: """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    readme_file = OUTPUT_DIR / "README.md"
    with open(readme_file, 'w', encoding='utf-8') as f:
        f.write(readme)

    print("   ✅ README.md")

def main():
    print("=" * 60)
    print("샘플 사이트용 데이터 익스포트")
    print("=" * 60)
    print(f"소스: {SOURCE_DB}")
    print(f"출력: {OUTPUT_DIR}")
    print("-" * 60)

    ensure_dirs()

    # 1. 메타데이터 DB + 관계 추출
    laws, relationships = export_metadata_db()

    # 2. JSON 익스포트
    export_json(laws)

    # 3. Neo4j CSV
    export_neo4j_csv(laws, relationships)

    # 4. 통계
    export_stats(laws, relationships)

    # 5. README
    create_readme()

    print("-" * 60)
    print("✅ 익스포트 완료!")
    print(f"   출력 폴더: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
