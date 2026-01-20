"""URL validation service for pre-crawl checks and change detection."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import Config, config as default_config
from src.services.crawler import JENIS_URL_MAP
from src.utils.http import HttpClient
from src.utils.logging import get_logger

logger = get_logger("validator")


# URL configuration with primary and alias paths
JENIS_URL_CONFIG = {
    "UNDANG-UNDANG": {
        "primary": "/uu",
        "aliases": [],
    },
    "PERPPU": {
        "primary": "/perppu",
        "aliases": [],
    },
    "PERATURAN PEMERINTAH": {
        "primary": "/pp",
        "aliases": [],
    },
    "PERATURAN PRESIDEN": {
        "primary": "/perpres",
        "aliases": [],
    },
    "PERATURAN MENTERI": {
        "primary": "/permen",
        "aliases": [],
    },
    "PERATURAN BADAN/LEMBAGA": {
        "primary": "/perban",
        "aliases": ["/peraturan-badan"],  # Known alias from previous URL
    },
}


@dataclass
class ValidationResult:
    """Result of validating a single URL."""

    url: str
    status_code: int
    is_valid: bool
    redirect_url: Optional[str] = None
    error_message: Optional[str] = None
    response_time_ms: float = 0.0
    checked_at: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        status = "OK" if self.is_valid else "FAILED"
        redirect = f" -> {self.redirect_url}" if self.redirect_url else ""
        return f"[{status}] {self.url}{redirect} ({self.response_time_ms:.0f}ms)"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "url": self.url,
            "status_code": self.status_code,
            "is_valid": self.is_valid,
            "redirect_url": self.redirect_url,
            "error_message": self.error_message,
            "response_time_ms": self.response_time_ms,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass
class URLChange:
    """Detected URL change or redirect."""

    jenis: str
    old_url: str
    new_url: str
    detected_at: datetime = field(default_factory=datetime.now)
    note: Optional[str] = None
    applied: bool = False  # Whether this change has been applied to JENIS_URL_MAP

    def __str__(self) -> str:
        return f"{self.jenis}: {self.old_url} -> {self.new_url}"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "jenis": self.jenis,
            "old_url": self.old_url,
            "new_url": self.new_url,
            "detected_at": self.detected_at.isoformat(),
            "note": self.note,
            "applied": self.applied,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "URLChange":
        """Create from dictionary."""
        return cls(
            jenis=data["jenis"],
            old_url=data["old_url"],
            new_url=data["new_url"],
            detected_at=datetime.fromisoformat(data["detected_at"]),
            note=data.get("note"),
            applied=data.get("applied", False),
        )


class URLChangeHistory:
    """Persistent history of URL changes."""

    def __init__(self, history_file: Path):
        """Initialize URL change history.

        Args:
            history_file: Path to the history JSON file
        """
        self.history_file = history_file
        self.changes: list[URLChange] = []
        self.last_check: Optional[datetime] = None
        self._load()

    def _load(self) -> None:
        """Load history from file."""
        if self.history_file.exists():
            try:
                data = json.loads(self.history_file.read_text())
                self.changes = [URLChange.from_dict(c) for c in data.get("changes", [])]
                if data.get("last_check"):
                    self.last_check = datetime.fromisoformat(data["last_check"])
            except Exception as e:
                logger.warning(f"Failed to load URL change history: {e}")

    def save(self) -> None:
        """Save history to file."""
        data = {
            "changes": [c.to_dict() for c in self.changes],
            "last_check": self.last_check.isoformat() if self.last_check else None,
        }
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(json.dumps(data, indent=2))

    def add_change(self, change: URLChange) -> None:
        """Add a new change to history."""
        # Check if similar change already exists
        for existing in self.changes:
            if existing.jenis == change.jenis and existing.new_url == change.new_url:
                return  # Already recorded

        self.changes.append(change)
        self.save()
        logger.warning(f"URL change detected and recorded: {change}")

    def get_pending_changes(self) -> list[URLChange]:
        """Get changes that haven't been applied yet."""
        return [c for c in self.changes if not c.applied]

    def mark_applied(self, change: URLChange) -> None:
        """Mark a change as applied."""
        for c in self.changes:
            if c.jenis == change.jenis and c.new_url == change.new_url:
                c.applied = True
        self.save()

    def update_last_check(self) -> None:
        """Update last check timestamp."""
        self.last_check = datetime.now()
        self.save()

    def get_summary(self) -> dict:
        """Get summary of change history."""
        return {
            "total_changes": len(self.changes),
            "pending_changes": len(self.get_pending_changes()),
            "last_check": self.last_check.isoformat() if self.last_check else None,
        }


class URLValidator:
    """Validates URLs before crawling and detects changes."""

    def __init__(self, config: Optional[Config] = None, track_history: bool = True):
        """Initialize validator.

        Args:
            config: Configuration object
            track_history: Whether to track URL change history
        """
        self.config = config or default_config
        self.http = HttpClient(self.config)

        # Initialize change history if tracking enabled
        self.history: Optional[URLChangeHistory] = None
        if track_history:
            history_file = self.config.data_dir / "url_change_history.json"
            self.history = URLChangeHistory(history_file)

    async def validate_single_url(self, url: str) -> ValidationResult:
        """Validate a single URL.

        Args:
            url: URL to validate

        Returns:
            ValidationResult with status and details
        """
        start_time = datetime.now()

        try:
            response = await self.http.get(url, follow_redirects=False)
            response_time = (datetime.now() - start_time).total_seconds() * 1000

            # Check for redirect
            redirect_url = None
            if response.status_code in (301, 302, 307, 308):
                redirect_url = response.headers.get("location")
                if redirect_url and redirect_url.startswith("/"):
                    redirect_url = f"{self.config.base_url}{redirect_url}"

            is_valid = 200 <= response.status_code < 400

            return ValidationResult(
                url=url,
                status_code=response.status_code,
                is_valid=is_valid,
                redirect_url=redirect_url,
                response_time_ms=response_time,
            )

        except Exception as e:
            response_time = (datetime.now() - start_time).total_seconds() * 1000
            return ValidationResult(
                url=url,
                status_code=0,
                is_valid=False,
                error_message=str(e),
                response_time_ms=response_time,
            )

    async def validate_url_map(self) -> dict[str, ValidationResult]:
        """Validate all URLs in JENIS_URL_MAP.

        Returns:
            Dictionary mapping jenis to ValidationResult
        """
        results = {}

        async with self.http.session():
            for jenis, path in JENIS_URL_MAP.items():
                url = f"{self.config.base_url}{path}"
                result = await self.validate_single_url(url)
                results[jenis] = result

                if result.is_valid:
                    logger.info(f"URL OK: {jenis} -> {url}")
                else:
                    logger.error(
                        f"URL FAILED: {jenis} -> {url} "
                        f"(status={result.status_code}, error={result.error_message})"
                    )

                if result.redirect_url:
                    logger.warning(
                        f"URL REDIRECT: {jenis} -> {url} redirects to {result.redirect_url}"
                    )

                # Small delay between checks to be polite
                await asyncio.sleep(0.5)

        return results

    async def detect_url_changes(self, record_history: bool = True) -> list[URLChange]:
        """Detect URL changes by checking for redirects.

        Args:
            record_history: Whether to record detected changes in history

        Returns:
            List of detected URL changes
        """
        changes = []

        async with self.http.session():
            for jenis, path in JENIS_URL_MAP.items():
                url = f"{self.config.base_url}{path}"
                result = await self.validate_single_url(url)

                # Detect redirect to different path
                if result.redirect_url and result.redirect_url != url:
                    new_path = result.redirect_url.replace(self.config.base_url, "")
                    change = URLChange(
                        jenis=jenis,
                        old_url=path,
                        new_url=new_path,
                        note="Redirect detected",
                    )
                    changes.append(change)
                    logger.warning(f"URL change detected: {change}")

                    # Record in history
                    if record_history and self.history:
                        self.history.add_change(change)

                # Detect 404 (URL no longer valid)
                if result.status_code == 404:
                    # Try to find working URL from aliases
                    working_url = await self._find_working_alias(jenis)

                    change = URLChange(
                        jenis=jenis,
                        old_url=path,
                        new_url=working_url or "UNKNOWN",
                        note="URL returns 404" + (" - alias found" if working_url else " - needs investigation"),
                    )
                    changes.append(change)

                    if working_url:
                        logger.warning(f"URL broken but alias found: {change}")
                    else:
                        logger.error(f"URL broken: {change}")

                    # Record in history
                    if record_history and self.history:
                        self.history.add_change(change)

                await asyncio.sleep(0.5)

        # Update last check timestamp
        if record_history and self.history:
            self.history.update_last_check()

        return changes

    async def _find_working_alias(self, jenis: str) -> Optional[str]:
        """Try to find a working URL from known aliases.

        Args:
            jenis: Document type to find alias for

        Returns:
            Working alias path or None
        """
        config = JENIS_URL_CONFIG.get(jenis)
        if not config:
            return None

        aliases = config.get("aliases", [])
        for alias in aliases:
            url = f"{self.config.base_url}{alias}"
            result = await self.validate_single_url(url)
            if result.is_valid:
                logger.info(f"Found working alias for {jenis}: {alias}")
                return alias

        return None

    def get_url_update_suggestions(self) -> list[dict]:
        """Get suggestions for updating JENIS_URL_MAP based on detected changes.

        Returns:
            List of suggested code changes
        """
        if not self.history:
            return []

        suggestions = []
        pending = self.history.get_pending_changes()

        for change in pending:
            if change.new_url != "UNKNOWN":
                suggestions.append({
                    "jenis": change.jenis,
                    "current_url": change.old_url,
                    "suggested_url": change.new_url,
                    "detected_at": change.detected_at.isoformat(),
                    "note": change.note,
                    "code_change": f'    "{change.jenis}": "{change.new_url}",',
                })

        return suggestions

    def get_change_report(self) -> str:
        """Generate a human-readable report of URL changes.

        Returns:
            Formatted report string
        """
        if not self.history:
            return "No change history available."

        summary = self.history.get_summary()
        report_lines = [
            "# URL Change Report",
            f"Last check: {summary['last_check'] or 'Never'}",
            f"Total changes: {summary['total_changes']}",
            f"Pending changes: {summary['pending_changes']}",
            "",
        ]

        if summary['pending_changes'] > 0:
            report_lines.append("## Pending Changes (require code update)")
            for change in self.history.get_pending_changes():
                report_lines.append(f"- {change}")

            report_lines.append("")
            report_lines.append("## Suggested Code Update for JENIS_URL_MAP:")
            report_lines.append("```python")
            for suggestion in self.get_url_update_suggestions():
                report_lines.append(suggestion['code_change'])
            report_lines.append("```")

        return "\n".join(report_lines)

    async def pre_crawl_check(self, jenis_filter: Optional[str] = None) -> tuple[bool, list[str]]:
        """Perform pre-crawl validation checks.

        Args:
            jenis_filter: Specific document type to check, or None for all

        Returns:
            Tuple of (all_valid, list_of_error_messages)
        """
        errors = []

        async with self.http.session():
            if jenis_filter:
                # Check specific type
                if jenis_filter not in JENIS_URL_MAP:
                    errors.append(f"Unknown document type: {jenis_filter}")
                    return False, errors

                path = JENIS_URL_MAP[jenis_filter]
                url = f"{self.config.base_url}{path}"
                result = await self.validate_single_url(url)

                if not result.is_valid:
                    errors.append(
                        f"{jenis_filter}: URL {url} returned {result.status_code}"
                    )
            else:
                # Check all types
                results = await self.validate_url_map()

                for jenis, result in results.items():
                    if not result.is_valid:
                        errors.append(
                            f"{jenis}: URL returned {result.status_code}"
                        )
                    if result.redirect_url:
                        errors.append(
                            f"{jenis}: URL redirects to {result.redirect_url}"
                        )

        all_valid = len(errors) == 0
        return all_valid, errors


async def validate_urls(
    jenis: Optional[str] = None,
    check_redirects: bool = True,
) -> tuple[bool, dict]:
    """Convenience function to validate URLs.

    Args:
        jenis: Specific document type to validate, or None for all
        check_redirects: Whether to check for redirects

    Returns:
        Tuple of (all_valid, results_dict)
    """
    validator = URLValidator()
    results = {}

    if jenis:
        # Validate specific jenis
        path = JENIS_URL_MAP.get(jenis)
        if not path:
            return False, {"error": f"Unknown jenis: {jenis}"}

        async with validator.http.session():
            url = f"{validator.config.base_url}{path}"
            result = await validator.validate_single_url(url)
            results[jenis] = {
                "url": url,
                "status_code": result.status_code,
                "is_valid": result.is_valid,
                "redirect_url": result.redirect_url,
                "error": result.error_message,
            }
            all_valid = result.is_valid
    else:
        # validate_url_map manages its own session
        validation_results = await validator.validate_url_map()
        all_valid = True

        for jenis_name, result in validation_results.items():
            results[jenis_name] = {
                "url": result.url,
                "status_code": result.status_code,
                "is_valid": result.is_valid,
                "redirect_url": result.redirect_url,
                "error": result.error_message,
            }
            if not result.is_valid:
                all_valid = False

    return all_valid, results
