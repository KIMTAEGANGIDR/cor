#!/usr/bin/env python3
"""
로컬 테스트 스크립트 (CPU only, PaddleOCR 없이)

테스트 항목:
1. 품질 점수 계산
2. 인도네시아어 사전
3. Tesseract OCR
4. PDF 처리 흐름
"""

import sys
from pathlib import Path

# 모듈 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

import fitz  # PyMuPDF
from rich.console import Console
from rich.table import Table

console = Console()


def test_indonesian_dict():
    """인도네시아어 사전 테스트"""
    console.print("\n[bold cyan]1. 인도네시아어 사전 테스트[/bold cyan]")

    from peraturan.src.ocr.indonesian_dict import IndonesianDictionary

    dict = IndonesianDictionary()

    test_words = [
        ("undang", True, True),      # 법률 용어
        ("peraturan", True, True),   # 법률 용어
        ("PERPRES", True, True),     # 약어
        ("pasal", True, True),       # 구조 용어
        ("adalah", True, False),     # 일반 단어
        ("xyz123", False, False),    # 무효
    ]

    table = Table(title="단어 검증")
    table.add_column("단어")
    table.add_column("유효?")
    table.add_column("법률용어?")
    table.add_column("예상")
    table.add_column("결과")

    all_passed = True
    for word, exp_valid, exp_legal in test_words:
        valid = dict.is_valid_word(word)
        legal = dict.is_legal_term(word)

        passed = (valid == exp_valid) and (legal == exp_legal)
        all_passed = all_passed and passed

        status = "✓" if passed else "✗"
        table.add_row(
            word,
            str(valid),
            str(legal),
            f"v={exp_valid}, l={exp_legal}",
            status,
        )

    console.print(table)
    return all_passed


def test_quality_scorer():
    """품질 점수 계산 테스트"""
    console.print("\n[bold cyan]2. 품질 점수 계산 테스트[/bold cyan]")

    from peraturan.src.ocr.quality_scorer import QualityScorer, ProcessingStrategy

    scorer = QualityScorer()

    test_cases = [
        # (텍스트, 예상 전략)
        (
            """
            UNDANG-UNDANG REPUBLIK INDONESIA
            NOMOR 12 TAHUN 2011
            TENTANG PEMBENTUKAN PERATURAN PERUNDANG-UNDANGAN

            Pasal 1
            Dalam Undang-Undang ini yang dimaksud dengan:
            1. Pembentukan Peraturan Perundang-undangan adalah pembuatan
               Peraturan Perundang-undangan yang mencakup tahapan perencanaan.
            """,
            ProcessingStrategy.TEXT,  # 좋은 품질
        ),
        (
            "■■■□□□ ???????????? Pas▯▯ 1 ___________",
            ProcessingStrategy.OCR,  # 나쁜 품질
        ),
        (
            "",
            ProcessingStrategy.OCR,  # 빈 텍스트 → OCR 시도 (스캔본일 수 있음)
        ),
    ]

    table = Table(title="품질 점수")
    table.add_column("케이스")
    table.add_column("점수")
    table.add_column("전략")
    table.add_column("예상")
    table.add_column("결과")

    all_passed = True
    for i, (text, expected_strategy) in enumerate(test_cases):
        result = scorer.score_text(text)
        passed = result.strategy == expected_strategy
        all_passed = all_passed and passed

        status = "✓" if passed else "✗"
        table.add_row(
            f"케이스 {i+1}",
            f"{result.score:.3f}",
            result.strategy.value,
            expected_strategy.value,
            status,
        )

    console.print(table)
    return all_passed


def test_pdf_extraction(pdf_path: str):
    """PDF 텍스트 추출 테스트"""
    console.print(f"\n[bold cyan]3. PDF 텍스트 추출 테스트[/bold cyan]")
    console.print(f"   파일: {pdf_path}")

    from peraturan.src.ocr.quality_scorer import QualityScorer

    scorer = QualityScorer()

    try:
        doc = fitz.open(pdf_path)
        console.print(f"   페이지 수: {len(doc)}")

        table = Table(title=f"페이지별 품질 (처음 5페이지)")
        table.add_column("페이지")
        table.add_column("문자 수")
        table.add_column("품질")
        table.add_column("전략")

        for page_num in range(min(5, len(doc))):
            page = doc[page_num]
            text = page.get_text("text")
            result = scorer.score_text(text)

            table.add_row(
                str(page_num + 1),
                str(len(text)),
                f"{result.score:.3f}",
                result.strategy.value,
            )

        doc.close()
        console.print(table)
        return True

    except Exception as e:
        console.print(f"[red]오류: {e}[/red]")
        return False


def test_tesseract_ocr(pdf_path: str):
    """Tesseract OCR 테스트"""
    console.print(f"\n[bold cyan]4. Tesseract OCR 테스트[/bold cyan]")

    try:
        import pytesseract
        from PIL import Image
        import io

        doc = fitz.open(pdf_path)
        page = doc[0]  # 첫 페이지만

        # 이미지로 변환
        pix = page.get_pixmap(dpi=150)  # 테스트용으로 낮은 DPI
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))

        console.print("   Tesseract 실행 중...")

        # OCR 실행
        text = pytesseract.image_to_string(img, lang='ind+eng')

        doc.close()

        console.print(f"   추출된 텍스트 길이: {len(text)}")
        console.print(f"   처음 200자:\n   {text[:200]}...")

        return len(text) > 0

    except Exception as e:
        console.print(f"[red]오류: {e}[/red]")
        return False


def main():
    console.print("[bold green]===== OCR 파이프라인 로컬 테스트 =====[/bold green]")

    results = {}

    # 1. 인도네시아어 사전
    results["indonesian_dict"] = test_indonesian_dict()

    # 2. 품질 점수
    results["quality_scorer"] = test_quality_scorer()

    # 3. PDF 추출 (샘플 파일)
    pdf_dir = Path("/Users/kim/GIT/crawling/peraturan/data/pdfs/uu")
    pdf_files = list(pdf_dir.glob("*.pdf"))[:3]

    if pdf_files:
        results["pdf_extraction"] = test_pdf_extraction(str(pdf_files[0]))

        # 4. Tesseract OCR
        results["tesseract"] = test_tesseract_ocr(str(pdf_files[0]))
    else:
        console.print("[yellow]PDF 파일을 찾을 수 없습니다[/yellow]")
        results["pdf_extraction"] = False
        results["tesseract"] = False

    # 결과 요약
    console.print("\n[bold green]===== 테스트 결과 요약 =====[/bold green]")

    table = Table()
    table.add_column("테스트")
    table.add_column("결과")

    for name, passed in results.items():
        status = "[green]✓ PASS[/green]" if passed else "[red]✗ FAIL[/red]"
        table.add_row(name, status)

    console.print(table)

    all_passed = all(results.values())
    if all_passed:
        console.print("\n[bold green]모든 테스트 통과![/bold green]")
    else:
        console.print("\n[bold red]일부 테스트 실패[/bold red]")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
