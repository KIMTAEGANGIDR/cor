"""OCR text cleaner for Indonesian legal documents."""

from __future__ import annotations

import re
from dataclasses import dataclass


NOISE_PATTERNS = [
    r"^\s*-?\s*\d{1,4}\s*-?\s*$",  # page numbers
    r"^\s*LEMBARAN\s+NEGARA\s*$",
    r"^\s*BERITA\s+NEGARA\s*$",
    r"^\s*REPUBLIK\s+INDONESIA\s*$",
    r"^\s*SALINAN\s*$",
    r"^PRESIDEN$",
    r"^MENTERI$",
    r"^DIREKTUR\s+JENDERAL$",
    r"^SK\s*NO\.?\s*\d+",
    r"www\.peraturan\.go\.id",
    r"www\.djpp\.[a-z0-9.-]+",
]


@dataclass
class CleanResult:
    """Result of OCR cleaning."""

    cleaned_pages: list[str]
    repeated_lines: set[str]
    repeated_ratio: float
    removed_lines: int
    total_lines: int


def normalize_line(line: str, min_len: int = 4) -> str:
    """Normalize a line for repeated-line detection."""
    cleaned = " ".join(line.split()).strip().lower()
    if not cleaned:
        return ""
    if cleaned.isdigit() or len(cleaned) < min_len:
        return ""
    return cleaned


def extract_page_lines(text: str) -> list[str]:
    """Extract normalized lines from a page."""
    lines = []
    for line in text.splitlines():
        normalized = normalize_line(line)
        if normalized:
            lines.append(normalized)
    return lines


def find_repeated_lines(
    page_lines: list[list[str]],
    total_pages: int,
    threshold: float = 0.6,
) -> tuple[set[str], float]:
    """Find lines repeated across most pages."""
    if total_pages <= 1:
        return set(), 0.0

    line_page_hits: dict[str, int] = {}
    total_unique_lines = 0

    for lines in page_lines:
        unique_lines = set(lines)
        total_unique_lines += len(unique_lines)
        for ln in unique_lines:
            line_page_hits[ln] = line_page_hits.get(ln, 0) + 1

    repeated_lines = {
        ln for ln, cnt in line_page_hits.items()
        if (cnt / total_pages) >= threshold
    }

    repeated_hits = sum(line_page_hits.get(ln, 0) for ln in repeated_lines)
    repeated_ratio = (repeated_hits / total_unique_lines) if total_unique_lines else 0.0

    return repeated_lines, repeated_ratio


def clean_ocr_pages(
    page_texts: list[str],
    noise_patterns: list[str] | None = None,
    repeat_threshold: float = 0.6,
) -> CleanResult:
    """Remove noise and repeated lines from OCR page texts."""
    noise_patterns = noise_patterns or NOISE_PATTERNS
    noise_regex = [re.compile(p, re.IGNORECASE) for p in noise_patterns]

    page_lines = [extract_page_lines(text) for text in page_texts]
    repeated_lines, repeated_ratio = find_repeated_lines(
        page_lines, len(page_texts), threshold=repeat_threshold
    )

    cleaned_pages: list[str] = []
    removed_lines = 0
    total_lines = 0

    for text in page_texts:
        cleaned_lines = []
        for line in text.splitlines():
            total_lines += 1
            is_noise = False
            for pattern in noise_regex:
                if pattern.search(line):
                    is_noise = True
                    break
            normalized = normalize_line(line)
            if normalized and normalized in repeated_lines:
                is_noise = True
            if is_noise:
                removed_lines += 1
                continue
            cleaned_lines.append(line)
        cleaned_pages.append("\n".join(cleaned_lines).strip())

    return CleanResult(
        cleaned_pages=cleaned_pages,
        repeated_lines=repeated_lines,
        repeated_ratio=repeated_ratio,
        removed_lines=removed_lines,
        total_lines=total_lines,
    )
