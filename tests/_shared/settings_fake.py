"""In-memory settings-service stand-in for tests.

The organization team-navigation read/write path only exercises
``get`` / ``set`` over ``(namespace, key)``, so this fake implements
just that surface: enough to round-trip the ``company.departments``
blob through :class:`~synthorg.organization._team_service.TeamService`
without booting the real settings persistence stack.
"""

from types import SimpleNamespace

from synthorg.settings.errors import SettingNotFoundError


class FakeSettingsService:
    """Stateful in-memory stand-in storing raw ``(namespace, key)`` values."""

    def __init__(self, initial: dict[tuple[str, str], str] | None = None) -> None:
        self._store: dict[tuple[str, str], str] = dict(initial or {})

    async def get(self, namespace: str, key: str) -> SimpleNamespace:
        """Return a settings-value-shaped object or raise if absent.

        Returns:
            A namespace object carrying the stored ``value`` string.

        Raises:
            SettingNotFoundError: When ``(namespace, key)`` is unset.
        """
        try:
            value = self._store[namespace, key]
        except KeyError as exc:
            msg = f"Setting {namespace}/{key} not found"
            raise SettingNotFoundError(msg) from exc
        return SimpleNamespace(value=value, updated_at="")

    async def set(self, namespace: str, key: str, value: str) -> None:
        """Store ``value`` under ``(namespace, key)``."""
        self._store[namespace, key] = value


__all__ = ["FakeSettingsService"]
