"""Pytest fixtures for Peraturan Crawler tests."""

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from src.config import Config
from src.services.database import Database
from src.models.peraturan import Peraturan


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_config(temp_dir: Path) -> Config:
    """Create a test configuration with temporary paths."""
    return Config(
        db_path=temp_dir / "test.db",
        data_dir=temp_dir,
        log_level="DEBUG",
        delay=0.1,  # Faster for tests
    )


@pytest.fixture
def database(test_config: Config) -> Generator[Database, None, None]:
    """Create a test database."""
    db = Database(config=test_config)
    db.connect()
    yield db
    db.close()


@pytest.fixture
def sample_peraturan() -> Peraturan:
    """Create a sample peraturan for testing."""
    return Peraturan(
        slug="uu-no-2-tahun-2025",
        jenis="UNDANG-UNDANG",
        nomor="2",
        tahun=2025,
        tentang="PERUBAHAN KEEMPAT ATAS UNDANG-UNDANG NOMOR 4 TAHUN 2009",
        source_url="https://peraturan.go.id/id/uu-no-2-tahun-2025",
        pemrakarsa="PEMERINTAH PUSAT",
        tempat_penetapan="Jakarta",
        tanggal_penetapan="2025-01-15",
        status="Berlaku",
    )


@pytest.fixture
def sample_peraturan_list() -> list[Peraturan]:
    """Create a list of sample peraturan for testing."""
    return [
        Peraturan(
            slug="uu-no-1-tahun-2024",
            jenis="UNDANG-UNDANG",
            nomor="1",
            tahun=2024,
            tentang="ANGGARAN PENDAPATAN DAN BELANJA NEGARA",
            source_url="https://peraturan.go.id/id/uu-no-1-tahun-2024",
            status="Berlaku",
        ),
        Peraturan(
            slug="pp-no-10-tahun-2024",
            jenis="PERATURAN PEMERINTAH",
            nomor="10",
            tahun=2024,
            tentang="PERATURAN PELAKSANAAN",
            source_url="https://peraturan.go.id/id/pp-no-10-tahun-2024",
            status="Berlaku",
        ),
        Peraturan(
            slug="perpres-no-5-tahun-2023",
            jenis="PERATURAN PRESIDEN",
            nomor="5",
            tahun=2023,
            tentang="PENETAPAN HARI LIBUR NASIONAL",
            source_url="https://peraturan.go.id/id/perpres-no-5-tahun-2023",
            status="Tidak Berlaku",
        ),
    ]


# HTML fixture for parser tests
SAMPLE_DETAIL_HTML = """
<!DOCTYPE html>
<html>
<head><title>Detail Peraturan</title></head>
<body>
<div class="detail-peraturan">
    <h1>UNDANG-UNDANG NOMOR 2 TAHUN 2025</h1>
    <table class="table-detail">
        <tr><th>Jenis</th><td>UNDANG-UNDANG</td></tr>
        <tr><th>Nomor</th><td>2</td></tr>
        <tr><th>Tahun</th><td>2025</td></tr>
        <tr><th>Tentang</th><td>PERUBAHAN KEEMPAT ATAS UNDANG-UNDANG NOMOR 4 TAHUN 2009</td></tr>
        <tr><th>Pemrakarsa</th><td>PEMERINTAH PUSAT</td></tr>
        <tr><th>Tempat Penetapan</th><td>Jakarta</td></tr>
        <tr><th>Tanggal Penetapan</th><td>15 Januari 2025</td></tr>
        <tr><th>Status</th><td>Berlaku</td></tr>
    </table>
</div>
</body>
</html>
"""

SAMPLE_LIST_HTML = """
<!DOCTYPE html>
<html>
<head><title>Daftar Peraturan</title></head>
<body>
<div class="list-peraturan">
    <table class="table-list">
        <tr>
            <td><a href="/id/uu-no-2-tahun-2025">UU No. 2 Tahun 2025</a></td>
            <td>PERUBAHAN KEEMPAT...</td>
        </tr>
        <tr>
            <td><a href="/id/uu-no-1-tahun-2024">UU No. 1 Tahun 2024</a></td>
            <td>ANGGARAN PENDAPATAN...</td>
        </tr>
    </table>
    <div class="pagination">
        <a href="?page=1">1</a>
        <a href="?page=2">2</a>
        <a href="?page=3">3</a>
    </div>
</div>
</body>
</html>
"""


@pytest.fixture
def sample_detail_html() -> str:
    """Sample detail page HTML for parser tests."""
    return SAMPLE_DETAIL_HTML


@pytest.fixture
def sample_list_html() -> str:
    """Sample list page HTML for parser tests."""
    return SAMPLE_LIST_HTML
