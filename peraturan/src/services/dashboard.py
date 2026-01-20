"""Real-time progress dashboard for crawler monitoring."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

from src.config import Config, config as default_config
from src.services.database import Database
from src.services.health import HealthMonitor, HealthStatus
from src.services.scheduler import DOCUMENT_TYPES, SchedulerConfig
from src.utils.logging import get_logger

logger = get_logger("dashboard")


@dataclass
class TypeProgress:
    """Progress for a single document type."""

    jenis: str
    crawled: int
    expected: int
    percent: float
    last_updated: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "jenis": self.jenis,
            "crawled": self.crawled,
            "expected": self.expected,
            "percent": self.percent,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


@dataclass
class DashboardStats:
    """Dashboard statistics."""

    session_start: datetime
    items_per_hour: float
    estimated_completion: Optional[datetime]
    total_crawled: int
    total_expected: int
    completion_percent: float
    type_progress: dict[str, TypeProgress]
    is_healthy: bool
    health_status: str = "unknown"
    current_type: Optional[str] = None
    scheduler_mode: Optional[str] = None
    errors_count: int = 0
    last_activity: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "session_start": self.session_start.isoformat(),
            "items_per_hour": self.items_per_hour,
            "estimated_completion": self.estimated_completion.isoformat() if self.estimated_completion else None,
            "total_crawled": self.total_crawled,
            "total_expected": self.total_expected,
            "completion_percent": self.completion_percent,
            "type_progress": {k: v.to_dict() for k, v in self.type_progress.items()},
            "is_healthy": self.is_healthy,
            "health_status": self.health_status,
            "current_type": self.current_type,
            "scheduler_mode": self.scheduler_mode,
            "errors_count": self.errors_count,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
        }


class Dashboard:
    """Real-time progress dashboard."""

    def __init__(
        self,
        config: Optional[Config] = None,
        health_monitor: Optional[HealthMonitor] = None,
    ):
        """Initialize dashboard.

        Args:
            config: Configuration object
            health_monitor: Optional health monitor for status
        """
        self.config = config or default_config
        self.db = Database(self.config)
        self.db.connect()
        self.health_monitor = health_monitor
        self.scheduler_config = SchedulerConfig()
        self.console = Console()

        self._session_start: Optional[datetime] = None
        self._initial_counts: dict[str, int] = {}

    def start_session(self) -> None:
        """Start a new dashboard session."""
        self._session_start = datetime.now()
        self._initial_counts = self._get_current_counts()
        logger.info("Dashboard session started")

    def _get_current_counts(self) -> dict[str, int]:
        """Get current document counts by type."""
        counts = {}
        for jenis in DOCUMENT_TYPES:
            count = self.db.get_count_by_jenis(jenis)
            counts[jenis] = count
        return counts

    def _get_expected_totals(self) -> dict[str, int]:
        """Get expected totals from scheduler config."""
        return self.scheduler_config.known_totals

    def get_stats(self) -> DashboardStats:
        """Get current dashboard statistics.

        Returns:
            DashboardStats with current progress
        """
        if not self._session_start:
            self.start_session()

        current_counts = self._get_current_counts()
        expected_totals = self._get_expected_totals()

        # Calculate progress per type
        type_progress = {}
        total_crawled = 0
        total_expected = 0

        for jenis in DOCUMENT_TYPES:
            crawled = current_counts.get(jenis, 0)
            expected = expected_totals.get(jenis, 0)
            percent = (crawled / expected * 100) if expected > 0 else 0.0

            type_progress[jenis] = TypeProgress(
                jenis=jenis,
                crawled=crawled,
                expected=expected,
                percent=min(percent, 100.0),
            )

            total_crawled += crawled
            total_expected += expected

        # Calculate overall progress
        completion_percent = (total_crawled / total_expected * 100) if total_expected > 0 else 0.0

        # Calculate items per hour
        elapsed = datetime.now() - self._session_start
        elapsed_hours = elapsed.total_seconds() / 3600

        items_this_session = sum(
            current_counts.get(j, 0) - self._initial_counts.get(j, 0)
            for j in DOCUMENT_TYPES
        )
        items_per_hour = items_this_session / elapsed_hours if elapsed_hours > 0 else 0.0

        # Estimate completion time
        remaining = total_expected - total_crawled
        estimated_completion = None
        if items_per_hour > 0 and remaining > 0:
            hours_remaining = remaining / items_per_hour
            estimated_completion = datetime.now() + timedelta(hours=hours_remaining)

        # Health status
        is_healthy = True
        health_status = "healthy"
        errors_count = 0
        last_activity = None

        if self.health_monitor:
            status = self.health_monitor.get_status()
            is_healthy = status == HealthStatus.HEALTHY
            health_status = status.value
            errors_count = self.health_monitor.metrics.errors_count
            last_activity = self.health_monitor.metrics.last_activity

        return DashboardStats(
            session_start=self._session_start,
            items_per_hour=items_per_hour,
            estimated_completion=estimated_completion,
            total_crawled=total_crawled,
            total_expected=total_expected,
            completion_percent=min(completion_percent, 100.0),
            type_progress=type_progress,
            is_healthy=is_healthy,
            health_status=health_status,
            errors_count=errors_count,
            last_activity=last_activity,
        )

    def render_table(self, stats: DashboardStats) -> Table:
        """Render progress table.

        Args:
            stats: Dashboard statistics

        Returns:
            Rich Table with progress
        """
        table = Table(title="Document Type Progress", show_header=True, header_style="bold cyan")
        table.add_column("Type", style="dim", width=25)
        table.add_column("Crawled", justify="right", style="green")
        table.add_column("Expected", justify="right", style="blue")
        table.add_column("Progress", justify="right")
        table.add_column("Bar", width=20)

        for jenis in DOCUMENT_TYPES:
            progress = stats.type_progress.get(jenis)
            if not progress:
                continue

            # Color based on completion
            if progress.percent >= 95:
                bar_color = "green"
            elif progress.percent >= 50:
                bar_color = "yellow"
            else:
                bar_color = "red"

            # Progress bar
            bar_width = 15
            filled = int(progress.percent / 100 * bar_width)
            bar = f"[{bar_color}]{'█' * filled}{'░' * (bar_width - filled)}[/{bar_color}]"

            table.add_row(
                jenis,
                f"{progress.crawled:,}",
                f"{progress.expected:,}",
                f"{progress.percent:.1f}%",
                bar,
            )

        # Total row
        table.add_row(
            "[bold]TOTAL[/bold]",
            f"[bold]{stats.total_crawled:,}[/bold]",
            f"[bold]{stats.total_expected:,}[/bold]",
            f"[bold]{stats.completion_percent:.1f}%[/bold]",
            "",
            style="bold",
        )

        return table

    def render_summary(self, stats: DashboardStats) -> Panel:
        """Render summary panel.

        Args:
            stats: Dashboard statistics

        Returns:
            Rich Panel with summary
        """
        # Health indicator
        if stats.is_healthy:
            health_icon = "[green]●[/green]"
            health_text = "Healthy"
        elif stats.health_status == "warning":
            health_icon = "[yellow]●[/yellow]"
            health_text = "Warning"
        else:
            health_icon = "[red]●[/red]"
            health_text = stats.health_status.title()

        lines = [
            f"[bold]Session Started:[/bold] {stats.session_start.strftime('%Y-%m-%d %H:%M:%S')}",
            f"[bold]Status:[/bold] {health_icon} {health_text}",
            f"[bold]Items/Hour:[/bold] {stats.items_per_hour:.1f}",
            f"[bold]Errors:[/bold] {stats.errors_count}",
        ]

        if stats.estimated_completion:
            eta = stats.estimated_completion.strftime('%Y-%m-%d %H:%M')
            lines.append(f"[bold]ETA:[/bold] {eta}")
        else:
            lines.append("[bold]ETA:[/bold] Calculating...")

        if stats.last_activity:
            elapsed = datetime.now() - stats.last_activity
            lines.append(f"[bold]Last Activity:[/bold] {elapsed.seconds}s ago")

        return Panel(
            "\n".join(lines),
            title="Summary",
            border_style="blue",
        )

    def render_cli(self, stats: Optional[DashboardStats] = None) -> None:
        """Render dashboard to CLI.

        Args:
            stats: Optional pre-computed statistics
        """
        if stats is None:
            stats = self.get_stats()

        self.console.clear()
        self.console.print()
        self.console.print(Panel.fit(
            "[bold cyan]Peraturan Crawler Dashboard[/bold cyan]",
            border_style="cyan",
        ))
        self.console.print()

        # Summary panel
        self.console.print(self.render_summary(stats))
        self.console.print()

        # Progress table
        self.console.print(self.render_table(stats))
        self.console.print()

        # Footer
        footer = Text()
        footer.append("Press ", style="dim")
        footer.append("Ctrl+C", style="bold")
        footer.append(" to exit", style="dim")
        self.console.print(footer)

    async def run_live(self, refresh_seconds: int = 5) -> None:
        """Run live dashboard with auto-refresh.

        Args:
            refresh_seconds: Seconds between refresh
        """
        self.start_session()

        try:
            while True:
                self.render_cli()
                await asyncio.sleep(refresh_seconds)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Dashboard stopped.[/yellow]")

    def get_compact_status(self) -> str:
        """Get compact one-line status.

        Returns:
            Compact status string
        """
        stats = self.get_stats()

        health_icon = "✓" if stats.is_healthy else "✗"
        eta_str = ""
        if stats.estimated_completion:
            eta = stats.estimated_completion - datetime.now()
            if eta.total_seconds() > 0:
                hours = int(eta.total_seconds() // 3600)
                minutes = int((eta.total_seconds() % 3600) // 60)
                eta_str = f" ETA: {hours}h{minutes}m"

        return (
            f"[{health_icon}] {stats.total_crawled:,}/{stats.total_expected:,} "
            f"({stats.completion_percent:.1f}%) | "
            f"{stats.items_per_hour:.1f}/h{eta_str}"
        )


async def show_dashboard(refresh: int = 5) -> None:
    """Show live dashboard.

    Args:
        refresh: Refresh interval in seconds
    """
    dashboard = Dashboard()
    await dashboard.run_live(refresh_seconds=refresh)
