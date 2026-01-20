"""State models for tracking crawling and download progress."""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from enum import Enum


class StateStatus(Enum):
    """Status values for crawl/download state."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CrawlState:
    """Crawling progress state.

    Tracks the progress of metadata collection from peraturan.go.id.
    """

    id: int = 1  # Single row, always id=1

    # Progress tracking
    last_page: int = 0
    total_count: int = 0
    completed_count: int = 0
    failed_count: int = 0

    # Timestamps
    started_at: Optional[str] = None
    last_updated_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Status
    status: str = field(default=StateStatus.IDLE.value)

    def start(self) -> None:
        """Mark crawling as started."""
        self.status = StateStatus.RUNNING.value
        self.started_at = datetime.now().isoformat()
        self.last_updated_at = self.started_at
        self.completed_at = None

    def update_progress(self, page: int, completed: int, failed: int = 0) -> None:
        """Update crawling progress."""
        self.last_page = page
        self.completed_count = completed
        self.failed_count = failed
        self.last_updated_at = datetime.now().isoformat()

    def complete(self) -> None:
        """Mark crawling as completed."""
        self.status = StateStatus.COMPLETED.value
        self.completed_at = datetime.now().isoformat()
        self.last_updated_at = self.completed_at

    def pause(self) -> None:
        """Mark crawling as paused."""
        self.status = StateStatus.PAUSED.value
        self.last_updated_at = datetime.now().isoformat()

    def fail(self) -> None:
        """Mark crawling as failed."""
        self.status = StateStatus.FAILED.value
        self.last_updated_at = datetime.now().isoformat()

    @property
    def progress_percent(self) -> float:
        """Calculate progress percentage."""
        if self.total_count == 0:
            return 0.0
        return (self.completed_count / self.total_count) * 100


@dataclass
class DownloadState:
    """PDF download progress state.

    Tracks the progress of PDF file downloads.
    """

    id: int = 1  # Single row, always id=1

    # Progress tracking
    total_pdfs: int = 0
    completed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0

    # Timestamps
    started_at: Optional[str] = None
    last_updated_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Status
    status: str = field(default=StateStatus.IDLE.value)

    def start(self, total: int) -> None:
        """Mark download as started."""
        self.status = StateStatus.RUNNING.value
        self.total_pdfs = total
        self.started_at = datetime.now().isoformat()
        self.last_updated_at = self.started_at
        self.completed_at = None

    def update_progress(
        self,
        completed: int,
        skipped: int = 0,
        failed: int = 0,
    ) -> None:
        """Update download progress."""
        self.completed_count = completed
        self.skipped_count = skipped
        self.failed_count = failed
        self.last_updated_at = datetime.now().isoformat()

    def complete(self) -> None:
        """Mark download as completed."""
        self.status = StateStatus.COMPLETED.value
        self.completed_at = datetime.now().isoformat()
        self.last_updated_at = self.completed_at

    @property
    def progress_percent(self) -> float:
        """Calculate progress percentage."""
        if self.total_pdfs == 0:
            return 0.0
        return ((self.completed_count + self.skipped_count) / self.total_pdfs) * 100


@dataclass
class FailedItem:
    """Failed item for retry queue."""

    id: Optional[int] = None
    url: str = ""
    item_type: str = ""  # 'metadata' or 'pdf'
    error_message: Optional[str] = None
    retry_count: int = 0
    failed_at: Optional[str] = field(default_factory=lambda: datetime.now().isoformat())
    last_retry_at: Optional[str] = None

    def increment_retry(self) -> None:
        """Increment retry count and update timestamp."""
        self.retry_count += 1
        self.last_retry_at = datetime.now().isoformat()
