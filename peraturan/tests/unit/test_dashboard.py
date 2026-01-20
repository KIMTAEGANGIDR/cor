"""Unit tests for dashboard service."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

from src.services.dashboard import (
    TypeProgress,
    DashboardStats,
    Dashboard,
)
from src.services.health import HealthStatus
from src.config import Config


class TestTypeProgress:
    """Tests for TypeProgress dataclass."""

    def test_create_progress(self) -> None:
        """Test creating type progress."""
        progress = TypeProgress(
            jenis="UNDANG-UNDANG",
            crawled=500,
            expected=1000,
            percent=50.0,
        )

        assert progress.jenis == "UNDANG-UNDANG"
        assert progress.crawled == 500
        assert progress.expected == 1000
        assert progress.percent == 50.0

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        progress = TypeProgress(
            jenis="UNDANG-UNDANG",
            crawled=500,
            expected=1000,
            percent=50.0,
            last_updated=datetime(2025, 1, 1, 12, 0, 0),
        )

        data = progress.to_dict()
        assert data["jenis"] == "UNDANG-UNDANG"
        assert data["crawled"] == 500
        assert data["expected"] == 1000
        assert data["percent"] == 50.0
        assert data["last_updated"] == "2025-01-01T12:00:00"

    def test_to_dict_no_last_updated(self) -> None:
        """Test to_dict with no last_updated."""
        progress = TypeProgress(
            jenis="TEST",
            crawled=0,
            expected=100,
            percent=0.0,
        )

        data = progress.to_dict()
        assert data["last_updated"] is None


class TestDashboardStats:
    """Tests for DashboardStats dataclass."""

    def test_create_stats(self) -> None:
        """Test creating dashboard stats."""
        stats = DashboardStats(
            session_start=datetime(2025, 1, 1, 10, 0, 0),
            items_per_hour=100.5,
            estimated_completion=datetime(2025, 1, 2, 10, 0, 0),
            total_crawled=5000,
            total_expected=10000,
            completion_percent=50.0,
            type_progress={},
            is_healthy=True,
        )

        assert stats.items_per_hour == 100.5
        assert stats.total_crawled == 5000
        assert stats.completion_percent == 50.0
        assert stats.is_healthy is True

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        type_progress = {
            "TEST": TypeProgress(
                jenis="TEST",
                crawled=50,
                expected=100,
                percent=50.0,
            )
        }

        stats = DashboardStats(
            session_start=datetime(2025, 1, 1, 10, 0, 0),
            items_per_hour=100.0,
            estimated_completion=datetime(2025, 1, 1, 20, 0, 0),
            total_crawled=50,
            total_expected=100,
            completion_percent=50.0,
            type_progress=type_progress,
            is_healthy=True,
            health_status="healthy",
            errors_count=0,
        )

        data = stats.to_dict()
        assert data["session_start"] == "2025-01-01T10:00:00"
        assert data["items_per_hour"] == 100.0
        assert data["estimated_completion"] == "2025-01-01T20:00:00"
        assert data["total_crawled"] == 50
        assert data["completion_percent"] == 50.0
        assert "TEST" in data["type_progress"]

    def test_to_dict_no_eta(self) -> None:
        """Test to_dict with no ETA."""
        stats = DashboardStats(
            session_start=datetime.now(),
            items_per_hour=0.0,
            estimated_completion=None,
            total_crawled=0,
            total_expected=100,
            completion_percent=0.0,
            type_progress={},
            is_healthy=True,
        )

        data = stats.to_dict()
        assert data["estimated_completion"] is None


class TestDashboard:
    """Tests for Dashboard class."""

    @pytest.fixture
    def config(self) -> Config:
        """Create test config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Config()
            cfg.data_dir = Path(tmpdir)
            cfg.db_path = Path(tmpdir) / "test.db"
            yield cfg

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database."""
        mock = MagicMock()
        mock.get_count_by_jenis.return_value = 100
        return mock

    def test_start_session(self, config: Config) -> None:
        """Test starting dashboard session."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            mock_db_class.return_value.get_count_by_jenis.return_value = 0

            dashboard = Dashboard(config=config)
            dashboard.start_session()

            assert dashboard._session_start is not None
            assert isinstance(dashboard._initial_counts, dict)

    def test_get_stats_initializes_session(self, config: Config) -> None:
        """Test get_stats initializes session if needed."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            mock_db_class.return_value.get_count_by_jenis.return_value = 100

            dashboard = Dashboard(config=config)
            assert dashboard._session_start is None

            stats = dashboard.get_stats()

            assert dashboard._session_start is not None
            assert isinstance(stats, DashboardStats)

    def test_get_stats_calculates_progress(self, config: Config) -> None:
        """Test get_stats calculates progress correctly."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            # Mock different counts for different types
            def get_count(jenis):
                if jenis == "UNDANG-UNDANG":
                    return 1000
                return 500

            mock_db_class.return_value.get_count_by_jenis.side_effect = get_count

            dashboard = Dashboard(config=config)
            dashboard.start_session()

            # Simulate some items were crawled this session
            dashboard._initial_counts["UNDANG-UNDANG"] = 900
            dashboard._initial_counts["PERPPU"] = 500

            stats = dashboard.get_stats()

            assert stats.total_crawled > 0
            assert "UNDANG-UNDANG" in stats.type_progress

    def test_get_stats_with_health_monitor(self, config: Config) -> None:
        """Test get_stats uses health monitor status."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            mock_db_class.return_value.get_count_by_jenis.return_value = 100

            # Create mock health monitor
            mock_monitor = MagicMock()
            mock_monitor.get_status.return_value = HealthStatus.WARNING
            mock_monitor.metrics.errors_count = 5
            mock_monitor.metrics.last_activity = datetime.now()

            dashboard = Dashboard(config=config, health_monitor=mock_monitor)
            stats = dashboard.get_stats()

            assert stats.is_healthy is False
            assert stats.health_status == "warning"
            assert stats.errors_count == 5

    def test_get_compact_status(self, config: Config) -> None:
        """Test compact status string."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            mock_db_class.return_value.get_count_by_jenis.return_value = 100

            dashboard = Dashboard(config=config)
            status = dashboard.get_compact_status()

            assert isinstance(status, str)
            assert "/" in status  # Contains progress ratio
            assert "%" in status  # Contains percentage

    def test_render_table(self, config: Config) -> None:
        """Test render table creates valid table."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            mock_db_class.return_value.get_count_by_jenis.return_value = 100

            dashboard = Dashboard(config=config)
            stats = dashboard.get_stats()

            table = dashboard.render_table(stats)

            # Check table was created
            assert table is not None
            assert table.title == "Document Type Progress"

    def test_render_summary(self, config: Config) -> None:
        """Test render summary creates valid panel."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            mock_db_class.return_value.get_count_by_jenis.return_value = 100

            dashboard = Dashboard(config=config)
            stats = dashboard.get_stats()

            panel = dashboard.render_summary(stats)

            # Check panel was created
            assert panel is not None
            assert panel.title == "Summary"


class TestDashboardCalculations:
    """Tests for dashboard calculations."""

    @pytest.fixture
    def config(self) -> Config:
        """Create test config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Config()
            cfg.data_dir = Path(tmpdir)
            cfg.db_path = Path(tmpdir) / "test.db"
            yield cfg

    def test_items_per_hour_calculation(self, config: Config) -> None:
        """Test items per hour is calculated correctly."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            # Return 100 for each document type
            mock_db_class.return_value.get_count_by_jenis.return_value = 100

            dashboard = Dashboard(config=config)

            # Start session 1 hour ago
            dashboard._session_start = datetime.now() - timedelta(hours=1)
            # Initialize with 0 for all types (6 types * 100 = 600 items crawled)
            # Note: PERATURAN DAERAH excluded from scope
            dashboard._initial_counts = {jenis: 0 for jenis in [
                "UNDANG-UNDANG", "PERPPU", "PERATURAN PEMERINTAH",
                "PERATURAN PRESIDEN", "PERATURAN MENTERI",
                "PERATURAN BADAN/LEMBAGA"
            ]}

            stats = dashboard.get_stats()

            # Should show 600 items/hour (6 types * 100 items each)
            assert stats.items_per_hour == pytest.approx(600.0, rel=0.1)

    def test_completion_percent_capped_at_100(self, config: Config) -> None:
        """Test completion percent doesn't exceed 100%."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            # More items than expected
            mock_db_class.return_value.get_count_by_jenis.return_value = 2000

            dashboard = Dashboard(config=config)
            stats = dashboard.get_stats()

            # Per-type progress should be capped at 100%
            for progress in stats.type_progress.values():
                assert progress.percent <= 100.0

    def test_eta_calculation(self, config: Config) -> None:
        """Test ETA is calculated when items are being crawled."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            # Start with 0, now at 100
            mock_db_class.return_value.get_count_by_jenis.return_value = 100

            dashboard = Dashboard(config=config)
            dashboard._session_start = datetime.now() - timedelta(hours=1)
            dashboard._initial_counts = {"UNDANG-UNDANG": 0, "PERPPU": 0}

            stats = dashboard.get_stats()

            # Should have an ETA since we have items per hour
            # (may be None if already complete or no items remaining)
            if stats.total_expected > stats.total_crawled and stats.items_per_hour > 0:
                assert stats.estimated_completion is not None
                assert stats.estimated_completion > datetime.now()

    def test_eta_none_when_no_progress(self, config: Config) -> None:
        """Test ETA is None when no progress."""
        with patch('src.services.dashboard.Database') as mock_db_class:
            mock_db_class.return_value.get_count_by_jenis.return_value = 0

            dashboard = Dashboard(config=config)
            dashboard.start_session()

            stats = dashboard.get_stats()

            # No progress means no ETA
            assert stats.estimated_completion is None
