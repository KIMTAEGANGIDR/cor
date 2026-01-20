"""Unit tests for HTML parser."""

import pytest
from pathlib import Path

from src.services.parser import Parser, ParseError


class TestParser:
    """Tests for HTML parser."""

    @pytest.fixture
    def parser(self) -> Parser:
        """Create parser instance."""
        return Parser()

    @pytest.fixture
    def detail_html(self) -> str:
        """Load detail page HTML fixture."""
        fixture_path = Path(__file__).parent.parent / "fixtures" / "html" / "detail_page.html"
        return fixture_path.read_text(encoding="utf-8")

    @pytest.fixture
    def list_html(self) -> str:
        """Load list page HTML fixture."""
        fixture_path = Path(__file__).parent.parent / "fixtures" / "html" / "list_page.html"
        return fixture_path.read_text(encoding="utf-8")

    def test_parse_detail_page(self, parser: Parser, detail_html: str) -> None:
        """Test parsing detail page extracts all fields."""
        result = parser.parse_detail_page(detail_html, "uu-no-2-tahun-2025")

        assert result.slug == "uu-no-2-tahun-2025"
        assert result.jenis == "UNDANG-UNDANG"
        assert result.nomor == "2"
        assert result.tahun == 2025
        assert "PERUBAHAN KEEMPAT" in result.tentang
        assert result.pemrakarsa == "PEMERINTAH PUSAT"
        assert result.tempat_penetapan == "Jakarta"
        assert result.status == "Berlaku"

    def test_parse_detail_page_promulgation_info(
        self, parser: Parser, detail_html: str
    ) -> None:
        """Test parsing promulgation information."""
        result = parser.parse_detail_page(detail_html, "uu-no-2-tahun-2025")

        assert result.tahun_pengundangan == 2025
        assert result.nomor_pengundangan == "2"
        assert result.nomor_tambahan == "6800"
        assert result.pejabat_pengundangan == "MENTERI SEKRETARIS NEGARA"

    def test_parse_list_page(self, parser: Parser, list_html: str) -> None:
        """Test parsing list page extracts links."""
        links = parser.parse_list_page(list_html)

        assert len(links) == 3
        assert "/id/uu-no-2-tahun-2025" in links
        assert "/id/uu-no-1-tahun-2025" in links
        assert "/id/pp-no-1-tahun-2025" in links

    def test_parse_total_count(self, parser: Parser, list_html: str) -> None:
        """Test extracting total count from list page."""
        total = parser.parse_total_count(list_html)
        assert total == 61341

    def test_parse_last_page(self, parser: Parser, list_html: str) -> None:
        """Test extracting last page number from pagination."""
        last_page = parser.parse_last_page(list_html)
        assert last_page == 3068

    def test_extract_slug_from_url(self, parser: Parser) -> None:
        """Test extracting slug from URL."""
        test_cases = [
            ("/id/uu-no-2-tahun-2025", "uu-no-2-tahun-2025"),
            ("https://peraturan.go.id/id/pp-no-1-tahun-2024", "pp-no-1-tahun-2024"),
            ("/id/perpres-no-10-tahun-2023", "perpres-no-10-tahun-2023"),
        ]

        for url, expected in test_cases:
            assert parser.extract_slug_from_url(url) == expected

    def test_parse_empty_html_raises_error(self, parser: Parser) -> None:
        """Test that parsing empty HTML raises ParseError."""
        with pytest.raises(ParseError):
            parser.parse_detail_page("", "test-slug")

    def test_parse_invalid_html(self, parser: Parser) -> None:
        """Test parsing invalid HTML with missing fields."""
        invalid_html = "<html><body><div>No data here</div></body></html>"

        with pytest.raises(ParseError):
            parser.parse_detail_page(invalid_html, "test-slug")

    def test_normalize_date(self, parser: Parser) -> None:
        """Test date normalization to ISO format."""
        test_cases = [
            ("15 Januari 2025", "2025-01-15"),
            ("1 Desember 2024", "2024-12-01"),
            ("20 Maret 2023", "2023-03-20"),
            ("5 Juli 2022", "2022-07-05"),
        ]

        for input_date, expected in test_cases:
            assert parser.normalize_date(input_date) == expected

    def test_normalize_date_invalid(self, parser: Parser) -> None:
        """Test date normalization with invalid input returns None."""
        assert parser.normalize_date("invalid date") is None
        assert parser.normalize_date("") is None
        assert parser.normalize_date(None) is None


class TestLinkValidation:
    """Tests for link validation functionality."""

    @pytest.fixture
    def parser(self) -> Parser:
        """Create parser instance."""
        return Parser()

    def test_valid_peraturan_link(self, parser: Parser) -> None:
        """Test that valid peraturan links are accepted."""
        valid_links = [
            "/id/uu-no-2-tahun-2025",
            "/id/pp-no-1-tahun-2024",
            "/id/perpres-no-10-tahun-2023",
            "https://peraturan.go.id/id/permen-no-5-tahun-2024",
        ]

        for link in valid_links:
            is_valid, reason = parser._is_valid_peraturan_link(link)
            assert is_valid, f"Link should be valid: {link}"
            assert reason is None

    def test_hash_links_filtered(self, parser: Parser) -> None:
        """Test that hash placeholder links are filtered."""
        hash_links = [
            "#",
            "/id/#",
            "/id/something#",
            "https://peraturan.go.id/id/#",
        ]

        for link in hash_links:
            is_valid, reason = parser._is_valid_peraturan_link(link)
            assert not is_valid, f"Hash link should be filtered: {link}"
            assert reason == "hash"

    def test_javascript_links_filtered(self, parser: Parser) -> None:
        """Test that javascript: links are filtered."""
        js_links = [
            "javascript:void(0)",
            "javascript:alert('test')",
            "JAVASCRIPT:something",
        ]

        for link in js_links:
            is_valid, reason = parser._is_valid_peraturan_link(link)
            assert not is_valid, f"Javascript link should be filtered: {link}"
            assert reason == "javascript"

    def test_empty_links_filtered(self, parser: Parser) -> None:
        """Test that empty or whitespace links are filtered."""
        empty_links = [
            "",
            "   ",
            "\t",
            "\n",
            "/id/",
            "/id",
        ]

        for link in empty_links:
            is_valid, reason = parser._is_valid_peraturan_link(link)
            assert not is_valid, f"Empty link should be filtered: {repr(link)}"

    def test_invalid_slug_links_filtered(self, parser: Parser) -> None:
        """Test that links with invalid slugs are filtered."""
        invalid_slug_links = [
            "/id/ ",
            "/id/\t",
            "/id/slug\twith\ttabs",
        ]

        for link in invalid_slug_links:
            is_valid, reason = parser._is_valid_peraturan_link(link)
            assert not is_valid, f"Invalid slug link should be filtered: {repr(link)}"

    def test_non_peraturan_links_silent_filter(self, parser: Parser) -> None:
        """Test that non-peraturan links are filtered without reason."""
        non_peraturan_links = [
            "/about",
            "/contact",
            "https://example.com/page",
        ]

        for link in non_peraturan_links:
            is_valid, reason = parser._is_valid_peraturan_link(link)
            assert not is_valid, f"Non-peraturan link should be filtered: {link}"
            assert reason is None  # Silent filter

    def test_parse_list_page_with_stats(self, parser: Parser) -> None:
        """Test parse_list_page returns stats when requested."""
        html = """
        <html><body>
            <a href="/id/uu-no-1-tahun-2025">Link 1</a>
            <a href="/id/#">Placeholder</a>
            <a href="javascript:void(0)">JS Link</a>
            <a href="/id/pp-no-2-tahun-2024">Link 2</a>
            <a href="/about">About</a>
        </body></html>
        """

        links, stats = parser.parse_list_page(html, return_stats=True)

        assert len(links) == 2
        assert "/id/uu-no-1-tahun-2025" in links
        assert "/id/pp-no-2-tahun-2024" in links
        assert stats["valid_links"] == 2
        assert stats["filtered_hash"] >= 1
        assert stats["filtered_javascript"] >= 1

    def test_parse_list_page_backward_compatible(self, parser: Parser) -> None:
        """Test parse_list_page works without return_stats for backward compatibility."""
        html = """
        <html><body>
            <a href="/id/uu-no-1-tahun-2025">Link 1</a>
        </body></html>
        """

        links = parser.parse_list_page(html)

        assert isinstance(links, list)
        assert len(links) == 1


class TestYearExtraction:
    """Tests for year extraction with fallback logic."""

    @pytest.fixture
    def parser(self) -> Parser:
        """Create parser instance."""
        return Parser()

    def test_is_valid_year(self, parser: Parser) -> None:
        """Test year validation."""
        # Valid years
        assert parser._is_valid_year(1945) is True  # Independence year
        assert parser._is_valid_year(2024) is True
        assert parser._is_valid_year(2025) is True

        # Invalid years
        assert parser._is_valid_year(1944) is False  # Before independence
        assert parser._is_valid_year(1465) is False  # Way too old
        assert parser._is_valid_year(3000) is False  # Too far future
        assert parser._is_valid_year(None) is False

    def test_parse_year_from_text(self, parser: Parser) -> None:
        """Test year extraction from text."""
        test_cases = [
            ("Peraturan Tahun 2024", 2024),
            ("tahun 2025", 2025),
            ("Berlaku sejak Tahun  2023", 2023),
            ("No year here", None),
            ("Invalid Tahun 1400", None),  # Invalid year
            ("", None),
            (None, None),
        ]

        for text, expected in test_cases:
            result = parser._parse_year_from_text(text)
            assert result == expected, f"Expected {expected} for '{text}', got {result}"

    def test_parse_year_from_slug(self, parser: Parser) -> None:
        """Test year extraction from slug."""
        test_cases = [
            ("uu-no-2-tahun-2025", 2025),
            ("pp-no-1-tahun-2024", 2024),
            ("perpres-no-10-tahun-2023", 2023),
            ("permen-kp-no-37-permen-kp-2018-tahun-1465", None),  # Invalid year 1465
            ("some-slug-2024", 2024),  # Year at end
            ("no-year-slug", None),
            ("", None),
        ]

        for slug, expected in test_cases:
            result = parser._parse_year_from_slug(slug)
            assert result == expected, f"Expected {expected} for '{slug}', got {result}"

    def test_parse_year_from_date(self, parser: Parser) -> None:
        """Test year extraction from date string."""
        test_cases = [
            ("15 Januari 2025", 2025),
            ("1 Desember 2024", 2024),
            ("No date", None),
            ("Invalid date 1400", None),  # Invalid year
            ("", None),
            (None, None),
        ]

        for date_str, expected in test_cases:
            result = parser._parse_year_from_date(date_str)
            assert result == expected, f"Expected {expected} for '{date_str}', got {result}"

    def test_extract_year_with_fallback_direct(self, parser: Parser) -> None:
        """Test year extraction with direct field value."""
        fields = {"tahun": "2025", "tentang": "Some title"}
        year = parser._extract_year_with_fallback(fields, "uu-no-1-tahun-2025")
        assert year == 2025

    def test_extract_year_with_fallback_from_tentang(self, parser: Parser) -> None:
        """Test year extraction falls back to tentang text."""
        fields = {"tahun": "1465", "tentang": "Peraturan Tahun 2018"}
        year = parser._extract_year_with_fallback(fields, "permen-kp-no-37")
        assert year == 2018

    def test_extract_year_with_fallback_from_slug(self, parser: Parser) -> None:
        """Test year extraction falls back to slug."""
        fields = {"tahun": "invalid", "tentang": "No year in title"}
        year = parser._extract_year_with_fallback(fields, "uu-no-2-tahun-2024")
        assert year == 2024

    def test_extract_year_with_fallback_from_date(self, parser: Parser) -> None:
        """Test year extraction falls back to date."""
        fields = {
            "tahun": "1465",
            "tentang": "No year here",
            "tanggal_penetapan": "15 Januari 2023"
        }
        year = parser._extract_year_with_fallback(fields, "some-slug-no-year")
        assert year == 2023

    def test_extract_year_with_fallback_raises_error(self, parser: Parser) -> None:
        """Test year extraction raises error when all strategies fail."""
        fields = {
            "tahun": "1465",  # Invalid
            "tentang": "No year",
        }

        with pytest.raises(ParseError) as exc_info:
            parser._extract_year_with_fallback(fields, "no-year-in-slug")

        assert "Cannot determine valid year" in str(exc_info.value)
