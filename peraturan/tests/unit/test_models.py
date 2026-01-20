"""Unit tests for data models."""

import pytest

from src.models.peraturan import Peraturan
from src.models.state import CrawlState, DownloadState, FailedItem, StateStatus


class TestPeraturan:
    """Tests for Peraturan model."""

    def test_create_peraturan(self, sample_peraturan: Peraturan) -> None:
        """Test creating a peraturan instance."""
        assert sample_peraturan.slug == "uu-no-2-tahun-2025"
        assert sample_peraturan.jenis == "UNDANG-UNDANG"
        assert sample_peraturan.nomor == "2"
        assert sample_peraturan.tahun == 2025
        assert sample_peraturan.status == "Berlaku"

    def test_peraturan_pdf_url_not_auto_generated(self) -> None:
        """Test that PDF URL is NOT auto-generated (extracted from detail page instead)."""
        p = Peraturan(
            slug="uu-no-1-tahun-2024",
            jenis="UNDANG-UNDANG",
            nomor="1",
            tahun=2024,
            tentang="TEST",
            source_url="https://peraturan.go.id/id/uu-no-1-tahun-2024",
        )
        # PDF URL should be None - it's extracted from detail page by parser
        assert p.pdf_url is None

    def test_peraturan_display_name(self, sample_peraturan: Peraturan) -> None:
        """Test display name property."""
        assert sample_peraturan.display_name == "UNDANG-UNDANG No. 2 Tahun 2025"

    def test_peraturan_short_type(self) -> None:
        """Test short type property for various document types."""
        test_cases = [
            ("UNDANG-UNDANG", "uu"),
            ("UNDANG-UNDANG DARURAT", "uu-darurat"),
            ("PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG", "perppu"),
            ("PERATURAN PEMERINTAH", "pp"),
            ("PERATURAN PRESIDEN", "perpres"),
            ("KEPUTUSAN PRESIDEN", "keppres"),
            ("INSTRUKSI PRESIDEN", "inpres"),
            ("PERATURAN MENTERI", "permen"),
            ("UNKNOWN TYPE", "other"),
        ]

        for jenis, expected in test_cases:
            p = Peraturan(
                slug=f"test-{jenis.lower().replace(' ', '-')}",
                jenis=jenis,
                nomor="1",
                tahun=2024,
                tentang="TEST",
                source_url="https://example.com",
            )
            assert p.short_type == expected

    def test_peraturan_invalid_year(self) -> None:
        """Test that invalid year raises ValueError."""
        with pytest.raises(ValueError, match="Invalid year"):
            Peraturan(
                slug="test",
                jenis="UU",
                nomor="1",
                tahun=1900,  # Too old
                tentang="TEST",
                source_url="https://example.com",
            )

    def test_peraturan_to_dict(self, sample_peraturan: Peraturan) -> None:
        """Test converting to dictionary."""
        data = sample_peraturan.to_dict()
        assert data["slug"] == "uu-no-2-tahun-2025"
        assert data["jenis"] == "UNDANG-UNDANG"
        assert "created_at" in data

    def test_peraturan_from_dict(self) -> None:
        """Test creating from dictionary."""
        data = {
            "slug": "pp-no-5-tahun-2024",
            "jenis": "PERATURAN PEMERINTAH",
            "nomor": "5",
            "tahun": 2024,
            "tentang": "TEST PERATURAN",
            "source_url": "https://peraturan.go.id/id/pp-no-5-tahun-2024",
            "status": "Berlaku",
        }
        p = Peraturan.from_dict(data)
        assert p.slug == "pp-no-5-tahun-2024"
        assert p.tahun == 2024


class TestCrawlState:
    """Tests for CrawlState model."""

    def test_default_state(self) -> None:
        """Test default crawl state values."""
        state = CrawlState()
        assert state.status == StateStatus.IDLE.value
        assert state.last_page == 0
        assert state.total_count == 0
        assert state.completed_count == 0

    def test_start_crawl(self) -> None:
        """Test starting crawl state."""
        state = CrawlState()
        state.start()
        assert state.status == StateStatus.RUNNING.value
        assert state.started_at is not None

    def test_update_progress(self) -> None:
        """Test updating crawl progress."""
        state = CrawlState()
        state.start()
        state.update_progress(page=10, completed=200, failed=5)
        assert state.last_page == 10
        assert state.completed_count == 200
        assert state.failed_count == 5

    def test_complete_crawl(self) -> None:
        """Test completing crawl."""
        state = CrawlState()
        state.start()
        state.complete()
        assert state.status == StateStatus.COMPLETED.value
        assert state.completed_at is not None

    def test_progress_percent(self) -> None:
        """Test progress percentage calculation."""
        state = CrawlState(total_count=1000, completed_count=250)
        assert state.progress_percent == 25.0

    def test_progress_percent_zero_total(self) -> None:
        """Test progress percentage with zero total."""
        state = CrawlState(total_count=0, completed_count=0)
        assert state.progress_percent == 0.0


class TestDownloadState:
    """Tests for DownloadState model."""

    def test_default_state(self) -> None:
        """Test default download state values."""
        state = DownloadState()
        assert state.status == StateStatus.IDLE.value
        assert state.total_pdfs == 0

    def test_start_download(self) -> None:
        """Test starting download."""
        state = DownloadState()
        state.start(total=5000)
        assert state.status == StateStatus.RUNNING.value
        assert state.total_pdfs == 5000

    def test_update_download_progress(self) -> None:
        """Test updating download progress."""
        state = DownloadState()
        state.start(total=1000)
        state.update_progress(completed=300, skipped=100, failed=10)
        assert state.completed_count == 300
        assert state.skipped_count == 100
        assert state.failed_count == 10

    def test_download_progress_percent(self) -> None:
        """Test download progress percentage."""
        state = DownloadState(total_pdfs=1000, completed_count=200, skipped_count=100)
        assert state.progress_percent == 30.0  # (200 + 100) / 1000


class TestFailedItem:
    """Tests for FailedItem model."""

    def test_create_failed_item(self) -> None:
        """Test creating a failed item."""
        item = FailedItem(
            url="https://example.com/test",
            item_type="metadata",
            error_message="Connection timeout",
        )
        assert item.url == "https://example.com/test"
        assert item.retry_count == 0
        assert item.failed_at is not None

    def test_increment_retry(self) -> None:
        """Test incrementing retry count."""
        item = FailedItem(url="https://example.com", item_type="pdf")
        item.increment_retry()
        assert item.retry_count == 1
        assert item.last_retry_at is not None

        item.increment_retry()
        assert item.retry_count == 2
