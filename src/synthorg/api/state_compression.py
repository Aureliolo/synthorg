"""Live response-compression threshold primitive.

Owns the ``CompressionConfig`` object handed to Litestar. The compression
middleware reads ``config.minimum_size`` per response off that same object,
so mutating it in place is what makes the threshold operator-tunable without
rebuilding the app. Composed onto ``AppState`` as ``app_state.compression``.

It is an owner rather than a slice field because ``CompressionConfig`` is a
Litestar dataclass whose ``compression_facade`` annotation is a
``TYPE_CHECKING``-only forward reference: a Pydantic slice cannot build a
schema for it.
"""

from litestar.config.compression import CompressionConfig

from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED

logger = get_logger(__name__)


class CompressionState:
    """The live compression config, or ``None`` before the app is built."""

    __slots__ = ("_config",)

    def __init__(self) -> None:
        """Build with no config attached."""
        self._config: CompressionConfig | None = None

    @property
    def minimum_size(self) -> int | None:
        """Return the live threshold, or ``None`` before the app is built.

        Deliberately not the config object: handing that out would let a
        caller assign ``minimum_size`` directly and skip the positivity
        check the setter exists to apply.
        """
        config = self._config
        return None if config is None else config.minimum_size

    def install(self, config: CompressionConfig) -> None:
        """Attach the config handed to Litestar at app construction."""
        self._config = config

    def set_minimum_size(self, minimum_size: int) -> bool:
        """Retune the compression threshold on the live config.

        Args:
            minimum_size: New threshold in bytes; must be positive, matching
                the floor ``CompressionConfig.__post_init__`` enforces.

        Returns:
            ``True`` when applied, ``False`` when no app has been built yet
            (nothing is serving responses, so there is nothing to retune).

        Raises:
            ValueError: If *minimum_size* is not positive.
        """
        if minimum_size <= 0:
            msg = f"minimum_size must be positive, got {minimum_size}"
            raise ValueError(msg)
        config = self._config
        if config is None:
            return False
        previous = config.minimum_size
        config.minimum_size = minimum_size
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="compression_config",
            old_minimum_size=previous,
            new_minimum_size=minimum_size,
        )
        return True
