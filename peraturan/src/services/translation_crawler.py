"""Translation crawler for e-penerjemahan.peraturan.go.id.

This module handles crawling of official translations from the Indonesian
legal translation system. The translation system requires authentication.

Site: https://e-penerjemahan.peraturan.go.id
Status: Requires login (username/password)

Future Implementation Notes:
- Need to implement session-based authentication
- Translations may have different structure than original laws
- Each translation links back to original regulation
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from src.config import Config, config as default_config
from src.services.database import Database
from src.services.parser import Parser
from src.utils.http import HttpClient
from src.utils.logging import get_logger

logger = get_logger("translation_crawler")

# Translation site configuration
TRANSLATION_BASE_URL = "https://e-penerjemahan.peraturan.go.id"
TRANSLATION_LOGIN_URL = f"{TRANSLATION_BASE_URL}/login/do_login"
TRANSLATION_LIST_URL = f"{TRANSLATION_BASE_URL}/terjemahan"  # Assumed endpoint


@dataclass
class Translation:
    """Official translation of an Indonesian regulation."""

    # Primary key
    id: str

    # Reference to original regulation
    original_slug: str  # Links to peraturan.slug
    original_title: str  # Indonesian title (tentang)

    # Translation info
    translated_title: str  # English title
    language: str = "en"  # ISO 639-1 code

    # File information
    pdf_url: Optional[str] = None
    local_pdf_path: Optional[str] = None

    # Metadata
    translator: Optional[str] = None
    translation_date: Optional[str] = None
    source_url: Optional[str] = None

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class TranslationCrawlerError(Exception):
    """Raised when translation crawling fails."""
    pass


class AuthenticationError(TranslationCrawlerError):
    """Raised when authentication fails."""
    pass


class TranslationCrawler:
    """Crawler for official translations from e-penerjemahan.peraturan.go.id.

    This crawler requires authentication to access the translation system.

    Usage:
        crawler = TranslationCrawler()
        await crawler.login(username, password)
        translations = await crawler.crawl()
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        database: Optional[Database] = None,
    ):
        """Initialize translation crawler.

        Args:
            config: Configuration object
            database: Database instance
        """
        self.config = config or default_config
        self.db = database or Database(self.config)
        self.http = HttpClient(self.config)
        self.parser = Parser()
        self._authenticated = False
        self._session_cookie: Optional[str] = None

    async def login(self, username: str, password: str) -> bool:
        """Authenticate with the translation system.

        Args:
            username: Login username
            password: Login password

        Returns:
            True if authentication successful

        Raises:
            AuthenticationError: If login fails
        """
        logger.info("Attempting login to translation system...")

        try:
            async with self.http.session() as session:
                # Submit login form
                response = await session.post(
                    TRANSLATION_LOGIN_URL,
                    data={
                        "getUsername": username,
                        "getPassword": password,
                    },
                    follow_redirects=True,
                )

                # Check if login successful (usually redirects to dashboard)
                if response.status_code == 200:
                    # Check for login failure indicators
                    if "Login" in response.text and "Username" in response.text:
                        raise AuthenticationError("Login failed - invalid credentials")

                    self._authenticated = True
                    # Store session cookie for subsequent requests
                    cookies = response.cookies
                    if "ci_session" in cookies:
                        self._session_cookie = cookies["ci_session"]

                    logger.info("Login successful")
                    return True
                else:
                    raise AuthenticationError(f"Login failed with status {response.status_code}")

        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"Login failed: {e}") from e

    async def crawl(self, limit: int = 0) -> list[Translation]:
        """Crawl translations from the translation system.

        Args:
            limit: Maximum number of translations to crawl (0 = unlimited)

        Returns:
            List of Translation objects

        Raises:
            TranslationCrawlerError: If not authenticated or crawl fails
        """
        if not self._authenticated:
            raise TranslationCrawlerError("Not authenticated. Call login() first.")

        logger.info("Crawling translations...")
        translations = []

        # TODO: Implement actual crawling logic when access is available
        # The implementation would:
        # 1. Fetch list page from TRANSLATION_LIST_URL
        # 2. Parse translation entries
        # 3. For each entry, fetch detail page
        # 4. Extract translation metadata and PDF link
        # 5. Create Translation objects

        logger.warning(
            "Translation crawling not fully implemented. "
            "Requires authenticated access to e-penerjemahan.peraturan.go.id"
        )

        return translations

    async def crawl_public_list(self) -> list[dict]:
        """Crawl publicly available translation list from peraturan.go.id.

        This method attempts to get any publicly visible translation info
        without authentication.

        Returns:
            List of translation info dictionaries
        """
        logger.info("Crawling public translation list...")

        translations = []

        try:
            async with self.http.session():
                # Try the public terjemahresmi page
                url = "https://peraturan.go.id/terjemahresmi"
                response = await self.http.get(url)

                from bs4 import BeautifulSoup
                soup = BeautifulSoup(response.text, "lxml")

                # Look for any regulation links that might have translations
                # Note: The actual structure needs to be determined from the live site
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    if "/id/" in href and "terjemah" in href.lower():
                        translations.append({
                            "url": href,
                            "title": link.get_text(strip=True),
                        })

                logger.info(f"Found {len(translations)} potential translations")

        except Exception as e:
            logger.warning(f"Failed to crawl public list: {e}")

        return translations


# Database schema extension for translations
TRANSLATION_TABLE_SQL = """
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
);

CREATE INDEX IF NOT EXISTS idx_translations_original ON translations(original_slug);
CREATE INDEX IF NOT EXISTS idx_translations_language ON translations(language);
"""


async def run_translation_crawler(
    username: Optional[str] = None,
    password: Optional[str] = None,
    limit: int = 0,
    public_only: bool = True,
) -> dict:
    """Run the translation crawler.

    Args:
        username: Login username (required if public_only=False)
        password: Login password (required if public_only=False)
        limit: Maximum translations to crawl
        public_only: If True, only crawl publicly available info

    Returns:
        Dictionary with crawl statistics
    """
    crawler = TranslationCrawler()

    if public_only:
        # Crawl only public list
        translations = await crawler.crawl_public_list()
        return {
            "mode": "public",
            "found": len(translations),
            "translations": translations,
        }
    else:
        # Full authenticated crawl
        if not username or not password:
            raise ValueError("Username and password required for authenticated crawl")

        await crawler.login(username, password)
        translations = await crawler.crawl(limit=limit)

        return {
            "mode": "authenticated",
            "crawled": len(translations),
        }
