"""Unit tests for health monitoring service."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
import asyncio

from src.services.health import (
    HealthMetrics,
    HealthConfig,
    HealthMonitor,
    HealthStatus,
    CrawlerHealthMonitor,
)


class TestHealthMetrics:
    """Tests for HealthMetrics dataclass."""

    def test_initial_state(self) -> None:
        """Test initial metrics state."""
        metrics = HealthMetrics()

        assert metrics.last_activity is None
        assert metrics.last_successful_request is None
        assert metrics.requests_count == 0
        assert metrics.errors_count == 0

    def test_record_activity(self) -> None:
        """Test recording activity."""
        metrics = HealthMetrics()
        metrics.record_activity()

        assert metrics.last_activity is not None
        assert (datetime.now() - metrics.last_activity).seconds < 1

    def test_record_success(self) -> None:
        """Test recording successful request."""
        metrics = HealthMetrics()
        metrics.record_success()

        assert metrics.last_activity is not None
        assert metrics.last_successful_request is not None
        assert metrics.requests_count == 1

    def test_record_error(self) -> None:
        """Test recording error."""
        metrics = HealthMetrics()
        metrics.record_error("Connection timeout")

        assert metrics.last_error is not None
        assert metrics.last_error_message == "Connection timeout"
        assert metrics.errors_count == 1

    def test_record_item_processed(self) -> None:
        """Test recording item processed."""
        metrics = HealthMetrics()
        metrics.record_item_processed()

        assert metrics.items_processed == 1
        assert metrics.last_activity is not None

    def test_record_stall(self) -> None:
        """Test recording stall."""
        metrics = HealthMetrics()
        metrics.record_stall()

        assert metrics.stall_count == 1

    def test_to_dict(self) -> None:
        """Test converting metrics to dictionary."""
        metrics = HealthMetrics()
        metrics.record_success()
        metrics.record_error("Test error")

        result = metrics.to_dict()

        assert "last_activity" in result
        assert "requests_count" in result
        assert result["requests_count"] == 1
        assert result["errors_count"] == 1


class TestHealthConfig:
    """Tests for HealthConfig dataclass."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = HealthConfig()

        assert config.stall_threshold_seconds == 300
        assert config.warning_threshold_seconds == 120
        assert config.max_consecutive_errors == 10
        assert config.check_interval_seconds == 30
        assert config.auto_recovery is True

    def test_custom_config(self) -> None:
        """Test custom configuration."""
        config = HealthConfig(
            stall_threshold_seconds=60,
            max_consecutive_errors=5,
        )

        assert config.stall_threshold_seconds == 60
        assert config.max_consecutive_errors == 5


class TestHealthMonitor:
    """Tests for HealthMonitor class."""

    @pytest.fixture
    def monitor(self) -> HealthMonitor:
        """Create health monitor instance."""
        config = HealthConfig(
            stall_threshold_seconds=10,
            warning_threshold_seconds=5,
            max_consecutive_errors=3,
            check_interval_seconds=1,
        )
        return HealthMonitor(config=config)

    def test_initial_status_healthy(self, monitor: HealthMonitor) -> None:
        """Test initial status is healthy."""
        assert monitor.get_status() == HealthStatus.HEALTHY

    def test_record_success_resets_errors(self, monitor: HealthMonitor) -> None:
        """Test that success resets consecutive error count."""
        monitor.record_error("Error 1")
        monitor.record_error("Error 2")
        assert monitor._consecutive_errors == 2

        monitor.record_success()
        assert monitor._consecutive_errors == 0

    def test_is_stalled_false_initially(self, monitor: HealthMonitor) -> None:
        """Test is_stalled is False initially."""
        assert not monitor.is_stalled()

    def test_is_stalled_after_threshold(self, monitor: HealthMonitor) -> None:
        """Test is_stalled after threshold exceeded."""
        # Record activity in the past
        monitor.metrics.last_activity = datetime.now() - timedelta(seconds=15)

        assert monitor.is_stalled()
        assert monitor.get_status() == HealthStatus.STALLED

    def test_warning_status(self, monitor: HealthMonitor) -> None:
        """Test warning status before stall."""
        # Activity 7 seconds ago (after warning, before stall)
        monitor.metrics.last_activity = datetime.now() - timedelta(seconds=7)

        assert monitor.get_status() == HealthStatus.WARNING
        assert not monitor.is_stalled()

    def test_error_threshold_exceeded(self, monitor: HealthMonitor) -> None:
        """Test error threshold detection."""
        monitor.record_error("Error 1")
        monitor.record_error("Error 2")
        assert not monitor.is_error_threshold_exceeded()

        monitor.record_error("Error 3")
        assert monitor.is_error_threshold_exceeded()
        assert monitor.get_status() == HealthStatus.ERROR

    def test_get_health_report(self, monitor: HealthMonitor) -> None:
        """Test health report generation."""
        monitor.record_success()
        monitor.record_error("Test error")

        report = monitor.get_health_report()

        assert "status" in report
        assert "is_healthy" in report
        assert "metrics" in report
        assert "config" in report

    def test_reset(self, monitor: HealthMonitor) -> None:
        """Test monitor reset."""
        monitor.record_success()
        monitor.record_error("Error")
        monitor._recovery_attempts = 2

        monitor.reset()

        assert monitor.metrics.requests_count == 0
        assert monitor._consecutive_errors == 0
        assert monitor._recovery_attempts == 0

    @pytest.mark.asyncio
    async def test_start_stop_monitoring(self, monitor: HealthMonitor) -> None:
        """Test starting and stopping monitoring."""
        await monitor.start_monitoring()
        assert monitor._running

        await asyncio.sleep(0.1)

        await monitor.stop_monitoring()
        assert not monitor._running

    @pytest.mark.asyncio
    async def test_stall_callback_triggered(self) -> None:
        """Test stall callback is triggered when stalled."""
        callback_called = False

        async def on_stall():
            nonlocal callback_called
            callback_called = True

        config = HealthConfig(
            stall_threshold_seconds=1,
            check_interval_seconds=0.5,
        )
        monitor = HealthMonitor(config=config, on_stall=on_stall)

        # Record activity in the past to trigger stall
        monitor.metrics.last_activity = datetime.now() - timedelta(seconds=5)

        await monitor.start_monitoring()
        await asyncio.sleep(1)
        await monitor.stop_monitoring()

        assert callback_called


class TestCrawlerHealthMonitor:
    """Tests for CrawlerHealthMonitor class."""

    @pytest.fixture
    def monitor(self) -> CrawlerHealthMonitor:
        """Create crawler health monitor instance."""
        return CrawlerHealthMonitor()

    def test_initialization(self, monitor: CrawlerHealthMonitor) -> None:
        """Test monitor initialization."""
        assert monitor._on_stall is not None
        assert monitor._on_error_threshold is not None

    @pytest.mark.asyncio
    async def test_handle_stall_with_callbacks(self) -> None:
        """Test stall handling with callbacks."""
        skip_called = False
        reset_called = False

        async def skip_item():
            nonlocal skip_called
            skip_called = True

        async def reset_session():
            nonlocal reset_called
            reset_called = True

        monitor = CrawlerHealthMonitor(
            skip_item_callback=skip_item,
            reset_session_callback=reset_session,
        )

        await monitor._handle_stall()

        assert skip_called
        assert reset_called

    @pytest.mark.asyncio
    async def test_handle_error_threshold(self) -> None:
        """Test error threshold handling."""
        reset_called = False

        async def reset_session():
            nonlocal reset_called
            reset_called = True

        monitor = CrawlerHealthMonitor(
            reset_session_callback=reset_session,
        )

        # Simulate consecutive errors
        monitor._consecutive_errors = 10

        await monitor._handle_error_threshold()

        assert reset_called
        assert monitor._consecutive_errors == 0  # Should be reset
