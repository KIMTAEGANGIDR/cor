"""
ILIS LLM OCR Corrector

LLM을 사용한 OCR 오류 교정
- OpenRouter API (Claude Sonnet 4.5) 사용
- 규칙 기반 교정 실패 시 LLM으로 교정 시도
- 인도네시아 법률 문서 특화 프롬프트

사용법:
    from peraturan.src.ocr.llm_corrector import LLMCorrector

    corrector = LLMCorrector()  # OPENROUTER_API_KEY 환경변수 필요
    result = corrector.correct(text, issues)
"""

import os
import re
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict

# .env 파일 로드
try:
    from dotenv import load_dotenv
    # 프로젝트 루트의 .env 파일 로드
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

logger = logging.getLogger(__name__)

# OpenRouter 설정
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4"  # Claude Sonnet 4.5


@dataclass
class LLMCorrectionResult:
    """LLM 교정 결과"""
    text: str                           # 교정된 텍스트
    confidence: float                   # LLM 확신도 (0-1)
    corrections: List[Dict] = field(default_factory=list)  # 교정 내역
    explanation: str = ""               # LLM 설명
    error: Optional[str] = None         # 오류 메시지


class LLMCorrector:
    """LLM 기반 OCR 교정기"""

    # 시스템 프롬프트
    SYSTEM_PROMPT = """You are an expert in Indonesian legal documents (Peraturan Perundang-undangan).
Your task is to correct OCR errors in Indonesian legal text while preserving the original meaning.

Common OCR errors in Indonesian legal documents:
1. Character confusion: 0↔O, 1↔I↔l, 5↔S, 7↔T, rn↔m, cl↔d
2. Missing/extra spaces
3. Hyphen breaks across lines
4. Legal term misspellings: PRESIDEN, REPUBLIK, INDONESIA, PERATURAN, UNDANG-UNDANG, PASAL, AYAT

Important Indonesian legal terms to recognize:
- Document types: Undang-Undang, Peraturan Pemerintah, Perpres, Permen, Keputusan
- Structure: Pasal, Ayat, Huruf, Angka, Bab, Bagian
- Actions: Menimbang, Mengingat, Memutuskan, Menetapkan
- Entities: Presiden, Menteri, Pemerintah, Republik Indonesia

Rules:
1. Only correct obvious OCR errors, don't change content meaning
2. Preserve original formatting (paragraphs, line breaks)
3. Be conservative - if unsure, keep original text
4. Report confidence level honestly"""

    # 교정 요청 프롬프트 템플릿
    CORRECTION_PROMPT = """Please correct OCR errors in the following Indonesian legal document text.

Known issues detected by automated validation:
{issues}

Text to correct:
---
{text}
---

Respond in JSON format:
{{
    "corrected_text": "the corrected text",
    "confidence": 0.95,  // your confidence in corrections (0-1)
    "corrections": [
        {{"original": "error text", "corrected": "fixed text", "reason": "brief explanation"}}
    ],
    "explanation": "brief overall explanation of corrections made"
}}

If no corrections needed, return the original text with confidence 1.0 and empty corrections list."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
        temperature: float = 0.1,  # 낮은 temperature로 일관성 유지
    ):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

        if not self.api_key:
            logger.warning("OPENROUTER_API_KEY not set. LLM correction will fail.")

        # OpenAI 클라이언트 초기화
        self._client = None

    def _get_client(self):
        """OpenAI 클라이언트 (lazy initialization)"""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=OPENROUTER_BASE_URL,
                )
            except ImportError:
                raise ImportError("openai package required. Install: pip install openai")
        return self._client

    def correct(
        self,
        text: str,
        issues: List[str],
        max_text_length: int = 8000,
    ) -> Optional[Dict]:
        """
        LLM으로 OCR 오류 교정

        Args:
            text: OCR 추출 텍스트
            issues: 품질 검증에서 발견된 문제점 리스트
            max_text_length: 최대 텍스트 길이 (토큰 제한)

        Returns:
            {
                "text": "교정된 텍스트",
                "confidence": 0.95,
                "corrections": [...],
                "explanation": "..."
            }
            또는 None (오류 시)
        """
        if not self.api_key:
            logger.error("OPENROUTER_API_KEY not set")
            return None

        # 텍스트가 너무 길면 청크로 분할
        if len(text) > max_text_length:
            return self._correct_chunked(text, issues, max_text_length)

        try:
            client = self._get_client()

            # 프롬프트 구성
            issues_text = "\n".join(f"- {issue}" for issue in issues) if issues else "- No specific issues detected"
            user_prompt = self.CORRECTION_PROMPT.format(
                issues=issues_text,
                text=text,
            )

            # API 호출
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                extra_headers={
                    "HTTP-Referer": "https://github.com/ilis-project",
                    "X-Title": "ILIS OCR Corrector",
                },
            )

            # 응답 파싱
            response_text = response.choices[0].message.content
            result = self._parse_response(response_text, text)

            if result:
                logger.info(
                    f"LLM 교정 완료: {len(result.get('corrections', []))}개 수정, "
                    f"확신도: {result.get('confidence', 0):.0%}"
                )

            return result

        except Exception as e:
            logger.error(f"LLM 교정 오류: {e}")
            return None

    def _correct_chunked(
        self,
        text: str,
        issues: List[str],
        chunk_size: int,
    ) -> Optional[Dict]:
        """긴 텍스트를 청크로 분할하여 교정"""
        # 문단 단위로 분할
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = []
        current_length = 0

        for para in paragraphs:
            if current_length + len(para) > chunk_size and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [para]
                current_length = len(para)
            else:
                current_chunk.append(para)
                current_length += len(para)

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        # 각 청크 교정
        all_corrections = []
        corrected_chunks = []
        total_confidence = 0.0

        for i, chunk in enumerate(chunks):
            logger.info(f"청크 {i+1}/{len(chunks)} 교정 중...")
            result = self.correct(chunk, issues, chunk_size)

            if result:
                corrected_chunks.append(result.get("text", chunk))
                all_corrections.extend(result.get("corrections", []))
                total_confidence += result.get("confidence", 0.5)
            else:
                corrected_chunks.append(chunk)
                total_confidence += 0.5

        avg_confidence = total_confidence / len(chunks) if chunks else 0.0

        return {
            "text": "\n\n".join(corrected_chunks),
            "confidence": avg_confidence,
            "corrections": all_corrections,
            "explanation": f"Processed {len(chunks)} chunks",
        }

    def _parse_response(self, response_text: str, original_text: str) -> Optional[Dict]:
        """LLM 응답 파싱"""
        try:
            # JSON 블록 추출
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if not json_match:
                logger.warning("LLM 응답에서 JSON을 찾을 수 없음")
                return None

            data = json.loads(json_match.group())

            # 필수 필드 확인
            corrected_text = data.get("corrected_text", original_text)
            confidence = float(data.get("confidence", 0.5))
            corrections = data.get("corrections", [])
            explanation = data.get("explanation", "")

            # 신뢰도 범위 검증
            confidence = max(0.0, min(1.0, confidence))

            return {
                "text": corrected_text,
                "confidence": confidence,
                "corrections": corrections,
                "explanation": explanation,
            }

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 파싱 오류: {e}")
            return None
        except Exception as e:
            logger.warning(f"응답 파싱 오류: {e}")
            return None

    def test_connection(self) -> bool:
        """API 연결 테스트"""
        if not self.api_key:
            print("❌ OPENROUTER_API_KEY not set")
            return False

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Say 'OK' if you can read this."}],
                max_tokens=10,
            )
            result = response.choices[0].message.content
            print(f"✅ API 연결 성공: {result}")
            return True
        except Exception as e:
            print(f"❌ API 연결 실패: {e}")
            return False


def main():
    """테스트"""
    import argparse

    parser = argparse.ArgumentParser(description="LLM OCR Corrector")
    parser.add_argument("--test", action="store_true", help="Test API connection")
    parser.add_argument("--correct", type=str, help="Text to correct")
    args = parser.parse_args()

    corrector = LLMCorrector()

    if args.test:
        corrector.test_connection()

    elif args.correct:
        result = corrector.correct(args.correct, ["OCR quality below 90%"])
        if result:
            print(f"\n교정된 텍스트:\n{result['text']}")
            print(f"\n확신도: {result['confidence']:.0%}")
            print(f"\n교정 내역:")
            for c in result.get("corrections", []):
                print(f"  - {c}")
        else:
            print("교정 실패")

    else:
        # 기본 테스트
        test_text = """
        PRES1DEN REPUB1IK 1NDONESIA
        PERATURAN PEMER1NTAH N0MOR 10 7AHUN 2024
        7ENTANG
        PERUBAHAN ATAS UNDANG-UNDANG
        """

        print("=" * 60)
        print("LLM OCR 교정기 테스트")
        print("=" * 60)
        print(f"\n원본 텍스트:\n{test_text}")

        result = corrector.correct(test_text, [
            "낮은 OCR 신뢰도",
            "단어 내 숫자 혼동 감지",
        ])

        if result:
            print(f"\n교정된 텍스트:\n{result['text']}")
            print(f"\n확신도: {result['confidence']:.0%}")
            print(f"\n교정 내역:")
            for c in result.get("corrections", []):
                print(f"  - {c}")
        else:
            print("\n교정 실패 (API 키 확인 필요)")


if __name__ == "__main__":
    main()
