"""Coverage for ``synthorg.workers.__main__._resolve_worker_count``.

The worker process is a separate entry point launched via
``python -m synthorg.workers``; it has no ``SettingsService`` in
scope. Worker count is sourced from the explicit ``--workers`` flag,
then the ``SYNTHORG_WORKERS`` env var, then the registered default.
The env-var name must match the registered
``workers.count.env_var_override``.
"""

import pytest

from synthorg.workers.__main__ import _DEFAULT_WORKER_COUNT, _resolve_worker_count

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
