"""Crawler service for collecting peraturan metadata."""

import asyncio
from typing import Optional, Callable
from rich.progress import Progress, TaskID, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

from src.config import Config, config as default_config
from src.models.peraturan import Peraturan
from src.models.state import CrawlState, FailedItem, StateStatus
from src.services.database import Database
from src.services.graph import GraphService
from src.services.relation_extractor import RelationExtractor, RelationType
from src.services.parser import Parser, ParseError
from src.utils.http import HttpClient
from src.utils.retry import retry_with_backoff, RetryError
from src.utils.logging import get_logger

logger = get_logger("crawler")

# URL path mapping for document types
JENIS_URL_MAP = {
    # 기존 유형
    "UNDANG-UNDANG": "/uu",
    "PERPPU": "/perppu",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "/perppu",
    "PERATURAN PEMERINTAH": "/pp",
    "PERATURAN PRESIDEN": "/perpres",
    "PERATURAN MENTERI": "/permen",
    "PERATURAN BADAN/LEMBAGA": "/perban",
    # 추가 유형 (RFP 대상)
    "TAP MPR": "/tapmpr",
    "KETETAPAN MPR": "/tapmpr",
    "KETETAPAN MAJELIS PERMUSYAWARATAN RAKYAT": "/tapmpr",
    "PENPRES": "/penpres",
    "PENETAPAN PRESIDEN": "/penpres",
    "INSTRUKSI PRESIDEN": "/inpres",
    "KEPUTUSAN PRESIDEN": "/keppres",
    # KOICA 누락 보완
    "UUDRT": "/uudrt",
    "UNDANG-UNDANG DARURAT": "/uudrt",
}


class CrawlerError(Exception):
    """Raised when crawling fails."""
    pass


def calculate_last_page(total_count: int, items_per_page: int = 20) -> int:
    """Calculate last page number from total count.

    Args:
        total_count: Total number of items
        items_per_page: Number of items per page (default 20)

    Returns:
        Last page number (1-indexed), minimum 1
    """
    if total_count <= 0:
        return 1
    return (total_count + items_per_page - 1) // items_per_page


class Crawler:
    """Metadata crawler for peraturan.go.id."""

    def __init__(
        self,
        config: Optional[Config] = None,
        database: Optional[Database] = None,
        graph: Optional[GraphService] = None,
    ):
        """Initialize crawler.

        Args:
            config: Configuration object
            database: Database instance
            graph: Graph database instance for Neo4j
        """
        self.config = config or default_config
        self.db = database or Database(self.config)
        self.graph = graph or GraphService(self.config)
        self.relation_extractor = RelationExtractor()
        self.parser = Parser()
        self.http = HttpClient(self.config)
        self._stop_requested = False
        self._graph_connected = False

    def stop(self) -> None:
        """Request crawler to stop gracefully."""
        self._stop_requested = True
        logger.info("Stop requested, finishing current page...")

    async def crawl(
        self,
        resume: bool = False,
        limit: int = 0,
        jenis_filter: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, int], None]] = None,
        show_progress: bool = True,
    ) -> CrawlState:
        """Crawl peraturan metadata from peraturan.go.id.

        Args:
            resume: If True, resume from last saved state
            limit: Maximum number of items to crawl (0 = unlimited)
            jenis_filter: Filter by document type
            progress_callback: Optional callback(completed, total, failed)
            show_progress: Whether to show progress bar

        Returns:
            Final crawl state
        """
        self._stop_requested = False
        self.db.connect()

        # Connect to Neo4j (optional - continues without if unavailable)
        self._graph_connected = self.graph.connect()
        if self._graph_connected:
            logger.info("Neo4j dual storage enabled")
        else:
            logger.info("Neo4j not available, using SQLite only")

        try:
            # Get or create crawl state
            state = self.db.get_crawl_state()

            if not resume or state.status != StateStatus.PAUSED.value:
                # Fresh start
                state = CrawlState()
                state.start()
                self.db.update_crawl_state(state)
                logger.info("Starting fresh crawl")
            else:
                state.status = StateStatus.RUNNING.value
                self.db.update_crawl_state(state)
                logger.info(f"Resuming from page {state.last_page}")

            async with self.http.session():
                # Get total count and pages
                if state.total_count == 0:
                    total, _ = await self._get_pagination_info(jenis_filter)
                    state.total_count = total
                    self.db.update_crawl_state(state)

                # Calculate last_page from total_count
                last_page = calculate_last_page(state.total_count)
                logger.info(f"Total peraturan: {state.total_count}, pages: {last_page}")

                # Create progress bar
                if show_progress:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                        TextColumn("({task.completed}/{task.total})"),
                        TextColumn("Failed: {task.fields[failed]}"),
                        TimeRemainingColumn(),
                    ) as progress:
                        task = progress.add_task(
                            "Crawling",
                            total=state.total_count,
                            completed=state.completed_count,
                            failed=state.failed_count,
                        )
                        await self._crawl_pages(
                            state, last_page, limit, jenis_filter, progress, task
                        )
                else:
                    await self._crawl_pages(
                        state, last_page, limit, jenis_filter, None, None
                    )

            # Mark as completed or paused
            if self._stop_requested:
                state.pause()
            elif state.completed_count >= state.total_count or (limit > 0 and state.completed_count >= limit):
                state.complete()

            self.db.update_crawl_state(state)
            return state

        finally:
            self.db.close()
            if self._graph_connected:
                self.graph.close()

    async def _get_pagination_info(self, jenis_filter: Optional[str] = None) -> tuple[int, int]:
        """Get total count and last page from list page.

        Args:
            jenis_filter: Document type filter

        Returns:
            Tuple of (total_count, calculated_last_page)

        Note:
            last_page is calculated from total_count (not parsed from UI)
            because UI pagination may only show limited page links.
        """
        # Use type-specific URL path if jenis_filter is provided
        if jenis_filter and jenis_filter in JENIS_URL_MAP:
            path = JENIS_URL_MAP[jenis_filter]
            url = f"{self.config.base_url}{path}"
        else:
            url = f"{self.config.base_url}/id"

        response = await self.http.get(url)
        html = response.text

        total = self.parser.parse_total_count(html)
        ui_last_page = self.parser.parse_last_page(html)
        calculated_last_page = calculate_last_page(total)

        # Log pagination validation
        if calculated_last_page != ui_last_page:
            logger.warning(
                f"Pagination mismatch detected: UI shows {ui_last_page} pages, "
                f"but calculated {calculated_last_page} pages from {total} items. "
                f"Using calculated value."
            )

        return total, calculated_last_page

    async def _crawl_pages(
        self,
        state: CrawlState,
        last_page: int,
        limit: int,
        jenis_filter: Optional[str],
        progress: Optional[Progress],
        task: Optional[TaskID],
    ) -> None:
        """Crawl all pages.

        Args:
            state: Current crawl state
            last_page: Last page number
            limit: Maximum items to crawl
            jenis_filter: Document type filter
            progress: Rich progress bar
            task: Progress task ID
        """
        start_page = state.last_page + 1

        for page in range(start_page, last_page + 1):
            if self._stop_requested:
                break

            if limit > 0 and state.completed_count >= limit:
                break

            try:
                items_crawled = await self._crawl_page(page, jenis_filter)
                state.update_progress(
                    page=page,
                    completed=state.completed_count + items_crawled,
                    failed=state.failed_count,
                )

                if progress and task is not None:
                    progress.update(
                        task,
                        completed=state.completed_count,
                        failed=state.failed_count,
                    )

                self.db.update_crawl_state(state)

            except Exception as e:
                logger.error(f"Error crawling page {page}: {e}")
                state.failed_count += 1
                self.db.update_crawl_state(state)

    async def _crawl_page(self, page: int, jenis_filter: Optional[str] = None) -> int:
        """Crawl a single list page.

        Args:
            page: Page number
            jenis_filter: Document type filter

        Returns:
            Number of items successfully crawled
        """
        # Use type-specific URL path if jenis_filter is provided
        if jenis_filter and jenis_filter in JENIS_URL_MAP:
            path = JENIS_URL_MAP[jenis_filter]
            url = f"{self.config.base_url}{path}?page={page}"
        else:
            url = f"{self.config.base_url}/id?page={page}"

        logger.debug(f"Crawling page {page}: {url}")

        response = await self.http.get(url)
        links = self.parser.parse_list_page(response.text)

        items_crawled = 0

        for link in links:
            if self._stop_requested:
                break

            slug = self.parser.extract_slug_from_url(link)

            # Skip if already exists in SQLite
            existing = self.db.get_peraturan(slug)
            if existing:
                # But still sync to Neo4j if connected and not yet synced
                if self._graph_connected and not self.graph.get_peraturan(slug):
                    self._save_to_graph(existing)
                items_crawled += 1
                continue

            try:
                peraturan = await self._crawl_detail(slug)
                if peraturan:
                    # Save to SQLite
                    self.db.upsert_peraturan(peraturan)

                    # Save to Neo4j (if connected)
                    if self._graph_connected:
                        self._save_to_graph(peraturan)

                    items_crawled += 1
                    logger.debug(f"Saved: {slug}")
            except Exception as e:
                logger.warning(f"Failed to crawl {slug}: {e}")
                self.db.add_failed_item(FailedItem(
                    url=f"{self.config.base_url}/id/{slug}",
                    item_type="metadata",
                    error_message=str(e),
                ))

        return items_crawled

    async def _crawl_detail(self, slug: str) -> Optional[Peraturan]:
        """Crawl a detail page.

        Args:
            slug: Peraturan slug

        Returns:
            Peraturan instance or None on failure
        """
        url = f"{self.config.base_url}/id/{slug}"

        try:
            response = await retry_with_backoff(
                self.http.get,
                url,
                max_retries=self.config.max_retries,
                backoff_factor=self.config.backoff_factor,
            )
            return self.parser.parse_detail_page(response.text, slug)

        except RetryError as e:
            logger.error(f"Failed to fetch {slug} after retries: {e}")
            raise
        except ParseError as e:
            logger.error(f"Failed to parse {slug}: {e}")
            raise

    def _save_to_graph(self, peraturan: Peraturan) -> None:
        """Save peraturan to Neo4j and extract relationships.

        Args:
            peraturan: Peraturan instance to save
        """
        try:
            # Save node
            self.graph.upsert_peraturan(peraturan)

            # Extract relationships from title
            relations = self.relation_extractor.extract_from_title(peraturan.tentang)

            # Create relationships
            for rel in relations:
                self._create_graph_relationship(peraturan.slug, rel)

        except Exception as e:
            logger.warning(f"Failed to save to Neo4j: {peraturan.slug} - {e}")

    def _create_graph_relationship(self, source_slug: str, rel) -> None:
        """Create relationship in Neo4j.

        Args:
            source_slug: Source peraturan slug
            rel: ExtractedRelation instance
        """
        try:
            if rel.rel_type == RelationType.MENCABUT:
                self.graph.create_mencabut(
                    source_slug, rel.target_slug, rel.pasal_dasar
                )
            elif rel.rel_type == RelationType.MENGUBAH:
                self.graph.create_mengubah(
                    source_slug, rel.target_slug
                )
            elif rel.rel_type == RelationType.MERUJUK:
                self.graph.create_merujuk(
                    source_slug, rel.target_slug
                )
            elif rel.rel_type == RelationType.BERLAKU_BERSYARAT:
                self.graph.create_berlaku_bersyarat(
                    rel.target_slug, source_slug, rel.kondisi or ""
                )
            elif rel.rel_type == RelationType.MASA_PERALIHAN:
                self.graph.create_masa_peralihan(
                    rel.target_slug, source_slug, rel.kondisi or ""
                )

            logger.debug(f"Created {rel.rel_type.value}: {source_slug} -> {rel.target_slug}")

        except Exception as e:
            logger.warning(f"Failed to create relationship: {e}")


    async def crawl_uud(self) -> int:
        """Crawl UUD 1945 page (special case: single page with multiple PDFs).

        UUD has a unique structure:
        - Single detail page at /uud (no list pagination)
        - Multiple PDF files (original + amendments) in meta tags

        Returns:
            Number of PDFs added as attachments
        """
        logger.info("Crawling UUD 1945 (special handler)")
        self.db.connect()

        try:
            async with self.http.session():
                url = f"{self.config.base_url}/uud"
                response = await self.http.get(url)
                html = response.text

                # Parse the page
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")

                # Extract all PDF URLs
                pdf_urls = self.parser.extract_all_pdf_urls(soup)
                logger.info(f"Found {len(pdf_urls)} PDF files for UUD")

                if not pdf_urls:
                    logger.warning("No PDF URLs found for UUD page")
                    return 0

                # Create or update UUD peraturan entry
                uud_slug = "uud-1945"
                existing = self.db.get_peraturan(uud_slug)

                if not existing:
                    # Create new entry
                    uud = Peraturan(
                        slug=uud_slug,
                        jenis="UUD",
                        nomor="-",
                        tahun=1945,
                        tentang="Undang-Undang Dasar Negara Republik Indonesia Tahun 1945",
                        source_url=url,
                        pdf_url=pdf_urls[0] if pdf_urls else None,  # Primary PDF
                        status="Berlaku",
                    )
                    self.db.upsert_peraturan(uud)
                    logger.info(f"Created UUD entry: {uud_slug}")

                # Add all PDFs as attachments
                added = 0
                for i, pdf_url in enumerate(pdf_urls):
                    # Determine version from filename
                    version = self._extract_uud_version(pdf_url)
                    self.db.add_attachment(
                        slug=uud_slug,
                        pdf_url=pdf_url,
                        version=version,
                    )
                    added += 1
                    logger.debug(f"Added attachment: {version} -> {pdf_url}")

                logger.info(f"UUD crawl complete: {added} PDFs added as attachments")
                return added

        finally:
            self.db.close()

    def _extract_uud_version(self, pdf_url: str) -> str:
        """Extract UUD version from PDF URL.

        Args:
            pdf_url: PDF URL like 'https://peraturan.go.id/files/1945/UUD 1945 Perubahan Kedua.pdf'

        Returns:
            Version string like 'original', 'amendment_1', 'amendment_2', etc.
        """
        import urllib.parse
        filename = urllib.parse.unquote(pdf_url.split("/")[-1].replace(".pdf", ""))

        # Map Indonesian to version codes
        version_map = {
            "UUD1945": "original",
            "UUD 1945": "original",
            "Perubahan Pertama": "amendment_1",
            "Perubahan Kedua": "amendment_2",
            "Perubahan Ketiga": "amendment_3",
            "Perubahan Keempat": "amendment_4",
        }

        for key, version in version_map.items():
            if key in filename:
                return version

        # Default to filename-based version
        return filename.lower().replace(" ", "_")


async def run_crawler(
    resume: bool = False,
    limit: int = 0,
    jenis: Optional[str] = None,
    delay: float = 1.0,
    verbose: bool = False,
) -> CrawlState:
    """Run the crawler with the given options.

    Args:
        resume: Resume from previous state
        limit: Maximum items to crawl
        jenis: Document type filter
        delay: Delay between requests
        verbose: Enable verbose logging

    Returns:
        Final crawl state
    """
    config = Config(delay=delay)

    if verbose:
        config.log_level = "DEBUG"

    crawler = Crawler(config=config)
    return await crawler.crawl(resume=resume, limit=limit, jenis_filter=jenis)
