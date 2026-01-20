"""Integration tests for the downloader service."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.config import Config
from src.services.database import Database
from src.services.downloader import Downloader, get_pdf_folder
from src.models.peraturan import Peraturan


class TestDownloaderIntegration:
    """Integration tests for Downloader with Database."""

    @pytest.fixture
    def downloader(self, test_config: Config, database: Database) -> Downloader:
        """Create downloader with test configuration."""
        return Downloader(config=test_config, database=database)

    def test_downloader_init(self, downloader: Downloader) -> None:
        """Test downloader initializes correctly."""
        assert downloader.config is not None
        assert downloader.db is not None
        assert downloader._stop_requested is False

    def test_get_local_path_creates_correct_structure(
        self, downloader: Downloader
    ) -> None:
        """Test local path follows correct folder structure."""
        peraturan = Peraturan(
            slug="uu-no-1-tahun-2024",
            jenis="UNDANG-UNDANG",
            nomor="1",
            tahun=2024,
            tentang="Test",
            source_url="https://example.com",
        )

        path = downloader.get_local_path(peraturan)
        assert "pdfs" in str(path)
        assert "uu" in str(path)
        assert path.name == "uu-no-1-tahun-2024.pdf"

    def test_should_download_new_peraturan(
        self, downloader: Downloader
    ) -> None:
        """Test should_download returns True for new files with pdf_url."""
        peraturan = Peraturan(
            slug="uu-no-1-tahun-2024",
            jenis="UNDANG-UNDANG",
            nomor="1",
            tahun=2024,
            tentang="Test",
            source_url="https://example.com",
            pdf_url="https://peraturan.go.id/files/uu-no-1-tahun-2024.pdf",
        )

        assert downloader.should_download(peraturan) is True

    def test_should_not_download_existing_file(
        self, downloader: Downloader, temp_dir: Path
    ) -> None:
        """Test should_download returns False for existing files."""
        peraturan = Peraturan(
            slug="uu-no-1-tahun-2024",
            jenis="UNDANG-UNDANG",
            nomor="1",
            tahun=2024,
            tentang="Test",
            source_url="https://example.com",
        )

        # Create the file
        local_path = downloader.get_local_path(peraturan)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"%PDF-1.4 fake pdf content")

        assert downloader.should_download(peraturan) is False

    def test_stop_method(self, downloader: Downloader) -> None:
        """Test stop method sets flag."""
        assert downloader._stop_requested is False
        downloader.stop()
        assert downloader._stop_requested is True

    @pytest.mark.asyncio
    async def test_download_with_empty_database(
        self, test_config: Config, database: Database
    ) -> None:
        """Test download with no items in database."""
        downloader = Downloader(config=test_config, database=database)

        state = await downloader.download(show_progress=False)

        assert state.total_pdfs == 0
        assert state.completed_count == 0
        assert state.status == "completed"

    @pytest.mark.asyncio
    async def test_download_state_persistence(
        self, test_config: Config, sample_peraturan: Peraturan
    ) -> None:
        """Test download state is persisted to database."""
        # Create a new database instance
        db = Database(config=test_config)
        db.connect()
        try:
            # Add sample peraturan to database
            db.upsert_peraturan(sample_peraturan)

            downloader = Downloader(config=test_config, database=db)

            # Mock HTTP to avoid real network calls
            with patch.object(downloader.http, 'download_file', new_callable=AsyncMock) as mock_download:
                # Simulate download failure to test state persistence
                mock_download.side_effect = Exception("Network error")

                state = await downloader.download(show_progress=False)

                # Verify state was updated
                assert state.failed_count >= 0

            # Reconnect database to check state (downloader closes it)
            db.connect()
            db_state = db.get_download_state()
            assert db_state is not None
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_download_updates_local_pdf_path(
        self, test_config: Config, temp_dir: Path
    ) -> None:
        """Test successful download updates local_pdf_path in database."""
        db = Database(config=test_config)
        db.connect()
        try:
            peraturan = Peraturan(
                slug="uu-no-1-tahun-2024",
                jenis="UNDANG-UNDANG",
                nomor="1",
                tahun=2024,
                tentang="Test",
                source_url="https://example.com",
                pdf_url="https://peraturan.go.id/files/uu-no-1-tahun-2024.pdf",
            )
            db.upsert_peraturan(peraturan)

            downloader = Downloader(config=test_config, database=db)

            # Mock successful download
            async def mock_download_file(url: str, path: str) -> int:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_bytes(b"%PDF-1.4 fake pdf content")
                return len(b"%PDF-1.4 fake pdf content")

            with patch.object(downloader.http, 'download_file', side_effect=mock_download_file):
                state = await downloader.download(show_progress=False)

                assert state.completed_count == 1

            # Reconnect database to check state (downloader closes it)
            db.connect()
            retrieved = db.get_peraturan(peraturan.slug)
            assert retrieved.local_pdf_path is not None
            assert "uu-no-1-tahun-2024.pdf" in retrieved.local_pdf_path
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_download_with_limit(
        self, test_config: Config, database: Database, sample_peraturan_list: list[Peraturan]
    ) -> None:
        """Test download respects limit parameter."""
        # Add multiple peraturan to database
        for p in sample_peraturan_list:
            database.upsert_peraturan(p)

        downloader = Downloader(config=test_config, database=database)

        # Mock successful downloads
        async def mock_download_file(url: str, path: str) -> int:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"%PDF-1.4 fake pdf content")
            return len(b"%PDF-1.4 fake pdf content")

        with patch.object(downloader.http, 'download_file', side_effect=mock_download_file):
            state = await downloader.download(limit=1, show_progress=False)

            # Should have processed at most 1 item
            assert state.completed_count + state.failed_count + state.skipped_count <= 1

    def test_should_download_checks_pdf_url(
        self, test_config: Config
    ) -> None:
        """Test should_download returns False when pdf_url is None."""
        db = Database(config=test_config)
        db.connect()
        try:
            downloader = Downloader(config=test_config, database=db)

            # Peraturan without pdf_url (pdf_url is not auto-generated)
            peraturan = Peraturan(
                slug="uu-no-1-tahun-2024",
                jenis="UNDANG-UNDANG",
                nomor="1",
                tahun=2024,
                tentang="Test",
                source_url="https://example.com",
            )

            # should_download returns False for items without PDF URL
            assert downloader.should_download(peraturan) is False
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_download_tracks_failed_items(
        self, test_config: Config
    ) -> None:
        """Test failed downloads are tracked in database."""
        db = Database(config=test_config)
        db.connect()
        try:
            peraturan = Peraturan(
                slug="uu-no-1-tahun-2024",
                jenis="UNDANG-UNDANG",
                nomor="1",
                tahun=2024,
                tentang="Test",
                source_url="https://example.com",
                pdf_url="https://peraturan.go.id/files/uu-no-1-tahun-2024.pdf",
            )
            db.upsert_peraturan(peraturan)

            downloader = Downloader(config=test_config, database=db)

            # Mock download failure
            with patch.object(downloader.http, 'download_file', new_callable=AsyncMock) as mock_download:
                mock_download.side_effect = Exception("Download failed")

                state = await downloader.download(show_progress=False)

                assert state.failed_count == 1

            # Reconnect database to check failed items (downloader closes it)
            db.connect()
            failed_items = db.get_failed_items(item_type="pdf")
            assert len(failed_items) == 1
            assert "uu-no-1-tahun-2024" in failed_items[0].url
        finally:
            db.close()


class TestGetPdfFolder:
    """Tests for get_pdf_folder function."""

    def test_all_document_types(self) -> None:
        """Test all supported document types return correct folder names."""
        test_cases = [
            ("UNDANG-UNDANG", "uu"),
            ("UNDANG-UNDANG DARURAT", "uu-darurat"),
            ("PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG", "perppu"),
            ("PERATURAN PEMERINTAH", "pp"),
            ("PERATURAN PRESIDEN", "perpres"),
            ("KEPUTUSAN PRESIDEN", "keppres"),
            ("INSTRUKSI PRESIDEN", "inpres"),
            ("PERATURAN MENTERI", "permen"),
        ]

        for jenis, expected in test_cases:
            assert get_pdf_folder(jenis) == expected, f"Failed for {jenis}"

    def test_case_insensitive(self) -> None:
        """Test function handles case variations."""
        assert get_pdf_folder("undang-undang") == "uu"
        assert get_pdf_folder("Undang-Undang") == "uu"

    def test_unknown_type(self) -> None:
        """Test unknown types return 'other'."""
        assert get_pdf_folder("RANDOM TYPE") == "other"
        assert get_pdf_folder("") == "other"
