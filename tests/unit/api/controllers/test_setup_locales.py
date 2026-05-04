"""Tests for setup wizard locale configuration.

Covers available locales listing, name locale get/save endpoints,
and the _check_has_name_locales / _read_name_locales helpers.
"""

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from litestar.testing import TestClient


@pytest.mark.unit
class TestGetAvailableLocales:
    """GET /api/v1/setup/name-locales/available -- list available locales."""

    def test_returns_regions_and_display_names(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/setup/name-locales/available")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "regions" in data
        assert "display_names" in data
        assert isinstance(data["regions"], dict)
        assert isinstance(data["display_names"], dict)
        # Verify at least some regions and display names are present.
        assert len(data["regions"]) >= 5
        assert len(data["display_names"]) >= 50


@pytest.mark.unit
class TestGetNameLocales:
    """GET /api/v1/setup/name-locales -- get current locale configuration."""

    def test_returns_all_sentinel_when_not_configured(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns the ``__all__`` sentinel when no DB preference is stored.

        The endpoint returns the raw sentinel so the frontend can show
        the "All (worldwide)" toggle as ON.  Resolution to concrete
        locale codes happens only in the name-generation path.
        """
        resp = test_client.get("/api/v1/setup/name-locales")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["locales"] == ["__all__"]

    def test_returns_stored_locales_when_configured(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns stored locales when the setting is in the DB."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US", "fr_FR"]),
            now,
        )
        try:
            resp = test_client.get("/api/v1/setup/name-locales")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["locales"] == ["en_US", "fr_FR"]
        finally:
            repo._store.pop(("company", "name_locales"), None)

    def test_returns_all_sentinel_when_explicitly_stored(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns ``__all__`` sentinel when it is explicitly stored in DB.

        Verifies the round-trip: saving ``["__all__"]`` then reading it
        back returns the sentinel, not the full expanded list of
        concrete locale codes.
        """
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["__all__"]),
            now,
        )
        try:
            resp = test_client.get("/api/v1/setup/name-locales")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["locales"] == ["__all__"]
        finally:
            repo._store.pop(("company", "name_locales"), None)


@pytest.mark.unit
class TestSaveNameLocales:
    """PUT /api/v1/setup/name-locales -- save locale preferences."""

    def test_saves_valid_locales(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": ["en_US", "de_DE"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["locales"] == ["en_US", "de_DE"]

    def test_saves_all_sentinel(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": ["__all__"]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["locales"] == ["__all__"]

    def test_rejects_mixed_sentinel_with_explicit_codes(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Mixing __all__ with explicit locale codes returns 422."""
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": ["__all__", "en_US"]},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False

    def test_rejects_invalid_locale_codes(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": ["en_US", "invalid_XX"]},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False

    def test_rejects_empty_list(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Empty list is rejected by Pydantic min_length=1."""
        resp = test_client.put(
            "/api/v1/setup/name-locales",
            json={"locales": []},
        )
        assert resp.status_code == 400

    def test_rejects_save_after_setup_complete(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Saving locales after setup is complete returns 409."""
        app_state = test_client.app.state.app_state
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("api", "setup_complete")] = ("true", now)
        try:
            resp = test_client.put(
                "/api/v1/setup/name-locales",
                json={"locales": ["en_US"]},
            )
            assert resp.status_code == 409
        finally:
            repo._store.pop(("api", "setup_complete"), None)


@pytest.mark.unit
class TestCheckHasNameLocales:
    """Unit tests for the _check_has_name_locales helper."""

    async def test_returns_false_when_not_in_db(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Code default resolves as non-DATABASE source, returns False."""
        from synthorg.api.controllers.setup_helpers import (
            check_has_name_locales as _check_has_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        # Ensure the key is absent from DB so code default kicks in.
        repo = app_state.persistence._settings_repo
        repo._store.pop(("company", "name_locales"), None)

        result = await _check_has_name_locales(settings_svc)
        assert result is False

    async def test_returns_true_when_db_sourced_and_nonempty(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            check_has_name_locales as _check_has_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US"]),
            now,
        )
        try:
            result = await _check_has_name_locales(settings_svc)
            assert result is True
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_returns_false_on_generic_exception(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns False when get_entry raises a generic exception."""
        from synthorg.api.controllers.setup_helpers import (
            check_has_name_locales as _check_has_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service

        original = settings_svc.get_entry
        settings_svc.get_entry = AsyncMock(
            side_effect=RuntimeError("db connection lost"),
        )
        try:
            result = await _check_has_name_locales(settings_svc)
            assert result is False
        finally:
            settings_svc.get_entry = original

    async def test_returns_false_on_setting_not_found_error(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns False when get_entry raises SettingNotFoundError."""
        from synthorg.api.controllers.setup_helpers import (
            check_has_name_locales as _check_has_name_locales,
        )
        from synthorg.settings.errors import SettingNotFoundError

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service

        original = settings_svc.get_entry
        settings_svc.get_entry = AsyncMock(
            side_effect=SettingNotFoundError("company/name_locales"),
        )
        try:
            result = await _check_has_name_locales(settings_svc)
            assert result is False
        finally:
            settings_svc.get_entry = original


@pytest.mark.unit
class TestReadNameLocales:
    """Unit tests for the _read_name_locales helper."""

    async def test_returns_all_locales_when_db_absent_and_code_default(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """When DB key is absent, code default ["__all__"] resolves to all."""
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )
        from synthorg.templates.locales import ALL_LATIN_LOCALES

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        repo._store.pop(("company", "name_locales"), None)

        result = await _read_name_locales(settings_svc)
        # Code default is ["__all__"], resolve_locales returns all.
        assert result == list(ALL_LATIN_LOCALES)

    async def test_returns_none_when_setting_not_found(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Returns None when get_entry raises SettingNotFoundError."""
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )
        from synthorg.settings.errors import SettingNotFoundError

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service

        original = settings_svc.get_entry
        settings_svc.get_entry = AsyncMock(
            side_effect=SettingNotFoundError("company/name_locales"),
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result is None
        finally:
            settings_svc.get_entry = original

    async def test_returns_resolved_locales_when_valid(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US", "fr_FR"]),
            now,
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result == ["en_US", "fr_FR"]
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_returns_none_on_json_decode_error(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            "not-valid-json{{{",
            now,
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result is None
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_returns_none_on_non_list_json(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps({"not": "a list"}),
            now,
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result is None
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_filters_invalid_locales(
        self,
        test_client: TestClient[Any],
    ) -> None:
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US", "invalid_XX", "fr_FR"]),
            now,
        )
        try:
            result = await _read_name_locales(settings_svc)
            assert result == ["en_US", "fr_FR"]
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_resolve_false_returns_sentinel_as_is(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """With resolve=False, the __all__ sentinel passes through raw."""
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["__all__"]),
            now,
        )
        try:
            result = await _read_name_locales(
                settings_svc,
                resolve=False,
            )
            assert result == ["__all__"]
        finally:
            repo._store.pop(("company", "name_locales"), None)

    async def test_resolve_false_skips_validation_filtering(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """With resolve=False, invalid codes are not filtered out."""
        from synthorg.api.controllers.setup_helpers import (
            read_name_locales as _read_name_locales,
        )

        app_state = test_client.app.state.app_state
        settings_svc = app_state.settings_service
        repo = app_state.persistence._settings_repo
        now = datetime.now(UTC).isoformat()
        repo._store[("company", "name_locales")] = (
            json.dumps(["en_US", "invalid_XX"]),
            now,
        )
        try:
            result = await _read_name_locales(
                settings_svc,
                resolve=False,
            )
            assert result == ["en_US", "invalid_XX"]
        finally:
            repo._store.pop(("company", "name_locales"), None)
