"""Download watchdog - monitors and auto-restarts downloads if stalled.

Robust monitoring system for unattended overnight operation.
Handles crashes, stalls, and automatically restarts the download process.

Usage:
    python -u -m src.watchdog                    # Default settings
    python -u -m src.watchdog -i 2 -b 500        # Custom interval and batch
    python -u -m src.watchdog --direct           # Use direct download instead of scheduler
"""

import subprocess
import sqlite3
import time
import os
import sys
import signal
from pathlib import Path
from datetime import datetime, timedelta


class DownloadWatchdog:
    """Monitors download progress and restarts if stalled."""

    def __init__(
        self,
        db_path: str = "data/peraturan.db",
        interval: int = 2,
        batch_size: int = 500,
        stall_threshold: int = 180,  # seconds without progress (increased)
        check_interval: int = 30,  # check every N seconds
        use_direct_download: bool = False,  # Use direct download instead of scheduler
        max_restarts: int = 100,  # Maximum restarts before giving up
    ):
        self.db_path = db_path
        self.interval = interval
        self.batch_size = batch_size
        self.stall_threshold = stall_threshold
        self.check_interval = check_interval
        self.use_direct_download = use_direct_download
        self.max_restarts = max_restarts
        self.scheduler_process = None
        self.last_count = 0
        self.last_progress_time = time.time()
        self.restart_count = 0
        self.start_time = None
        self.initial_count = 0

    def get_download_count(self) -> tuple[int, int, int]:
        """Get current download count, total with PDF URL, and failed count."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM peraturan WHERE local_pdf_path IS NOT NULL AND local_pdf_path != ''"
            )
            downloaded = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM peraturan WHERE pdf_url IS NOT NULL AND pdf_url != ''"
            )
            total_with_pdf = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM failed_items WHERE item_type = 'pdf'")
            failed = cursor.fetchone()[0]
            conn.close()
            return downloaded, total_with_pdf, failed
        except sqlite3.Error as e:
            self._print(f"[{self._timestamp()}] ⚠️  DB Error: {e}")
            return self.last_count, 0, 0

    def start_scheduler(self):
        """Start the download scheduler or direct download."""
        if self.scheduler_process and self.scheduler_process.poll() is None:
            self._print(f"[{self._timestamp()}] Process already running (PID: {self.scheduler_process.pid})")
            return

        self.restart_count += 1

        if self.restart_count > self.max_restarts:
            self._print(f"[{self._timestamp()}] ❌ Max restarts ({self.max_restarts}) exceeded. Stopping.")
            return

        try:
            if self.use_direct_download:
                # Direct download mode - simpler, just downloads PDFs
                self._print(f"[{self._timestamp()}] Starting direct download (batch={self.batch_size})")
                self.scheduler_process = subprocess.Popen(
                    ["peraturan", "download", "--limit", str(self.batch_size)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            else:
                # Scheduler mode - crawls and downloads
                self._print(f"[{self._timestamp()}] Starting scheduler (interval={self.interval}m, batch={self.batch_size})")
                self.scheduler_process = subprocess.Popen(
                    ["peraturan", "schedule", "-i", str(self.interval), "-b", str(self.batch_size)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )

            mode = "direct" if self.use_direct_download else "scheduler"
            self._print(f"[{self._timestamp()}] ✓ {mode.capitalize()} started (PID: {self.scheduler_process.pid}, restart #{self.restart_count})")
            self.last_progress_time = time.time()

        except Exception as e:
            self._print(f"[{self._timestamp()}] ❌ Failed to start process: {e}")

    def stop_scheduler(self):
        """Stop the download scheduler."""
        if self.scheduler_process and self.scheduler_process.poll() is None:
            self._print(f"[{self._timestamp()}] Stopping scheduler (PID: {self.scheduler_process.pid})")
            self.scheduler_process.terminate()
            try:
                self.scheduler_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.scheduler_process.kill()
            self._print(f"[{self._timestamp()}] Scheduler stopped")

    def restart_scheduler(self):
        """Restart the scheduler."""
        self._print(f"[{self._timestamp()}] Restarting scheduler...")
        self.stop_scheduler()
        time.sleep(2)
        self.start_scheduler()

    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _print(self, msg: str):
        """Print with immediate flush."""
        print(msg, flush=True)

    def _format_duration(self, seconds: float) -> str:
        """Format duration as human-readable string."""
        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def run(self):
        """Main watchdog loop."""
        self.start_time = time.time()
        mode_str = "Direct Download" if self.use_direct_download else "Scheduler"

        self._print("")
        self._print("=" * 70)
        self._print("  🐕 Download Watchdog Started")
        self._print("=" * 70)
        self._print(f"  Mode: {mode_str}")
        self._print(f"  Interval: {self.interval} min | Batch: {self.batch_size}")
        self._print(f"  Stall threshold: {self.stall_threshold}s | Check: {self.check_interval}s")
        self._print(f"  Max restarts: {self.max_restarts}")
        self._print("=" * 70)
        self._print("  Press Ctrl+C to stop gracefully")
        self._print("=" * 70)
        self._print("")

        # Initial counts
        self.last_count, total, failed = self.get_download_count()
        self.initial_count = self.last_count
        self.last_progress_time = time.time()

        # Start process
        self.start_scheduler()

        try:
            while True:
                time.sleep(self.check_interval)

                # Check progress
                current_count, total, failed = self.get_download_count()
                remaining = total - current_count - failed
                percent = (current_count / total * 100) if total > 0 else 0

                # Calculate session stats
                session_downloaded = current_count - self.initial_count
                elapsed = time.time() - self.start_time
                rate_per_hour = (session_downloaded / elapsed * 3600) if elapsed > 0 else 0

                # Check if process died
                if self.scheduler_process and self.scheduler_process.poll() is not None:
                    exit_code = self.scheduler_process.returncode
                    self._print(f"[{self._timestamp()}] ⚠️  Process died (exit code: {exit_code})! Restarting...")
                    time.sleep(2)  # Brief pause before restart
                    self.start_scheduler()
                    continue

                # Check if max restarts exceeded
                if self.restart_count > self.max_restarts:
                    self._print(f"[{self._timestamp()}] ❌ Max restarts exceeded. Stopping.")
                    break

                # Check progress
                if current_count > self.last_count:
                    # Progress made
                    diff = current_count - self.last_count
                    instant_rate = diff / self.check_interval * 60  # per minute
                    eta_min = remaining / instant_rate if instant_rate > 0 else 0

                    self._print(
                        f"[{self._timestamp()}] ✓ {current_count:,}/{total:,} ({percent:.1f}%) "
                        f"+{diff} | {instant_rate:.0f}/min | ETA: {eta_min:.0f}m | "
                        f"Session: +{session_downloaded:,} | Restarts: {self.restart_count}"
                    )
                    self.last_count = current_count
                    self.last_progress_time = time.time()
                else:
                    # No progress
                    stall_duration = time.time() - self.last_progress_time
                    self._print(
                        f"[{self._timestamp()}] ⏸ {current_count:,}/{total:,} ({percent:.1f}%) "
                        f"Stalled: {stall_duration:.0f}s/{self.stall_threshold}s | "
                        f"Failed: {failed:,} | Restarts: {self.restart_count}"
                    )

                    if stall_duration > self.stall_threshold:
                        self._print(f"[{self._timestamp()}] ⚠️  Stall detected! Restarting...")
                        self.restart_scheduler()
                        self.last_progress_time = time.time()

                # Check if done
                if remaining <= 0:
                    self._print(f"[{self._timestamp()}] ✅ All downloads complete!")
                    break

        except KeyboardInterrupt:
            self._print(f"\n[{self._timestamp()}] 🛑 Watchdog stopped by user")
        finally:
            self.stop_scheduler()

            # Final summary
            final_count, total, failed = self.get_download_count()
            session_downloaded = final_count - self.initial_count
            elapsed = time.time() - self.start_time

            self._print("")
            self._print("=" * 70)
            self._print("  📊 Session Summary")
            self._print("=" * 70)
            self._print(f"  Duration: {self._format_duration(elapsed)}")
            self._print(f"  Downloaded: {session_downloaded:,} PDFs")
            self._print(f"  Total: {final_count:,}/{total:,} ({final_count/total*100:.1f}%)")
            self._print(f"  Failed: {failed:,}")
            self._print(f"  Restarts: {self.restart_count}")
            if elapsed > 0 and session_downloaded > 0:
                self._print(f"  Rate: {session_downloaded/elapsed*3600:.0f}/hour")
            self._print("=" * 70)
            self._print("")


def main():
    """Run the watchdog."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Download watchdog - monitors and auto-restarts downloads",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -u -m src.watchdog                    # Default scheduler mode
  python -u -m src.watchdog --direct           # Direct download mode (simpler)
  python -u -m src.watchdog -b 300 -s 120      # Custom batch and stall timeout
  nohup python -u -m src.watchdog > watchdog.log 2>&1 &  # Run in background
        """
    )
    parser.add_argument("-i", "--interval", type=int, default=2,
                        help="Scheduler interval in minutes (default: 2)")
    parser.add_argument("-b", "--batch", type=int, default=500,
                        help="Batch size per cycle (default: 500)")
    parser.add_argument("-s", "--stall", type=int, default=180,
                        help="Stall threshold in seconds (default: 180)")
    parser.add_argument("-c", "--check", type=int, default=30,
                        help="Check interval in seconds (default: 30)")
    parser.add_argument("--direct", action="store_true",
                        help="Use direct download instead of scheduler")
    parser.add_argument("--max-restarts", type=int, default=100,
                        help="Maximum automatic restarts (default: 100)")

    args = parser.parse_args()

    watchdog = DownloadWatchdog(
        interval=args.interval,
        batch_size=args.batch,
        stall_threshold=args.stall,
        check_interval=args.check,
        use_direct_download=args.direct,
        max_restarts=args.max_restarts,
    )

    # Handle SIGTERM for graceful shutdown
    def handle_sigterm(signum, frame):
        watchdog.stop_scheduler()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    watchdog.run()


if __name__ == "__main__":
    main()
