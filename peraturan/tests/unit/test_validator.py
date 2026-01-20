"""Unit tests for URL validator service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from pathlib import Path
import tempfile
import json

from src.services.validator import (
    ValidationResult,
    URLChange,
    URLValidator,
    URLChangeHistory,
    JENIS_URL_CONFIG,
)


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_valid_result(self) -> None:
        """Test creating a valid result."""
        result = ValidationResult(
            url="https://peraturan.go.id/uu",
            status_code=200,
            is_valid=True,
            response_time_ms=150.5,
        )

        assert result.is_valid
        assert result.status_code == 200
        assert result.redirect_url is None
        assert result.error_message is None

    def test_invalid_result(self) -> None:
        """Test creating an invalid result."""
        result = ValidationResult(
            url="https://peraturan.go.id/invalid",
            status_code=404,
            is_valid=False,
            error_message="Not Found",
        )

        assert not result.is_valid
        assert result.status_code == 404

    def test_redirect_result(self) -> None:
        """Test result with redirect."""
        result = ValidationResult(
            url="https://peraturan.go.id/old-path",
            status_code=301,
            is_valid=True,
            redirect_url="https://peraturan.go.id/new-path",
        )

        assert result.is_valid
        assert result.redirect_url == "https://peraturan.go.id/new-path"

    def test_str_representation(self) -> None:
        """Test string representation."""
        result = ValidationResult(
            url="https://peraturan.go.id/uu",
            status_code=200,
            is_valid=True,
            response_time_ms=100.0,
        )

        result_str = str(result)
        assert "[OK]" in result_str
        assert "https://peraturan.go.id/uu" in result_str

    def test_str_with_redirect(self) -> None:
        """Test string representation with redirect."""
        result = ValidationResult(
            url="https://peraturan.go.id/old",
            status_code=301,
            is_valid=True,
            redirect_url="https://peraturan.go.id/new",
            response_time_ms=50.0,
        )

        result_str = str(result)
        assert "->" in result_str
        assert "new" in result_str


class TestURLChange:
    """Tests for URLChange dataclass."""

    def test_create_change(self) -> None:
        """Test creating a URL change."""
        change = URLChange(
            jenis="PERATURAN BADAN/LEMBAGA",
            old_url="/peraturan-badan",
            new_url="/perban",
            note="Redirect detected",
        )

        assert change.jenis == "PERATURAN BADAN/LEMBAGA"
        assert change.old_url == "/peraturan-badan"
        assert change.new_url == "/perban"

    def test_str_representation(self) -> None:
        """Test string representation."""
        change = URLChange(
            jenis="TEST",
            old_url="/old",
            new_url="/new",
        )

        change_str = str(change)
        assert "TEST" in change_str
        assert "/old" in change_str
        assert "->" in change_str
        assert "/new" in change_str

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        change = URLChange(
            jenis="TEST",
            old_url="/old",
            new_url="/new",
            note="Test note",
        )

        data = change.to_dict()
        assert data["jenis"] == "TEST"
        assert data["old_url"] == "/old"
        assert data["new_url"] == "/new"
        assert data["note"] == "Test note"
        assert "detected_at" in data
        assert data["applied"] is False

    def test_from_dict(self) -> None:
        """Test creation from dictionary."""
        data = {
            "jenis": "TEST",
            "old_url": "/old",
            "new_url": "/new",
            "detected_at": "2025-01-01T12:00:00",
            "note": "Test note",
            "applied": True,
        }

        change = URLChange.from_dict(data)
        assert change.jenis == "TEST"
        assert change.old_url == "/old"
        assert change.new_url == "/new"
        assert change.note == "Test note"
        assert change.applied is True


class TestURLChangeHistory:
    """Tests for URLChangeHistory class."""

    @pytest.fixture
    def temp_history_file(self) -> Path:
        """Create temporary history file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            return Path(f.name)

    def test_initial_state(self, temp_history_file: Path) -> None:
        """Test initial history state."""
        history = URLChangeHistory(temp_history_file)

        assert len(history.changes) == 0
        assert history.last_check is None

    def test_add_change(self, temp_history_file: Path) -> None:
        """Test adding a change."""
        history = URLChangeHistory(temp_history_file)

        change = URLChange(
            jenis="TEST",
            old_url="/old",
            new_url="/new",
        )
        history.add_change(change)

        assert len(history.changes) == 1
        assert history.changes[0].jenis == "TEST"

    def test_no_duplicate_changes(self, temp_history_file: Path) -> None:
        """Test that duplicate changes are not added."""
        history = URLChangeHistory(temp_history_file)

        change1 = URLChange(jenis="TEST", old_url="/old", new_url="/new")
        change2 = URLChange(jenis="TEST", old_url="/old", new_url="/new")

        history.add_change(change1)
        history.add_change(change2)  # Same jenis and new_url

        assert len(history.changes) == 1

    def test_save_and_load(self, temp_history_file: Path) -> None:
        """Test persistence of history."""
        history1 = URLChangeHistory(temp_history_file)
        change = URLChange(jenis="TEST", old_url="/old", new_url="/new")
        history1.add_change(change)
        history1.update_last_check()

        # Load in new instance
        history2 = URLChangeHistory(temp_history_file)
        assert len(history2.changes) == 1
        assert history2.changes[0].jenis == "TEST"
        assert history2.last_check is not None

    def test_get_pending_changes(self, temp_history_file: Path) -> None:
        """Test getting pending changes."""
        history = URLChangeHistory(temp_history_file)

        change1 = URLChange(jenis="TEST1", old_url="/old1", new_url="/new1")
        change2 = URLChange(jenis="TEST2", old_url="/old2", new_url="/new2", applied=True)

        history.changes = [change1, change2]

        pending = history.get_pending_changes()
        assert len(pending) == 1
        assert pending[0].jenis == "TEST1"

    def test_mark_applied(self, temp_history_file: Path) -> None:
        """Test marking change as applied."""
        history = URLChangeHistory(temp_history_file)

        change = URLChange(jenis="TEST", old_url="/old", new_url="/new")
        history.add_change(change)

        assert not history.changes[0].applied

        history.mark_applied(change)
        assert history.changes[0].applied

    def test_get_summary(self, temp_history_file: Path) -> None:
        """Test getting history summary."""
        history = URLChangeHistory(temp_history_file)

        change1 = URLChange(jenis="TEST1", old_url="/old1", new_url="/new1")
        change2 = URLChange(jenis="TEST2", old_url="/old2", new_url="/new2", applied=True)
        history.changes = [change1, change2]
        history.update_last_check()

        summary = history.get_summary()
        assert summary["total_changes"] == 2
        assert summary["pending_changes"] == 1
        assert summary["last_check"] is not None


class TestJenisURLConfig:
    """Tests for JENIS_URL_CONFIG."""

    def test_config_structure(self) -> None:
        """Test that config has required structure."""
        for jenis, config in JENIS_URL_CONFIG.items():
            assert "primary" in config
            assert "aliases" in config
            assert isinstance(config["aliases"], list)

    def test_known_alias_exists(self) -> None:
        """Test that known alias for PERATURAN BADAN/LEMBAGA exists."""
        config = JENIS_URL_CONFIG.get("PERATURAN BADAN/LEMBAGA")
        assert config is not None
        assert "/peraturan-badan" in config["aliases"]


class TestURLValidator:
    """Tests for URLValidator class."""

    @pytest.fixture
    def validator(self) -> URLValidator:
        """Create validator instance."""
        return URLValidator()

    @pytest.mark.asyncio
    async def test_validate_single_url_success(self, validator: URLValidator) -> None:
        """Test validating a successful URL."""
        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}

        with patch.object(validator.http, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await validator.validate_single_url("https://example.com/test")

            assert result.is_valid
            assert result.status_code == 200
            assert result.redirect_url is None

    @pytest.mark.asyncio
    async def test_validate_single_url_redirect(self, validator: URLValidator) -> None:
        """Test validating a URL with redirect."""
        mock_response = MagicMock()
        mock_response.status_code = 301
        mock_response.headers = {"location": "/new-path"}

        with patch.object(validator.http, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await validator.validate_single_url("https://peraturan.go.id/old")

            assert result.is_valid  # 3xx is still valid
            assert result.status_code == 301
            assert result.redirect_url is not None

    @pytest.mark.asyncio
    async def test_validate_single_url_error(self, validator: URLValidator) -> None:
        """Test validating a URL that raises error."""
        with patch.object(validator.http, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = Exception("Connection timeout")

            result = await validator.validate_single_url("https://example.com/error")

            assert not result.is_valid
            assert result.status_code == 0
            assert "Connection timeout" in result.error_message

    @pytest.mark.asyncio
    async def test_validate_single_url_404(self, validator: URLValidator) -> None:
        """Test validating a 404 URL."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.headers = {}

        with patch.object(validator.http, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await validator.validate_single_url("https://example.com/notfound")

            assert not result.is_valid
            assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_pre_crawl_check_all_valid(self, validator: URLValidator) -> None:
        """Test pre-crawl check when all URLs are valid."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}

        with patch.object(validator.http, 'session') as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock()
            mock_session.return_value.__aexit__ = AsyncMock()

            with patch.object(validator, 'validate_single_url', new_callable=AsyncMock) as mock_validate:
                mock_validate.return_value = ValidationResult(
                    url="https://example.com",
                    status_code=200,
                    is_valid=True,
                )

                all_valid, errors = await validator.pre_crawl_check(jenis_filter="UNDANG-UNDANG")

                assert all_valid
                assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_pre_crawl_check_unknown_jenis(self, validator: URLValidator) -> None:
        """Test pre-crawl check with unknown document type."""
        with patch.object(validator.http, 'session') as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock()
            mock_session.return_value.__aexit__ = AsyncMock()

            all_valid, errors = await validator.pre_crawl_check(jenis_filter="UNKNOWN_TYPE")

            assert not all_valid
            assert len(errors) == 1
            assert "Unknown document type" in errors[0]
