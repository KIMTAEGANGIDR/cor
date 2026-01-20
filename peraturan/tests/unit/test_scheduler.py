"""Unit tests for scheduler service."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile
import json

from src.services.scheduler import (
    SchedulerMode,
    SchedulerConfig,
    SchedulerState,
    Scheduler,
    DOCUMENT_TYPES,
)
from src.config import Config


class TestSchedulerMode:
    """Tests for SchedulerMode enum."""

    def test_mode_values(self) -> None:
        """Test mode enum values."""
        assert SchedulerMode.INITIAL.value == "initial"
        assert SchedulerMode.MAINTENANCE.value == "maintenance"
        assert SchedulerMode.AUTO.value == "auto"


class TestSchedulerConfig:
    """Tests for SchedulerConfig dataclass."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = SchedulerConfig()

        # Initial mode settings
        assert config.initial_interval_minutes == 5
        assert config.initial_batch_size == 200
        assert config.initial_delay == 1.5

        # Maintenance mode settings
        assert config.maintenance_interval_minutes == 60
        assert config.maintenance_batch_size == 50
        assert config.maintenance_delay == 3.0

        # Auto switch threshold
        assert config.completion_threshold_percent == 95.0

    def test_custom_config(self) -> None:
        """Test custom configuration."""
        config = SchedulerConfig(
            initial_interval_minutes=3,
            initial_batch_size=300,
            completion_threshold_percent=90.0,
        )

        assert config.initial_interval_minutes == 3
        assert config.initial_batch_size == 300
        assert config.completion_threshold_percent == 90.0

    def test_known_totals_default(self) -> None:
        """Test default known totals are set."""
        config = SchedulerConfig()

        assert "UNDANG-UNDANG" in config.known_totals
        assert "PERATURAN MENTERI" in config.known_totals
        assert config.known_totals["UNDANG-UNDANG"] > 0


class TestSchedulerState:
    """Tests for SchedulerState class."""

    @pytest.fixture
    def temp_state_file(self) -> Path:
        """Create temporary state file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            return Path(f.name)

    def test_initial_state(self, temp_state_file: Path) -> None:
        """Test initial state values."""
        state = SchedulerState(temp_state_file)

        assert state.current_type_index == 0
        assert state.run_count == 0
        assert state.total_crawled == 0
        assert state.current_mode == SchedulerMode.AUTO.value
        assert state.mode_switch_count == 0

    def test_save_and_load(self, temp_state_file: Path) -> None:
        """Test state persistence."""
        state1 = SchedulerState(temp_state_file)
        state1.current_type_index = 3
        state1.run_count = 10
        state1.total_crawled = 500
        state1.current_mode = SchedulerMode.INITIAL.value
        state1.mode_switch_count = 2
        state1.save()

        # Load in new instance
        state2 = SchedulerState(temp_state_file)
        assert state2.current_type_index == 3
        assert state2.run_count == 10
        assert state2.total_crawled == 500
        assert state2.current_mode == SchedulerMode.INITIAL.value
        assert state2.mode_switch_count == 2

    def test_get_current_type(self, temp_state_file: Path) -> None:
        """Test getting current document type."""
        state = SchedulerState(temp_state_file)

        assert state.get_current_type() == DOCUMENT_TYPES[0]

        state.current_type_index = 2
        assert state.get_current_type() == DOCUMENT_TYPES[2]

    def test_advance(self, temp_state_file: Path) -> None:
        """Test advancing to next type."""
        state = SchedulerState(temp_state_file)

        initial_index = state.current_type_index
        state.advance()

        assert state.current_type_index == (initial_index + 1) % len(DOCUMENT_TYPES)
        assert state.run_count == 1
        assert state.last_run is not None


class TestScheduler:
    """Tests for Scheduler class."""

    @pytest.fixture
    def config(self) -> Config:
        """Create test config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config()
            config.data_dir = Path(tmpdir)
            config.db_path = Path(tmpdir) / "test.db"
            yield config

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database."""
        mock = MagicMock()
        mock._connection.cursor.return_value.fetchall.return_value = []
        return mock

    def test_initial_mode_settings(self, config: Config) -> None:
        """Test settings when in INITIAL mode."""
        with patch('src.services.scheduler.Database'):
            scheduler = Scheduler(
                config=config,
                mode=SchedulerMode.INITIAL,
            )

            assert scheduler.interval_minutes == 5
            assert scheduler.batch_size == 200
            assert scheduler.effective_delay == 1.5

    def test_maintenance_mode_settings(self, config: Config) -> None:
        """Test settings when in MAINTENANCE mode."""
        with patch('src.services.scheduler.Database'):
            scheduler = Scheduler(
                config=config,
                mode=SchedulerMode.MAINTENANCE,
            )

            assert scheduler.interval_minutes == 60
            assert scheduler.batch_size == 50
            assert scheduler.effective_delay == 3.0

    def test_explicit_override(self, config: Config) -> None:
        """Test explicit interval/batch override."""
        with patch('src.services.scheduler.Database'):
            scheduler = Scheduler(
                config=config,
                mode=SchedulerMode.INITIAL,
                interval_minutes=15,
                batch_size=100,
            )

            # Explicit values should override mode defaults
            assert scheduler.interval_minutes == 15
            assert scheduler.batch_size == 100

    def test_auto_mode_low_completion(self, config: Config) -> None:
        """Test AUTO mode with low completion starts as INITIAL."""
        with patch('src.services.scheduler.Database') as mock_db_class:
            # Mock low completion (10%)
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [
                {"jenis": "UNDANG-UNDANG", "cnt": 190},  # ~10% of 1907
            ]
            mock_db_class.return_value._connection.cursor.return_value = mock_cursor

            scheduler = Scheduler(
                config=config,
                mode=SchedulerMode.AUTO,
            )

            assert scheduler._effective_mode == SchedulerMode.INITIAL

    def test_auto_mode_high_completion(self, config: Config) -> None:
        """Test AUTO mode with high completion uses MAINTENANCE."""
        with patch('src.services.scheduler.Database') as mock_db_class:
            # Mock high completion (96%)
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [
                {"jenis": "UNDANG-UNDANG", "cnt": 1830},  # 96%
                {"jenis": "PERPPU", "cnt": 209},  # 96%
                {"jenis": "PERATURAN PEMERINTAH", "cnt": 4773},  # 96%
                {"jenis": "PERATURAN PRESIDEN", "cnt": 2513},  # 96%
                {"jenis": "PERATURAN MENTERI", "cnt": 18934},  # 96%
                {"jenis": "PERATURAN BADAN/LEMBAGA", "cnt": 6350},  # 96%
                {"jenis": "PERATURAN DAERAH", "cnt": 4800},  # 96%
            ]
            mock_db_class.return_value._connection.cursor.return_value = mock_cursor

            scheduler = Scheduler(
                config=config,
                mode=SchedulerMode.AUTO,
            )

            assert scheduler._effective_mode == SchedulerMode.MAINTENANCE

    def test_get_status_includes_mode(self, config: Config) -> None:
        """Test get_status includes mode information."""
        with patch('src.services.scheduler.Database') as mock_db_class:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = []
            mock_db_class.return_value._connection.cursor.return_value = mock_cursor

            scheduler = Scheduler(
                config=config,
                mode=SchedulerMode.AUTO,
            )

            status = scheduler.get_status()

            assert "mode" in status
            assert "effective_mode" in status
            assert "completion_percent" in status
            assert "effective_delay" in status
            assert "mode_switch_count" in status
            assert status["mode"] == "auto"

    def test_mode_switch_detection(self, config: Config) -> None:
        """Test mode switch is detected correctly."""
        with patch('src.services.scheduler.Database') as mock_db_class:
            # Start with low completion
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [
                {"jenis": "UNDANG-UNDANG", "cnt": 190},  # 10%
            ]
            mock_db_class.return_value._connection.cursor.return_value = mock_cursor

            scheduler = Scheduler(
                config=config,
                mode=SchedulerMode.AUTO,
            )

            assert scheduler._effective_mode == SchedulerMode.INITIAL

            # Simulate high completion
            mock_cursor.fetchall.return_value = [
                {"jenis": "UNDANG-UNDANG", "cnt": 1830},  # 96%
                {"jenis": "PERPPU", "cnt": 209},  # 96%
                {"jenis": "PERATURAN PEMERINTAH", "cnt": 4773},  # 96%
                {"jenis": "PERATURAN PRESIDEN", "cnt": 2513},  # 96%
                {"jenis": "PERATURAN MENTERI", "cnt": 18934},  # 96%
                {"jenis": "PERATURAN BADAN/LEMBAGA", "cnt": 6350},  # 96%
                {"jenis": "PERATURAN DAERAH", "cnt": 4800},  # 96%
            ]

            # Check for mode switch
            switched = scheduler._check_mode_switch()

            assert switched is True
            assert scheduler._effective_mode == SchedulerMode.MAINTENANCE
            assert scheduler.state.mode_switch_count == 1


class TestSchedulerMinInterval:
    """Tests for minimum interval enforcement."""

    @pytest.fixture
    def config(self) -> Config:
        """Create test config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config()
            config.data_dir = Path(tmpdir)
            config.db_path = Path(tmpdir) / "test.db"
            yield config

    def test_explicit_interval_minimum(self, config: Config) -> None:
        """Test explicit interval respects minimum."""
        with patch('src.services.scheduler.Database'):
            scheduler = Scheduler(
                config=config,
                mode=SchedulerMode.INITIAL,
                interval_minutes=1,  # Below minimum
                batch_size=50,
            )

            # Should be clamped to MIN_INTERVAL_MINUTES (5)
            assert scheduler.interval_minutes >= 5
