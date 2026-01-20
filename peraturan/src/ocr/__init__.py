"""
ILIS OCR Pipeline - Indonesian Legal Document OCR to Akoma Ntoso.

인도네시아 법령 PDF → Akoma Ntoso XML 변환 파이프라인

모듈 구성:
- quality_scorer: 페이지 품질 점수 계산
- indonesian_dict: 인도네시아어 사전 및 형태소 분석
- ensemble_ocr: PaddleOCR + Tesseract 앙상블
- pipeline: 메인 파이프라인
"""

__version__ = "0.2.0"

from .quality_scorer import QualityScorer, QualityResult, ProcessingStrategy
from .indonesian_dict import IndonesianDictionary
from .ensemble_ocr import EnsembleOCR, OCRResult, OCREngine
from .pipeline import OCRPipeline, DocumentResult, PageResult

__all__ = [
    # Quality Scorer
    "QualityScorer",
    "QualityResult",
    "ProcessingStrategy",
    # Indonesian Dictionary
    "IndonesianDictionary",
    # Ensemble OCR
    "EnsembleOCR",
    "OCRResult",
    "OCREngine",
    # Pipeline
    "OCRPipeline",
    "DocumentResult",
    "PageResult",
]
