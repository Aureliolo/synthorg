# module-kind: code
"""Helpers for ``RootConfig`` cross-field validation.

Extracted from ``config/schema.py`` so the schema module stays within
its size budget. These are plain functions over a ``RootConfig``
instance, invoked from the model's ``@model_validator`` wrappers.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

if TYPE_CHECKING:
    from synthorg.config.schema import RootConfig

logger = get_logger(__name__)


def collect_model_refs(config: RootConfig) -> set[str]:
    """Build the unique model-ref set, raising on cross-provider collisions.

    Returns:
        The set of every model id and alias across all providers.

    Raises:
        ValueError: When the same id or alias is defined by more than one
            provider.
    """
    ref_to_provider: dict[str, str] = {}
    for prov_name, provider in config.providers.items():
        for model in provider.models:
            for ref in (model.id, model.alias):
                if ref is None:
                    continue
                existing_provider = ref_to_provider.get(ref)
                if existing_provider is not None:
                    # Same provider (e.g. ``alias == id`` on one model) is
                    # not a cross-provider collision; only a different
                    # provider claiming the same ref is ambiguous.
                    if existing_provider == prov_name:
                        continue
                    msg = (
                        f"Ambiguous model reference {ref!r}: "
                        f"defined in both {existing_provider!r} "
                        f"and {prov_name!r}"
                    )
                    logger.warning(
                        CONFIG_VALIDATION_FAILED,
                        model="RootConfig",
                        error=msg,
                    )
                    raise ValueError(msg)
                ref_to_provider[ref] = prov_name
    return set(ref_to_provider)
