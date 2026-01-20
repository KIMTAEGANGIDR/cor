"""Configuration management for Peraturan Crawler."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Application configuration with environment variable support."""

    # Database - SQLite
    db_path: Path = field(default_factory=lambda: Path(
        os.getenv("PERATURAN_DB_PATH", "./data/peraturan.db")
    ))

    # Database - Neo4j
    neo4j_uri: str = field(default_factory=lambda: os.getenv(
        "NEO4J_URI", "bolt://localhost:7687"
    ))
    neo4j_user: str = field(default_factory=lambda: os.getenv(
        "NEO4J_USER", "neo4j"
    ))
    neo4j_password: str = field(default_factory=lambda: os.getenv(
        "NEO4J_PASSWORD", "password123"
    ))
    neo4j_enabled: bool = field(default_factory=lambda: os.getenv(
        "NEO4J_ENABLED", "true"
    ).lower() == "true")

    # Data directories
    data_dir: Path = field(default_factory=lambda: Path(
        os.getenv("PERATURAN_DATA_DIR", "./data")
    ))

    @property
    def pdf_dir(self) -> Path:
        """Directory for downloaded PDFs."""
        return self.data_dir / "pdfs"

    @property
    def log_dir(self) -> Path:
        """Directory for log files."""
        return self.data_dir / "logs"

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("PERATURAN_LOG_LEVEL", "INFO"))
    log_file: Path | None = None

    # Crawling - Conservative settings to avoid blocking
    delay: float = field(default_factory=lambda: float(os.getenv("PERATURAN_DELAY", "3.0")))
    delay_jitter: float = 1.5  # Random jitter added to delay (0 to this value)
    max_concurrent: int = 1   # Single request at a time to be polite
    max_retries: int = 5
    backoff_factor: float = 3.0  # Longer backoff between retries
    timeout: float = 60.0  # Longer timeout for slow responses

    # Rate limiting protection
    rate_limit_wait: int = 120  # Wait time when rate limited (seconds)
    consecutive_error_threshold: int = 3  # Pause after this many consecutive errors
    error_cooldown: int = 300  # Cooldown time after consecutive errors (seconds)

    # HTTP
    user_agent: str = (
        "PeraturanCrawler/1.0 "
        "(+https://github.com/user/peraturan-crawler; contact@example.com)"
    )

    # Target site
    base_url: str = "https://peraturan.go.id"

    def __post_init__(self) -> None:
        """Ensure paths are Path objects and directories exist."""
        if isinstance(self.db_path, str):
            self.db_path = Path(self.db_path)
        if isinstance(self.data_dir, str):
            self.data_dir = Path(self.data_dir)
        if self.log_file and isinstance(self.log_file, str):
            self.log_file = Path(self.log_file)

    def ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


# Default configuration instance
config = Config()
