"""Data models for Peraturan Crawler."""

from src.models.peraturan import Peraturan
from src.models.state import CrawlState, DownloadState, FailedItem

__all__ = ["Peraturan", "CrawlState", "DownloadState", "FailedItem"]
