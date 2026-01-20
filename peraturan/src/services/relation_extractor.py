"""Extract legal relationships from peraturan text and metadata."""

import re
from dataclasses import dataclass
from typing import Optional
from enum import Enum

from src.utils.logging import get_logger

logger = get_logger("relation_extractor")


class RelationType(Enum):
    """Types of legal relationships."""
    MENCABUT = "MENCABUT"              # Revokes
    MENGUBAH = "MENGUBAH"              # Amends
    MERUJUK = "MERUJUK"                # References
    BERLAKU_BERSYARAT = "BERLAKU_BERSYARAT"  # Conditionally valid
    MASA_PERALIHAN = "MASA_PERALIHAN"  # Transitional
    MENGIMPLEMENTASI = "MENGIMPLEMENTASI"  # Implements


@dataclass
class ExtractedRelation:
    """Extracted relationship from text."""
    rel_type: RelationType
    target_slug: Optional[str]
    target_jenis: str
    target_nomor: str
    target_tahun: int
    pasal_dasar: Optional[str] = None
    kondisi: Optional[str] = None
    confidence: float = 0.9


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

# Patterns for relationship detection
PATTERNS = {
    # Revocation patterns
    "mencabut": [
        re.compile(r"(?:dicabut|mencabut)\s+(?:dan\s+)?(?:dinyatakan\s+)?(?:tidak\s+berlaku)", re.I),
        re.compile(r"dinyatakan\s+tidak\s+berlaku", re.I),
        re.compile(r"(?:dengan\s+berlakunya|sejak\s+berlakunya).*?dicabut", re.I),
    ],

    # Amendment patterns
    "mengubah": [
        re.compile(r"(?:perubahan\s+(?:atas|terhadap|kedua|ketiga|keempat|kelima))", re.I),
        re.compile(r"mengubah\s+(?:ketentuan|beberapa\s+ketentuan)", re.I),
        re.compile(r"diubah\s+dengan", re.I),
    ],

    # Conditional validity patterns
    "bersyarat": [
        re.compile(r"sepanjang\s+tidak\s+bertentangan", re.I),
        re.compile(r"tetap\s+berlaku\s+sepanjang", re.I),
        re.compile(r"dinyatakan\s+(?:masih\s+)?tetap\s+berlaku\s+sepanjang", re.I),
    ],

    # Transitional patterns
    "peralihan": [
        re.compile(r"tetap\s+berlaku\s+sampai\s+(?:dengan\s+)?(?:ditetapkannya|diundangkannya)", re.I),
        re.compile(r"berlaku\s+sampai\s+dengan\s+(?:ditetapkan|diundangkan)", re.I),
        re.compile(r"masih\s+tetap\s+berlaku\s+sampai", re.I),
    ],

    # Reference patterns
    "merujuk": [
        re.compile(r"sebagaimana\s+(?:dimaksud|diatur)\s+(?:dalam|oleh)", re.I),
        re.compile(r"berdasarkan\s+(?:ketentuan|pasal)", re.I),
        re.compile(r"sesuai\s+(?:dengan\s+)?(?:ketentuan|pasal)", re.I),
    ],

    # Implementation patterns
    "implementasi": [
        re.compile(r"(?:untuk\s+)?melaksanakan\s+(?:ketentuan|pasal)", re.I),
        re.compile(r"sebagai\s+pelaksanaan\s+(?:dari|atas)", re.I),
        re.compile(r"peraturan\s+pelaksanaan\s+(?:dari|atas)", re.I),
    ],
}

# Pattern to extract article references
PASAL_PATTERN = re.compile(r"Pasal\s+(\d+)(?:\s+ayat\s+\((\d+)\))?", re.I)


class RelationExtractor:
    """Extract legal relationships from text."""

    def __init__(self):
        """Initialize the extractor."""
        pass

    def extract_from_title(self, tentang: str) -> list[ExtractedRelation]:
        """Extract relationships from document title/subject.

        Args:
            tentang: Document title/subject

        Returns:
            List of extracted relations
        """
        relations = []

        # Check for amendment in title
        if re.search(r"perubahan\s+(?:atas|kedua|ketiga|keempat|kelima)", tentang, re.I):
            # Find referenced peraturan
            match = PERATURAN_REF_PATTERN.search(tentang)
            if match:
                rel = self._create_relation_from_match(match, RelationType.MENGUBAH)
                if rel:
                    relations.append(rel)
                    logger.debug(f"Found amendment: {tentang[:50]}...")

        return relations

    def extract_from_text(self, text: str, source_slug: str) -> list[ExtractedRelation]:
        """Extract relationships from document text.

        Args:
            text: Full document text
            source_slug: Source peraturan slug

        Returns:
            List of extracted relations
        """
        if not text:
            return []

        relations = []

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

            # Determine relationship type from context
            rel_type = self._detect_relationship_type(sentence)
            if not rel_type:
                continue

            # Extract condition text if applicable
            kondisi = None
            if rel_type in (RelationType.BERLAKU_BERSYARAT, RelationType.MASA_PERALIHAN):
                kondisi = self._extract_condition(sentence)

            # Extract article reference
            pasal_match = PASAL_PATTERN.search(sentence)
            pasal_dasar = None
            if pasal_match:
                pasal_dasar = f"Pasal {pasal_match.group(1)}"
                if pasal_match.group(2):
                    pasal_dasar += f" ayat ({pasal_match.group(2)})"

            # Create relations for each reference
            for ref in refs:
                rel = self._create_relation_from_match(
                    ref, rel_type, pasal_dasar, kondisi
                )
                if rel:
                    relations.append(rel)

        return relations

    def _detect_relationship_type(self, sentence: str) -> Optional[RelationType]:
        """Detect relationship type from sentence context.

        Args:
            sentence: Sentence to analyze

        Returns:
            Detected relationship type or None
        """
        # Check patterns in priority order
        for pattern in PATTERNS["mencabut"]:
            if pattern.search(sentence):
                return RelationType.MENCABUT

        for pattern in PATTERNS["mengubah"]:
            if pattern.search(sentence):
                return RelationType.MENGUBAH

        for pattern in PATTERNS["bersyarat"]:
            if pattern.search(sentence):
                return RelationType.BERLAKU_BERSYARAT

        for pattern in PATTERNS["peralihan"]:
            if pattern.search(sentence):
                return RelationType.MASA_PERALIHAN

        for pattern in PATTERNS["implementasi"]:
            if pattern.search(sentence):
                return RelationType.MENGIMPLEMENTASI

        for pattern in PATTERNS["merujuk"]:
            if pattern.search(sentence):
                return RelationType.MERUJUK

        return None

    def _extract_condition(self, sentence: str) -> Optional[str]:
        """Extract condition text from sentence.

        Args:
            sentence: Sentence containing condition

        Returns:
            Condition text or None
        """
        # Pattern for conditional text
        patterns = [
            re.compile(r"(sepanjang\s+tidak\s+bertentangan[^.;]*)", re.I),
            re.compile(r"(sampai\s+(?:dengan\s+)?(?:ditetapkannya|diundangkannya)[^.;]*)", re.I),
        ]

        for pattern in patterns:
            match = pattern.search(sentence)
            if match:
                return match.group(1).strip()

        return None

    def _create_relation_from_match(
        self,
        match: re.Match,
        rel_type: RelationType,
        pasal_dasar: Optional[str] = None,
        kondisi: Optional[str] = None,
    ) -> Optional[ExtractedRelation]:
        """Create ExtractedRelation from regex match.

        Args:
            match: Regex match object
            rel_type: Relationship type
            pasal_dasar: Article basis
            kondisi: Condition text

        Returns:
            ExtractedRelation or None
        """
        try:
            # Parse matched groups
            full_match = match.group(0)

            # Determine jenis from matched text
            jenis = self._normalize_jenis(full_match)
            if not jenis:
                return None

            # Extract nomor and tahun from specific groups
            groups = match.groups()
            # Find the numeric groups
            nomor = None
            tahun = None
            for g in groups:
                if g and g.isdigit():
                    if len(g) == 4 and int(g) >= 1945:
                        tahun = int(g)
                    elif not nomor:
                        nomor = g

            if not nomor or not tahun:
                return None

            # Generate target slug
            target_slug = self._generate_slug(jenis, nomor, tahun)

            return ExtractedRelation(
                rel_type=rel_type,
                target_slug=target_slug,
                target_jenis=jenis,
                target_nomor=nomor,
                target_tahun=tahun,
                pasal_dasar=pasal_dasar,
                kondisi=kondisi,
            )

        except Exception as e:
            logger.warning(f"Failed to parse relation: {e}")
            return None

    def _normalize_jenis(self, text: str) -> Optional[str]:
        """Normalize document type from text.

        Args:
            text: Text containing document type

        Returns:
            Normalized jenis or None
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
        """Generate slug for a peraturan.

        Args:
            jenis: Document type
            nomor: Document number
            tahun: Year

        Returns:
            Generated slug
        """
        jenis_slug = jenis.lower().replace(" ", "-")
        return f"{jenis_slug}-no-{nomor}-tahun-{tahun}"


def extract_relations(
    tentang: str,
    text: Optional[str] = None,
    source_slug: Optional[str] = None,
) -> list[ExtractedRelation]:
    """Convenience function to extract relations.

    Args:
        tentang: Document title/subject
        text: Optional full text
        source_slug: Source peraturan slug

    Returns:
        List of extracted relations
    """
    extractor = RelationExtractor()
    relations = []

    # Extract from title
    relations.extend(extractor.extract_from_title(tentang))

    # Extract from text if provided
    if text and source_slug:
        relations.extend(extractor.extract_from_text(text, source_slug))

    return relations
