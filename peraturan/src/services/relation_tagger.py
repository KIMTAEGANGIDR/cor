"""
관계 표현 태거 (Relation Tagger)

법령 본문에서 관계 표현을 검출하고 태깅 (해석 없음)

⚠️ 중요 원칙:
- **추출이 아닌 태깅**: 표현이 검출되었음을 기록할 뿐, 법적 해석 없음
- 데이터 소스: peraturan.go.id (법제처) 단독 사용
- BPK 데이터 병합 금지

태깅 유형:
- MENCABUT_DETECTED: 폐지 표현 검출됨
- MENGUBAH_DETECTED: 개정 표현 검출됨
- CONDITIONAL_EXPR_DETECTED: 조건부 표현 검출됨
- TRANSITIONAL_DETECTED: 경과규정 표현 검출됨
- MERUJUK_DETECTED: 참조 표현 검출됨
"""

import re
from dataclasses import dataclass
from typing import Optional
from enum import Enum

try:
    from src.utils.logging import get_logger
    logger = get_logger("relation_tagger")
except ImportError:
    import logging
    logger = logging.getLogger("relation_tagger")


class ExpressionType(Enum):
    """검출된 표현 유형 (해석 아님, 태깅만)"""
    MENCABUT_DETECTED = "mencabut_detected"           # 폐지 표현 검출됨
    MENGUBAH_DETECTED = "mengubah_detected"           # 개정 표현 검출됨
    MERUJUK_DETECTED = "merujuk_detected"             # 참조 표현 검출됨
    CONDITIONAL_EXPR_DETECTED = "conditional_expr_detected"  # 조건부 표현 검출됨
    TRANSITIONAL_DETECTED = "transitional_detected"   # 경과규정 표현 검출됨
    IMPLEMENTASI_DETECTED = "implementasi_detected"   # 시행 표현 검출됨


@dataclass
class TaggedExpression:
    """태깅된 관계 표현 (해석 아님)"""
    expression_type: ExpressionType
    target_ref: str                     # 검출된 참조 텍스트 (예: "UU No. 5 Tahun 2020")
    target_slug: Optional[str]          # 매핑 가능한 경우 slug
    target_jenis: Optional[str]         # 법령 유형
    target_nomor: Optional[str]         # 법령 번호
    target_tahun: Optional[int]         # 법령 연도
    raw_text: str                       # 원문 발췌
    pasal_context: Optional[str] = None # 검출 위치 (Pasal X)
    kondisi_text: Optional[str] = None  # 조건부 표현 원문 (있는 경우)
    confidence: float = 0.9             # 패턴 매칭 신뢰도

    def to_dict(self) -> dict:
        return {
            "expression_type": self.expression_type.value,
            "target_ref": self.target_ref,
            "target_slug": self.target_slug,
            "target_jenis": self.target_jenis,
            "target_nomor": self.target_nomor,
            "target_tahun": self.target_tahun,
            "raw_text": self.raw_text,
            "pasal_context": self.pasal_context,
            "kondisi_text": self.kondisi_text,
            "confidence": round(self.confidence, 4),
        }


# Regex patterns for legal references
JENIS_PATTERNS = {
    "UU": r"(?:Undang[- ]?Undang|UU)",
    "PERPPU": r"(?:Peraturan Pemerintah Pengganti Undang[- ]?Undang|Perppu|PERPPU)",
    "PP": r"(?:Peraturan Pemerintah|PP)",
    "PERPRES": r"(?:Peraturan Presiden|Perpres|PERPRES)",
    "PERMEN": r"(?:Peraturan Menteri|Permen|PERMEN)",
}

# Combined pattern for any legal document type
JENIS_COMBINED = "|".join(f"({p})" for p in JENIS_PATTERNS.values())

# Pattern to match legal document references like "UU No. 4 Tahun 2009"
PERATURAN_REF_PATTERN = re.compile(
    rf"({JENIS_COMBINED})\s*"
    r"(?:Nomor|No\.?)\s*(\d+)\s*"
    r"(?:Tahun)\s*(\d{4})",
    re.IGNORECASE
)

# Patterns for expression detection (태깅용)
EXPRESSION_PATTERNS = {
    # Revocation expressions (폐지 표현)
    "mencabut": [
        re.compile(r"(?:dicabut|mencabut)\s+(?:dan\s+)?(?:dinyatakan\s+)?(?:tidak\s+berlaku)", re.I),
        re.compile(r"dinyatakan\s+tidak\s+berlaku", re.I),
        re.compile(r"(?:dengan\s+berlakunya|sejak\s+berlakunya).*?dicabut", re.I),
    ],

    # Amendment expressions (개정 표현)
    "mengubah": [
        re.compile(r"(?:perubahan\s+(?:atas|terhadap|kedua|ketiga|keempat|kelima))", re.I),
        re.compile(r"mengubah\s+(?:ketentuan|beberapa\s+ketentuan)", re.I),
        re.compile(r"diubah\s+dengan", re.I),
    ],

    # Conditional expressions (조건부 표현)
    "bersyarat": [
        re.compile(r"sepanjang\s+tidak\s+bertentangan", re.I),
        re.compile(r"tetap\s+berlaku\s+sepanjang", re.I),
        re.compile(r"dinyatakan\s+(?:masih\s+)?tetap\s+berlaku\s+sepanjang", re.I),
    ],

    # Transitional expressions (경과규정 표현)
    "peralihan": [
        re.compile(r"tetap\s+berlaku\s+sampai\s+(?:dengan\s+)?(?:ditetapkannya|diundangkannya)", re.I),
        re.compile(r"berlaku\s+sampai\s+dengan\s+(?:ditetapkan|diundangkan)", re.I),
        re.compile(r"masih\s+tetap\s+berlaku\s+sampai", re.I),
    ],

    # Reference expressions (참조 표현)
    "merujuk": [
        re.compile(r"sebagaimana\s+(?:dimaksud|diatur)\s+(?:dalam|oleh)", re.I),
        re.compile(r"berdasarkan\s+(?:ketentuan|pasal)", re.I),
        re.compile(r"sesuai\s+(?:dengan\s+)?(?:ketentuan|pasal)", re.I),
    ],

    # Implementation expressions (시행 표현)
    "implementasi": [
        re.compile(r"(?:untuk\s+)?melaksanakan\s+(?:ketentuan|pasal)", re.I),
        re.compile(r"sebagai\s+pelaksanaan\s+(?:dari|atas)", re.I),
        re.compile(r"peraturan\s+pelaksanaan\s+(?:dari|atas)", re.I),
    ],
}

# Pattern to extract article references
PASAL_PATTERN = re.compile(r"Pasal\s+(\d+)(?:\s+ayat\s+\((\d+)\))?", re.I)


class RelationTagger:
    """
    관계 표현 태거

    본문에서 관계 표현을 검출하고 태깅 (해석 없음)

    사용법:
        tagger = RelationTagger()
        tags = tagger.tag_from_text(text, source_slug)
    """

    def __init__(self):
        """Initialize the tagger."""
        pass

    def tag_from_title(self, tentang: str) -> list[TaggedExpression]:
        """
        제목에서 관계 표현 태깅

        Args:
            tentang: 문서 제목 (tentang 필드)

        Returns:
            태깅된 표현 목록
        """
        tags = []

        # Check for amendment in title
        if re.search(r"perubahan\s+(?:atas|kedua|ketiga|keempat|kelima)", tentang, re.I):
            # Find referenced peraturan
            match = PERATURAN_REF_PATTERN.search(tentang)
            if match:
                tag = self._create_tag_from_match(
                    match,
                    ExpressionType.MENGUBAH_DETECTED,
                    raw_text=tentang[:200],
                )
                if tag:
                    tags.append(tag)
                    logger.debug(f"Tagged amendment expression: {tentang[:50]}...")

        return tags

    def tag_from_text(self, text: str, source_slug: Optional[str] = None) -> list[TaggedExpression]:
        """
        본문에서 관계 표현 태깅

        Args:
            text: 문서 본문
            source_slug: 원본 법령 slug (참고용)

        Returns:
            태깅된 표현 목록
        """
        if not text:
            return []

        tags = []

        # Split into sentences for context
        sentences = re.split(r'[.;]', text)

        for sentence in sentences:
            # Skip very short sentences
            if len(sentence) < 20:
                continue

            # Find peraturan references
            refs = list(PERATURAN_REF_PATTERN.finditer(sentence))
            if not refs:
                continue

            # Detect expression type from context
            expr_type = self._detect_expression_type(sentence)
            if not expr_type:
                continue

            # Extract condition text if applicable
            kondisi_text = None
            if expr_type in (ExpressionType.CONDITIONAL_EXPR_DETECTED, ExpressionType.TRANSITIONAL_DETECTED):
                kondisi_text = self._extract_condition_text(sentence)

            # Extract article reference
            pasal_match = PASAL_PATTERN.search(sentence)
            pasal_context = None
            if pasal_match:
                pasal_context = f"Pasal {pasal_match.group(1)}"
                if pasal_match.group(2):
                    pasal_context += f" ayat ({pasal_match.group(2)})"

            # Create tags for each reference
            for ref in refs:
                tag = self._create_tag_from_match(
                    ref,
                    expr_type,
                    raw_text=sentence.strip()[:300],
                    pasal_context=pasal_context,
                    kondisi_text=kondisi_text,
                )
                if tag:
                    tags.append(tag)

        return tags

    def _detect_expression_type(self, sentence: str) -> Optional[ExpressionType]:
        """
        문장에서 표현 유형 검출

        Args:
            sentence: 분석할 문장

        Returns:
            검출된 표현 유형 또는 None
        """
        # Check patterns in priority order
        for pattern in EXPRESSION_PATTERNS["mencabut"]:
            if pattern.search(sentence):
                return ExpressionType.MENCABUT_DETECTED

        for pattern in EXPRESSION_PATTERNS["mengubah"]:
            if pattern.search(sentence):
                return ExpressionType.MENGUBAH_DETECTED

        for pattern in EXPRESSION_PATTERNS["bersyarat"]:
            if pattern.search(sentence):
                return ExpressionType.CONDITIONAL_EXPR_DETECTED

        for pattern in EXPRESSION_PATTERNS["peralihan"]:
            if pattern.search(sentence):
                return ExpressionType.TRANSITIONAL_DETECTED

        for pattern in EXPRESSION_PATTERNS["implementasi"]:
            if pattern.search(sentence):
                return ExpressionType.IMPLEMENTASI_DETECTED

        for pattern in EXPRESSION_PATTERNS["merujuk"]:
            if pattern.search(sentence):
                return ExpressionType.MERUJUK_DETECTED

        return None

    def _extract_condition_text(self, sentence: str) -> Optional[str]:
        """
        조건부 표현 원문 추출

        Args:
            sentence: 조건부 표현이 포함된 문장

        Returns:
            조건부 표현 원문 또는 None
        """
        patterns = [
            re.compile(r"(sepanjang\s+tidak\s+bertentangan[^.;]*)", re.I),
            re.compile(r"(sampai\s+(?:dengan\s+)?(?:ditetapkannya|diundangkannya)[^.;]*)", re.I),
        ]

        for pattern in patterns:
            match = pattern.search(sentence)
            if match:
                return match.group(1).strip()

        return None

    def _create_tag_from_match(
        self,
        match: re.Match,
        expr_type: ExpressionType,
        raw_text: str,
        pasal_context: Optional[str] = None,
        kondisi_text: Optional[str] = None,
    ) -> Optional[TaggedExpression]:
        """
        정규식 매치에서 TaggedExpression 생성

        Args:
            match: 정규식 매치 객체
            expr_type: 표현 유형
            raw_text: 원문 발췌
            pasal_context: 검출 위치
            kondisi_text: 조건부 표현 원문

        Returns:
            TaggedExpression 또는 None
        """
        try:
            # Parse matched groups
            full_match = match.group(0)

            # Determine jenis from matched text
            jenis = self._normalize_jenis(full_match)

            # Extract nomor and tahun from specific groups
            groups = match.groups()
            nomor = None
            tahun = None
            for g in groups:
                if g and g.isdigit():
                    if len(g) == 4 and int(g) >= 1945:
                        tahun = int(g)
                    elif not nomor:
                        nomor = g

            # Generate target slug if possible
            target_slug = None
            if jenis and nomor and tahun:
                target_slug = self._generate_slug(jenis, nomor, tahun)

            return TaggedExpression(
                expression_type=expr_type,
                target_ref=full_match,
                target_slug=target_slug,
                target_jenis=jenis,
                target_nomor=nomor,
                target_tahun=tahun,
                raw_text=raw_text,
                pasal_context=pasal_context,
                kondisi_text=kondisi_text,
            )

        except Exception as e:
            logger.warning(f"Failed to create tag: {e}")
            return None

    def _normalize_jenis(self, text: str) -> Optional[str]:
        """
        법령 유형 정규화

        Args:
            text: 법령 유형이 포함된 텍스트

        Returns:
            정규화된 jenis 또는 None
        """
        text_lower = text.lower()

        if "undang-undang" in text_lower or text_lower.startswith("uu"):
            if "pengganti" in text_lower or "perppu" in text_lower:
                return "PERPPU"
            return "UU"
        if "peraturan pemerintah" in text_lower or text_lower.startswith("pp"):
            return "PP"
        if "peraturan presiden" in text_lower or "perpres" in text_lower:
            return "PERPRES"
        if "peraturan menteri" in text_lower or "permen" in text_lower:
            return "PERMEN"

        return None

    def _generate_slug(self, jenis: str, nomor: str, tahun: int) -> str:
        """
        법령 slug 생성

        Args:
            jenis: 법령 유형
            nomor: 법령 번호
            tahun: 연도

        Returns:
            생성된 slug
        """
        jenis_slug = jenis.lower().replace(" ", "-")
        return f"{jenis_slug}-no-{nomor}-tahun-{tahun}"


def tag_relations(
    tentang: str,
    text: Optional[str] = None,
    source_slug: Optional[str] = None,
) -> list[TaggedExpression]:
    """
    편의 함수: 관계 표현 태깅

    Args:
        tentang: 문서 제목
        text: 문서 본문 (선택)
        source_slug: 원본 법령 slug

    Returns:
        태깅된 표현 목록
    """
    tagger = RelationTagger()
    tags = []

    # Tag from title
    tags.extend(tagger.tag_from_title(tentang))

    # Tag from text if provided
    if text:
        tags.extend(tagger.tag_from_text(text, source_slug))

    return tags


# CLI 테스트용
if __name__ == "__main__":
    print("=== 관계 표현 태깅 테스트 ===\n")

    tagger = RelationTagger()

    # 테스트 케이스
    test_cases = [
        {
            "tentang": "Perubahan Atas Undang-Undang Nomor 12 Tahun 2011",
            "text": None,
        },
        {
            "tentang": "Pembentukan Peraturan Perundang-undangan",
            "text": "Dengan berlakunya Undang-Undang ini, UU No. 10 Tahun 2004 dicabut dan dinyatakan tidak berlaku.",
        },
        {
            "tentang": "Peraturan Pelaksanaan",
            "text": "Peraturan yang ada tetap berlaku sepanjang tidak bertentangan dengan Undang-Undang ini sampai dengan ditetapkannya peraturan pelaksanaan sebagaimana dimaksud dalam Pasal 50.",
        },
    ]

    for tc in test_cases:
        print(f"제목: {tc['tentang']}")
        if tc['text']:
            print(f"본문: {tc['text'][:80]}...")

        tags = tag_relations(tc['tentang'], tc.get('text'))

        if tags:
            for tag in tags:
                print(f"  - {tag.expression_type.value}")
                print(f"    대상: {tag.target_ref}")
                if tag.target_slug:
                    print(f"    slug: {tag.target_slug}")
                if tag.kondisi_text:
                    print(f"    조건: {tag.kondisi_text[:50]}...")
        else:
            print("  (검출된 표현 없음)")
        print()
