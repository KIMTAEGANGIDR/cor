"""PDF downloader service for Peraturan Crawler."""

from pathlib import Path
from typing import Optional, Callable

from rich.progress import Progress, TaskID, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn, DownloadColumn

from src.config import Config, config as default_config
from src.models.peraturan import Peraturan
from src.models.state import DownloadState, FailedItem, StateStatus
from src.services.database import Database
from src.utils.http import HttpClient
from src.utils.retry import retry_with_backoff, RetryError
from src.utils.logging import get_logger

logger = get_logger("downloader")


def get_pdf_folder(jenis: str) -> str:
    """Get folder name for a document type.

    Args:
        jenis: Document type (e.g., 'UNDANG-UNDANG', 'PP')

    Returns:
        Folder name (e.g., 'uu', 'pp')
    """
    type_map = {
        # 헌법/국회
        "UUD": "uud",
        "UUD 1945": "uud",
        "UNDANG-UNDANG DASAR": "uud",
        "TAP MPR": "tapmpr",
        "KETETAPAN MPR": "tapmpr",
        "KETETAPAN MAJELIS PERMUSYAWARATAN RAKYAT": "tapmpr",
        # 중앙정부 법령
        "UNDANG-UNDANG": "uu",
        "UNDANG-UNDANG DARURAT": "uu-darurat",
        "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "perppu",
        "PERPPU": "perppu",
        "PERATURAN PEMERINTAH": "pp",
        "PERATURAN PRESIDEN": "perpres",
        "KEPUTUSAN PRESIDEN": "keppres",
        "INSTRUKSI PRESIDEN": "inpres",
        "PENETAPAN PRESIDEN": "penpres",
        "PENPRES": "penpres",
        # 부처/기관 규정
        "PERATURAN MENTERI": "permen",
        "PERATURAN BADAN/LEMBAGA": "perban",
    }
    return type_map.get(jenis.upper(), "other")


class DownloaderError(Exception):
    """Raised when download fails."""
    pass


class Downloader:
    """PDF file downloader for peraturan documents."""

    def __init__(
        self,
        config: Optional[Config] = None,
        database: Optional[Database] = None,
    ):
        """Initialize downloader.

        Args:
            config: Configuration object
            database: Database instance
        """
        self.config = config or default_config
        self.db = database or Database(self.config)
        self.http = HttpClient(self.config)
        self._stop_requested = False

    def stop(self) -> None:
        """Request downloader to stop gracefully."""
        self._stop_requested = True
        logger.info("Stop requested, finishing current download...")

    def get_local_path(self, peraturan: Peraturan) -> Path:
        """Get local file path for a peraturan PDF.

        Args:
            peraturan: Peraturan instance

        Returns:
            Local path for the PDF file
        """
        folder = get_pdf_folder(peraturan.jenis)
        return self.config.pdf_dir / folder / f"{peraturan.slug}.pdf"

    def should_download(self, peraturan: Peraturan) -> bool:
        """Check if a peraturan PDF should be downloaded.

        Args:
            peraturan: Peraturan instance

        Returns:
            True if should download, False otherwise
        """
        # No PDF URL available
        if not peraturan.pdf_url:
            return False

        # Check if file exists and is non-empty
        local_path = self.get_local_path(peraturan)
        if local_path.exists():
            if local_path.stat().st_size > 0:
                return False  # File exists and is valid
            else:
                return True  # Empty file, re-download

        return True

    async def download(
        self,
        resume: bool = False,
        limit: int = 0,
        jenis_filter: Optional[str] = None,
        retry_failed: bool = False,
        show_progress: bool = True,
    ) -> DownloadState:
        """Download PDF files for peraturan documents.

        Args:
            resume: If True, resume from last saved state
            limit: Maximum number of items to download (0 = unlimited)
            jenis_filter: Filter by document type
            retry_failed: If True, retry previously failed items
            show_progress: Whether to show progress bar

        Returns:
            Final download state
        """
        self._stop_requested = False
        self.db.connect()

        try:
            # Get items to download
            all_items = self.db.get_all_peraturan(jenis=jenis_filter, has_pdf=False)

            # Filter items that need downloading
            items_to_download = [p for p in all_items if self.should_download(p)]

            if limit > 0:
                items_to_download = items_to_download[:limit]

            # Get or create download state
            state = self.db.get_download_state()
            state.start(total=len(items_to_download))
            self.db.update_download_state(state)

            logger.info(f"PDFs to download: {len(items_to_download)}")

            try:
                async with self.http.session():
                    if show_progress:
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            BarColumn(),
                            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                            TextColumn("({task.completed}/{task.total})"),
                            TextColumn("Failed: {task.fields[failed]}"),
                            TextColumn("Skipped: {task.fields[skipped]}"),
                            TimeRemainingColumn(),
                        ) as progress:
                            task = progress.add_task(
                                "Downloading",
                                total=len(items_to_download),
                                completed=0,
                                failed=state.failed_count,
                                skipped=state.skipped_count,
                            )
                            await self._download_items(
                                items_to_download, state, progress, task
                            )
                    else:
                        await self._download_items(items_to_download, state, None, None)

            except KeyboardInterrupt:
                logger.info("Download interrupted by user")
                state.status = StateStatus.PAUSED
                self.db.update_download_state(state)
                raise
            except Exception as e:
                logger.error(f"Download session error: {e}")
                state.status = StateStatus.PAUSED
                self.db.update_download_state(state)
                raise

            # Mark as completed
            state.complete()
            self.db.update_download_state(state)

            return state

        finally:
            try:
                self.db.close()
            except Exception:
                pass

    async def _download_items(
        self,
        items: list[Peraturan],
        state: DownloadState,
        progress: Optional[Progress],
        task: Optional[TaskID],
    ) -> None:
        """Download a list of items.

        Args:
            items: List of peraturan to download
            state: Download state to update
            progress: Rich progress bar
            task: Progress task ID
        """
        consecutive_errors = 0
        max_consecutive_errors = 10

        for peraturan in items:
            if self._stop_requested:
                logger.info("Download stopped by user request")
                break

            try:
                downloaded = await self._download_single(peraturan)

                if downloaded:
                    state.completed_count += 1
                    consecutive_errors = 0  # Reset on success
                else:
                    state.skipped_count += 1

            except Exception as e:
                logger.warning(f"Failed to download {peraturan.slug}: {e}")
                state.failed_count += 1
                consecutive_errors += 1

                try:
                    self.db.add_failed_item(FailedItem(
                        url=peraturan.pdf_url or "",
                        item_type="pdf",
                        error_message=str(e)[:500],  # Limit error message length
                    ))
                except Exception as db_error:
                    logger.error(f"Failed to record error: {db_error}")

                # Check for too many consecutive errors
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        f"Too many consecutive errors ({consecutive_errors}). "
                        "Pausing for 30 seconds..."
                    )
                    import asyncio
                    await asyncio.sleep(30)
                    consecutive_errors = 0

            # Update progress (with error handling)
            try:
                if progress and task is not None:
                    progress.update(
                        task,
                        completed=state.completed_count + state.skipped_count + state.failed_count,
                        failed=state.failed_count,
                        skipped=state.skipped_count,
                    )

                state.update_progress(
                    completed=state.completed_count,
                    skipped=state.skipped_count,
                    failed=state.failed_count,
                )
                self.db.update_download_state(state)
            except Exception as update_error:
                logger.error(f"Failed to update state: {update_error}")

    def _validate_pdf(self, file_path: Path) -> tuple[bool, str]:
        """Validate that a downloaded file is actually a PDF.

        Args:
            file_path: Path to the downloaded file

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not file_path.exists():
            return False, "File does not exist"

        file_size = file_path.stat().st_size
        if file_size == 0:
            return False, "Empty file"

        # Check PDF magic bytes (%PDF-)
        with open(file_path, "rb") as f:
            header = f.read(8)

        if not header.startswith(b"%PDF-"):
            # Check if it's HTML (common error response)
            if header.startswith(b"<!DOCTYPE") or header.startswith(b"<html") or header.startswith(b"<HTML"):
                return False, "HTML content received instead of PDF"
            return False, f"Invalid PDF header: {header[:5]}"

        return True, ""

    async def _download_single(self, peraturan: Peraturan) -> bool:
        """Download a single PDF file.

        Args:
            peraturan: Peraturan to download

        Returns:
            True if downloaded, False if skipped

        Raises:
            DownloaderError: On download failure
        """
        if not peraturan.pdf_url:
            logger.debug(f"No PDF URL for {peraturan.slug}")
            return False

        local_path = self.get_local_path(peraturan)

        # Skip if already exists and is valid
        if local_path.exists() and local_path.stat().st_size > 0:
            is_valid, _ = self._validate_pdf(local_path)
            if is_valid:
                logger.debug(f"Already exists: {peraturan.slug}")
                return False
            else:
                # Invalid existing file, re-download
                logger.warning(f"Invalid existing file, re-downloading: {peraturan.slug}")
                local_path.unlink()

        # Create directory
        local_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Download with retry
            bytes_downloaded = await retry_with_backoff(
                self.http.download_file,
                peraturan.pdf_url,
                str(local_path),
                max_retries=self.config.max_retries,
                backoff_factor=self.config.backoff_factor,
            )

            # Verify file exists
            if bytes_downloaded == 0 or not local_path.exists():
                raise DownloaderError(f"Download produced empty file: {peraturan.slug}")

            # Validate PDF signature
            is_valid, error_msg = self._validate_pdf(local_path)
            if not is_valid:
                # Clean up invalid file
                local_path.unlink()
                raise DownloaderError(f"Invalid PDF for {peraturan.slug}: {error_msg}")

            # Update database with relative path
            relative_path = f"data/pdfs/{get_pdf_folder(peraturan.jenis)}/{peraturan.slug}.pdf"
            self.db.update_local_pdf_path(peraturan.slug, relative_path)
            logger.debug(f"Downloaded: {peraturan.slug} ({bytes_downloaded} bytes)")

            return True

        except RetryError as e:
            # Clean up partial download
            if local_path.exists():
                local_path.unlink()
            raise DownloaderError(f"Download failed after retries: {e}") from e

        except Exception as e:
            # Clean up partial download
            if local_path.exists():
                local_path.unlink()
            raise DownloaderError(f"Download failed: {e}") from e


    async def download_attachments(
        self,
        limit: int = 0,
        show_progress: bool = True,
    ) -> dict:
        """Download attachment PDFs (e.g., UUD amendments).

        Args:
            limit: Maximum number of attachments to download (0 = unlimited)
            show_progress: Whether to show progress bar

        Returns:
            Dictionary with download statistics
        """
        self._stop_requested = False
        self.db.connect()

        try:
            # Get attachments without local path
            attachments = self.db.get_attachments_without_local_path(
                limit=limit if limit > 0 else 1000
            )

            if not attachments:
                logger.info("No attachments to download")
                return {"total": 0, "downloaded": 0, "failed": 0}

            logger.info(f"Attachments to download: {len(attachments)}")

            stats = {"total": len(attachments), "downloaded": 0, "failed": 0}

            async with self.http.session():
                for att in attachments:
                    if self._stop_requested:
                        break

                    slug = att["slug"]
                    pdf_url = att["pdf_url"]
                    jenis = att.get("jenis", "UUD")
                    version = att.get("version", "")

                    # Determine local path
                    folder = get_pdf_folder(jenis)
                    filename = f"{slug}_{version}.pdf" if version else f"{slug}.pdf"
                    local_path = self.config.pdf_dir / folder / filename

                    try:
                        # Download
                        local_path.parent.mkdir(parents=True, exist_ok=True)

                        bytes_downloaded = await retry_with_backoff(
                            self.http.download_file,
                            pdf_url,
                            str(local_path),
                            max_retries=self.config.max_retries,
                            backoff_factor=self.config.backoff_factor,
                        )

                        if bytes_downloaded == 0:
                            raise DownloaderError("Empty file")

                        # Validate PDF
                        is_valid, error_msg = self._validate_pdf(local_path)
                        if not is_valid:
                            local_path.unlink()
                            raise DownloaderError(f"Invalid PDF: {error_msg}")

                        # Update database
                        relative_path = f"data/pdfs/{folder}/{filename}"
                        self.db.update_attachment_path(slug, pdf_url, relative_path)

                        stats["downloaded"] += 1
                        logger.debug(f"Downloaded attachment: {filename}")

                    except Exception as e:
                        stats["failed"] += 1
                        logger.warning(f"Failed to download attachment {slug}/{version}: {e}")

            return stats

        finally:
            self.db.close()


async def run_downloader(
    resume: bool = False,
    limit: int = 0,
    jenis: Optional[str] = None,
    retry_failed: bool = False,
    verbose: bool = False,
) -> DownloadState:
    """Run the downloader with the given options.

    Args:
        resume: Resume from previous state
        limit: Maximum items to download
        jenis: Document type filter
        retry_failed: Retry failed items
        verbose: Enable verbose logging

    Returns:
        Final download state
    """
    config = Config()

    if verbose:
        config.log_level = "DEBUG"

    downloader = Downloader(config=config)
    return await downloader.download(
        resume=resume,
        limit=limit,
        jenis_filter=jenis,
        retry_failed=retry_failed,
    )
