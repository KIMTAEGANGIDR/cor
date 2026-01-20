"""HTTP client wrapper for Peraturan Crawler."""

import asyncio
import random
from typing import Optional
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import httpx

from src.config import Config, config as default_config
from src.utils.logging import get_logger

logger = get_logger("http")


class RateLimitError(Exception):
    """Raised when rate limited by the server."""
    def __init__(self, wait_time: int, message: str = "Rate limited"):
        self.wait_time = wait_time
        super().__init__(f"{message}. Wait {wait_time}s")


class HttpClient:
    """HTTP client with rate limiting, User-Agent, and retry support."""

    def __init__(self, config: Optional[Config] = None):
        """Initialize HTTP client.

        Args:
            config: Configuration object (uses default if not provided)
        """
        self.config = config or default_config
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._last_request_time: float = 0
        self._consecutive_errors: int = 0

    @asynccontextmanager
    async def session(self) -> AsyncGenerator["HttpClient", None]:
        """Context manager for HTTP session.

        Yields:
            HttpClient instance with active session
        """
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": self.config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5,id;q=0.3",
            },
            timeout=httpx.Timeout(self.config.timeout),
            follow_redirects=True,
        )
        try:
            yield self
        finally:
            await self._client.aclose()
            self._client = None

    async def _apply_delay(self) -> None:
        """Apply delay between requests with random jitter to look more human."""
        current_time = asyncio.get_event_loop().time()
        elapsed = current_time - self._last_request_time

        # Base delay + random jitter
        base_delay = self.config.delay
        jitter = random.uniform(0, self.config.delay_jitter)
        total_delay = base_delay + jitter

        if elapsed < total_delay:
            wait_time = total_delay - elapsed
            logger.debug(f"Rate limiting: waiting {wait_time:.2f}s (delay={base_delay:.1f}s + jitter={jitter:.1f}s)")
            await asyncio.sleep(wait_time)

        self._last_request_time = asyncio.get_event_loop().time()

    async def get(
        self,
        url: str,
        follow_redirects: Optional[bool] = None,
    ) -> httpx.Response:
        """Make a GET request with rate limiting and concurrency control.

        Args:
            url: URL to request
            follow_redirects: Override default redirect behavior (None uses client default)

        Returns:
            HTTP response

        Raises:
            httpx.HTTPError: On HTTP errors
            RateLimitError: When rate limited
            RuntimeError: If client session is not active
        """
        if self._client is None:
            raise RuntimeError("HTTP client session not active. Use 'async with client.session():'")

        async with self._semaphore:
            await self._apply_delay()

            logger.debug(f"GET {url}")

            try:
                # Build request options
                request_kwargs = {}
                if follow_redirects is not None:
                    request_kwargs["follow_redirects"] = follow_redirects

                response = await self._client.get(url, **request_kwargs)

                logger.debug(f"Response: {response.status_code} ({len(response.content)} bytes)")

                # Handle rate limiting (429) and server errors (503)
                if response.status_code in (429, 503):
                    retry_after = response.headers.get("Retry-After", str(self.config.rate_limit_wait))
                    wait_time = int(retry_after) if retry_after.isdigit() else self.config.rate_limit_wait
                    logger.warning(f"Rate limited ({response.status_code}). Will wait {wait_time}s...")
                    self._consecutive_errors += 1
                    raise RateLimitError(wait_time, f"HTTP {response.status_code}")

                # Handle other client/server errors
                if response.status_code >= 400:
                    self._consecutive_errors += 1
                    logger.warning(f"HTTP error {response.status_code} for {url}")

                    # Check if we need to cooldown
                    if self._consecutive_errors >= self.config.consecutive_error_threshold:
                        cooldown = self.config.error_cooldown
                        logger.warning(
                            f"Too many consecutive errors ({self._consecutive_errors}). "
                            f"Cooling down for {cooldown}s..."
                        )
                        await asyncio.sleep(cooldown)
                        self._consecutive_errors = 0

                    response.raise_for_status()

                # Success - reset consecutive errors
                self._consecutive_errors = 0
                return response

            except httpx.TimeoutException as e:
                self._consecutive_errors += 1
                logger.warning(f"Timeout for {url}: {e}")
                raise
            except httpx.ConnectError as e:
                self._consecutive_errors += 1
                logger.warning(f"Connection error for {url}: {e}")
                raise

    async def download_file(
        self,
        url: str,
        dest_path: str,
        chunk_size: int = 8192,
    ) -> int:
        """Download a file to disk.

        Args:
            url: URL to download
            dest_path: Destination file path
            chunk_size: Chunk size for streaming download

        Returns:
            Total bytes downloaded

        Raises:
            httpx.HTTPError: On HTTP errors
        """
        if self._client is None:
            raise RuntimeError("HTTP client session not active. Use 'async with client.session():'")

        async with self._semaphore:
            await self._apply_delay()

            logger.debug(f"Downloading {url} -> {dest_path}")

            total_bytes = 0
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()

                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                        f.write(chunk)
                        total_bytes += len(chunk)

            logger.debug(f"Downloaded {total_bytes} bytes to {dest_path}")
            return total_bytes


# Default client instance
http_client = HttpClient()
