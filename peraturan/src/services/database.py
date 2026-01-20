"""SQLite database management for Peraturan Crawler."""

import sqlite3
from pathlib import Path
from typing import Optional, Iterator
from contextlib import contextmanager

from src.config import Config, config as default_config
from src.models.peraturan import Peraturan
from src.models.state import CrawlState, DownloadState, FailedItem
from src.utils.logging import get_logger

logger = get_logger("database")


class Database:
    """SQLite database manager for peraturan data."""

    def __init__(self, config: Optional[Config] = None):
        """Initialize database manager.

        Args:
            config: Configuration object (uses default if not provided)
        """
        self.config = config or default_config
        self.db_path = self.config.db_path
        self._connection: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Connect to the database and create tables if needed."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30.0,  # Wait up to 30 seconds for locked database
        )
        self._connection.row_factory = sqlite3.Row

        # Enable WAL mode for better concurrency
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")

        self._create_tables()
        logger.info(f"Connected to database: {self.db_path}")

    def close(self) -> None:
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None
            logger.info("Database connection closed")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """Context manager for database transactions.

        Yields:
            Database cursor

        Raises:
            RuntimeError: If not connected
        """
        if not self._connection:
            raise RuntimeError("Database not connected. Call connect() first.")

        cursor = self._connection.cursor()
        try:
            yield cursor
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        with self.transaction() as cursor:
            # Peraturan table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS peraturan (
                    slug TEXT PRIMARY KEY,
                    jenis TEXT NOT NULL,
                    nomor TEXT NOT NULL,
                    tahun INTEGER NOT NULL,
                    tentang TEXT NOT NULL,
                    pemrakarsa TEXT,
                    tempat_penetapan TEXT,
                    tanggal_penetapan TEXT,
                    pejabat_penetapan TEXT,
                    tahun_pengundangan INTEGER,
                    nomor_pengundangan TEXT,
                    nomor_tambahan TEXT,
                    tanggal_pengundangan TEXT,
                    pejabat_pengundangan TEXT,
                    status TEXT DEFAULT 'Berlaku',
                    pdf_url TEXT,
                    local_pdf_path TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    source_url TEXT NOT NULL
                )
            """)

            # Indexes for search performance
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_peraturan_jenis ON peraturan(jenis)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_peraturan_tahun ON peraturan(tahun)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_peraturan_status ON peraturan(status)"
            )

            # Crawl state table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS crawl_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_page INTEGER DEFAULT 0,
                    total_count INTEGER DEFAULT 0,
                    completed_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    started_at TEXT,
                    last_updated_at TEXT,
                    completed_at TEXT,
                    status TEXT DEFAULT 'idle'
                )
            """)
            cursor.execute("INSERT OR IGNORE INTO crawl_state (id) VALUES (1)")

            # Download state table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS download_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    total_pdfs INTEGER DEFAULT 0,
                    completed_count INTEGER DEFAULT 0,
                    skipped_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    started_at TEXT,
                    last_updated_at TEXT,
                    completed_at TEXT,
                    status TEXT DEFAULT 'idle'
                )
            """)
            cursor.execute("INSERT OR IGNORE INTO download_state (id) VALUES (1)")

            # Failed items table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS failed_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    failed_at TEXT DEFAULT (datetime('now')),
                    last_retry_at TEXT,
                    UNIQUE(url, item_type)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_failed_retry ON failed_items(retry_count)"
            )

            # Attachments table (for UUD with multiple PDFs, translations, etc.)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL,
                    pdf_url TEXT NOT NULL,
                    version TEXT,
                    local_path TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(slug, pdf_url),
                    FOREIGN KEY (slug) REFERENCES peraturan(slug) ON DELETE CASCADE
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_attachments_slug ON attachments(slug)"
            )

            # Translations table (for official English translations)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS translations (
                    id TEXT PRIMARY KEY,
                    original_slug TEXT NOT NULL,
                    original_title TEXT,
                    translated_title TEXT NOT NULL,
                    language TEXT DEFAULT 'en',
                    pdf_url TEXT,
                    local_pdf_path TEXT,
                    translator TEXT,
                    translation_date TEXT,
                    source_url TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (original_slug) REFERENCES peraturan(slug) ON DELETE CASCADE
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_translations_original ON translations(original_slug)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_translations_language ON translations(language)"
            )

        logger.debug("Database tables created/verified")

    # Peraturan CRUD operations

    def upsert_peraturan(self, peraturan: Peraturan) -> None:
        """Insert or update a peraturan record."""
        with self.transaction() as cursor:
            cursor.execute("""
                INSERT INTO peraturan (
                    slug, jenis, nomor, tahun, tentang, pemrakarsa,
                    tempat_penetapan, tanggal_penetapan, pejabat_penetapan,
                    tahun_pengundangan, nomor_pengundangan, nomor_tambahan,
                    tanggal_pengundangan, pejabat_pengundangan, status,
                    pdf_url, local_pdf_path, source_url, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(slug) DO UPDATE SET
                    jenis=excluded.jenis, nomor=excluded.nomor, tahun=excluded.tahun,
                    tentang=excluded.tentang, pemrakarsa=excluded.pemrakarsa,
                    tempat_penetapan=excluded.tempat_penetapan,
                    tanggal_penetapan=excluded.tanggal_penetapan,
                    pejabat_penetapan=excluded.pejabat_penetapan,
                    tahun_pengundangan=excluded.tahun_pengundangan,
                    nomor_pengundangan=excluded.nomor_pengundangan,
                    nomor_tambahan=excluded.nomor_tambahan,
                    tanggal_pengundangan=excluded.tanggal_pengundangan,
                    pejabat_pengundangan=excluded.pejabat_pengundangan,
                    status=excluded.status, pdf_url=excluded.pdf_url,
                    local_pdf_path=excluded.local_pdf_path,
                    source_url=excluded.source_url,
                    updated_at=datetime('now')
            """, (
                peraturan.slug, peraturan.jenis, peraturan.nomor, peraturan.tahun,
                peraturan.tentang, peraturan.pemrakarsa, peraturan.tempat_penetapan,
                peraturan.tanggal_penetapan, peraturan.pejabat_penetapan,
                peraturan.tahun_pengundangan, peraturan.nomor_pengundangan,
                peraturan.nomor_tambahan, peraturan.tanggal_pengundangan,
                peraturan.pejabat_pengundangan, peraturan.status,
                peraturan.pdf_url, peraturan.local_pdf_path, peraturan.source_url,
            ))

    def get_peraturan(self, slug: str) -> Optional[Peraturan]:
        """Get a peraturan by slug."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        cursor = self._connection.cursor()
        cursor.execute("SELECT * FROM peraturan WHERE slug = ?", (slug,))
        row = cursor.fetchone()
        cursor.close()

        if row:
            return Peraturan.from_dict(dict(row))
        return None

    def get_all_peraturan(
        self,
        limit: int = 0,
        offset: int = 0,
        jenis: Optional[str] = None,
        has_pdf: Optional[bool] = None,
    ) -> list[Peraturan]:
        """Get all peraturan records with optional filters."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        query = "SELECT * FROM peraturan WHERE 1=1"
        params: list = []

        if jenis:
            query += " AND jenis = ?"
            params.append(jenis)

        if has_pdf is True:
            query += " AND local_pdf_path IS NOT NULL"
        elif has_pdf is False:
            query += " AND local_pdf_path IS NULL"

        query += " ORDER BY tahun DESC, nomor ASC"

        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
            if offset > 0:
                query += " OFFSET ?"
                params.append(offset)

        cursor = self._connection.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()

        return [Peraturan.from_dict(dict(row)) for row in rows]

    def count_peraturan(self, jenis: Optional[str] = None) -> int:
        """Count total peraturan records."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        query = "SELECT COUNT(*) FROM peraturan"
        params: list = []

        if jenis:
            query += " WHERE jenis = ?"
            params.append(jenis)

        cursor = self._connection.cursor()
        cursor.execute(query, params)
        count = cursor.fetchone()[0]
        cursor.close()
        return count

    def update_local_pdf_path(self, slug: str, path: str) -> None:
        """Update the local PDF path for a peraturan."""
        with self.transaction() as cursor:
            cursor.execute(
                "UPDATE peraturan SET local_pdf_path = ?, updated_at = datetime('now') WHERE slug = ?",
                (path, slug),
            )

    # Crawl state operations

    def get_crawl_state(self) -> CrawlState:
        """Get current crawl state."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        cursor = self._connection.cursor()
        cursor.execute("SELECT * FROM crawl_state WHERE id = 1")
        row = cursor.fetchone()
        cursor.close()

        if row:
            return CrawlState(**dict(row))
        return CrawlState()

    def update_crawl_state(self, state: CrawlState) -> None:
        """Update crawl state."""
        with self.transaction() as cursor:
            cursor.execute("""
                UPDATE crawl_state SET
                    last_page = ?, total_count = ?, completed_count = ?,
                    failed_count = ?, started_at = ?, last_updated_at = ?,
                    completed_at = ?, status = ?
                WHERE id = 1
            """, (
                state.last_page, state.total_count, state.completed_count,
                state.failed_count, state.started_at, state.last_updated_at,
                state.completed_at, state.status,
            ))

    # Download state operations

    def get_download_state(self) -> DownloadState:
        """Get current download state."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        cursor = self._connection.cursor()
        cursor.execute("SELECT * FROM download_state WHERE id = 1")
        row = cursor.fetchone()
        cursor.close()

        if row:
            return DownloadState(**dict(row))
        return DownloadState()

    def update_download_state(self, state: DownloadState) -> None:
        """Update download state."""
        with self.transaction() as cursor:
            cursor.execute("""
                UPDATE download_state SET
                    total_pdfs = ?, completed_count = ?, skipped_count = ?,
                    failed_count = ?, started_at = ?, last_updated_at = ?,
                    completed_at = ?, status = ?
                WHERE id = 1
            """, (
                state.total_pdfs, state.completed_count, state.skipped_count,
                state.failed_count, state.started_at, state.last_updated_at,
                state.completed_at, state.status,
            ))

    # Failed items operations

    def add_failed_item(self, item: FailedItem) -> None:
        """Add or update a failed item."""
        with self.transaction() as cursor:
            cursor.execute("""
                INSERT INTO failed_items (url, item_type, error_message, retry_count, failed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url, item_type) DO UPDATE SET
                    error_message = excluded.error_message,
                    retry_count = retry_count + 1,
                    last_retry_at = datetime('now')
            """, (
                item.url, item.item_type, item.error_message,
                item.retry_count, item.failed_at,
            ))

    def get_failed_items(
        self,
        item_type: Optional[str] = None,
        max_retries: int = 3,
        limit: int = 100,
    ) -> list[FailedItem]:
        """Get failed items for retry."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        query = "SELECT * FROM failed_items WHERE retry_count < ?"
        params: list = [max_retries]

        if item_type:
            query += " AND item_type = ?"
            params.append(item_type)

        query += " ORDER BY failed_at ASC LIMIT ?"
        params.append(limit)

        cursor = self._connection.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()

        return [FailedItem(**dict(row)) for row in rows]

    def remove_failed_item(self, url: str, item_type: str) -> None:
        """Remove a failed item after successful retry."""
        with self.transaction() as cursor:
            cursor.execute(
                "DELETE FROM failed_items WHERE url = ? AND item_type = ?",
                (url, item_type),
            )

    def count_failed_items(self, item_type: Optional[str] = None) -> int:
        """Count failed items."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        query = "SELECT COUNT(*) FROM failed_items"
        params: list = []

        if item_type:
            query += " WHERE item_type = ?"
            params.append(item_type)

        cursor = self._connection.cursor()
        cursor.execute(query, params)
        count = cursor.fetchone()[0]
        cursor.close()
        return count

    # Statistics

    def get_statistics(self) -> dict:
        """Get database statistics."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        cursor = self._connection.cursor()

        # Total count
        cursor.execute("SELECT COUNT(*) FROM peraturan")
        total = cursor.fetchone()[0]

        # Count by type
        cursor.execute("""
            SELECT jenis, COUNT(*) as count
            FROM peraturan
            GROUP BY jenis
            ORDER BY count DESC
        """)
        by_type = {row["jenis"]: row["count"] for row in cursor.fetchall()}

        # Count with PDF URL (crawled)
        cursor.execute(
            "SELECT COUNT(*) FROM peraturan WHERE pdf_url IS NOT NULL"
        )
        with_pdf_url = cursor.fetchone()[0]

        # Count with PDF (downloaded)
        cursor.execute(
            "SELECT COUNT(*) FROM peraturan WHERE local_pdf_path IS NOT NULL AND local_pdf_path != ''"
        )
        with_pdf = cursor.fetchone()[0]

        # Failed items
        cursor.execute("SELECT COUNT(*) FROM failed_items")
        failed = cursor.fetchone()[0]

        cursor.close()

        return {
            "total": total,
            "by_type": by_type,
            "with_pdf_url": with_pdf_url,
            "with_pdf": with_pdf,
            "failed_items": failed,
        }

    def get_count_by_jenis(self, jenis: str) -> int:
        """Get document count for a specific jenis (document type).

        Args:
            jenis: Document type (e.g., "UNDANG-UNDANG", "PERPPU")

        Returns:
            Count of documents for the specified type
        """
        if not self._connection:
            raise RuntimeError("Database not connected")

        cursor = self._connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM peraturan WHERE jenis = ?",
            (jenis,)
        )
        count = cursor.fetchone()[0]
        cursor.close()
        return count

    # Attachments operations (for UUD with multiple PDFs)

    def add_attachment(
        self,
        slug: str,
        pdf_url: str,
        version: Optional[str] = None,
        local_path: Optional[str] = None,
    ) -> None:
        """Add an attachment (additional PDF) for a peraturan.

        Args:
            slug: Peraturan slug (foreign key)
            pdf_url: URL of the PDF file
            version: Version identifier (e.g., 'original', 'amendment_1')
            local_path: Local file path after download
        """
        with self.transaction() as cursor:
            cursor.execute("""
                INSERT INTO attachments (slug, pdf_url, version, local_path)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(slug, pdf_url) DO UPDATE SET
                    version = excluded.version,
                    local_path = COALESCE(excluded.local_path, attachments.local_path)
            """, (slug, pdf_url, version, local_path))

    def get_attachments(self, slug: str) -> list[dict]:
        """Get all attachments for a peraturan.

        Args:
            slug: Peraturan slug

        Returns:
            List of attachment dictionaries
        """
        if not self._connection:
            raise RuntimeError("Database not connected")

        cursor = self._connection.cursor()
        cursor.execute(
            "SELECT * FROM attachments WHERE slug = ? ORDER BY version",
            (slug,)
        )
        rows = cursor.fetchall()
        cursor.close()
        return [dict(row) for row in rows]

    def update_attachment_path(self, slug: str, pdf_url: str, local_path: str) -> None:
        """Update local path for an attachment.

        Args:
            slug: Peraturan slug
            pdf_url: PDF URL to identify the attachment
            local_path: Local file path
        """
        with self.transaction() as cursor:
            cursor.execute(
                "UPDATE attachments SET local_path = ? WHERE slug = ? AND pdf_url = ?",
                (local_path, slug, pdf_url),
            )

    def get_attachments_without_local_path(self, limit: int = 100) -> list[dict]:
        """Get attachments that need to be downloaded.

        Args:
            limit: Maximum number of attachments to return

        Returns:
            List of attachment dictionaries
        """
        if not self._connection:
            raise RuntimeError("Database not connected")

        cursor = self._connection.cursor()
        cursor.execute("""
            SELECT a.*, p.jenis
            FROM attachments a
            JOIN peraturan p ON a.slug = p.slug
            WHERE a.local_path IS NULL
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        cursor.close()
        return [dict(row) for row in rows]
