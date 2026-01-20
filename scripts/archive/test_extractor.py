#!/usr/bin/env python3
"""Test the text extractor with sample PDFs."""

from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.extractor import TextExtractor, extract_pdf


def test_single_pdf(pdf_path: Path):
    """Test extraction on a single PDF."""
    print(f"\n{'='*70}")
    print(f"📄 {pdf_path.name}")
    print(f"{'='*70}")

    result = extract_pdf(pdf_path)

    # Status
    status = "✅ 성공" if result.success else f"❌ 실패: {result.error}"
    print(f"\n상태: {status}")

    if result.needs_ocr:
        print("⚠️  OCR 필요 (스캔 이미지)")
        return result

    # Basic info
    print(f"\n📊 기본 정보:")
    print(f"   - 페이지 수: {result.total_pages}")
    print(f"   - 원본 텍스트 길이: {len(result.raw_text):,}자")
    print(f"   - 정제 텍스트 길이: {len(result.clean_text):,}자")
    print(f"   - 본문 텍스트 길이: {len(result.body_text):,}자")

    # Metadata
    print(f"\n📋 메타데이터:")
    print(f"   - 종류: {result.jenis or '(미확인)'}")
    print(f"   - 번호: {result.nomor or '(미확인)'}")
    print(f"   - 연도: {result.tahun or '(미확인)'}")
    print(f"   - 제목: {(result.tentang[:60] + '...') if result.tentang and len(result.tentang) > 60 else result.tentang or '(미확인)'}")

    # Structure
    print(f"\n📐 구조:")
    print(f"   - Pasal 수: {result.pasal_count}개")
    print(f"   - 파싱된 Pasal: {len(result.pasals)}개")
    print(f"   - PENJELASAN 포함: {'✅' if result.has_penjelasan else '❌'}")

    # Sample Pasals
    if result.pasals:
        print(f"\n📝 샘플 Pasal (처음 3개):")
        for pasal in result.pasals[:3]:
            preview = pasal.text[:150].replace('\n', ' ')
            if len(pasal.text) > 150:
                preview += "..."
            print(f"\n   Pasal {pasal.nomor}:")
            print(f"   {preview}")
            if pasal.ayats:
                print(f"   → Ayat 수: {len(pasal.ayats)}개")

    # Body preview
    print(f"\n📖 본문 미리보기 (처음 500자):")
    print("-" * 50)
    preview = result.body_text[:500].replace('\n', '\n   ')
    print(f"   {preview}")
    if len(result.body_text) > 500:
        print("   ...")
    print("-" * 50)

    return result


def main():
    """Run tests on sample PDFs."""
    print("\n" + "=" * 70)
    print("🔬 텍스트 추출기 테스트")
    print("=" * 70)

    # Test samples from each category
    samples = [
        # UU
        "data/pdfs/uu/uu-no-1-tahun-2020.pdf",
        "data/pdfs/uu/uu-no-11-tahun-2020.pdf",  # Omnibus law (large)

        # PP
        "data/pdfs/pp/pp-no-1-tahun-2020.pdf",

        # PERPRES
        "data/pdfs/perpres/perpres-no-1-tahun-2020.pdf",

        # PERPPU
        "data/pdfs/perppu/perppu-no-1-tahun-1998.pdf",

        # PERMEN
        "data/pdfs/permen/permenparekraf-no-18-tahun-2020.pdf",

        # Old document
        "data/pdfs/uu/uu-no-1-tahun-1946.pdf",
    ]

    results = []
    for path_str in samples:
        path = Path(path_str)
        if path.exists():
            result = test_single_pdf(path)
            results.append(result)
        else:
            print(f"\n⚠️ 파일 없음: {path_str}")

    # Summary
    print("\n\n" + "=" * 70)
    print("📊 테스트 요약")
    print("=" * 70)

    total = len(results)
    success = sum(1 for r in results if r.success)
    ocr_needed = sum(1 for r in results if r.needs_ocr)

    print(f"\n총 테스트: {total}개")
    print(f"  - 성공: {success}개 ({100*success/total:.1f}%)")
    print(f"  - OCR 필요: {ocr_needed}개")

    avg_pasal = sum(r.pasal_count for r in results) / total if total else 0
    print(f"  - 평균 Pasal 수: {avg_pasal:.1f}개")

    # Noise reduction stats
    if results:
        total_raw = sum(len(r.raw_text) for r in results)
        total_clean = sum(len(r.clean_text) for r in results)
        total_body = sum(len(r.body_text) for r in results)

        print(f"\n텍스트 정제 효과:")
        print(f"  - 원본 → 정제: {100*(1-total_clean/total_raw):.1f}% 감소")
        print(f"  - 원본 → 본문: {100*(1-total_body/total_raw):.1f}% 감소")


if __name__ == "__main__":
    main()
