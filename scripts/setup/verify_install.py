#!/usr/bin/env python3
"""
ILIS OCR Pipeline - 설치 검증 스크립트
"""

import sys

def check_import(name, package=None):
    """패키지 임포트 확인"""
    package = package or name
    try:
        __import__(package)
        print(f"  ✅ {name}")
        return True
    except ImportError as e:
        print(f"  ❌ {name}: {e}")
        return False

def main():
    print("=" * 50)
    print("ILIS OCR Pipeline - 설치 검증")
    print("=" * 50)

    all_ok = True

    # 기본 패키지
    print("\n[기본 패키지]")
    all_ok &= check_import("httpx")
    all_ok &= check_import("beautifulsoup4", "bs4")
    all_ok &= check_import("lxml")
    all_ok &= check_import("click")
    all_ok &= check_import("rich")
    all_ok &= check_import("PyMuPDF", "fitz")
    all_ok &= check_import("Pillow", "PIL")
    all_ok &= check_import("pytesseract")

    # OCR 패키지
    print("\n[OCR 패키지]")
    all_ok &= check_import("easyocr")
    all_ok &= check_import("paddleocr")

    # PaddlePaddle GPU 확인
    print("\n[GPU 확인]")
    try:
        import paddle
        cuda_ok = paddle.device.is_compiled_with_cuda()
        if cuda_ok:
            print(f"  ✅ PaddlePaddle CUDA: {cuda_ok}")
        else:
            print(f"  ⚠️ PaddlePaddle CUDA: {cuda_ok} (CPU 모드)")
    except Exception as e:
        print(f"  ❌ PaddlePaddle: {e}")
        all_ok = False

    try:
        import torch
        cuda_ok = torch.cuda.is_available()
        if cuda_ok:
            print(f"  ✅ PyTorch CUDA: {cuda_ok}")
            print(f"     GPU: {torch.cuda.get_device_name(0)}")
        else:
            print(f"  ⚠️ PyTorch CUDA: {cuda_ok}")
    except Exception as e:
        print(f"  ❌ PyTorch: {e}")

    # PaddleOCR 초기화 테스트
    print("\n[PaddleOCR 초기화]")
    try:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
        print("  ✅ PaddleOCR 초기화 성공")
    except Exception as e:
        print(f"  ❌ PaddleOCR 초기화 실패: {e}")
        all_ok = False

    # 파이프라인 모듈
    print("\n[파이프라인 모듈]")
    try:
        from peraturan.src.ocr.database import OCRPipelineDB
        print("  ✅ database")
    except Exception as e:
        print(f"  ❌ database: {e}")
        all_ok = False

    try:
        from peraturan.src.ocr.quality_scorer import QualityScorer
        print("  ✅ quality_scorer")
    except Exception as e:
        print(f"  ❌ quality_scorer: {e}")
        all_ok = False

    try:
        from peraturan.src.ocr.pipeline import OCRPipeline
        print("  ✅ pipeline")
    except Exception as e:
        print(f"  ❌ pipeline: {e}")
        all_ok = False

    # 결과
    print("\n" + "=" * 50)
    if all_ok:
        print("✅ 모든 검증 통과!")
    else:
        print("❌ 일부 검증 실패")
    print("=" * 50)

    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
