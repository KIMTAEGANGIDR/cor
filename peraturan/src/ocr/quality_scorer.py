"""
품질 점수 계산기 (Quality Scorer)

페이지별 텍스트 품질을 0.0~1.0 점수로 계산하여 처리 전략 결정

점수 구성 (가중치):
- alpha_ratio (0.2): 알파벳 비율
- broken_ratio (0.3): 깨진 문자 비율 (역수)
- word_ratio (0.3): 인도네시아어 단어 인식률
- legal_ratio (0.2): 법률 용어 매칭률

임계치:
- high (0.75~0.85): 내장 텍스트 사용
- low (0.45~0.55): OCR 필수
- 중간: OCR 비교 후 선택
"""

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .indonesian_dict import IndonesianDictionary


class ProcessingStrategy(Enum):
    """페이지 처리 전략"""
    TEXT = "text"       # 내장 텍스트 사용
    HYBRID = "hybrid"   # OCR 비교 후 선택
    OCR = "ocr"         # OCR 필수
    SKIP = "skip"       # 처리 불가 (빈 페이지 등)


@dataclass
class QualityScores:
    """개별 품질 점수"""
    alpha_ratio: float = 0.0      # 알파벳 비율 (0~1)
    broken_ratio: float = 0.0     # 깨진 문자 비율 (0~1, 낮을수록 좋음)
    word_ratio: float = 0.0       # 단어 인식률 (0~1)
    legal_ratio: float = 0.0      # 법률 용어 매칭률 (0~1)

    # 메타 정보
    char_count: int = 0           # 총 문자 수
    word_count: int = 0           # 총 단어 수
    broken_chars: list = field(default_factory=list)  # 발견된 깨진 문자


@dataclass
class QualityResult:
    """품질 평가 결과"""
    score: float                  # 종합 점수 (0~1)
    scores: QualityScores         # 개별 점수
    strategy: ProcessingStrategy  # 처리 전략
    confidence: str               # "high", "medium", "low"

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "strategy": self.strategy.value,
            "confidence": self.confidence,
            "details": {
                "alpha_ratio": round(self.scores.alpha_ratio, 4),
                "broken_ratio": round(self.scores.broken_ratio, 4),
                "word_ratio": round(self.scores.word_ratio, 4),
                "legal_ratio": round(self.scores.legal_ratio, 4),
                "char_count": self.scores.char_count,
                "word_count": self.scores.word_count,
            }
        }


class QualityScorer:
    """
    페이지 텍스트 품질 점수 계산기

    사용법:
        scorer = QualityScorer()
        result = scorer.score_text("페이지 텍스트...")
        print(result.score, result.strategy)
    """

    # 가중치 (계획서 기준)
    WEIGHTS = {
        "alpha": 0.2,
        "broken": 0.3,
        "word": 0.3,
        "legal": 0.2,
    }

    # 임계치 (파일럿에서 ROC 분석으로 조정)
    THRESHOLD_HIGH = 0.75  # 초기값, 파일럿 후 0.75~0.85 범위에서 확정
    THRESHOLD_LOW = 0.45   # 초기값, 파일럿 후 0.45~0.55 범위에서 확정

    # 깨진 문자 패턴 (NBSP, BULLET 제외 - 정상 문서에도 흔함)
    BROKEN_CHARS = set([
        '\ufffd',  # REPLACEMENT CHARACTER (�)
        '\u25a0',  # BLACK SQUARE (■)
        '\u25a1',  # WHITE SQUARE (□)
        '\u25aa',  # BLACK SMALL SQUARE
        '\u25ab',  # WHITE SMALL SQUARE
        # '\u2022',  # BULLET - 정상 리스트에 사용, 제외
        # '\u00a0',  # NBSP - 정상 포매팅에 사용, 제외
    ])

    # 깨진 문자 정규식 (연속된 특수문자만 - 단일 문자는 무시)
    BROKEN_PATTERN = re.compile(
        r'[\ufffd\u25a0\u25a1\u25aa\u25ab]{2,}|'  # 연속 깨진 문자
        r'\?{3,}|'  # 연속 물음표 (3개 이상)
        r'_{5,}'    # 연속 밑줄 (5개 이상)
    )

    # 짧은 페이지 임계치 (제목/서명 페이지 보존)
    MIN_CHAR_THRESHOLD = 20  # 50 → 20으로 낮춤

    def __init__(
        self,
        dictionary: Optional[IndonesianDictionary] = None,
        threshold_high: float = None,
        threshold_low: float = None,
    ):
        """
        Args:
            dictionary: 인도네시아어 사전 (없으면 자동 생성)
            threshold_high: 상위 임계치 (기본 0.80)
            threshold_low: 하위 임계치 (기본 0.50)
        """
        self.dictionary = dictionary or IndonesianDictionary()
        self.threshold_high = threshold_high or self.THRESHOLD_HIGH
        self.threshold_low = threshold_low or self.THRESHOLD_LOW

    def score_text(self, text: str) -> QualityResult:
        """
        텍스트 품질 점수 계산

        Args:
            text: 페이지에서 추출한 텍스트

        Returns:
            QualityResult: 품질 평가 결과
        """
        # 빈 텍스트 처리 → OCR 필요 (스캔본일 가능성)
        # Critical Fix: SKIP 대신 OCR 전략 반환
        if not text or not text.strip():
            return QualityResult(
                score=0.0,
                scores=QualityScores(),
                strategy=ProcessingStrategy.OCR,  # SKIP → OCR
                confidence="low",
            )

        # 개별 점수 계산
        scores = self._calculate_scores(text)

        # 가중 평균 계산
        # broken_ratio는 역수 (낮을수록 좋음 → 1 - broken_ratio)
        weighted_score = (
            self.WEIGHTS["alpha"] * scores.alpha_ratio +
            self.WEIGHTS["broken"] * (1 - scores.broken_ratio) +
            self.WEIGHTS["word"] * scores.word_ratio +
            self.WEIGHTS["legal"] * scores.legal_ratio
        )

        # 전략 결정
        strategy = self._determine_strategy(weighted_score, scores)

        # 신뢰도 결정
        confidence = self._determine_confidence(weighted_score)

        return QualityResult(
            score=weighted_score,
            scores=scores,
            strategy=strategy,
            confidence=confidence,
        )

    def _calculate_scores(self, text: str) -> QualityScores:
        """개별 점수 계산"""
        scores = QualityScores()

        # 문자 수
        scores.char_count = len(text)
        if scores.char_count == 0:
            return scores

        # 1. 알파벳 비율
        alpha_count = sum(1 for c in text if c.isalpha())
        scores.alpha_ratio = alpha_count / scores.char_count

        # 2. 깨진 문자 비율 (정규식 매칭만 사용 - 이중 카운팅 방지)
        broken_chars = []
        broken_count = 0

        # 연속 패턴만 검출 (단일 문자는 무시)
        broken_matches = self.BROKEN_PATTERN.findall(text)
        broken_count = sum(len(m) for m in broken_matches)

        # 발견된 깨진 문자 종류 기록
        for c in text:
            if c in self.BROKEN_CHARS and c not in broken_chars:
                broken_chars.append(c)

        scores.broken_ratio = min(broken_count / scores.char_count, 1.0)
        scores.broken_chars = broken_chars

        # 3. 단어 인식률 (인도네시아어 사전 기반)
        words = self._extract_words(text)
        scores.word_count = len(words)

        if scores.word_count > 0:
            recognized = sum(
                1 for w in words
                if self.dictionary.is_valid_word(w)
            )
            scores.word_ratio = recognized / scores.word_count

        # 4. 법률 용어 매칭률
        if scores.word_count > 0:
            legal_matches = sum(
                1 for w in words
                if self.dictionary.is_legal_term(w)
            )
            # 법률 용어는 전체의 일부이므로 정규화
            # 일반적으로 법률 문서의 5~15%가 법률 용어
            scores.legal_ratio = min(legal_matches / scores.word_count * 10, 1.0)

        return scores

    def _extract_words(self, text: str) -> list[str]:
        """텍스트에서 단어 추출"""
        # 하이픈을 공백으로 대체하여 분리된 단어로 처리
        # 예: "undang-undang" → "undang undang"
        normalized = re.sub(r'-', ' ', text.lower())
        # Unicode 문자 포함 단어 추출 (악센트 포함)
        words = re.findall(r'\b[a-zA-Z\u00C0-\u024F]{2,}\b', normalized)
        return words

    def _determine_strategy(
        self,
        score: float,
        scores: QualityScores
    ) -> ProcessingStrategy:
        """처리 전략 결정"""
        # 짧은 텍스트 페이지 처리 (제목/서명 페이지 vs 스캔본 오버레이)
        # - 거의 빈 페이지 (< 5자) → OCR 시도
        # - 짧은 텍스트 (5-19자) → 품질 점수 기반 판단
        #   - 고품질 (alpha_ratio 높음) → TEXT (진짜 제목/서명)
        #   - 저품질 (깨진 문자 많음) → HYBRID (스캔본 오버레이일 수 있음)
        if scores.char_count < self.MIN_CHAR_THRESHOLD:
            if scores.char_count < 5:
                # 거의 빈 페이지 → OCR 시도 (스캔본일 수 있음)
                return ProcessingStrategy.OCR
            else:
                # 짧은 텍스트: 품질 점수로 판단
                # alpha_ratio > 0.7 이고 broken_ratio < 0.1 이면 깨끗한 텍스트
                if scores.alpha_ratio > 0.7 and scores.broken_ratio < 0.1:
                    return ProcessingStrategy.TEXT
                else:
                    # 품질 의심 → HYBRID (OCR과 비교)
                    return ProcessingStrategy.HYBRID

        # 점수 기반 전략
        if score >= self.threshold_high:
            return ProcessingStrategy.TEXT
        elif score >= self.threshold_low:
            return ProcessingStrategy.HYBRID
        else:
            return ProcessingStrategy.OCR

    def _determine_confidence(self, score: float) -> str:
        """신뢰도 결정"""
        if score >= 0.85:
            return "high"
        elif score >= 0.60:
            return "medium"
        else:
            return "low"

    def calibrate_thresholds(
        self,
        samples: list[tuple[str, bool]]
    ) -> tuple[float, float]:
        """
        파일럿 샘플로 임계치 캘리브레이션 (ROC 분석)

        Args:
            samples: [(텍스트, 품질양호여부), ...] 리스트

        Returns:
            (threshold_high, threshold_low) 튜플
        """
        # 점수 계산
        results = []
        for text, is_good in samples:
            result = self.score_text(text)
            results.append((result.score, is_good))

        # 정렬
        results.sort(key=lambda x: x[0], reverse=True)

        # 간단한 임계치 찾기 (실제로는 ROC 분석 필요)
        good_scores = [s for s, g in results if g]
        bad_scores = [s for s, g in results if not g]

        if good_scores and bad_scores:
            # 상위 임계치: 좋은 샘플의 하위 10%
            threshold_high = sorted(good_scores)[int(len(good_scores) * 0.1)]
            # 하위 임계치: 나쁜 샘플의 상위 10%
            threshold_low = sorted(bad_scores, reverse=True)[int(len(bad_scores) * 0.1)]

            self.threshold_high = max(threshold_high, 0.75)
            self.threshold_low = min(threshold_low, 0.55)

        return (self.threshold_high, self.threshold_low)


# CLI 테스트용
if __name__ == "__main__":
    import sys

    scorer = QualityScorer()

    # 테스트 텍스트
    test_texts = [
        # 좋은 품질
        """
        UNDANG-UNDANG REPUBLIK INDONESIA
        NOMOR 12 TAHUN 2011
        TENTANG PEMBENTUKAN PERATURAN PERUNDANG-UNDANGAN

        Pasal 1
        Dalam Undang-Undang ini yang dimaksud dengan:
        1. Pembentukan Peraturan Perundang-undangan adalah pembuatan
           Peraturan Perundang-undangan yang mencakup tahapan perencanaan.
        """,

        # 나쁜 품질 (깨진 문자)
        """
        ■■■□□□ ????????????
        Pas▯▯ 1 ___________
        D▯l▯m Und▯ng-Und▯ng ▯n▯
        """,

        # 빈 텍스트
        "",
    ]

    for i, text in enumerate(test_texts):
        result = scorer.score_text(text)
        print(f"\n=== 테스트 {i+1} ===")
        print(f"점수: {result.score:.4f}")
        print(f"전략: {result.strategy.value}")
        print(f"신뢰도: {result.confidence}")
        print(f"상세: {result.to_dict()['details']}")
