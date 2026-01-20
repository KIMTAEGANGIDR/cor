"""
OCR 앙상블 (Ensemble OCR)

PaddleOCR + Tesseract 조합으로 최적 결과 선택

판단 로직 (계획서 기준):
- PaddleOCR 신뢰도 >= 0.85: 단독 채택
- 0.70 <= 신뢰도 < 0.85: Tesseract 병행, 품질 점수로 선택
- 신뢰도 < 0.70: 앙상블 실패 시 수동 검토 큐

선택 규칙:
- 품질 점수로 비교
- 유사도 0.9 이상이면 빠른 결과 (PaddleOCR) 우선
- 불일치/저신뢰는 수동 큐 이동
"""

import os
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Optional, List, Tuple

import numpy as np
from PIL import Image

# OCR 엔진 import (조건부)
try:
    from paddleocr import PaddleOCR
    PADDLE_AVAILABLE = True
except ImportError:
    PADDLE_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

from .quality_scorer import QualityScorer, QualityResult


class OCREngine(Enum):
    """OCR 엔진 종류"""
    PADDLE = "paddle"
    TESSERACT = "tesseract"
    ENSEMBLE = "ensemble"
    MANUAL = "manual"  # 수동 검토 필요


@dataclass
class OCRResult:
    """OCR 결과"""
    text: str                      # 추출된 텍스트
    confidence: float              # 신뢰도 (0~1)
    engine: OCREngine              # 사용된 엔진
    quality: Optional[QualityResult] = None  # 품질 점수
    boxes: Optional[List] = None   # 텍스트 박스 좌표 (선택)

    def to_dict(self) -> dict:
        return {
            "text_length": len(self.text),
            "confidence": round(self.confidence, 4),
            "engine": self.engine.value,
            "quality_score": self.quality.score if self.quality else None,
        }


class EnsembleOCR:
    """
    OCR 앙상블 엔진

    사용법:
        ocr = EnsembleOCR()
        result = ocr.process_image(image_path)
        print(result.text, result.engine)
    """

    # 신뢰도 임계치
    CONFIDENCE_HIGH = 0.85    # 단독 채택
    CONFIDENCE_MID = 0.70     # 앙상블
    CONFIDENCE_LOW = 0.50     # 수동 검토

    # 유사도 임계치
    SIMILARITY_THRESHOLD = 0.90

    # 지원 언어 (고정 - 인도네시아 법률 문서 전용)
    SUPPORTED_LANG = "id"  # PaddleOCR: 'id', Tesseract: 'ind+eng'

    def __init__(
        self,
        use_gpu: bool = True,
        quality_scorer: Optional[QualityScorer] = None,
    ):
        """
        Args:
            use_gpu: GPU 사용 여부
            quality_scorer: 품질 점수 계산기

        Note:
            언어는 인도네시아어(id)로 고정됨. 다른 언어가 필요하면 코드 수정 필요.
        """
        self.use_gpu = use_gpu
        self.quality_scorer = quality_scorer or QualityScorer()

        # PaddleOCR 초기화
        self._paddle_ocr = None
        if PADDLE_AVAILABLE:
            try:
                self._paddle_ocr = PaddleOCR(
                    use_angle_cls=True,
                    lang='id',  # 인도네시아어 (latin 기반)
                    use_gpu=use_gpu,
                    show_log=False,
                )
            except Exception as e:
                print(f"Warning: PaddleOCR 초기화 실패: {e}")

        # Tesseract 설정
        self._tesseract_lang = "ind+eng"  # 인도네시아어 + 영어
        if not TESSERACT_AVAILABLE:
            print("Warning: Tesseract를 사용할 수 없습니다")

    def process_image(
        self,
        image: Image.Image | str | Path | np.ndarray,
        force_engine: Optional[OCREngine] = None,
    ) -> OCRResult:
        """
        이미지 OCR 처리

        Args:
            image: PIL Image, 파일 경로, 또는 numpy 배열
            force_engine: 특정 엔진 강제 사용 (테스트용)

        Returns:
            OCRResult: OCR 결과
        """
        # 이미지 로드
        img = self._load_image(image)
        if img is None:
            return OCRResult(
                text="",
                confidence=0.0,
                engine=OCREngine.MANUAL,
            )

        # 강제 엔진 지정
        if force_engine == OCREngine.PADDLE:
            return self._run_paddle(img)
        elif force_engine == OCREngine.TESSERACT:
            return self._run_tesseract(img)

        # 앙상블 로직
        return self._ensemble_process(img)

    def _load_image(
        self,
        image: Image.Image | str | Path | np.ndarray
    ) -> Optional[np.ndarray]:
        """이미지를 numpy 배열로 변환"""
        try:
            if isinstance(image, np.ndarray):
                return image
            elif isinstance(image, Image.Image):
                return np.array(image)
            elif isinstance(image, (str, Path)):
                # 파일 핸들 누수 방지
                with Image.open(image) as img:
                    return np.array(img)
        except Exception as e:
            print(f"이미지 로드 실패: {e}")
        return None

    def _ensemble_process(self, img: np.ndarray) -> OCRResult:
        """앙상블 OCR 처리"""
        # 1. PaddleOCR 먼저 실행
        paddle_result = self._run_paddle(img)

        # 2. 신뢰도 체크
        if paddle_result.confidence >= self.CONFIDENCE_HIGH:
            # 단독 채택
            return paddle_result

        elif paddle_result.confidence >= self.CONFIDENCE_MID:
            # Tesseract 병행
            tesseract_result = self._run_tesseract(img)

            # 품질 점수 비교
            return self._select_best_result(paddle_result, tesseract_result)

        else:
            # 저신뢰 - Tesseract도 시도
            tesseract_result = self._run_tesseract(img)

            # 둘 다 저신뢰면 수동 검토
            best = self._select_best_result(paddle_result, tesseract_result)

            if best.confidence < self.CONFIDENCE_LOW:
                best.engine = OCREngine.MANUAL  # 수동 검토 표시
                return best

            return best

    def _run_paddle(self, img: np.ndarray) -> OCRResult:
        """PaddleOCR 실행"""
        if not self._paddle_ocr:
            return OCRResult(
                text="",
                confidence=0.0,
                engine=OCREngine.PADDLE,
            )

        try:
            result = self._paddle_ocr.ocr(img, cls=True)

            if not result or not result[0]:
                return OCRResult(
                    text="",
                    confidence=0.0,
                    engine=OCREngine.PADDLE,
                )

            # 텍스트 및 신뢰도 추출
            texts = []
            confidences = []
            boxes = []

            for line in result[0]:
                if line and len(line) >= 2:
                    box, (text, conf) = line[0], line[1]
                    texts.append(text)
                    confidences.append(conf)
                    boxes.append(box)

            full_text = "\n".join(texts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0

            # 품질 점수 계산
            quality = self.quality_scorer.score_text(full_text)

            return OCRResult(
                text=full_text,
                confidence=avg_confidence,
                engine=OCREngine.PADDLE,
                quality=quality,
                boxes=boxes,
            )

        except Exception as e:
            print(f"PaddleOCR 오류: {e}")
            return OCRResult(
                text="",
                confidence=0.0,
                engine=OCREngine.PADDLE,
            )

    def _run_tesseract(self, img: np.ndarray) -> OCRResult:
        """Tesseract OCR 실행"""
        if not TESSERACT_AVAILABLE:
            return OCRResult(
                text="",
                confidence=0.0,
                engine=OCREngine.TESSERACT,
            )

        try:
            # numpy → PIL
            pil_img = Image.fromarray(img)

            # OCR 실행
            text = pytesseract.image_to_string(
                pil_img,
                lang=self._tesseract_lang,
            )

            # 신뢰도 (상세 데이터에서 추출)
            data = pytesseract.image_to_data(
                pil_img,
                lang=self._tesseract_lang,
                output_type=pytesseract.Output.DICT,
            )

            # 신뢰도 파싱 (float 처리)
            confidences = []
            for c in data['conf']:
                try:
                    val = float(c)
                    if val >= 0:  # -1은 무효값
                        confidences.append(val)
                except (ValueError, TypeError):
                    continue

            avg_confidence = (
                sum(confidences) / len(confidences) / 100
                if confidences else 0.0
            )

            # 품질 점수 계산
            quality = self.quality_scorer.score_text(text)

            return OCRResult(
                text=text.strip(),
                confidence=avg_confidence,
                engine=OCREngine.TESSERACT,
                quality=quality,
            )

        except Exception as e:
            print(f"Tesseract 오류: {e}")
            return OCRResult(
                text="",
                confidence=0.0,
                engine=OCREngine.TESSERACT,
            )

    def _select_best_result(
        self,
        result1: OCRResult,
        result2: OCRResult
    ) -> OCRResult:
        """두 결과 중 더 나은 것 선택"""
        # 둘 중 하나가 비어있으면 다른 것 선택
        if not result1.text.strip():
            return result2
        if not result2.text.strip():
            return result1

        # 유사도 계산
        similarity = self._text_similarity(result1.text, result2.text)

        # 유사도 높으면 빠른 결과 (PaddleOCR) 선택
        if similarity >= self.SIMILARITY_THRESHOLD:
            if result1.engine == OCREngine.PADDLE:
                result1.engine = OCREngine.ENSEMBLE  # 앙상블로 표시
                return result1
            else:
                result2.engine = OCREngine.ENSEMBLE
                return result2

        # 품질 점수로 비교
        score1 = result1.quality.score if result1.quality else 0
        score2 = result2.quality.score if result2.quality else 0

        if score1 >= score2:
            result1.engine = OCREngine.ENSEMBLE
            return result1
        else:
            result2.engine = OCREngine.ENSEMBLE
            return result2

    def _text_similarity(self, text1: str, text2: str) -> float:
        """텍스트 유사도 계산 (0~1)"""
        if not text1 or not text2:
            return 0.0
        return SequenceMatcher(None, text1, text2).ratio()

    def check_engines(self) -> dict:
        """사용 가능한 엔진 확인"""
        return {
            "paddle": PADDLE_AVAILABLE and self._paddle_ocr is not None,
            "tesseract": TESSERACT_AVAILABLE,
            "gpu": self.use_gpu and PADDLE_AVAILABLE,
        }


# CLI 테스트용
if __name__ == "__main__":
    import sys

    print("=== OCR 앙상블 테스트 ===\n")

    ocr = EnsembleOCR(use_gpu=False)

    # 엔진 상태 확인
    engines = ocr.check_engines()
    print("사용 가능한 엔진:")
    for name, available in engines.items():
        status = "✓" if available else "✗"
        print(f"  {name}: {status}")

    # 이미지 테스트 (인자로 받은 경우)
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        print(f"\n이미지 처리: {image_path}")

        result = ocr.process_image(image_path)
        print(f"\n결과:")
        print(f"  엔진: {result.engine.value}")
        print(f"  신뢰도: {result.confidence:.4f}")
        print(f"  텍스트 길이: {len(result.text)}")
        if result.quality:
            print(f"  품질 점수: {result.quality.score:.4f}")
        print(f"\n텍스트 (처음 500자):\n{result.text[:500]}")
