"""Unit tests for Postgres Atlas URL helpers and revisions resolution."""

import subprocess
import urllib.parse

import pytest
from pydantic import SecretStr

from synthorg.persistence import atlas
from synthorg.persistence.atlas import _redact_url, _revisions_dir_postgres
from synthorg.persistence.config import PostgresConfig


def _cfg(**overrides: object) -> PostgresConfig:
    defaults: dict[str, object] = {
        "database": "synthorg",
        "username": "pg_user",
        "password": SecretStr("pg_secret"),
    }
    defaults.update(overrides)
    return PostgresConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestToPostgresUrl:
    def test_default_url_uses_require_ssl(self) -> None:
        cfg = _cfg()
        url = atlas.to_postgres_url(cfg)
        assert url.startswith("postgres://")
        parsed = urllib.parse.urlparse(url)
        assert parsed.scheme == "postgres"
        assert parsed.hostname == "localhost"
        assert parsed.port == 5432
        assert parsed.path == "/synthorg"
        assert parsed.username == "pg_user"
        assert parsed.password == "pg_secret"
        query = urllib.parse.parse_qs(parsed.query)
        assert query["sslmode"] == ["require"]
        assert query["application_name"] == ["synthorg"]

    def test_custom_host_port(self) -> None:
        cfg = _cfg(host="db.internal", port=6432)
        url = atlas.to_postgres_url(cfg)
        parsed = urllib.parse.urlparse(url)
        assert parsed.hostname == "db.internal"
        assert parsed.port == 6432

    @pytest.mark.parametrize(
        "mode",
        ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"],
    )
    def test_all_ssl_modes_propagate(self, mode: str) -> None:
        cfg = _cfg(ssl_mode=mode)
        url = atlas.to_postgres_url(cfg)
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        assert query["sslmode"] == [mode]

    def test_custom_application_name_propagates(self) -> None:
        cfg = _cfg(application_name="synthorg-api")
        url = atlas.to_postgres_url(cfg)
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        assert query["application_name"] == ["synthorg-api"]

    @pytest.mark.parametrize(
        ("raw", "expected_decoded"),
        [
            ("has space", "has space"),
            ("has/slash", "has/slash"),
            ("has@at", "has@at"),
            ("has:colon", "has:colon"),
            ("has?q=1&b=2", "has?q=1&b=2"),
            ("has#hash", "has#hash"),
            ("!@#$%^&*()", "!@#$%^&*()"),
            ("utf8 \u00e9 \u4e2d", "utf8 \u00e9 \u4e2d"),
        ],
    )
    def test_special_characters_in_password_are_encoded(
        self,
        raw: str,
        expected_decoded: str,
    ) -> None:
        cfg = _cfg(password=SecretStr(raw))
        url = atlas.to_postgres_url(cfg)
        parsed = urllib.parse.urlparse(url)
        # urlparse does not decode percent-encoding on credentials;
        # unquote before comparing.
        assert urllib.parse.unquote(parsed.password or "") == expected_decoded
        # Ensure the raw password does not appear literally in the URL
        # (except for trivially safe characters).
        if any(c in raw for c in "@:/?# %!"):
            assert raw not in url

    @pytest.mark.parametrize(
        ("raw", "expected_decoded"),
        [
            ("user space", "user space"),
            ("user@host", "user@host"),
            ("user:colon", "user:colon"),
        ],
    )
    def test_special_characters_in_username_are_encoded(
        self,
        raw: str,
        expected_decoded: str,
    ) -> None:
        cfg = _cfg(username=raw)
        url = atlas.to_postgres_url(cfg)
        parsed = urllib.parse.urlparse(url)
        assert urllib.parse.unquote(parsed.username or "") == expected_decoded
        # Raw username with reserved chars must not appear literally.
        assert raw not in url

    def test_database_name_with_special_chars(self) -> None:
        cfg = _cfg(database="tenant a_42")
        url = atlas.to_postgres_url(cfg)
        parsed = urllib.parse.urlparse(url)
        assert urllib.parse.unquote(parsed.path.lstrip("/")) == "tenant a_42"

    def test_does_not_leak_raw_password_via_repr(self) -> None:
        cfg = _cfg(password=SecretStr("supersecret"))
        # to_postgres_url unwraps the secret intentionally; verify the raw
        # SecretStr repr still redacts.
        assert "supersecret" not in repr(cfg.password)


@pytest.mark.unit
class TestRevisionsDirPostgres:
    def test_returns_file_url(self) -> None:
        url = _revisions_dir_postgres()
        assert url.startswith("file://")

    def test_points_at_postgres_revisions_package(self) -> None:
        url = _revisions_dir_postgres()
        assert "postgres/revisions" in url or "postgres\\revisions" in url

    def test_is_distinct_from_sqlite_revisions(self) -> None:
        from synthorg.persistence.atlas import _revisions_dir

        sqlite_url = _revisions_dir()
        postgres_url = _revisions_dir_postgres()
        assert sqlite_url != postgres_url


@pytest.mark.unit
class TestRedactUrl:
    def test_redacts_postgres_url(self) -> None:
        url = "postgres://user:secret@host:5432/db?sslmode=require"
        redacted = _redact_url(url)
        assert "secret" not in redacted
        assert "user" not in redacted
        assert redacted.startswith("postgres://")

    def test_redacts_postgresql_url(self) -> None:
        url = "postgresql://user:secret@host:5432/db"
        redacted = _redact_url(url)
        assert "secret" not in redacted
        assert redacted.startswith("postgresql://")

    def test_redacts_sqlite_url(self) -> None:
        """Backward compatibility: existing SQLite redaction still works."""
        url = "sqlite://C:/path/to/synthorg.db"
        redacted = _redact_url(url)
        assert redacted.startswith("sqlite://")

    def test_handles_malformed_url(self) -> None:
        assert _redact_url("not_a_url") == "REDACTED"


@pytest.mark.unit
class TestMigrateBackendKwarg:
    """The atlas.migrate_* functions accept a backend kwarg.

    The actual Atlas subprocess is not invoked in these unit tests;
    they verify the kwarg is forwarded to ``_run_atlas`` via a
    monkey-patched stub, and that ``_run_atlas`` resolves it against
    the correct installed revisions package when no explicit
    ``revisions_url`` is passed.
    """

    async def test_migrate_apply_forwards_postgres_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        async def fake_run(
            *args: str,
            db_url: str | None = None,
            revisions_url: str | None = None,
            skip_lock: bool = False,
            backend: str = "sqlite",
        ) -> tuple[str, str]:
            captured["args"] = args
            captured["db_url"] = db_url
            captured["revisions_url"] = revisions_url
            captured["skip_lock"] = skip_lock
            captured["backend"] = backend
            return ("[]", "")

        monkeypatch.setattr(atlas, "_run_atlas", fake_run)

        await atlas.migrate_apply(
            "postgres://user:pw@host/db",
            backend="postgres",
        )

        assert captured["backend"] == "postgres"
        # migrate_apply does not pre-resolve revisions_url; resolution
        # happens inside _run_atlas.  Explicit None passes through.
        assert captured["revisions_url"] is None

    async def test_migrate_apply_defaults_to_sqlite_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        async def fake_run(
            *args: str,
            db_url: str | None = None,
            revisions_url: str | None = None,
            skip_lock: bool = False,
            backend: str = "sqlite",
        ) -> tuple[str, str]:
            captured["backend"] = backend
            return ("[]", "")

        monkeypatch.setattr(atlas, "_run_atlas", fake_run)

        await atlas.migrate_apply("sqlite://test.db")

        assert captured["backend"] == "sqlite"

    async def test_migrate_apply_explicit_revisions_url_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit revisions_url is forwarded verbatim to _run_atlas."""
        captured: dict[str, object] = {}

        async def fake_run(
            *args: str,
            db_url: str | None = None,
            revisions_url: str | None = None,
            skip_lock: bool = False,
            backend: str = "sqlite",
        ) -> tuple[str, str]:
            captured["revisions_url"] = revisions_url
            captured["backend"] = backend
            return ("[]", "")

        monkeypatch.setattr(atlas, "_run_atlas", fake_run)

        await atlas.migrate_apply(
            "postgres://host/db",
            backend="postgres",
            revisions_url="file:///custom/override",
        )

        assert captured["revisions_url"] == "file:///custom/override"
        assert captured["backend"] == "postgres"

    async def test_run_atlas_resolves_sqlite_revisions_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_run_atlas resolves to the sqlite revisions package when called
        with backend='sqlite' and no explicit revisions_url."""
        captured: dict[str, object] = {}

        class _FakeProc:
            returncode = 0

            def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
                del timeout
                return (b"[]", b"")

            def kill(self) -> None:
                pass

        def fake_popen(cmd: list[str], **_kwargs: object) -> _FakeProc:
            captured["cmd"] = cmd
            return _FakeProc()

        monkeypatch.setattr(atlas, "_require_atlas", lambda: "atlas")
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        await atlas._run_atlas("migrate", "status", backend="sqlite")

        cmd = captured["cmd"]
        assert isinstance(cmd, list)
        dir_idx = cmd.index("--dir")
        rev_url = str(cmd[dir_idx + 1])
        assert "sqlite/revisions" in rev_url or "sqlite\\revisions" in rev_url
        assert "postgres/revisions" not in rev_url
        assert "postgres\\revisions" not in rev_url

    async def test_run_atlas_resolves_postgres_revisions_with_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_run_atlas resolves to the postgres revisions package when called
        with backend='postgres' and no explicit revisions_url."""
        captured: dict[str, object] = {}

        class _FakeProc:
            returncode = 0

            def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
                del timeout
                return (b"[]", b"")

            def kill(self) -> None:
                pass

        def fake_popen(cmd: list[str], **_kwargs: object) -> _FakeProc:
            captured["cmd"] = cmd
            return _FakeProc()

        monkeypatch.setattr(atlas, "_require_atlas", lambda: "atlas")
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        await atlas._run_atlas("migrate", "status", backend="postgres")

        cmd = captured["cmd"]
        assert isinstance(cmd, list)
        dir_idx = cmd.index("--dir")
        rev_url = str(cmd[dir_idx + 1])
        assert "postgres/revisions" in rev_url or "postgres\\revisions" in rev_url
        assert "sqlite/revisions" not in rev_url
        assert "sqlite\\revisions" not in rev_url
