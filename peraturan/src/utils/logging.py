"""Logging configuration for Peraturan Crawler."""

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    format_string: Optional[str] = None,
) -> logging.Logger:
    """Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARN, ERROR)
        log_file: Optional path to log file
        format_string: Optional custom format string

    Returns:
        Configured root logger
    """
    if format_string is None:
        format_string = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"

    # Get numeric level
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger("peraturan")
    root_logger.setLevel(numeric_level)

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(logging.Formatter(format_string))
    root_logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
        file_handler.setFormatter(logging.Formatter(format_string))
        root_logger.addHandler(file_handler)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name under the peraturan namespace.

    Args:
        name: Logger name (will be prefixed with 'peraturan.')

    Returns:
        Logger instance
    """
    return logging.getLogger(f"peraturan.{name}")
