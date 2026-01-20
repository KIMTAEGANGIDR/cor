"""Structure parser for Indonesian legal documents.

Parses legal text into hierarchical structure:
BAB (Chapter) → Pasal (Article) → Ayat (Paragraph) → Huruf (Letter) → Angka (Number)
"""

import re
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Angka:
    """Number item (목) - e.g., 1., 2., 3."""
    nomor: int
    text: str


@dataclass
class Huruf:
    """Letter item (호) - e.g., a., b., c."""
    huruf: str  # a, b, c, etc.
    text: str
    angkas: list[Angka] = field(default_factory=list)


@dataclass
class Ayat:
    """Paragraph (항) - e.g., (1), (2), (3)."""
    nomor: int
    text: str
    hurufs: list[Huruf] = field(default_factory=list)


@dataclass
class Pasal:
    """Article (조) - e.g., Pasal 1, Pasal 2."""
    nomor: int
    text: str
    ayats: list[Ayat] = field(default_factory=list)


@dataclass
class Bab:
    """Chapter (장) - e.g., BAB I, BAB II."""
    nomor: str  # Roman numeral
    judul: str  # Title
    pasals: list[Pasal] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """Fully parsed legal document."""
    slug: str
    babs: list[Bab] = field(default_factory=list)
    pasals: list[Pasal] = field(default_factory=list)  # Pasals without BAB
    total_pasal: int = 0
    total_ayat: int = 0
    total_huruf: int = 0
    parse_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "slug": self.slug,
            "babs": [self._bab_to_dict(b) for b in self.babs],
            "pasals": [self._pasal_to_dict(p) for p in self.pasals],
            "stats": {
                "total_pasal": self.total_pasal,
                "total_ayat": self.total_ayat,
                "total_huruf": self.total_huruf,
            },
            "parse_errors": self.parse_errors,
        }

    def _bab_to_dict(self, bab: Bab) -> dict:
        return {
            "nomor": bab.nomor,
            "judul": bab.judul,
            "pasals": [self._pasal_to_dict(p) for p in bab.pasals],
        }

    def _pasal_to_dict(self, pasal: Pasal) -> dict:
        return {
            "nomor": pasal.nomor,
            "text": pasal.text,
            "ayats": [self._ayat_to_dict(a) for a in pasal.ayats],
        }

    def _ayat_to_dict(self, ayat: Ayat) -> dict:
        return {
            "nomor": ayat.nomor,
            "text": ayat.text,
            "hurufs": [self._huruf_to_dict(h) for h in ayat.hurufs],
        }

    def _huruf_to_dict(self, huruf: Huruf) -> dict:
        return {
            "huruf": huruf.huruf,
            "text": huruf.text,
            "angkas": [asdict(a) for a in huruf.angkas],
        }


class StructureParser:
    """Parser for Indonesian legal document structure."""

    # Patterns
    BAB_PATTERN = re.compile(
        r'BAB\s+([IVXLCDM]+)\.?\s*\n\s*(.+?)(?=\n|Pasal)',
        re.IGNORECASE
    )

    PASAL_PATTERN = re.compile(
        r'Pasal\s+(\d+)\s*\.?\s*\n(.*?)(?=Pasal\s+\d+|BAB\s+[IVXLCDM]+|Ditetapkan\s+di|PENJELASAN|$)',
        re.IGNORECASE | re.DOTALL
    )

    AYAT_PATTERN = re.compile(
        r'\((\d+)\)\s*(.*?)(?=\(\d+\)|$)',
        re.DOTALL
    )

    HURUF_PATTERN = re.compile(
        r'^([a-z])\.?\s+(.+?)(?=^[a-z]\.|\(\d+\)|$)',
        re.MULTILINE | re.DOTALL
    )

    ANGKA_PATTERN = re.compile(
        r'^(\d+)\.?\s+(.+?)(?=^\d+\.|\n[a-z]\.|$)',
        re.MULTILINE | re.DOTALL
    )

    def parse(self, text: str, slug: str = "") -> ParsedDocument:
        """Parse legal document text into structured format.

        Args:
            text: Extracted legal document text
            slug: Document identifier

        Returns:
            ParsedDocument with hierarchical structure
        """
        doc = ParsedDocument(slug=slug)

        if not text or len(text.strip()) < 50:
            doc.parse_errors.append("Text too short or empty")
            return doc

        # Try to find BABs first
        bab_matches = list(self.BAB_PATTERN.finditer(text))

        if bab_matches:
            # Document has BAB structure
            doc = self._parse_with_babs(text, slug, bab_matches)
        else:
            # Document without BABs - just Pasals
            doc = self._parse_without_babs(text, slug)

        # Calculate totals
        self._calculate_totals(doc)

        return doc

    def _parse_with_babs(self, text: str, slug: str, bab_matches: list) -> ParsedDocument:
        """Parse document that has BAB structure."""
        doc = ParsedDocument(slug=slug)

        for i, bab_match in enumerate(bab_matches):
            bab_nomor = bab_match.group(1)
            bab_judul = bab_match.group(2).strip()

            # Find end of this BAB (start of next BAB or end of text)
            bab_start = bab_match.end()
            if i + 1 < len(bab_matches):
                bab_end = bab_matches[i + 1].start()
            else:
                bab_end = len(text)

            bab_text = text[bab_start:bab_end]

            bab = Bab(nomor=bab_nomor, judul=bab_judul)

            # Find Pasals within this BAB
            pasal_matches = list(self.PASAL_PATTERN.finditer(bab_text))
            for pasal_match in pasal_matches:
                pasal = self._parse_pasal(pasal_match)
                bab.pasals.append(pasal)

            doc.babs.append(bab)

        return doc

    def _parse_without_babs(self, text: str, slug: str) -> ParsedDocument:
        """Parse document without BAB structure."""
        doc = ParsedDocument(slug=slug)

        pasal_matches = list(self.PASAL_PATTERN.finditer(text))
        for pasal_match in pasal_matches:
            pasal = self._parse_pasal(pasal_match)
            doc.pasals.append(pasal)

        return doc

    def _parse_pasal(self, match: re.Match) -> Pasal:
        """Parse a single Pasal."""
        nomor = int(match.group(1))
        content = match.group(2).strip()

        pasal = Pasal(nomor=nomor, text=content)

        # Check if content has Ayat structure
        ayat_matches = list(self.AYAT_PATTERN.finditer(content))

        if ayat_matches:
            for ayat_match in ayat_matches:
                ayat = self._parse_ayat(ayat_match)
                pasal.ayats.append(ayat)
        else:
            # No Ayat - check for Huruf directly
            hurufs = self._parse_hurufs(content)
            if hurufs:
                # Create a single implicit Ayat
                ayat = Ayat(nomor=0, text=content, hurufs=hurufs)
                pasal.ayats.append(ayat)

        return pasal

    def _parse_ayat(self, match: re.Match) -> Ayat:
        """Parse a single Ayat."""
        nomor = int(match.group(1))
        content = match.group(2).strip()

        ayat = Ayat(nomor=nomor, text=content)

        # Check for Huruf
        hurufs = self._parse_hurufs(content)
        ayat.hurufs = hurufs

        return ayat

    def _parse_hurufs(self, text: str) -> list[Huruf]:
        """Parse Huruf items from text."""
        hurufs = []

        # Simple pattern: lines starting with a., b., c., etc.
        lines = text.split('\n')
        current_huruf = None

        for line in lines:
            line = line.strip()
            huruf_match = re.match(r'^([a-z])\.?\s+(.+)', line)

            if huruf_match:
                if current_huruf:
                    hurufs.append(current_huruf)

                huruf_letter = huruf_match.group(1)
                huruf_text = huruf_match.group(2).strip()
                current_huruf = Huruf(huruf=huruf_letter, text=huruf_text)
            elif current_huruf:
                # Continuation of previous huruf
                current_huruf.text += " " + line

        if current_huruf:
            hurufs.append(current_huruf)

        # Parse Angka within each Huruf
        for huruf in hurufs:
            huruf.angkas = self._parse_angkas(huruf.text)

        return hurufs

    def _parse_angkas(self, text: str) -> list[Angka]:
        """Parse Angka items from text."""
        angkas = []

        lines = text.split('\n')
        current_angka = None

        for line in lines:
            line = line.strip()
            angka_match = re.match(r'^(\d+)\.?\s+(.+)', line)

            if angka_match:
                if current_angka:
                    angkas.append(current_angka)

                angka_num = int(angka_match.group(1))
                angka_text = angka_match.group(2).strip()
                current_angka = Angka(nomor=angka_num, text=angka_text)
            elif current_angka:
                current_angka.text += " " + line

        if current_angka:
            angkas.append(current_angka)

        return angkas

    def _calculate_totals(self, doc: ParsedDocument) -> None:
        """Calculate total counts."""
        total_pasal = 0
        total_ayat = 0
        total_huruf = 0

        # Count from BABs
        for bab in doc.babs:
            total_pasal += len(bab.pasals)
            for pasal in bab.pasals:
                total_ayat += len(pasal.ayats)
                for ayat in pasal.ayats:
                    total_huruf += len(ayat.hurufs)

        # Count standalone Pasals
        total_pasal += len(doc.pasals)
        for pasal in doc.pasals:
            total_ayat += len(pasal.ayats)
            for ayat in pasal.ayats:
                total_huruf += len(ayat.hurufs)

        doc.total_pasal = total_pasal
        doc.total_ayat = total_ayat
        doc.total_huruf = total_huruf


def parse_document(text: str, slug: str = "") -> ParsedDocument:
    """Convenience function to parse a document.

    Args:
        text: Legal document text
        slug: Document identifier

    Returns:
        ParsedDocument with hierarchical structure
    """
    parser = StructureParser()
    return parser.parse(text, slug)
