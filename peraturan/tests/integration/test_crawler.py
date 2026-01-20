"""Integration tests for the crawler service."""

import pytest
from pathlib import Path

from src.config import Config
from src.services.database import Database
from src.services.crawler import Crawler
from src.models.peraturan import Peraturan


class TestCrawlerIntegration:
    """Integration tests for Crawler with Database."""

    @pytest.fixture
    def crawler(self, test_config: Config, database: Database) -> Crawler:
        """Create crawler with test configuration."""
        return Crawler(config=test_config, database=database)

    def test_database_connection(self, database: Database) -> None:
        """Test database connects and creates tables."""
        assert database._connection is not None
        stats = database.get_statistics()
        assert stats["total"] == 0

    def test_upsert_and_retrieve_peraturan(
        self, database: Database, sample_peraturan: Peraturan
    ) -> None:
        """Test inserting and retrieving a peraturan."""
        database.upsert_peraturan(sample_peraturan)

        retrieved = database.get_peraturan(sample_peraturan.slug)
        assert retrieved is not None
        assert retrieved.slug == sample_peraturan.slug
        assert retrieved.jenis == sample_peraturan.jenis
        assert retrieved.tahun == sample_peraturan.tahun

    def test_upsert_updates_existing(
        self, database: Database, sample_peraturan: Peraturan
    ) -> None:
        """Test that upsert updates existing records."""
        database.upsert_peraturan(sample_peraturan)

        # Modify and upsert again
        sample_peraturan.tentang = "UPDATED TITLE"
        database.upsert_peraturan(sample_peraturan)

        retrieved = database.get_peraturan(sample_peraturan.slug)
        assert retrieved.tentang == "UPDATED TITLE"

        # Should still be only one record
        assert database.count_peraturan() == 1

    def test_multiple_peraturan(
        self, database: Database, sample_peraturan_list: list[Peraturan]
    ) -> None:
        """Test storing multiple peraturan records."""
        for p in sample_peraturan_list:
            database.upsert_peraturan(p)

        assert database.count_peraturan() == 3

        # Filter by type
        uu_count = database.count_peraturan(jenis="UNDANG-UNDANG")
        assert uu_count == 1

    def test_crawl_state_persistence(self, database: Database) -> None:
        """Test crawl state is persisted correctly."""
        state = database.get_crawl_state()
        assert state.status == "idle"

        state.start()
        state.update_progress(page=10, completed=200, failed=5)
        database.update_crawl_state(state)

        # Retrieve fresh
        retrieved = database.get_crawl_state()
        assert retrieved.last_page == 10
        assert retrieved.completed_count == 200
        assert retrieved.failed_count == 5
        assert retrieved.status == "running"

    def test_failed_items_tracking(self, database: Database) -> None:
        """Test failed items are tracked correctly."""
        from src.models.state import FailedItem

        item = FailedItem(
            url="https://example.com/test",
            item_type="metadata",
            error_message="Connection timeout",
        )
        database.add_failed_item(item)

        failed = database.get_failed_items(item_type="metadata")
        assert len(failed) == 1
        assert failed[0].url == "https://example.com/test"

        # Increment retry
        item.increment_retry()
        database.add_failed_item(item)

        failed = database.get_failed_items()
        assert failed[0].retry_count == 1

    def test_update_local_pdf_path(
        self, database: Database, sample_peraturan: Peraturan
    ) -> None:
        """Test updating local PDF path."""
        database.upsert_peraturan(sample_peraturan)

        database.update_local_pdf_path(
            sample_peraturan.slug,
            "/data/pdfs/uu/uu-no-2-tahun-2025.pdf"
        )

        retrieved = database.get_peraturan(sample_peraturan.slug)
        assert retrieved.local_pdf_path == "/data/pdfs/uu/uu-no-2-tahun-2025.pdf"

    def test_statistics(
        self, database: Database, sample_peraturan_list: list[Peraturan]
    ) -> None:
        """Test statistics generation."""
        for p in sample_peraturan_list:
            database.upsert_peraturan(p)

        # Add PDF path to one
        database.update_local_pdf_path(
            sample_peraturan_list[0].slug,
            "/data/pdfs/uu/test.pdf"
        )

        stats = database.get_statistics()
        assert stats["total"] == 3
        assert stats["with_pdf"] == 1
        assert "UNDANG-UNDANG" in stats["by_type"]
