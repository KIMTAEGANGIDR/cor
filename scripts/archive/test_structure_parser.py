#!/usr/bin/env python3
"""Test the structure parser with sample documents."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.structure_parser import StructureParser, parse_document


def get_sample_texts(db_path: str = "data/peraturan.db", limit: int = 5) -> list[tuple[str, str, str]]:
    """Get sample texts for testing."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get diverse samples
    cursor.execute("""
        SELECT slug, jenis, extracted_text
        FROM peraturan
        WHERE extraction_success = 1
            AND length(extracted_text) > 1000
            AND pasal_count > 5
        ORDER BY RANDOM()
        LIMIT ?
    """, (limit,))

    results = cursor.fetchall()
    conn.close()
    return results


def test_parser():
    """Test the structure parser."""
    print("\n" + "=" * 60)
    print("구조화 파서 테스트")
    print("=" * 60)

    parser = StructureParser()

    samples = get_sample_texts(limit=5)

    for slug, jenis, text in samples:
        print(f"\n{'─' * 60}")
        print(f"📄 {slug}")
        print(f"   유형: {jenis}")
        print(f"   텍스트 길이: {len(text):,}자")
        print(f"{'─' * 60}")

        # Parse
        doc = parser.parse(text, slug)

        # Results
        print(f"\n📊 파싱 결과:")
        print(f"   - BAB 수: {len(doc.babs)}")
        print(f"   - Pasal 수: {doc.total_pasal}")
        print(f"   - Ayat 수: {doc.total_ayat}")
        print(f"   - Huruf 수: {doc.total_huruf}")

        if doc.parse_errors:
            print(f"   ⚠️ 오류: {doc.parse_errors}")

        # Show structure sample
        if doc.babs:
            print(f"\n📋 BAB 구조:")
            for bab in doc.babs[:3]:
                print(f"   BAB {bab.nomor}: {bab.judul[:50]}...")
                print(f"      └─ Pasal: {len(bab.pasals)}개")

        if doc.pasals:
            print(f"\n📋 Pasal 샘플:")
            for pasal in doc.pasals[:3]:
                preview = pasal.text[:100].replace('\n', ' ')
                print(f"   Pasal {pasal.nomor}: {preview}...")
                if pasal.ayats:
                    print(f"      └─ Ayat: {len(pasal.ayats)}개")
                    for ayat in pasal.ayats[:2]:
                        ayat_preview = ayat.text[:60].replace('\n', ' ')
                        print(f"         ({ayat.nomor}) {ayat_preview}...")
                        if ayat.hurufs:
                            print(f"             └─ Huruf: {len(ayat.hurufs)}개")

    # Summary
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)


def test_specific_document(slug: str):
    """Test parsing a specific document."""
    conn = sqlite3.connect("data/peraturan.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT extracted_text FROM peraturan WHERE slug = ?
    """, (slug,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        print(f"문서를 찾을 수 없음: {slug}")
        return

    text = row[0]
    doc = parse_document(text, slug)

    print(f"\n문서: {slug}")
    print(f"BAB: {len(doc.babs)}, Pasal: {doc.total_pasal}, Ayat: {doc.total_ayat}")

    # Convert to dict and show
    import json
    result = doc.to_dict()

    # Show first BAB or Pasal
    if result["babs"]:
        print("\n첫 번째 BAB:")
        print(json.dumps(result["babs"][0], indent=2, ensure_ascii=False)[:1000])
    elif result["pasals"]:
        print("\n첫 번째 Pasal:")
        print(json.dumps(result["pasals"][0], indent=2, ensure_ascii=False)[:1000])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="Test specific document by slug")
    args = parser.parse_args()

    if args.slug:
        test_specific_document(args.slug)
    else:
        test_parser()
