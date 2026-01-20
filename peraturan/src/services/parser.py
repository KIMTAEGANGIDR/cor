"""HTML parser for peraturan.go.id pages."""

import re
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup

from src.models.peraturan import Peraturan
from src.utils.logging import get_logger

logger = get_logger("parser")

# Valid year range for Indonesian laws (since independence in 1945)
MIN_VALID_YEAR = 1945
MAX_VALID_YEAR = datetime.now().year + 1


class ParseError(Exception):
    """Raised when parsing fails."""
    pass


class Parser:
    """HTML parser for extracting peraturan data from web pages."""

    # Indonesian month names to numbers
    MONTHS = {
        "januari": 1, "februari": 2, "maret": 3, "april": 4,
        "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
        "september": 9, "oktober": 10, "november": 11, "desember": 12,
    }

    def __init__(self) -> None:
        """Initialize parser."""
        self.base_url = "https://peraturan.go.id"

    def parse_detail_page(self, html: str, slug: str) -> Peraturan:
        """Parse a detail page and extract peraturan metadata.

        Args:
            html: HTML content of the detail page
            slug: URL slug of the peraturan

        Returns:
            Peraturan instance with extracted data

        Raises:
            ParseError: If required fields cannot be extracted
        """
        if not html or not html.strip():
            raise ParseError(f"Empty HTML for {slug}")

        soup = BeautifulSoup(html, "lxml")

        # Find the detail table
        table = soup.find("table", class_=lambda x: x and "table" in x)
        if not table:
            raise ParseError(f"No detail table found for {slug}")

        # Extract fields from table rows
        fields = self._extract_table_fields(table)

        # Validate required fields (except tahun which uses fallback)
        required = ["jenis", "nomor", "tentang"]
        missing = [f for f in required if not fields.get(f)]
        if missing:
            raise ParseError(f"Missing required fields {missing} for {slug}")

        # Extract year with fallback logic
        tahun = self._extract_year_with_fallback(fields, slug)

        # Extract PDF URL from page
        pdf_url = self._extract_pdf_url(soup)

        # Build peraturan
        try:
            return Peraturan(
                slug=slug,
                jenis=fields["jenis"],
                nomor=fields["nomor"],
                tahun=tahun,
                tentang=fields["tentang"],
                source_url=f"{self.base_url}/id/{slug}",
                pemrakarsa=fields.get("pemrakarsa"),
                tempat_penetapan=fields.get("tempat_penetapan"),
                tanggal_penetapan=self.normalize_date(fields.get("tanggal_penetapan")),
                pejabat_penetapan=fields.get("pejabat_penetapan"),
                tahun_pengundangan=self._safe_int(fields.get("tahun_pengundangan")),
                nomor_pengundangan=fields.get("nomor_pengundangan"),
                nomor_tambahan=fields.get("nomor_tambahan"),
                tanggal_pengundangan=self.normalize_date(fields.get("tanggal_pengundangan")),
                pejabat_pengundangan=fields.get("pejabat_pengundangan"),
                status=fields.get("status", "Berlaku"),
                pdf_url=pdf_url,
            )
        except Exception as e:
            raise ParseError(f"Failed to create Peraturan for {slug}: {e}") from e

    def _extract_pdf_url(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract PDF download URL from detail page.

        Args:
            soup: BeautifulSoup object of the page

        Returns:
            Full PDF URL or None if not found

        Extraction priority:
        1. Direct <a href="/files/*.pdf"> links
        2. PDF icon links (img inside a tag)
        3. Meta tags <meta name="article:tag" content="*.pdf">
        """
        # Method 1: Look for direct links to PDF files
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/files/" in href and href.endswith(".pdf"):
                # Convert relative URL to absolute
                if href.startswith("/"):
                    return f"{self.base_url}{href}"
                elif not href.startswith("http"):
                    return f"{self.base_url}/{href}"
                return href

        # Method 2: Check for PDF icon links (img inside a tag)
        for a in soup.find_all("a", href=True):
            img = a.find("img")
            if img and "pdf" in img.get("src", "").lower():
                href = a["href"]
                if href.startswith("/"):
                    return f"{self.base_url}{href}"
                elif not href.startswith("http"):
                    return f"{self.base_url}/{href}"
                return href

        # Method 3: Extract from meta tags (used by UUD, TAP MPR, etc.)
        # <meta name="article:tag" content="https://peraturan.go.id/files/....pdf">
        for meta in soup.find_all("meta", {"name": "article:tag"}):
            content = meta.get("content", "")
            if content.endswith(".pdf") and "/files/" in content:
                # Already a full URL
                if content.startswith("http"):
                    return content
                # Relative URL
                if content.startswith("/"):
                    return f"{self.base_url}{content}"

        # Method 4: Check keywords meta tag (backup)
        keywords_meta = soup.find("meta", {"name": "keywords"})
        if keywords_meta:
            keywords = keywords_meta.get("content", "")
            # Find PDF URL in keywords
            for part in keywords.split(","):
                part = part.strip()
                if part.endswith(".pdf") and "/files/" in part:
                    if part.startswith("http"):
                        return part

        return None

    def extract_all_pdf_urls(self, soup: BeautifulSoup) -> list[str]:
        """Extract all PDF URLs from a page (for UUD with multiple amendments).

        Args:
            soup: BeautifulSoup object of the page

        Returns:
            List of PDF URLs found on the page
        """
        pdf_urls = []
        seen = set()

        # From meta tags
        for meta in soup.find_all("meta", {"name": "article:tag"}):
            content = meta.get("content", "")
            if content.endswith(".pdf") and "/files/" in content:
                url = content if content.startswith("http") else f"{self.base_url}{content}"
                if url not in seen:
                    pdf_urls.append(url)
                    seen.add(url)

        # From direct links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/files/" in href and href.endswith(".pdf"):
                if href.startswith("/"):
                    url = f"{self.base_url}{href}"
                elif href.startswith("http"):
                    url = href
                else:
                    url = f"{self.base_url}/{href}"

                if url not in seen:
                    pdf_urls.append(url)
                    seen.add(url)

        return pdf_urls

    def _extract_table_fields(self, table) -> dict:
        """Extract fields from a detail table.

        Args:
            table: BeautifulSoup table element

        Returns:
            Dictionary of field names to values
        """
        fields = {}

        # Field mappings - ordered from most specific to least specific
        # to prevent "nomor" matching "nomor pengundangan" etc.
        field_patterns = [
            # Most specific patterns first (compound names)
            ("nomor_pengundangan", ["nomor pengundangan"]),
            ("nomor_tambahan", ["nomor tambahan"]),
            ("tahun_pengundangan", ["tahun pengundangan"]),
            ("tanggal_pengundangan", ["tanggal pengundangan"]),
            ("tanggal_penetapan", ["ditetapkan tanggal", "tanggal penetapan"]),
            ("tempat_penetapan", ["tempat penetapan"]),
            ("pejabat_pengundangan", ["pejabat pengundangan", "pejabat yang mengundangkan"]),
            ("pejabat_penetapan", ["pejabat yang menetapkan", "pejabat penetapan"]),
            ("jenis", ["jenis/bentuk peraturan", "jenis / bentuk", "jenis/bentuk", "jenis"]),
            # Simple patterns last
            ("nomor", ["nomor"]),
            ("tahun", ["tahun"]),
            ("tentang", ["tentang", "perihal"]),
            ("pemrakarsa", ["pemrakarsa"]),
            ("status", ["status"]),
        ]

        for row in table.find_all("tr"):
            th = row.find("th")
            td = row.find("td")

            if not th or not td:
                continue

            header = th.get_text(strip=True).lower()
            value = td.get_text(strip=True)

            # Find matching field - try most specific patterns first
            for field_name, patterns in field_patterns:
                # Skip if already found
                if field_name in fields:
                    continue

                # Check for exact match first, then contains match
                matched = False
                for pattern in patterns:
                    # Exact match or header equals pattern
                    if header == pattern or header.replace("/", " / ") == pattern:
                        matched = True
                        break
                    # Contains match - but only if pattern is compound (has space)
                    if " " in pattern and pattern in header:
                        matched = True
                        break
                    # For simple patterns, require they are not part of a compound header
                    if " " not in pattern and header == pattern:
                        matched = True
                        break

                if matched:
                    fields[field_name] = value
                    break

        return fields

    def parse_list_page(self, html: str, return_stats: bool = False) -> list[str] | tuple[list[str], dict]:
        """Parse a list page and extract peraturan links.

        Args:
            html: HTML content of the list page
            return_stats: If True, return tuple of (links, stats)

        Returns:
            List of relative URLs to detail pages, or tuple (links, stats) if return_stats=True
        """
        soup = BeautifulSoup(html, "lxml")
        links = []
        stats = {
            "total_links": 0,
            "valid_links": 0,
            "filtered_hash": 0,
            "filtered_javascript": 0,
            "filtered_empty": 0,
            "filtered_invalid_slug": 0,
        }

        # Find all links that point to detail pages
        for a in soup.find_all("a", href=True):
            href = a["href"]
            stats["total_links"] += 1

            # Validate link
            is_valid, filter_reason = self._is_valid_peraturan_link(href)
            if not is_valid:
                if filter_reason:
                    stats[f"filtered_{filter_reason}"] = stats.get(f"filtered_{filter_reason}", 0) + 1
                continue

            # Normalize to relative URL
            if href.startswith("http"):
                href = "/" + "/".join(href.split("/")[3:])
            links.append(href)
            stats["valid_links"] += 1

        # Deduplicate while preserving order
        seen = set()
        unique_links = []
        for link in links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)

        if return_stats:
            return unique_links, stats
        return unique_links

    def _is_valid_peraturan_link(self, href: str) -> tuple[bool, str | None]:
        """Validate if a link points to a real peraturan page.

        Args:
            href: Link URL to validate

        Returns:
            Tuple of (is_valid, filter_reason) where filter_reason is None if valid

        Invalid links to filter:
        - Empty or whitespace-only links
        - "#" placeholder links
        - JavaScript links (javascript:)
        - Links ending with just /id/
        - Links with empty or invalid slugs
        """
        # Empty or whitespace check
        if not href or href.isspace():
            return False, "empty"

        # Strip whitespace
        href = href.strip()

        # Check for javascript: links
        if href.lower().startswith("javascript:"):
            return False, "javascript"

        # Check for hash-only or hash placeholder links
        if href == "#" or href.endswith("#") or "/id/#" in href:
            return False, "hash"

        # Must contain /id/ to be a peraturan link
        if "/id/" not in href:
            return False, None  # Not a peraturan link, filter silently

        # Check if link ends with just /id/ (no slug)
        if href.endswith("/id/") or href.rstrip("/").endswith("/id"):
            return False, "empty"

        # Extract and validate slug
        slug = self.extract_slug_from_url(href)
        if not slug or slug in ["#", "", " "] or slug.isspace():
            return False, "invalid_slug"

        # Check for tab characters or other invalid characters in slug
        if "\t" in slug or "\n" in slug or "\r" in slug:
            logger.warning(f"Invalid characters in slug: {repr(slug)}")
            return False, "invalid_slug"

        return True, None

    def parse_total_count(self, html: str) -> int:
        """Extract total count from list page.

        Args:
            html: HTML content of the list page

        Returns:
            Total number of peraturan
        """
        soup = BeautifulSoup(html, "lxml")

        # Look for text like "Menampilkan X - Y dari Z peraturan"
        text = soup.get_text()
        match = re.search(r"dari\s+([\d.,]+)\s+peraturan", text, re.IGNORECASE)

        if match:
            count_str = match.group(1).replace(".", "").replace(",", "")
            return int(count_str)

        return 0

    def parse_last_page(self, html: str) -> int:
        """Extract last page number from pagination.

        Args:
            html: HTML content of the list page

        Returns:
            Last page number
        """
        soup = BeautifulSoup(html, "lxml")

        # Find pagination links
        pagination = soup.find("ul", class_="pagination")
        if not pagination:
            pagination = soup.find("nav", class_="pagination-wrapper")

        if pagination:
            # Find the highest page number
            max_page = 1
            for a in pagination.find_all("a", href=True):
                text = a.get_text(strip=True)
                if text.isdigit():
                    max_page = max(max_page, int(text))

            return max_page

        return 1

    def extract_slug_from_url(self, url: str) -> str:
        """Extract slug from a peraturan URL.

        Args:
            url: Full or relative URL

        Returns:
            Slug (e.g., 'uu-no-2-tahun-2025')
        """
        # Handle both full URLs and relative paths
        if "/id/" in url:
            parts = url.split("/id/")
            if len(parts) > 1:
                return parts[1].rstrip("/")

        return url.rstrip("/")

    def normalize_date(self, date_str: Optional[str]) -> Optional[str]:
        """Convert Indonesian date to ISO format (YYYY-MM-DD).

        Args:
            date_str: Date string like "15 Januari 2025"

        Returns:
            ISO date string or None if parsing fails
        """
        if not date_str:
            return None

        try:
            # Pattern: "15 Januari 2025"
            match = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", date_str.strip())
            if not match:
                return None

            day = int(match.group(1))
            month_name = match.group(2).lower()
            year = int(match.group(3))

            month = self.MONTHS.get(month_name)
            if not month:
                return None

            return f"{year:04d}-{month:02d}-{day:02d}"
        except (ValueError, AttributeError):
            return None

    def _safe_int(self, value: Optional[str]) -> Optional[int]:
        """Safely convert string to int.

        Args:
            value: String value or None

        Returns:
            Integer or None
        """
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _is_valid_year(self, year: Optional[int]) -> bool:
        """Check if year is in valid range for Indonesian law.

        Args:
            year: Year to validate

        Returns:
            True if year is valid, False otherwise
        """
        if year is None:
            return False
        return MIN_VALID_YEAR <= year <= MAX_VALID_YEAR

    def _parse_year_from_text(self, text: Optional[str]) -> Optional[int]:
        """Parse year from text containing 'Tahun YYYY'.

        Args:
            text: Text that may contain year reference

        Returns:
            Year if found and valid, None otherwise
        """
        if not text:
            return None

        match = re.search(r"[Tt]ahun\s+(\d{4})", text)
        if match:
            year = int(match.group(1))
            if self._is_valid_year(year):
                return year
        return None

    def _parse_year_from_slug(self, slug: str) -> Optional[int]:
        """Parse year from slug containing 'tahun-YYYY' or '-YYYY'.

        Args:
            slug: URL slug like 'uu-no-2-tahun-2025'

        Returns:
            Year if found and valid, None otherwise
        """
        if not slug:
            return None

        # Try tahun-YYYY pattern first
        match = re.search(r"tahun[- ](\d{4})", slug.lower())
        if match:
            year = int(match.group(1))
            if self._is_valid_year(year):
                return year

        # Try year at end of slug
        match = re.search(r"-(\d{4})$", slug)
        if match:
            year = int(match.group(1))
            if self._is_valid_year(year):
                return year

        return None

    def _parse_year_from_date(self, date_str: Optional[str]) -> Optional[int]:
        """Parse year from date string.

        Args:
            date_str: Date string like '15 Januari 2025'

        Returns:
            Year if found and valid, None otherwise
        """
        if not date_str:
            return None

        match = re.search(r"(\d{4})", date_str)
        if match:
            year = int(match.group(1))
            if self._is_valid_year(year):
                return year
        return None

    def _extract_year_with_fallback(self, fields: dict, slug: str) -> int:
        """Extract year using multiple fallback strategies.

        Args:
            fields: Extracted fields from detail page
            slug: URL slug of the peraturan

        Returns:
            Valid year

        Raises:
            ParseError: If no valid year can be determined

        Strategy order:
        1. Direct 'tahun' field from table
        2. Parse from 'tentang' text (e.g., "Tahun 2024")
        3. Parse from slug (e.g., "uu-no-1-tahun-2024")
        4. Parse from 'tanggal_penetapan' date
        """
        strategies = [
            ("direct", lambda: self._safe_int(fields.get("tahun"))),
            ("tentang", lambda: self._parse_year_from_text(fields.get("tentang", ""))),
            ("slug", lambda: self._parse_year_from_slug(slug)),
            ("date", lambda: self._parse_year_from_date(fields.get("tanggal_penetapan"))),
        ]

        for strategy_name, extractor in strategies:
            year = extractor()
            if year and self._is_valid_year(year):
                if strategy_name != "direct":
                    logger.info(f"Year extracted via fallback ({strategy_name}): {slug} -> {year}")
                return year

        # Log available data for debugging
        raw_year = fields.get("tahun")
        logger.error(
            f"Cannot determine valid year for {slug}. "
            f"Raw tahun='{raw_year}', tentang='{fields.get('tentang', '')[:50]}...'"
        )
        raise ParseError(f"Cannot determine valid year for {slug}: raw tahun='{raw_year}'")
