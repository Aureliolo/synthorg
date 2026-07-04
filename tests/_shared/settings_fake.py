"""In-memory settings-service stand-in for tests.

The organization team-navigation read/write path exercises ``get`` /
``get_versioned`` / ``set`` over ``(namespace, key)`` with compare-and-set
semantics (``expected_updated_at``). This fake implements exactly that
surface, tracking a monotonic version token per key so a CAS write with a
stale token raises :class:`VersionConflictError`, letting tests drive the
real optimistic-concurrency path through
:class:`~synthorg.organization._team_service.TeamService` without booting
the settings persistence stack.
"""

from types import SimpleNamespace

from synthorg.core.domain_errors import VersionConflictError
from synthorg.settings.errors import SettingNotFoundError


class FakeSettingsService:
    """Stateful in-memory stand-in with per-key CAS version tokens."""

    def __init__(self, initial: dict[tuple[str, str], str] | None = None) -> None:
        # (namespace, key) -> (value, version-token)
        self._store: dict[tuple[str, str], tuple[str, str]] = {
            key: (value, "1") for key, value in (initial or {}).items()
        }
        self._counter = 1

    async def get(self, namespace: str, key: str) -> SimpleNamespace:
        """Return a settings-value-shaped object or raise if absent.

        Returns:
            A namespace object carrying the stored ``value`` + version.

        Raises:
            SettingNotFoundError: When ``(namespace, key)`` is unset.
        """
        try:
            value, version = self._store[namespace, key]
        except KeyError as exc:
            msg = f"Setting {namespace}/{key} not found"
            raise SettingNotFoundError(msg) from exc
        return SimpleNamespace(value=value, updated_at=version)

    async def get_versioned(self, namespace: str, key: str) -> tuple[str, str]:
        """Return ``(value, version)`` for CAS; ``("", "")`` when unset.

        Returns:
            The stored value + version token, or the first-write sentinel
            ``("", "")`` when the key has never been written.
        """
        return self._store.get((namespace, key), ("", ""))

    async def set(
        self,
        namespace: str,
        key: str,
        value: str,
        *,
        expected_updated_at: str | None = None,
    ) -> None:
        """Store ``value``, enforcing compare-and-set when a token is given.

        Raises:
            VersionConflictError: When ``expected_updated_at`` is supplied
                and does not match the key's current version token.
        """
        current = self._store.get((namespace, key))
        current_version = current[1] if current is not None else ""
        if expected_updated_at is not None and expected_updated_at != current_version:
            msg = (
                f"Version conflict on {namespace}/{key}: "
                f"expected {expected_updated_at!r}, have {current_version!r}"
            )
            raise VersionConflictError(msg)
        self._counter += 1
        self._store[namespace, key] = (value, str(self._counter))

    def force_version_bump(self, namespace: str, key: str) -> None:
        """Bump a key's version token in place, leaving its value unchanged.

        Simulates a concurrent external writer landing between a CAS
        caller's read and its guarded write, so a compare-and-set test can
        drive a mid-flight :class:`VersionConflictError` (and the retry that
        follows) deterministically.
        """
        value, _ = self._store.get((namespace, key), ("", ""))
        self._counter += 1
        self._store[namespace, key] = (value, str(self._counter))


__all__ = ["FakeSettingsService"]
