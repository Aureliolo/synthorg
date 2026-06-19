"""Coverage for ``synthorg.workers.__main__._resolve_worker_count``.

The worker process is a separate entry point launched via
``python -m synthorg.workers``; it has no ``SettingsService`` in
scope. Worker count is sourced from the explicit ``--workers`` flag,
then the ``SYNTHORG_WORKERS`` env var, then the registered default.
The env-var name must match the registered
``workers.count.env_var_override``.
"""

import pytest

from synthorg.workers.__main__ import (
    _DEFAULT_WORKER_COUNT,
    _build_parser,
    _resolve_http_timeout,
    _resolve_worker_count,
)

pytestmark = pytest.mark.unit


def test_explicit_flag_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``--workers`` flag short-circuits env and default."""
    monkeypatch.setenv("SYNTHORG_WORKERS", "8")
    assert _resolve_worker_count(explicit=4) == 4


def test_env_used_when_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SYNTHORG_WORKERS`` env var resolves when no flag is supplied."""
    monkeypatch.setenv("SYNTHORG_WORKERS", "8")
    assert _resolve_worker_count(explicit=None) == 8


def test_default_when_neither_flag_nor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to the registered default when env and flag are absent."""
    monkeypatch.delenv("SYNTHORG_WORKERS", raising=False)
    assert _resolve_worker_count(explicit=None) == _DEFAULT_WORKER_COUNT


def test_legacy_worker_count_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy ``SYNTHORG_WORKER_COUNT`` name must NOT be consulted.

    The registered env-var override for ``workers.count`` is
    ``SYNTHORG_WORKERS``. The legacy name predates the consolidation
    of every Cat-2 env var onto the auto-derived ``SYNTHORG_<NS>_<KEY>``
    shape and is intentionally not honoured.
    """
    monkeypatch.delenv("SYNTHORG_WORKERS", raising=False)
    monkeypatch.setenv("SYNTHORG_WORKER_COUNT", "16")
    assert _resolve_worker_count(explicit=None) == _DEFAULT_WORKER_COUNT


def test_invalid_env_returns_none_for_caller_to_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-integer env returns None so the caller can emit a structured error."""
    monkeypatch.setenv("SYNTHORG_WORKERS", "not-a-number")
    assert _resolve_worker_count(explicit=None) is None


# ── _resolve_http_timeout: flag > env > registered default ────────
#
# Same Cat-2 boot-knob contract as worker count: the worker
# subprocess has no SettingsService, so the executor HTTP timeout is
# sourced from the explicit ``--http-timeout-seconds`` flag, then
# ``SYNTHORG_WORKER_HTTP_TIMEOUT_SECONDS``, then the registered
# ``workers.executor_http_timeout_seconds`` default (60.0). The
# env-var name must match that setting's ``env_var_override``.

_REGISTERED_HTTP_TIMEOUT_DEFAULT = 60.0


def test_http_timeout_explicit_flag_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit ``--http-timeout-seconds`` overrides env + default."""
    monkeypatch.setenv("SYNTHORG_WORKER_HTTP_TIMEOUT_SECONDS", "120")
    assert _resolve_http_timeout(explicit=5.0) == 5.0


def test_http_timeout_env_used_when_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var resolves when no flag is supplied."""
    monkeypatch.setenv("SYNTHORG_WORKER_HTTP_TIMEOUT_SECONDS", "120")
    assert _resolve_http_timeout(explicit=None) == 120.0


def test_http_timeout_default_when_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registered default resolves when neither flag nor env is set."""
    monkeypatch.delenv("SYNTHORG_WORKER_HTTP_TIMEOUT_SECONDS", raising=False)
    assert _resolve_http_timeout(explicit=None) == _REGISTERED_HTTP_TIMEOUT_DEFAULT


def test_http_timeout_invalid_env_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-numeric env returns None so the caller emits a structured error."""
    monkeypatch.setenv("SYNTHORG_WORKER_HTTP_TIMEOUT_SECONDS", "not-a-number")
    assert _resolve_http_timeout(explicit=None) is None


def test_nats_url_default_matches_registry_not_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-env nats-url default resolves to the registry value, not localhost.

    The argparse default routes through the bootstrap resolver so the
    worker observes the registry default ``nats://nats:4222`` and stays
    in lockstep with the API rather than diverging to a hard-coded
    ``nats://localhost:4222``.
    """
    monkeypatch.delenv("SYNTHORG_NATS_URL", raising=False)
    args = _build_parser().parse_args([])
    assert args.nats_url == "nats://nats:4222"


def test_nats_url_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SYNTHORG_NATS_URL`` overrides the registered default."""
    monkeypatch.setenv("SYNTHORG_NATS_URL", "nats://custom:4242")
    args = _build_parser().parse_args([])
    assert args.nats_url == "nats://custom:4242"
