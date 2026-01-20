"""Text extractor for Indonesian legal PDFs."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


@dataclass
class ExtractionResult:
    """Result of text extraction from a PDF."""

    # Source info
    slug: str
    pdf_path: str
    total_pages: int

    # Extraction status
    success: bool
    error: Optional[str] = None
    needs_ocr: bool = False

    # Quality metrics
    avg_chars_per_page: float = 0.0
    repeated_line_ratio: float = 0.0
    quality_score: float = 0.0
    quality_flags: list[str] = field(default_factory=list)
    markers: dict[str, bool] = field(default_factory=dict)

    # Extracted content
    raw_text: str = ""
    clean_text: str = ""
    body_text: str = ""  # Main legal content only

    # Metadata extracted
    jenis: Optional[str] = None  # Document type
    nomor: Optional[str] = None  # Document number
    tahun: Optional[int] = None  # Year
    tentang: Optional[str] = None  # Subject/title

    # Structure info
    pasal_count: int = 0
    has_penjelasan: bool = False  # Has explanation section

    # Parsed structure
    pasals: list = field(default_factory=list)


@dataclass
class Pasal:
    """Represents a single Pasal (article)."""

    nomor: int
    text: str
    ayats: list = field(default_factory=list)


@dataclass
class Ayat:
    """Represents a single Ayat (paragraph)."""

    nomor: int
    text: str


class TextExtractor:
    """Extract and clean text from Indonesian legal PDFs."""

    # Noise patterns to remove
    NOISE_PATTERNS = [
        # Page numbers (standalone)
        r'^\s*-?\s*\d{1,4}\s*-?\s*$',
        # LEMBARAN/BERITA NEGARA header
        r'^\s*LEMBARAN\s+NEGARA\s*$',
        r'^\s*BERITA\s+NEGARA\s*$',
        r'^\s*REPUBLIK\s+INDONESIA\s*$',
        r'^\s*No\.\s*\d+,\s*\d{4}\s*$',
        # Watermarks
        r'^\s*SALINAN\s*$',
        r'www\.peraturan\.go\.id',
        r'www\.djpp\.[a-z0-9.-]+',
        # Empty lines with just whitespace
        r'^\s+$',
    ]

    # Repeated-line detection
    REPEAT_LINE_THRESHOLD = 0.6
    MIN_REPEAT_LINE_LEN = 4
    MAX_MARKER_SCAN_CHARS = 20000

    # Body markers
    BODY_START_PATTERNS = [
        r'MEMUTUSKAN\s*:',
        r'MENETAPKAN\s*:',
    ]

    BODY_END_PATTERNS = [
        r'Ditetapkan\s+di\s+\w+',
        r'DITETAPKAN\s+DI\s+\w+',
    ]

    # Structure patterns
    PASAL_PATTERN = re.compile(
        r'Pasal\s+(\d+)\s*\.?\s*\n(.*?)(?=Pasal\s+\d+|Ditetapkan\s+di|BAB\s+[IVXLCDM]+|PENJELASAN|$)',
        re.IGNORECASE | re.DOTALL
    )

    AYAT_PATTERN = re.compile(
        r'\((\d+)\)\s+(.*?)(?=\(\d+\)|$)',
        re.DOTALL
    )

    def __init__(self):
        """Initialize the extractor."""
        self._noise_regex = [re.compile(p, re.MULTILINE) for p in self.NOISE_PATTERNS]

    def extract(self, pdf_path: Path, slug: str = "") -> ExtractionResult:
        """Extract text from a PDF file.

        Args:
            pdf_path: Path to the PDF file
            slug: Document slug identifier

        Returns:
            ExtractionResult with extracted content
        """
        if not slug:
            slug = pdf_path.stem

        result = ExtractionResult(
            slug=slug,
            pdf_path=str(pdf_path),
            total_pages=0,
            success=False,
        )

        try:
            # Open PDF
            doc = fitz.open(pdf_path)
            result.total_pages = len(doc)

            # Extract raw text from all pages
            raw_parts = []
            page_lines = []
            for page in doc:
                text = page.get_text()
                lines = self._extract_page_lines(text)
                page_lines.append(lines)
                raw_parts.append(text)

            result.raw_text = "\n".join(raw_parts)
            doc.close()

            # Compute repeated-line noise
            repeated_lines, repeated_ratio = self._find_repeated_lines(
                page_lines, result.total_pages
            )
            result.repeated_line_ratio = repeated_ratio
            result.avg_chars_per_page = (
                len(result.raw_text) / result.total_pages if result.total_pages > 0 else 0
            )

            # Check if OCR is needed
            avg_chars_per_page = len(result.raw_text) / result.total_pages if result.total_pages > 0 else 0
            if avg_chars_per_page < 100 and result.total_pages > 1:
                result.needs_ocr = True
                result.error = "Scanned PDF - OCR required"
                return result

            # Clean the text
            result.clean_text = self._clean_text(result.raw_text, repeated_lines)

            # Extract body (main legal content)
            result.body_text = self._extract_body(result.clean_text)

            # Quality assessment and OCR candidate flags
            self._assess_quality(result)

            # Extract metadata
            self._extract_metadata(result)

            # Parse structure
            self._parse_structure(result)

            result.success = True

        except Exception as e:
            result.error = str(e)

        return result

    def _clean_text(self, text: str, repeated_lines: Optional[set[str]] = None) -> str:
        """Remove noise patterns from text."""
        lines = text.split('\n')
        clean_lines = []
        repeated_lines = repeated_lines or set()

        for line in lines:
            # Check against noise patterns
            is_noise = False
            for pattern in self._noise_regex:
                if pattern.match(line):
                    is_noise = True
                    break
            if not is_noise:
                normalized = self._normalize_line(line)
                if normalized and normalized in repeated_lines:
                    is_noise = True

            if not is_noise:
                clean_lines.append(line)

        # Join and normalize whitespace
        cleaned = '\n'.join(clean_lines)

        # Remove excessive blank lines (more than 2 consecutive)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

        return cleaned.strip()

    def _extract_page_lines(self, text: str) -> list[str]:
        """Extract normalized lines from a page."""
        lines = []
        for line in text.splitlines():
            normalized = self._normalize_line(line)
            if normalized:
                lines.append(normalized)
        return lines

    def _normalize_line(self, line: str) -> str:
        """Normalize a line for repeated-line detection."""
        cleaned = " ".join(line.split()).strip().lower()
        if not cleaned:
            return ""
        if cleaned.isdigit() or len(cleaned) < self.MIN_REPEAT_LINE_LEN:
            return ""
        return cleaned

    def _find_repeated_lines(
        self, page_lines: list[list[str]], total_pages: int
    ) -> tuple[set[str], float]:
        """Find lines repeated across most pages."""
        if total_pages <= 1:
            return set(), 0.0

        line_page_hits = {}
        total_unique_lines = 0

        for lines in page_lines:
            unique_lines = set(lines)
            total_unique_lines += len(unique_lines)
            for ln in unique_lines:
                line_page_hits[ln] = line_page_hits.get(ln, 0) + 1

        repeated_lines = {
            ln for ln, cnt in line_page_hits.items()
            if (cnt / total_pages) >= self.REPEAT_LINE_THRESHOLD
        }

        repeated_hits = sum(line_page_hits.get(ln, 0) for ln in repeated_lines)
        repeated_ratio = (repeated_hits / total_unique_lines) if total_unique_lines else 0.0

        return repeated_lines, repeated_ratio

    def _assess_quality(self, result: ExtractionResult) -> None:
        """Score extraction quality and set OCR candidate flags."""
        sample_text = result.clean_text[: self.MAX_MARKER_SCAN_CHARS].lower()
        markers = {
            "memutuskan": "memutuskan" in sample_text,
            "menetapkan": "menetapkan" in sample_text,
            "bab": "\nbab " in sample_text or " bab " in sample_text,
            "pasal": "pasal" in sample_text,
        }
        result.markers = markers

        score = 100.0
        flags = []

        if result.avg_chars_per_page < 80 and result.total_pages > 1:
            score -= 60
            flags.append("low_text_density")
        elif result.avg_chars_per_page < 200 and result.total_pages > 1:
            score -= 30
            flags.append("low_text_density")

        if not markers["pasal"]:
            score -= 20
            flags.append("missing_pasal")

        if not (markers["memutuskan"] or markers["menetapkan"]):
            score -= 15
            flags.append("missing_decision_marker")

        if result.repeated_line_ratio >= 0.3:
            score -= 10
            flags.append("high_repetition")

        result.quality_score = max(score, 0.0)
        result.quality_flags = flags

        # OCR candidate if text is very sparse or structure is missing
        if result.avg_chars_per_page < 80 and result.total_pages > 1:
            result.needs_ocr = True
        elif result.avg_chars_per_page < 200 and not markers["pasal"] and result.total_pages > 1:
            result.needs_ocr = True

    def _extract_body(self, text: str) -> str:
        """Extract main legal body content."""
        body_start = 0
        body_end = len(text)

        # Find body start
        for pattern in self.BODY_START_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                body_start = match.end()
                break

        # Find body end
        for pattern in self.BODY_END_PATTERNS:
            match = re.search(pattern, text[body_start:], re.IGNORECASE)
            if match:
                body_end = body_start + match.start()
                break

        body = text[body_start:body_end].strip()

        # Check for PENJELASAN (explanation) section
        penjelasan_match = re.search(r'\bPENJELASAN\b', text[body_end:], re.IGNORECASE)
        if penjelasan_match:
            # Could extract penjelasan separately if needed
            pass

        return body

    def _extract_metadata(self, result: ExtractionResult) -> None:
        """Extract document metadata from text."""
        text = result.raw_text[:3000]  # Metadata is in the beginning

        # Extract document type (jenis)
        jenis_patterns = [
            (r'UNDANG-UNDANG\s+REPUBLIK\s+INDONESIA', 'UNDANG-UNDANG'),
            (r'PERATURAN\s+PEMERINTAH\s+PENGGANTI\s+UNDANG-UNDANG', 'PERPPU'),
            (r'PERATURAN\s+PEMERINTAH\s+REPUBLIK\s+INDONESIA', 'PERATURAN PEMERINTAH'),
            (r'PERATURAN\s+PRESIDEN\s+REPUBLIK\s+INDONESIA', 'PERATURAN PRESIDEN'),
            (r'KEPUTUSAN\s+PRESIDEN\s+REPUBLIK\s+INDONESIA', 'KEPUTUSAN PRESIDEN'),
            (r'PERATURAN\s+MENTERI\s+(\w+)', 'PERATURAN MENTERI'),
        ]

        for pattern, jenis in jenis_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                result.jenis = jenis
                break

        # Extract nomor and tahun
        nomor_match = re.search(r'NOMOR\s+(\d+)\s+TAHUN\s+(\d{4})', text, re.IGNORECASE)
        if nomor_match:
            result.nomor = nomor_match.group(1)
            result.tahun = int(nomor_match.group(2))

        # Extract tentang (subject)
        tentang_match = re.search(r'TENTANG\s*\n\s*(.+?)(?:\n\s*\n|DENGAN\s+RAHMAT)', text, re.IGNORECASE | re.DOTALL)
        if tentang_match:
            result.tentang = ' '.join(tentang_match.group(1).split())

    def _parse_structure(self, result: ExtractionResult) -> None:
        """Parse the legal structure (Pasal, Ayat)."""
        body = result.body_text

        # Find all Pasals
        pasal_matches = list(self.PASAL_PATTERN.finditer(body))
        result.pasal_count = len(pasal_matches)

        for match in pasal_matches[:100]:  # Limit to first 100 for performance
            pasal_num = int(match.group(1))
            pasal_text = match.group(2).strip()

            pasal = Pasal(nomor=pasal_num, text=pasal_text)

            # Find Ayats within this Pasal
            ayat_matches = list(self.AYAT_PATTERN.finditer(pasal_text))
            for ayat_match in ayat_matches:
                ayat_num = int(ayat_match.group(1))
                ayat_text = ayat_match.group(2).strip()
                pasal.ayats.append(Ayat(nomor=ayat_num, text=ayat_text))

            result.pasals.append(pasal)

        # Check for penjelasan
        result.has_penjelasan = bool(re.search(r'\bPENJELASAN\b', result.raw_text, re.IGNORECASE))


def extract_pdf(pdf_path: str | Path, slug: str = "") -> ExtractionResult:
    """Convenience function to extract text from a PDF.

    Args:
        pdf_path: Path to the PDF file
        slug: Document slug identifier

    Returns:
        ExtractionResult with extracted content
    """
    extractor = TextExtractor()
    return extractor.extract(Path(pdf_path), slug)
