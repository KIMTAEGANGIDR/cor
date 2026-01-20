"""Unit tests for PDF downloader."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.downloader import Downloader, get_pdf_folder
from src.models.peraturan import Peraturan
from src.config import Config


class TestDownloader:
    """Tests for PDF downloader service."""

    def test_get_pdf_folder_uu(self) -> None:
        """Test folder name for UU documents."""
        assert get_pdf_folder("UNDANG-UNDANG") == "uu"

    def test_get_pdf_folder_pp(self) -> None:
        """Test folder name for PP documents."""
        assert get_pdf_folder("PERATURAN PEMERINTAH") == "pp"

    def test_get_pdf_folder_perpres(self) -> None:
        """Test folder name for Perpres documents."""
        assert get_pdf_folder("PERATURAN PRESIDEN") == "perpres"

    def test_get_pdf_folder_permen(self) -> None:
        """Test folder name for Permen documents."""
        assert get_pdf_folder("PERATURAN MENTERI") == "permen"

    def test_get_pdf_folder_keppres(self) -> None:
        """Test folder name for Keppres documents."""
        assert get_pdf_folder("KEPUTUSAN PRESIDEN") == "keppres"

    def test_get_pdf_folder_inpres(self) -> None:
        """Test folder name for Inpres documents."""
        assert get_pdf_folder("INSTRUKSI PRESIDEN") == "inpres"

    def test_get_pdf_folder_perppu(self) -> None:
        """Test folder name for Perppu documents."""
        assert get_pdf_folder("PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG") == "perppu"

    def test_get_pdf_folder_unknown(self) -> None:
        """Test folder name for unknown document types."""
        assert get_pdf_folder("UNKNOWN TYPE") == "other"

    def test_get_local_path(self, temp_dir: Path) -> None:
        """Test generating local PDF path."""
        config = Config(data_dir=temp_dir)
        downloader = Downloader(config=config)

        peraturan = Peraturan(
            slug="uu-no-2-tahun-2025",
            jenis="UNDANG-UNDANG",
            nomor="2",
            tahun=2025,
            tentang="Test",
            source_url="https://example.com",
        )

        path = downloader.get_local_path(peraturan)
        assert path == temp_dir / "pdfs" / "uu" / "uu-no-2-tahun-2025.pdf"

    def test_get_local_path_pp(self, temp_dir: Path) -> None:
        """Test local path for PP documents."""
        config = Config(data_dir=temp_dir)
        downloader = Downloader(config=config)

        peraturan = Peraturan(
            slug="pp-no-10-tahun-2024",
            jenis="PERATURAN PEMERINTAH",
            nomor="10",
            tahun=2024,
            tentang="Test PP",
            source_url="https://example.com",
        )

        path = downloader.get_local_path(peraturan)
        assert path == temp_dir / "pdfs" / "pp" / "pp-no-10-tahun-2024.pdf"

    def test_should_download_new_file(self, temp_dir: Path) -> None:
        """Test that new files should be downloaded."""
        config = Config(data_dir=temp_dir)
        downloader = Downloader(config=config)

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

    def test_should_not_download_existing_file(self, temp_dir: Path) -> None:
        """Test that existing files should not be downloaded."""
        config = Config(data_dir=temp_dir)
        downloader = Downloader(config=config)

        peraturan = Peraturan(
            slug="uu-no-1-tahun-2024",
            jenis="UNDANG-UNDANG",
            nomor="1",
            tahun=2024,
            tentang="Test",
            source_url="https://example.com",
        )

        # Create the file
        pdf_dir = temp_dir / "pdfs" / "uu"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / "uu-no-1-tahun-2024.pdf"
        pdf_path.write_bytes(b"fake pdf content")

        assert downloader.should_download(peraturan) is False

    def test_should_download_empty_file(self, temp_dir: Path) -> None:
        """Test that empty files should be re-downloaded."""
        config = Config(data_dir=temp_dir)
        downloader = Downloader(config=config)

        peraturan = Peraturan(
            slug="uu-no-1-tahun-2024",
            jenis="UNDANG-UNDANG",
            nomor="1",
            tahun=2024,
            tentang="Test",
            source_url="https://example.com",
            pdf_url="https://peraturan.go.id/files/uu-no-1-tahun-2024.pdf",
        )

        # Create empty file
        pdf_dir = temp_dir / "pdfs" / "uu"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / "uu-no-1-tahun-2024.pdf"
        pdf_path.write_bytes(b"")

        assert downloader.should_download(peraturan) is True

    def test_should_not_download_no_pdf_url(self, temp_dir: Path) -> None:
        """Test that documents without PDF URL should not be downloaded."""
        config = Config(data_dir=temp_dir)
        downloader = Downloader(config=config)

        peraturan = Peraturan(
            slug="uu-no-1-tahun-2024",
            jenis="UNDANG-UNDANG",
            nomor="1",
            tahun=2024,
            tentang="Test",
            source_url="https://example.com",
            pdf_url=None,
        )
        # Override auto-generated URL
        peraturan.pdf_url = None

        assert downloader.should_download(peraturan) is False
