"""Scheduled crawler for rotating through document types.

Key features:
- Dual mode: INITIAL (aggressive) and MAINTENANCE (conservative)
- Rotates through document types to avoid hammering one endpoint
- Tracks progress per document type
- Handles rate limiting with exponential backoff
- Skips already-crawled documents (no duplicates)
- Respects conservative delays between requests
- Auto-switches mode based on completion percentage
"""

import asyncio
import json
import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from src.config import Config
from src.services.crawler import Crawler
from src.services.downloader import Downloader
from src.services.database import Database
from src.utils.logging import get_logger

logger = get_logger("scheduler")


class SchedulerMode(Enum):
    """Scheduler operating modes."""

    INITIAL = "initial"  # Aggressive: fast batch collection
    MAINTENANCE = "maintenance"  # Conservative: slow incremental updates
    AUTO = "auto"  # Automatically switch based on completion


@dataclass
class SchedulerConfig:
    """Configuration for scheduler modes."""

    # Initial mode (aggressive collection)
    initial_interval_minutes: int = 5
    initial_batch_size: int = 200
    initial_delay: float = 1.5  # Shorter delay between requests

    # Maintenance mode (conservative updates)
    maintenance_interval_minutes: int = 60
    maintenance_batch_size: int = 50
    maintenance_delay: float = 3.0  # Longer delay for politeness

    # Auto mode switching threshold
    completion_threshold_percent: float = 95.0

    # Known totals for each document type
    known_totals: dict = None

    def __post_init__(self):
        if self.known_totals is None:
            # Updated 2025-12-20 from peraturan.go.id (PERATURAN DAERAH excluded)
            self.known_totals = {
                "UNDANG-UNDANG": 1907,
                "PERPPU": 202,  # Stored as "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG" in DB
                "PERATURAN PEMERINTAH": 4972,
                "PERATURAN PRESIDEN": 2618,
                "PERATURAN MENTERI": 19500,
                "PERATURAN BADAN/LEMBAGA": 6540,
                # "PERATURAN DAERAH": 19720,  # Excluded from crawl scope
            }


# Document types to rotate through (in priority order)
# Note: PERATURAN DAERAH excluded from scope
DOCUMENT_TYPES = [
    "UNDANG-UNDANG",
    "PERPPU",
    "PERATURAN PEMERINTAH",
    "PERATURAN PRESIDEN",
    "PERATURAN MENTERI",
    "PERATURAN BADAN/LEMBAGA",
]

# Default settings (for backward compatibility)
DEFAULT_INTERVAL_MINUTES = 60  # 1 hour
DEFAULT_BATCH_SIZE = 50
MIN_INTERVAL_MINUTES = 5  # Don't allow less than 5 minutes


class SchedulerState:
    """Persistent state for the scheduler."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.current_type_index = 0
        self.last_run: Optional[str] = None
        self.run_count = 0
        self.total_crawled = 0
        self.total_downloaded = 0
        self.total_skipped = 0  # Already existed (duplicates avoided)
        self.consecutive_errors = 0
        self.last_error: Optional[str] = None
        self.rate_limited_until: Optional[str] = None
        self.current_mode: str = SchedulerMode.AUTO.value
        self.mode_switch_count = 0
        self._load()

    def _load(self) -> None:
        """Load state from file."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.current_type_index = data.get("current_type_index", 0)
                self.last_run = data.get("last_run")
                self.run_count = data.get("run_count", 0)
                self.total_crawled = data.get("total_crawled", 0)
                self.total_downloaded = data.get("total_downloaded", 0)
                self.total_skipped = data.get("total_skipped", 0)
                self.consecutive_errors = data.get("consecutive_errors", 0)
                self.last_error = data.get("last_error")
                self.rate_limited_until = data.get("rate_limited_until")
                self.current_mode = data.get("current_mode", SchedulerMode.AUTO.value)
                self.mode_switch_count = data.get("mode_switch_count", 0)
            except Exception as e:
                logger.warning(f"Failed to load scheduler state: {e}")

    def save(self) -> None:
        """Save state to file."""
        data = {
            "current_type_index": self.current_type_index,
            "last_run": self.last_run,
            "run_count": self.run_count,
            "total_crawled": self.total_crawled,
            "total_downloaded": self.total_downloaded,
            "total_skipped": self.total_skipped,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
            "rate_limited_until": self.rate_limited_until,
            "current_mode": self.current_mode,
            "mode_switch_count": self.mode_switch_count,
        }
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(data, indent=2))

    def get_current_type(self) -> str:
        """Get current document type to crawl."""
        return DOCUMENT_TYPES[self.current_type_index % len(DOCUMENT_TYPES)]

    def advance(self) -> None:
        """Move to next document type."""
        self.current_type_index = (self.current_type_index + 1) % len(DOCUMENT_TYPES)
        self.last_run = datetime.now().isoformat()
        self.run_count += 1
        self.save()

    def record_error(self, error: str) -> None:
        """Record an error and increment counter."""
        self.consecutive_errors += 1
        self.last_error = error
        self.save()

    def clear_errors(self) -> None:
        """Clear error state after successful run."""
        self.consecutive_errors = 0
        self.last_error = None
        self.save()

    def set_rate_limited(self, until: datetime) -> None:
        """Set rate limited state."""
        self.rate_limited_until = until.isoformat()
        self.save()

    def is_rate_limited(self) -> bool:
        """Check if currently rate limited."""
        if not self.rate_limited_until:
            return False
        until = datetime.fromisoformat(self.rate_limited_until)
        if datetime.now() < until:
            return True
        # Clear expired rate limit
        self.rate_limited_until = None
        self.save()
        return False


class Scheduler:
    """Scheduled crawler that rotates through document types.

    Features:
    - Dual mode: INITIAL (aggressive) and MAINTENANCE (conservative)
    - Conservative request timing to avoid blocking
    - Automatic rate limit detection and backoff
    - Duplicate prevention (skips already-crawled items)
    - State persistence for resumability
    - Auto-switches mode based on completion percentage
    """

    def __init__(
        self,
        config: Config,
        mode: SchedulerMode = SchedulerMode.AUTO,
        interval_minutes: Optional[int] = None,
        batch_size: Optional[int] = None,
        download_pdfs: bool = True,
        scheduler_config: Optional[SchedulerConfig] = None,
    ):
        self.config = config
        self.mode = mode
        self.scheduler_config = scheduler_config or SchedulerConfig()
        self.download_pdfs = download_pdfs
        self._running = False
        self._stop_requested = False

        # State file in data directory
        self.state = SchedulerState(config.data_dir / "scheduler_state.json")

        # Override interval/batch if explicitly provided
        self._explicit_interval = interval_minutes
        self._explicit_batch = batch_size

        # Set initial effective mode
        self._effective_mode = self._determine_effective_mode()

    def _determine_effective_mode(self) -> SchedulerMode:
        """Determine effective mode based on settings and completion.

        Returns:
            Effective scheduler mode
        """
        if self.mode != SchedulerMode.AUTO:
            return self.mode

        # Calculate overall completion percentage
        completion = self._calculate_completion_percent()

        if completion >= self.scheduler_config.completion_threshold_percent:
            return SchedulerMode.MAINTENANCE
        else:
            return SchedulerMode.INITIAL

    def _calculate_completion_percent(self) -> float:
        """Calculate overall crawl completion percentage.

        Returns:
            Completion percentage (0-100)
        """
        db = Database(config=self.config)
        db.connect()

        try:
            cursor = db._connection.cursor()
            cursor.execute("""
                SELECT jenis, COUNT(*) as cnt
                FROM peraturan
                GROUP BY jenis
            """)
            crawled = {row['jenis']: row['cnt'] for row in cursor.fetchall()}
            cursor.close()

            total_crawled = 0
            total_expected = 0

            for jenis, target in self.scheduler_config.known_totals.items():
                total_crawled += crawled.get(jenis, 0)
                total_expected += target

            if total_expected == 0:
                return 0.0

            return (total_crawled / total_expected) * 100

        finally:
            db.close()

    def _get_mode_settings(self) -> tuple[int, int, float]:
        """Get current mode settings (interval, batch_size, delay).

        Returns:
            Tuple of (interval_minutes, batch_size, delay)
        """
        # Use explicit values if provided
        if self._explicit_interval is not None and self._explicit_batch is not None:
            return (
                max(self._explicit_interval, MIN_INTERVAL_MINUTES),
                self._explicit_batch,
                self.config.delay,
            )

        # Use mode-based settings
        if self._effective_mode == SchedulerMode.INITIAL:
            return (
                self.scheduler_config.initial_interval_minutes,
                self.scheduler_config.initial_batch_size,
                self.scheduler_config.initial_delay,
            )
        else:
            return (
                self.scheduler_config.maintenance_interval_minutes,
                self.scheduler_config.maintenance_batch_size,
                self.scheduler_config.maintenance_delay,
            )

    def _check_mode_switch(self) -> bool:
        """Check if mode should switch (for AUTO mode).

        Returns:
            True if mode was switched
        """
        if self.mode != SchedulerMode.AUTO:
            return False

        new_mode = self._determine_effective_mode()
        if new_mode != self._effective_mode:
            old_mode = self._effective_mode
            self._effective_mode = new_mode
            self.state.current_mode = new_mode.value
            self.state.mode_switch_count += 1
            self.state.save()

            logger.info(
                f"Mode switched: {old_mode.value} -> {new_mode.value} "
                f"(completion: {self._calculate_completion_percent():.1f}%)"
            )
            return True

        return False

    @property
    def interval_minutes(self) -> int:
        """Get current interval in minutes."""
        interval, _, _ = self._get_mode_settings()
        return interval

    @property
    def batch_size(self) -> int:
        """Get current batch size."""
        _, batch, _ = self._get_mode_settings()
        return batch

    @property
    def effective_delay(self) -> float:
        """Get current request delay."""
        _, _, delay = self._get_mode_settings()
        return delay

    def stop(self) -> None:
        """Request scheduler to stop."""
        self._stop_requested = True
        logger.info("Stop requested, will stop after current task completes")

    async def run_once(self, jenis: Optional[str] = None) -> dict:
        """Run a single crawl cycle for one document type.

        Args:
            jenis: Document type to crawl. If None, uses rotation.

        Returns:
            Dictionary with results
        """
        # Check rate limit
        if self.state.is_rate_limited():
            wait_until = datetime.fromisoformat(self.state.rate_limited_until)
            wait_seconds = (wait_until - datetime.now()).total_seconds()
            logger.warning(f"Rate limited. Waiting {wait_seconds:.0f}s...")
            return {
                "type": "RATE_LIMITED",
                "started_at": datetime.now().isoformat(),
                "crawled": 0,
                "downloaded": 0,
                "skipped": 0,
                "errors": [f"Rate limited until {wait_until}"],
                "completed_at": datetime.now().isoformat(),
            }

        if jenis is None:
            jenis = self.state.get_current_type()

        logger.info(f"Starting crawl for: {jenis}")
        results = {
            "type": jenis,
            "started_at": datetime.now().isoformat(),
            "crawled": 0,
            "downloaded": 0,
            "skipped": 0,
            "errors": [],
        }

        try:
            # Crawl metadata
            crawler = Crawler(config=self.config)
            crawl_state = await crawler.crawl(
                resume=True,
                limit=self.batch_size,
                jenis_filter=jenis,
                show_progress=False,
            )

            results["crawled"] = crawl_state.completed_count
            self.state.total_crawled += crawl_state.completed_count
            logger.info(f"Crawled {crawl_state.completed_count} items for {jenis}")

            # Download PDFs if enabled
            if self.download_pdfs and crawl_state.completed_count > 0:
                # Add random delay before downloading to be polite
                delay = random.uniform(5, 15)
                logger.info(f"Waiting {delay:.1f}s before downloading PDFs...")
                await asyncio.sleep(delay)

                downloader = Downloader(config=self.config)
                download_state = await downloader.download(
                    resume=True,
                    limit=self.batch_size,
                    jenis_filter=None,  # Download all pending PDFs, not just current type
                    show_progress=False,
                )
                results["downloaded"] = download_state.completed_count
                results["skipped"] = download_state.skipped_count
                self.state.total_downloaded += download_state.completed_count
                self.state.total_skipped += download_state.skipped_count
                logger.info(
                    f"Downloaded {download_state.completed_count}, "
                    f"skipped {download_state.skipped_count} PDFs for {jenis}"
                )

            # Clear errors on success
            self.state.clear_errors()

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error during scheduled crawl: {error_msg}")
            results["errors"].append(error_msg)
            self.state.record_error(error_msg)

            # Check if we should back off
            if self.state.consecutive_errors >= 3:
                # Exponential backoff: 5min, 15min, 45min, etc.
                backoff_minutes = 5 * (3 ** (self.state.consecutive_errors - 3))
                backoff_minutes = min(backoff_minutes, 120)  # Cap at 2 hours
                until = datetime.now().replace(
                    minute=datetime.now().minute + backoff_minutes
                )
                logger.warning(
                    f"Too many errors ({self.state.consecutive_errors}). "
                    f"Backing off for {backoff_minutes} minutes..."
                )
                self.state.set_rate_limited(until)

        results["completed_at"] = datetime.now().isoformat()

        # Validate PDFs after download
        if self.download_pdfs:
            validated = self._validate_pdfs()
            results["validated"] = validated
            if validated > 0:
                logger.info(f"Cleaned {validated} invalid PDF files")

        # Generate report
        self._generate_report(results)

        # Advance to next type
        self.state.advance()

        return results

    def _generate_report(self, results: dict) -> None:
        """Generate a report file after each run."""
        report_dir = self.config.data_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        db = Database(config=self.config)
        db.connect()

        try:
            # Get statistics
            cursor = db._connection.cursor()

            # Crawl progress by type
            cursor.execute("""
                SELECT jenis, COUNT(*) as cnt
                FROM peraturan
                GROUP BY jenis
                ORDER BY cnt DESC
            """)
            crawl_progress = {row['jenis']: row['cnt'] for row in cursor.fetchall()}

            # PDF stats
            cursor.execute("SELECT COUNT(*) FROM peraturan WHERE local_pdf_path IS NOT NULL")
            pdf_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM peraturan")
            total_count = cursor.fetchone()[0]

            # Failed items
            cursor.execute("SELECT item_type, COUNT(*) as cnt FROM failed_items GROUP BY item_type")
            failed_items = {row['item_type']: row['cnt'] for row in cursor.fetchall()}

            # Invalid PDFs count
            invalid_pdfs = 0
            pdf_dir = self.config.data_dir / "pdfs"
            if pdf_dir.exists():
                for pdf_path in pdf_dir.rglob("*.pdf"):
                    try:
                        with open(pdf_path, "rb") as f:
                            if not f.read(8).startswith(b"%PDF"):
                                invalid_pdfs += 1
                    except Exception:
                        invalid_pdfs += 1

            cursor.close()

            # Known totals
            known_totals = {
                "UNDANG-UNDANG": 1907,
                "PERPPU": 218,
                "PERATURAN PEMERINTAH": 4972,
                "PERATURAN PRESIDEN": 2618,
                "PERATURAN MENTERI": 19723,
                "PERATURAN BADAN/LEMBAGA": 6614,
                "PERATURAN DAERAH": 5000,
            }

            # Generate report content
            now = datetime.now()
            report = f"""# Peraturan Crawler Report
Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}

## Last Run
- Type: {results.get('type', 'N/A')}
- Crawled: {results.get('crawled', 0)}
- Downloaded: {results.get('downloaded', 0)}
- Validated: {results.get('validated', 0)} invalid files cleaned

## Overall Progress
- Total Documents: {total_count:,}
- PDFs Downloaded: {pdf_count:,}
- Invalid PDFs: {invalid_pdfs}

## Crawl Progress by Type
| Type | Crawled | Target | Progress |
|------|---------|--------|----------|
"""
            for jenis, target in known_totals.items():
                cnt = crawl_progress.get(jenis, 0)
                pct = (cnt / target * 100) if target > 0 else 0
                status = "✓" if pct >= 95 else f"{pct:.1f}%"
                report += f"| {jenis} | {cnt:,} | {target:,} | {status} |\n"

            # Add unknown types
            for jenis, cnt in crawl_progress.items():
                if jenis not in known_totals:
                    report += f"| {jenis} | {cnt:,} | ? | - |\n"

            report += f"""
## Failed Items
"""
            if failed_items:
                for item_type, cnt in failed_items.items():
                    report += f"- {item_type}: {cnt}\n"
            else:
                report += "- No failed items\n"

            report += f"""
## Scheduler Stats
- Total Runs: {self.state.run_count}
- Total Crawled: {self.state.total_crawled:,}
- Total Downloaded: {self.state.total_downloaded:,}
- Last Run: {self.state.last_run or 'N/A'}
"""

            # Write latest report
            latest_report = report_dir / "latest.md"
            latest_report.write_text(report)

            # Also save timestamped report (daily)
            daily_report = report_dir / f"report-{now.strftime('%Y-%m-%d')}.md"
            daily_report.write_text(report)

            logger.info(f"Report generated: {latest_report}")

        finally:
            db.close()

    def _validate_pdfs(self) -> int:
        """Validate downloaded PDFs and fix invalid ones.

        Returns:
            Number of invalid files fixed
        """
        pdf_dir = self.config.data_dir / "pdfs"
        if not pdf_dir.exists():
            return 0

        invalid_files = []

        for pdf_path in pdf_dir.rglob("*.pdf"):
            try:
                with open(pdf_path, "rb") as f:
                    header = f.read(8)
                    if not header.startswith(b"%PDF"):
                        invalid_files.append({
                            "path": pdf_path,
                            "slug": pdf_path.stem,
                        })
            except Exception:
                invalid_files.append({
                    "path": pdf_path,
                    "slug": pdf_path.stem,
                })

        if not invalid_files:
            return 0

        # Fix invalid files
        db = Database(config=self.config)
        db.connect()

        try:
            for item in invalid_files:
                # Delete file
                try:
                    item["path"].unlink()
                except Exception:
                    pass

                # Update DB
                cursor = db._connection.cursor()
                cursor.execute(
                    "UPDATE peraturan SET local_pdf_path = NULL WHERE slug = ?",
                    (item["slug"],)
                )
                db._connection.commit()
                cursor.close()

            # Reset failed count
            cursor = db._connection.cursor()
            cursor.execute("UPDATE download_state SET failed_count = 0 WHERE id = 1")
            db._connection.commit()
            cursor.close()

        finally:
            db.close()

        return len(invalid_files)

    async def run(self, callback=None) -> None:
        """Run the scheduler continuously.

        Args:
            callback: Optional callback function called after each cycle
                     with (results, state) arguments
        """
        self._running = True
        self._stop_requested = False

        completion = self._calculate_completion_percent()
        logger.info(
            f"Scheduler started: mode={self._effective_mode.value}, "
            f"interval={self.interval_minutes}min, batch={self.batch_size}"
        )
        logger.info(f"Completion: {completion:.1f}%, download_pdfs={self.download_pdfs}")
        logger.info(f"Document types: {', '.join(DOCUMENT_TYPES)}")
        logger.info(
            f"Request delay: {self.effective_delay}s + jitter={self.config.delay_jitter}s"
        )

        while not self._stop_requested:
            try:
                # Check for mode switch before each run
                self._check_mode_switch()

                results = await self.run_once()

                if callback:
                    callback(results, self.state)

                if self._stop_requested:
                    break

                # Calculate actual sleep time with random jitter
                base_sleep = self.interval_minutes * 60
                jitter = random.uniform(0, 60)  # Up to 1 minute jitter
                total_sleep = base_sleep + jitter

                logger.info(
                    f"[{self._effective_mode.value.upper()}] Sleeping for {total_sleep/60:.1f} minutes... "
                    f"(Next: {self.state.get_current_type()})"
                )

                # Sleep in 1-second intervals to allow for stop requests
                for _ in range(int(total_sleep)):
                    if self._stop_requested:
                        break
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                self.state.record_error(str(e))
                # Sleep longer after errors
                await asyncio.sleep(120)

        self._running = False
        logger.info("Scheduler stopped")

    def get_status(self) -> dict:
        """Get current scheduler status."""
        return {
            "running": self._running,
            "mode": self.mode.value,
            "effective_mode": self._effective_mode.value,
            "completion_percent": self._calculate_completion_percent(),
            "current_type": self.state.get_current_type(),
            "next_types": [
                DOCUMENT_TYPES[(self.state.current_type_index + i) % len(DOCUMENT_TYPES)]
                for i in range(1, 4)
            ],
            "last_run": self.state.last_run,
            "run_count": self.state.run_count,
            "total_crawled": self.state.total_crawled,
            "total_downloaded": self.state.total_downloaded,
            "total_skipped": self.state.total_skipped,
            "consecutive_errors": self.state.consecutive_errors,
            "last_error": self.state.last_error,
            "rate_limited": self.state.is_rate_limited(),
            "rate_limited_until": self.state.rate_limited_until,
            "interval_minutes": self.interval_minutes,
            "batch_size": self.batch_size,
            "effective_delay": self.effective_delay,
            "mode_switch_count": self.state.mode_switch_count,
        }


def get_db_stats(config: Config) -> dict:
    """Get current database statistics for display."""
    db = Database(config=config)
    db.connect()
    try:
        return db.get_statistics()
    finally:
        db.close()
