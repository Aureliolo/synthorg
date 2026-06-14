"""Backend-keyed construction dispatch for per-service repositories.

Replaces the open-coded ``if backend.kind == "sqlite": ... else: ...``
chains in the wiring helpers with a single dispatch that raises
``StrategyFactoryNotFoundError`` for an unregistered backend, mirroring
:class:`synthorg.persistence.registry.PersistenceBackendRegistry`. Each
per-service factory is a zero-arg closure that captures its own
construction dependencies (connection / pool, currency getter, etc.), so
the wiring site stays free of any backend-name literal.
"""

from collections.abc import Callable

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.persistence.protocol import PersistenceBackend


def build_for_backend[T](
    backend: PersistenceBackend,
    *,
    sqlite: Callable[[], T],
    postgres: Callable[[], T],
) -> T:
    """Dispatch to the per-backend factory for *backend*'s discriminator.

    Args:
        backend: The connected persistence backend.
        sqlite: Zero-arg factory building the SQLite repository.
        postgres: Zero-arg factory building the Postgres repository.

    Returns:
        Whatever the selected factory returns.

    Raises:
        StrategyFactoryNotFoundError: If ``backend.kind`` has no
            registered factory (a future backend added without a
            matching branch).
    """
    builders: dict[str, Callable[[], T]] = {"sqlite": sqlite, "postgres": postgres}
    builder = builders.get(backend.kind)
    if builder is None:
        msg = (
            f"No repository factory registered for persistence backend "
            f"{backend.kind!r}. Available: {', '.join(sorted(builders))}"
        )
        raise StrategyFactoryNotFoundError(msg, context={"backend": backend.kind})
    return builder()
