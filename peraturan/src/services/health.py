"""Health monitoring and auto-recovery for crawler processes."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Callable, Awaitable

from src.utils.logging import get_logger

logger = get_logger("health")


class HealthStatus(Enum):
    """Health status levels."""

    HEALTHY = "healthy"
    WARNING = "warning"
    STALLED = "stalled"
    ERROR = "error"


class RecoveryAction(Enum):
    """Types of recovery actions."""

    SKIP_ITEM = "skip_item"
    RESET_SESSION = "reset_session"
    INCREASE_DELAY = "increase_delay"
    RESTART = "restart"


@dataclass
class HealthMetrics:
    """Health check metrics."""

    last_activity: Optional[datetime] = None
    last_successful_request: Optional[datetime] = None
    last_error: Optional[datetime] = None
    last_error_message: Optional[str] = None
    requests_count: int = 0
    errors_count: int = 0
    items_processed: int = 0
    stall_count: int = 0
    recovery_count: int = 0

    def record_activity(self) -> None:
        """Record any activity."""
        self.last_activity = datetime.now()

    def record_success(self) -> None:
        """Record successful request."""
        self.last_activity = datetime.now()
        self.last_successful_request = datetime.now()
        self.requests_count += 1

    def record_error(self, message: str) -> None:
        """Record error."""
        self.last_activity = datetime.now()
        self.last_error = datetime.now()
        self.last_error_message = message
        self.errors_count += 1

    def record_item_processed(self) -> None:
        """Record item processed."""
        self.items_processed += 1
        self.record_activity()

    def record_stall(self) -> None:
        """Record stall detected."""
        self.stall_count += 1

    def record_recovery(self) -> None:
        """Record recovery action taken."""
        self.recovery_count += 1

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "last_successful_request": self.last_successful_request.isoformat() if self.last_successful_request else None,
            "last_error": self.last_error.isoformat() if self.last_error else None,
            "last_error_message": self.last_error_message,
            "requests_count": self.requests_count,
            "errors_count": self.errors_count,
            "items_processed": self.items_processed,
            "stall_count": self.stall_count,
            "recovery_count": self.recovery_count,
        }


@dataclass
class HealthConfig:
    """Configuration for health monitoring."""

    # Stall detection
    stall_threshold_seconds: int = 300  # 5 minutes
    warning_threshold_seconds: int = 120  # 2 minutes

    # Error handling
    max_consecutive_errors: int = 10
    error_cooldown_seconds: int = 60

    # Monitoring
    check_interval_seconds: int = 30

    # Recovery
    auto_recovery: bool = True
    max_recovery_attempts: int = 3


class HealthMonitor:
    """Monitor crawler health and trigger recovery actions."""

    def __init__(
        self,
        config: Optional[HealthConfig] = None,
        on_stall: Optional[Callable[[], Awaitable[None]]] = None,
        on_error_threshold: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """Initialize health monitor.

        Args:
            config: Health monitoring configuration
            on_stall: Callback when stall is detected
            on_error_threshold: Callback when error threshold is exceeded
        """
        self.config = config or HealthConfig()
        self.metrics = HealthMetrics()
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._consecutive_errors = 0
        self._recovery_attempts = 0

        # Callbacks
        self._on_stall = on_stall
        self._on_error_threshold = on_error_threshold

    def record_activity(self) -> None:
        """Record crawler activity."""
        self.metrics.record_activity()

    def record_success(self) -> None:
        """Record successful request."""
        self.metrics.record_success()
        self._consecutive_errors = 0

    def record_error(self, message: str) -> None:
        """Record error."""
        self.metrics.record_error(message)
        self._consecutive_errors += 1

    def record_item_processed(self) -> None:
        """Record item processed."""
        self.metrics.record_item_processed()

    def get_status(self) -> HealthStatus:
        """Get current health status.

        Returns:
            Current health status
        """
        if not self.metrics.last_activity:
            return HealthStatus.HEALTHY

        elapsed = datetime.now() - self.metrics.last_activity

        if elapsed > timedelta(seconds=self.config.stall_threshold_seconds):
            return HealthStatus.STALLED

        if elapsed > timedelta(seconds=self.config.warning_threshold_seconds):
            return HealthStatus.WARNING

        if self._consecutive_errors >= self.config.max_consecutive_errors:
            return HealthStatus.ERROR

        return HealthStatus.HEALTHY

    def is_stalled(self) -> bool:
        """Check if crawler appears to be stalled.

        Returns:
            True if stalled, False otherwise
        """
        return self.get_status() == HealthStatus.STALLED

    def is_error_threshold_exceeded(self) -> bool:
        """Check if error rate is too high.

        Returns:
            True if error threshold exceeded, False otherwise
        """
        return self._consecutive_errors >= self.config.max_consecutive_errors

    async def start_monitoring(self) -> None:
        """Start background monitoring."""
        if self._running:
            return

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Health monitoring started")

    async def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Health monitoring stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            await asyncio.sleep(self.config.check_interval_seconds)

            status = self.get_status()

            if status == HealthStatus.STALLED:
                logger.warning(
                    f"Crawler appears stalled. No activity for "
                    f"{self.config.stall_threshold_seconds}s"
                )
                self.metrics.record_stall()

                if self.config.auto_recovery and self._on_stall:
                    await self._attempt_recovery("stall", self._on_stall)

            elif status == HealthStatus.ERROR:
                logger.warning(
                    f"Error threshold exceeded: {self._consecutive_errors} consecutive errors"
                )

                if self.config.auto_recovery and self._on_error_threshold:
                    await self._attempt_recovery("error", self._on_error_threshold)

            elif status == HealthStatus.WARNING:
                elapsed = datetime.now() - self.metrics.last_activity
                logger.debug(f"Health warning: no activity for {elapsed.seconds}s")

    async def _attempt_recovery(
        self,
        reason: str,
        recovery_callback: Callable[[], Awaitable[None]],
    ) -> None:
        """Attempt recovery action.

        Args:
            reason: Reason for recovery (for logging)
            recovery_callback: Callback to execute
        """
        if self._recovery_attempts >= self.config.max_recovery_attempts:
            logger.error(
                f"Max recovery attempts ({self.config.max_recovery_attempts}) exceeded. "
                f"Manual intervention required."
            )
            return

        self._recovery_attempts += 1
        self.metrics.record_recovery()
        logger.info(f"Attempting recovery (attempt {self._recovery_attempts}): {reason}")

        try:
            await recovery_callback()
            logger.info("Recovery action completed")
        except Exception as e:
            logger.error(f"Recovery action failed: {e}")

    def get_health_report(self) -> dict:
        """Get comprehensive health report.

        Returns:
            Dictionary with health information
        """
        status = self.get_status()

        return {
            "status": status.value,
            "is_healthy": status == HealthStatus.HEALTHY,
            "is_stalled": self.is_stalled(),
            "is_error_threshold_exceeded": self.is_error_threshold_exceeded(),
            "consecutive_errors": self._consecutive_errors,
            "recovery_attempts": self._recovery_attempts,
            "metrics": self.metrics.to_dict(),
            "config": {
                "stall_threshold_seconds": self.config.stall_threshold_seconds,
                "max_consecutive_errors": self.config.max_consecutive_errors,
                "auto_recovery": self.config.auto_recovery,
            },
        }

    def reset(self) -> None:
        """Reset health monitor state."""
        self.metrics = HealthMetrics()
        self._consecutive_errors = 0
        self._recovery_attempts = 0
        logger.info("Health monitor state reset")


class CrawlerHealthMonitor(HealthMonitor):
    """Specialized health monitor for the crawler with recovery actions."""

    def __init__(
        self,
        config: Optional[HealthConfig] = None,
        skip_item_callback: Optional[Callable[[], Awaitable[None]]] = None,
        reset_session_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """Initialize crawler health monitor.

        Args:
            config: Health monitoring configuration
            skip_item_callback: Callback to skip current item
            reset_session_callback: Callback to reset HTTP session
        """
        super().__init__(config=config)

        self._skip_item = skip_item_callback
        self._reset_session = reset_session_callback

        # Set up default recovery callbacks
        self._on_stall = self._handle_stall
        self._on_error_threshold = self._handle_error_threshold

    async def _handle_stall(self) -> None:
        """Handle stall by skipping current item and resetting session."""
        logger.info("Handling stall: skipping current item and resetting session")

        if self._skip_item:
            try:
                await self._skip_item()
            except Exception as e:
                logger.error(f"Failed to skip item: {e}")

        if self._reset_session:
            try:
                await self._reset_session()
            except Exception as e:
                logger.error(f"Failed to reset session: {e}")

    async def _handle_error_threshold(self) -> None:
        """Handle error threshold by increasing delay and resetting session."""
        logger.info("Handling error threshold: resetting session")

        if self._reset_session:
            try:
                await self._reset_session()
                self._consecutive_errors = 0  # Reset error count after recovery
            except Exception as e:
                logger.error(f"Failed to reset session: {e}")
