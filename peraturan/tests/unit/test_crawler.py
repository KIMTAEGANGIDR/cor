"""Unit tests for crawler functions."""

import pytest

from src.services.crawler import calculate_last_page


class TestCalculateLastPage:
    """Tests for pagination calculation."""

    def test_standard_pagination(self) -> None:
        """Test standard pagination calculation with 20 items per page."""
        test_cases = [
            (0, 1),      # 0 items = 1 page (minimum)
            (1, 1),      # 1 item = 1 page
            (19, 1),     # 19 items = 1 page
            (20, 1),     # 20 items = 1 page (exact fit)
            (21, 2),     # 21 items = 2 pages
            (40, 2),     # 40 items = 2 pages (exact fit)
            (100, 5),    # 100 items = 5 pages
            (1907, 96),  # Real example: UNDANG-UNDANG
            (19723, 987),  # Real example: PERATURAN MENTERI
            (6614, 331),   # Real example: PERATURAN BADAN/LEMBAGA
        ]

        for total, expected_pages in test_cases:
            result = calculate_last_page(total)
            assert result == expected_pages, f"Expected {expected_pages} pages for {total} items, got {result}"

    def test_negative_count_returns_one(self) -> None:
        """Test that negative count returns minimum of 1 page."""
        assert calculate_last_page(-1) == 1
        assert calculate_last_page(-100) == 1

    def test_custom_items_per_page(self) -> None:
        """Test pagination with custom items per page."""
        # 10 items per page
        assert calculate_last_page(100, items_per_page=10) == 10
        assert calculate_last_page(95, items_per_page=10) == 10
        assert calculate_last_page(91, items_per_page=10) == 10

        # 50 items per page
        assert calculate_last_page(100, items_per_page=50) == 2
        assert calculate_last_page(150, items_per_page=50) == 3

    def test_large_numbers(self) -> None:
        """Test pagination with very large numbers."""
        assert calculate_last_page(1000000) == 50000
        assert calculate_last_page(1000001) == 50001

    def test_edge_cases(self) -> None:
        """Test edge cases for pagination."""
        # Exactly on page boundary
        assert calculate_last_page(20) == 1
        assert calculate_last_page(40) == 2
        assert calculate_last_page(60) == 3

        # One more than page boundary
        assert calculate_last_page(21) == 2
        assert calculate_last_page(41) == 3
        assert calculate_last_page(61) == 4
