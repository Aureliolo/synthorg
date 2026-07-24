"""Repo-scope validation on the connection catalog's update path.

``model_copy(update=...)`` does not re-run validators, so the scope the
``Connection`` model enforces at construction would not be re-checked on a
PATCH. The catalog validates each entry itself, making the persistence
entry, not just the HTTP DTO, the boundary that holds the security scope.
"""

from collections.abc import Iterator

import pytest
from typeguard import suppress_type_checks

from synthorg.integrations.connections.catalog import _UNSET, ConnectionCatalog
from synthorg.integrations.errors import InvalidRepoScopeError

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _suppress_typeguard_for_stub_repo() -> Iterator[None]:
    """Suppress typeguard: the catalog is built with minimal doubles."""
    with suppress_type_checks():
        yield


class _StubRepo:
    """Minimal ``ConnectionRepository`` double; the scope check precedes I/O."""

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[object, ...]:
        del limit, offset
        return ()


class _StubSecretBackend:
    """No-op secret backend; the scope check never reaches it."""


def _catalog() -> ConnectionCatalog:
    return ConnectionCatalog(
        repository=_StubRepo(),  # type: ignore[arg-type]
        secret_backend=_StubSecretBackend(),  # type: ignore[arg-type]
    )


def _candidate(scope: tuple[str, ...] | object) -> dict[str, object]:
    return _catalog()._build_update_candidate(
        base_url=_UNSET,
        metadata=_UNSET,
        health_check_enabled=_UNSET,
        webhook_receipt_retention_days=_UNSET,
        sensitive=_UNSET,
        allowed_repos=scope,  # type: ignore[arg-type]
    )


class TestUpdateCandidateRepoScope:
    @pytest.mark.parametrize(
        "scope",
        [("acme/proj-1",), ("acme/*",), ("acme/proj-1", "org/repo"), ()],
        ids=["exact", "owner_glob", "multiple", "deny_all"],
    )
    def test_valid_scope_passes_through(self, scope: tuple[str, ...]) -> None:
        assert _candidate(scope)["allowed_repos"] == scope

    @pytest.mark.parametrize(
        "entry",
        ["*", "*/*", "*/repo", "acme", "a/b/c", "ac*/repo", "acme/pr..oj"],
        ids=[
            "bare_glob",
            "both_glob",
            "owner_glob",
            "no_slash",
            "multi_slash",
            "partial_glob_owner",
            "traversal",
        ],
    )
    def test_overbroad_entry_rejected(self, entry: str) -> None:
        with pytest.raises(InvalidRepoScopeError):
            _candidate((entry,))

    def test_omitted_scope_is_absent_from_candidate(self) -> None:
        assert "allowed_repos" not in _candidate(_UNSET)
