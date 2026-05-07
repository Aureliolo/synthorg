"""Atlas CLI wrapper for declarative schema migrations.

Provides an async Python interface to the Atlas CLI for applying
migrations, checking status, and detecting drift.  Atlas manages
the ``atlas_schema_revisions`` table automatically.
"""

import asyncio
import contextlib
import importlib.resources
import json
import math
import os
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from synthorg.core.persistence_errors import MigrationError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_MIGRATION_COMPLETED,
    PERSISTENCE_MIGRATION_FAILED,
    PERSISTENCE_MIGRATION_STARTED,
)
from synthorg.persistence.config import PostgresConfig  # noqa: TC001

logger = get_logger(__name__)

_ATLAS_BIN = "atlas"

BackendName = Literal["sqlite", "postgres"]

# Maximum wall-clock seconds a single Atlas CLI invocation may run.
# Protects callers from hanging indefinitely if Atlas gets stuck on a
# lock, network hang, or malformed migration.  Applied via
# ``Popen.wait(timeout=...)`` in ``_run_atlas``.
_ATLAS_SUBPROCESS_TIMEOUT_SECONDS = 120.0
_ATLAS_KILL_GRACE_SECONDS = 5.0
"""Hardcoded fallback grace period for the Atlas subprocess after kill.

The value matches the default of the
``tools.atlas_kill_grace_timeout_seconds`` setting, but the runtime
wiring from ``ConfigResolver`` -> ``_run_atlas`` is not in place yet;
this constant is the only source of truth used by ``_run_atlas``
today. Threading the setting through ``_run_atlas`` / the Atlas call
sites is tracked as follow-up work on #1398/#1400; until then a
change to the setting has no effect on Atlas subprocess teardown.
"""


def _redact_url(url: str) -> str:
    """Return scheme + host hint, stripping path/credentials."""
    scheme_end = url.find("://")
    if scheme_end == -1:
        return "REDACTED"
    return f"{url[:scheme_end]}://..."


@dataclass(frozen=True)
class MigrateResult:
    """Result of an ``atlas migrate apply`` invocation.

    Attributes:
        applied_count: Number of migrations applied in this run.
        current_version: The version the database is now at.
        output: Raw stdout from the Atlas CLI.
    """

    applied_count: int
    current_version: str
    output: str


@dataclass(frozen=True)
class MigrateStatus:
    """Current migration status of a database.

    Attributes:
        current_version: Latest applied migration version.
        pending_count: Number of migrations not yet applied.
        output: Raw stdout from the Atlas CLI.
    """

    current_version: str
    pending_count: int
    output: str


def _to_posix(path: str) -> str:
    r"""Convert a filesystem path to forward-slash POSIX form.

    On Windows, ``C:\\Users\\foo`` becomes ``C:/Users/foo``.
    On POSIX systems this is a no-op.
    """
    return str(PurePosixPath(PureWindowsPath(path)))


def _path_to_file_url(path: str) -> str:
    r"""Convert a filesystem path to a ``file://`` URL.

    Handles Windows drive-letter paths (``C:\\...``) by converting
    to forward slashes.  Atlas on Windows expects ``file://C:/...``
    (two slashes), not the RFC 8089 ``file:///C:/...`` (three).
    """
    posix_str = _to_posix(path)
    return f"file://{posix_str}"


def to_sqlite_url(path: str) -> str:
    r"""Convert a filesystem path to an Atlas SQLite URL.

    Atlas expects ``sqlite://C:/path/db.sqlite`` on Windows
    (forward-slash path), not ``sqlite://C:\\...``.

    Args:
        path: Database file path (native OS format).

    Returns:
        Atlas-compatible ``sqlite://`` URL.

    Raises:
        MigrationError: If *path* is ``":memory:"`` -- Atlas runs as
            a separate process and cannot target an in-memory database
            opened by aiosqlite in this process.
    """
    if path == ":memory:":
        msg = (
            "Atlas cannot migrate in-memory databases -- "
            "it runs as a separate process.  Use a file-backed "
            "database path instead."
        )
        logger.error(PERSISTENCE_MIGRATION_FAILED, error=msg)
        raise MigrationError(msg)
    posix_str = _to_posix(path)
    return f"sqlite://{posix_str}"


def to_postgres_url(config: PostgresConfig) -> str:
    """Build an Atlas-compatible ``postgres://`` URL from a ``PostgresConfig``.

    Unwraps ``SecretStr`` credentials via ``get_secret_value`` and
    URL-encodes user, password, and database so special characters
    (``@``, ``/``, ``:``, spaces, non-ASCII) do not corrupt the
    connection string.  Appends ``sslmode``, ``application_name``,
    and ``connect_timeout`` as query parameters so migrations use the
    same connection semantics as the runtime backend.

    libpq's ``connect_timeout`` accepts only integer seconds with a
    minimum of 2 -- sub-second configured values are rounded up so a
    configured ``0.5`` second timeout does not silently become
    "wait indefinitely" via ``int(0.5) == 0``.

    Args:
        config: Postgres configuration model.

    Returns:
        A ``postgres://user:password@host:port/database`` URL with
        ``sslmode``, ``application_name``, and ``connect_timeout``
        query parameters.
    """
    user = urllib.parse.quote(config.username, safe="")
    password = urllib.parse.quote(config.password.get_secret_value(), safe="")
    database = urllib.parse.quote(config.database, safe="")
    connect_timeout = max(2, math.ceil(config.connect_timeout_seconds))
    query = urllib.parse.urlencode(
        {
            "sslmode": config.ssl_mode,
            "application_name": config.application_name,
            "connect_timeout": connect_timeout,
        }
    )
    return (
        f"postgres://{user}:{password}@{config.host}:{config.port}/{database}?{query}"
    )


_REVISIONS_PACKAGE: dict[BackendName, str] = {
    "sqlite": "synthorg.persistence.sqlite.revisions",
    "postgres": "synthorg.persistence.postgres.revisions",
}


def copy_revisions(dest: Path, *, backend: BackendName = "sqlite") -> str:
    """Copy the revisions directory to *dest* and return its ``file://`` URL.

    Creates an isolated copy of the migration files so that parallel
    Atlas processes do not fight over a shared directory lock.
    Intended for test fixtures using ``tmp_path``.

    Args:
        dest: Destination directory (e.g. ``tmp_path / "revisions"``).
        backend: Which backend's revisions to copy (``"sqlite"`` or
            ``"postgres"``).  Defaults to ``"sqlite"`` for backward
            compatibility.

    Returns:
        A ``file://`` URL pointing to the copy.

    Raises:
        MigrationError: If the copy fails (permissions, disk space,
            destination already exists).
    """
    src_ref = importlib.resources.files(_REVISIONS_PACKAGE[backend])
    try:
        shutil.copytree(str(src_ref), str(dest))
    except (OSError, shutil.Error) as exc:
        msg = f"Failed to copy migration revisions to {dest}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MigrationError(msg) from exc
    return _path_to_file_url(str(dest))


def _revisions_dir_for(backend: BackendName) -> str:
    """Return a ``file://`` URL pointing to the revisions directory.

    Uses ``importlib.resources`` to locate the ``revisions`` package
    inside the installed ``synthorg`` distribution.

    Args:
        backend: Which backend's revisions to resolve.

    Returns:
        A ``file://`` URL suitable for Atlas ``--dir``.

    Raises:
        MigrationError: If the revisions directory cannot be located.
    """
    pkg = _REVISIONS_PACKAGE[backend]
    try:
        ref = importlib.resources.files(pkg)
        path = str(ref)
    except (ModuleNotFoundError, TypeError) as exc:
        msg = f"Cannot locate migration revisions package: {pkg}"
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MigrationError(msg) from exc
    return _path_to_file_url(path)


def _revisions_dir() -> str:
    """Return the SQLite revisions URL (backward-compatibility alias)."""
    return _revisions_dir_for("sqlite")


def _revisions_dir_postgres() -> str:
    """Return the Postgres revisions URL."""
    return _revisions_dir_for("postgres")


def _require_atlas() -> str:
    """Return the path to the Atlas binary, or raise.

    Raises:
        MigrationError: If the Atlas CLI is not found on ``PATH``.
    """
    path = shutil.which(_ATLAS_BIN)
    if path is None:
        msg = (
            "Atlas CLI not found on PATH. "
            "Install from https://atlasgo.io/getting-started"
        )
        logger.error(PERSISTENCE_MIGRATION_FAILED, error=msg)
        raise MigrationError(msg)
    return path


def _split_postgres_credentials(db_url: str) -> tuple[str, dict[str, str]]:
    """Strip the password from a postgres URL and return it as an env var.

    Atlas accepts URLs with inline credentials, but passing the
    password on the command line leaks it into ``ps``/Task Manager
    and shell history for any local user on the machine.  libpq
    (which Atlas uses for Postgres) honors ``PGPASSWORD`` as a
    fallback when the URL has no password, so we strip the password
    out of the URL and hand it to Atlas via the environment instead.

    Non-postgres URLs (e.g. ``sqlite://...``) are returned unchanged
    with an empty env dict.

    Args:
        db_url: Original Atlas-format URL, possibly containing a
            password.

    Returns:
        Tuple of (url_without_password, extra_env_vars).
    """
    if not db_url.startswith(("postgres://", "postgresql://")):
        return db_url, {}
    parsed = urllib.parse.urlsplit(db_url)
    if parsed.password is None:
        return db_url, {}
    # Rebuild netloc without the password component.
    user = parsed.username or ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    userinfo = f"{user}@" if user else ""
    scrubbed = urllib.parse.urlunsplit(
        (
            parsed.scheme,
            f"{userinfo}{host}{port}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
    return scrubbed, {"PGPASSWORD": parsed.password}


async def _run_atlas(  # noqa: C901, PLR0915 -- subprocess lifecycle + cancellation handling
    *args: str,
    db_url: str | None = None,
    revisions_url: str | None = None,
    skip_lock: bool = False,
    backend: BackendName = "sqlite",
) -> tuple[str, str]:
    """Run an Atlas CLI command and return (stdout, stderr).

    The command is executed via ``subprocess.Popen`` inside
    ``asyncio.to_thread`` so the call stays compatible with any
    asyncio event loop.  Windows ``SelectorEventLoop`` (required by
    psycopg async mode) does not implement ``create_subprocess_exec``
    and Windows ``ProactorEventLoop`` is incompatible with psycopg,
    so ``asyncio.to_thread`` side-steps the split.

    Cancellation safety: if the surrounding task is cancelled while
    the subprocess is running, we terminate the Atlas process in the
    worker thread before re-raising ``CancelledError``, so neither
    the thread nor the Atlas subprocess are left dangling.

    A hard wall-clock timeout (``_ATLAS_SUBPROCESS_TIMEOUT_SECONDS``)
    guards against Atlas hanging forever on a stuck lock or network
    wait -- the backend's ``_lifecycle_lock`` is held for the
    duration of a migration, so an unbounded hang would freeze all
    connect/disconnect operations on the backend.

    Args:
        *args: Atlas subcommand and flags.
        db_url: Optional ``--url`` value for the target database.
            For ``postgres://`` URLs the password is stripped from
            the URL and passed to Atlas via ``PGPASSWORD`` to avoid
            leaking credentials into the local process list.
        revisions_url: Override for the ``--dir`` revisions URL.
            When ``None``, the installed package location for the
            selected *backend* is used.
        skip_lock: If ``True``, append ``--skip-lock`` to disable
            Atlas directory locking.  Use only in test fixtures
            where each worker has an isolated revisions copy.
        backend: Which backend's revisions directory to use when
            *revisions_url* is not provided.

    Returns:
        Tuple of (stdout, stderr) as decoded strings.

    Raises:
        MigrationError: If the command exits non-zero or times out.
    """
    atlas_bin = _require_atlas()
    rev_url = revisions_url or _revisions_dir_for(backend)

    safe_url = db_url
    extra_env: dict[str, str] = {}
    if db_url is not None:
        safe_url, extra_env = _split_postgres_credentials(db_url)

    cmd: list[str] = [
        atlas_bin,
        *args,
        "--dir",
        rev_url,
    ]
    if skip_lock:
        cmd.append("--skip-lock")
    if safe_url is not None:
        cmd.extend(["--url", safe_url])

    # Redact --url value to avoid leaking host/db/user in debug logs.
    safe_cmd: list[str] = []
    skip_next = False
    for token in cmd:
        if skip_next:
            safe_cmd.append("REDACTED")
            skip_next = False
        elif token == "--url":  # noqa: S105
            safe_cmd.append(token)
            skip_next = True
        else:
            safe_cmd.append(token)
    logger.debug(
        PERSISTENCE_MIGRATION_STARTED,
        command=" ".join(safe_cmd),
    )

    env = {**os.environ, **extra_env} if extra_env else None

    def _spawn_and_wait() -> tuple[int, bytes, bytes]:
        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            msg = f"Failed to start Atlas process: {safe_error_description(exc)}"
            raise MigrationError(msg) from exc
        try:
            out, err = proc.communicate(timeout=_ATLAS_SUBPROCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            # Drain pipes so the process actually exits.
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=_ATLAS_KILL_GRACE_SECONDS)
            # Atlas stderr on timeout can carry DSNs / SQL fragments
            # / connection URIs; the structured log emitted upstream
            # already records `error_type`+`error` via the MigrationError
            # path, and the message itself must not embed raw stderr.
            msg = (
                f"Atlas command timed out after "
                f"{_ATLAS_SUBPROCESS_TIMEOUT_SECONDS:.0f}s"
            )
            raise MigrationError(msg) from exc
        except BaseException:
            # Cancellation / KeyboardInterrupt / anything else -- do
            # not orphan the subprocess.
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=_ATLAS_KILL_GRACE_SECONDS)
            raise
        return proc.returncode, out, err

    try:
        returncode, stdout_bytes, stderr_bytes = await asyncio.to_thread(
            _spawn_and_wait,
        )
    except MigrationError as exc:
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            command=" ".join(safe_cmd),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise

    stdout = stdout_bytes.decode()
    stderr = stderr_bytes.decode()

    if returncode != 0:
        msg = f"Atlas command failed (exit {returncode})"
        logger.error(
            PERSISTENCE_MIGRATION_FAILED,
            exit_code=returncode,
            stderr_length=len(stderr),
        )
        raise MigrationError(msg)

    return stdout, stderr


async def migrate_apply(
    db_url: str,
    *,
    revisions_url: str | None = None,
    skip_lock: bool = False,
    backend: BackendName = "sqlite",
) -> MigrateResult:
    """Apply pending migrations to the target database.

    Invokes ``atlas migrate apply`` with JSON output parsing.

    Args:
        db_url: Atlas-format database URL
            (e.g. ``"sqlite://C:/path/to/db.sqlite"`` or
            ``"postgres://user:pw@host/db"``).
        revisions_url: Optional override for the ``--dir`` URL.
            Useful for parallel test isolation -- pass a copy of
            the revisions directory per worker to avoid directory
            lock contention.  When provided, *backend* is ignored
            for revisions resolution.
        skip_lock: If ``True``, pass ``--skip-lock`` to Atlas.
            Use only in test fixtures where each worker has an
            isolated revisions copy.  Defaults to ``False`` so
            production multi-process deployments are protected
            by Atlas's directory lock.
        backend: Which backend's installed revisions directory to
            use when *revisions_url* is not supplied.  Defaults to
            ``"sqlite"`` for backward compatibility.

    Returns:
        A ``MigrateResult`` with the number of applied migrations
        and the current schema version.

    Raises:
        MigrationError: If the migration fails or Atlas is unavailable.
    """
    logger.info(
        PERSISTENCE_MIGRATION_STARTED,
        db_url=_redact_url(db_url),
        backend=backend,
    )

    stdout, _ = await _run_atlas(
        "migrate",
        "apply",
        "--format",
        "{{ json .Applied }}",
        db_url=db_url,
        revisions_url=revisions_url,
        skip_lock=skip_lock,
        backend=backend,
    )

    applied_count = 0
    current_version = ""
    try:
        applied = json.loads(stdout) if stdout.strip() else []
        if isinstance(applied, list):
            applied_count = len(applied)
            if applied:
                last = applied[-1]
                current_version = (
                    last.get("Version", "") if isinstance(last, dict) else ""
                )
    except json.JSONDecodeError as exc:
        msg = "Atlas returned non-JSON output"
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            note=msg,
            output_length=len(stdout),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MigrationError(msg) from exc

    logger.info(
        PERSISTENCE_MIGRATION_COMPLETED,
        applied_count=applied_count,
        current_version=current_version,
    )

    return MigrateResult(
        applied_count=applied_count,
        current_version=current_version,
        output=stdout,
    )


async def migrate_status(
    db_url: str,
    *,
    backend: BackendName = "sqlite",
) -> MigrateStatus:
    """Check the migration status of a database.

    Invokes ``atlas migrate status`` to report applied and pending
    migrations.

    Args:
        db_url: Atlas-format database URL.
        backend: Which backend's revisions directory to consult.

    Returns:
        A ``MigrateStatus`` with current version and pending count.

    Raises:
        MigrationError: If the status check fails.
    """
    stdout, _ = await _run_atlas(
        "migrate",
        "status",
        "--format",
        "{{ json . }}",
        db_url=db_url,
        backend=backend,
    )

    current_version = ""
    pending_count = 0
    try:
        data = json.loads(stdout) if stdout.strip() else {}
        if isinstance(data, dict):
            current_version = data.get("Current", "")
            pending = data.get("Pending", [])
            pending_count = len(pending) if isinstance(pending, list) else 0
    except json.JSONDecodeError as exc:
        msg = "Atlas status returned non-JSON output"
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            note=msg,
            output_length=len(stdout),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MigrationError(msg) from exc

    return MigrateStatus(
        current_version=current_version,
        pending_count=pending_count,
        output=stdout,
    )


async def migrate_apply_baseline(
    db_url: str,
    version: str,
    *,
    backend: BackendName = "sqlite",
) -> None:
    """Mark a database as already at a specific migration version.

    Used for existing databases that already have the schema but no
    Atlas revision history.  This records the baseline version in
    ``atlas_schema_revisions`` without executing any SQL.

    Args:
        db_url: Atlas-format database URL.
        version: Migration version to mark as applied
            (e.g. ``"20260409170223"``).
        backend: Which backend's revisions directory to consult.

    Raises:
        MigrationError: If the baseline marking fails.
    """
    logger.info(
        PERSISTENCE_MIGRATION_STARTED,
        db_url=_redact_url(db_url),
        baseline=version,
        backend=backend,
    )

    await _run_atlas(
        "migrate",
        "apply",
        "--baseline",
        version,
        db_url=db_url,
        backend=backend,
    )

    logger.info(
        PERSISTENCE_MIGRATION_COMPLETED,
        baseline=version,
    )


async def migrate_rollback(
    db_url: str,
    *,
    version: str,
    backend: BackendName = "sqlite",
) -> None:
    """Roll back the database to a specific migration version.

    Invokes ``atlas migrate down`` to revert migrations applied
    after *version*.

    Args:
        db_url: Atlas-format database URL.
        version: Target version to roll back to.
        backend: Which backend's revisions directory to consult.

    Raises:
        MigrationError: If the rollback fails.
    """
    logger.info(
        PERSISTENCE_MIGRATION_STARTED,
        db_url=_redact_url(db_url),
        rollback_target=version,
        backend=backend,
    )

    await _run_atlas(
        "migrate",
        "down",
        "--to-version",
        version,
        db_url=db_url,
        backend=backend,
    )

    logger.info(
        PERSISTENCE_MIGRATION_COMPLETED,
        rollback_target=version,
    )
