# module-kind: code
"""Cross-cutting preset invariant audits run at module load.

Extracted from ``presets.py``. Each audit takes the merged preset tuple
as an argument and raises ``ValueError`` on a violation so a
misconfiguration fails the import rather than reaching runtime.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.providers.preset_models import CloudPreset, LocalPreset

logger = get_logger(__name__)


def _audit_duplicate_names(
    presets: tuple[CloudPreset | LocalPreset, ...],
) -> None:
    """Reject duplicate ``name`` values across the merged preset tuple.

    Args:
        presets: The merged featured + soft preset tuple.

    Raises:
        ValueError: If two or more presets share the same ``name``.
    """
    seen: dict[str, CloudPreset | LocalPreset] = {}
    for preset in presets:
        if preset.name in seen:
            other = seen[preset.name]
            msg = f"Duplicate preset name {preset.name!r}: {other!r} and {preset!r}"
            logger.error(
                CONFIG_VALIDATION_FAILED,
                model="PROVIDER_PRESETS",
                check="duplicate_name",
                preset_name=preset.name,
                error=msg,
            )
            raise ValueError(msg)
        seen[preset.name] = preset


def _audit_namespace_collisions(
    presets: tuple[CloudPreset | LocalPreset, ...],
) -> None:
    """Reject soft presets that duplicate a featured ``litellm_provider``.

    Multiple presets sharing one ``litellm_provider`` is allowed by
    design for re-uses such as ollama (``ollama`` and ``ollama-cloud``)
    and the local presets that share a chat-completions wire protocol
    (``lm-studio`` / ``vllm``).
    A collision is only rejected when both sides are CloudPresets and
    at least one is a soft preset, because that means the auto-derive
    layer leaked a duplicate of a featured entry.

    Args:
        presets: The merged featured + soft preset tuple.

    Raises:
        ValueError: If a soft ``CloudPreset`` duplicates a featured
            ``CloudPreset``'s ``litellm_provider``.
    """
    seen: dict[str, CloudPreset | LocalPreset] = {}
    for preset in presets:
        if preset.litellm_provider not in seen:
            seen[preset.litellm_provider] = preset
            continue
        other = seen[preset.litellm_provider]
        both_cloud = isinstance(preset, CloudPreset) and isinstance(other, CloudPreset)
        either_soft = not (preset.is_featured and other.is_featured)
        if both_cloud and either_soft:
            msg = (
                f"Duplicate litellm_provider {preset.litellm_provider!r} "
                f"between {other.name!r} and {preset.name!r}; soft "
                f"presets must dedupe against featured."
            )
            logger.error(
                CONFIG_VALIDATION_FAILED,
                model="PROVIDER_PRESETS",
                check="soft_duplicates_featured_namespace",
                preset_name=preset.name,
                other_preset_name=other.name,
                litellm_provider=preset.litellm_provider,
                error=msg,
            )
            raise ValueError(msg)


def _audit_featured_order(
    presets: tuple[CloudPreset | LocalPreset, ...],
) -> None:
    """Reject any featured preset that appears after a soft preset.

    The API contract surfaces featured entries first (driving the
    wizard's primary-grid / more-providers split).

    Args:
        presets: The merged featured + soft preset tuple.

    Raises:
        ValueError: If any featured preset appears after a soft preset
            in the tuple.
    """
    saw_soft = False
    for preset in presets:
        if not preset.is_featured:
            saw_soft = True
        elif saw_soft:
            msg = (
                f"Featured preset {preset.name!r} appears after a soft preset; "
                "PROVIDER_PRESETS must list featured entries first."
            )
            logger.error(
                CONFIG_VALIDATION_FAILED,
                model="PROVIDER_PRESETS",
                check="featured_after_soft",
                preset_name=preset.name,
                error=msg,
            )
            raise ValueError(msg)


def audit_presets(presets: tuple[CloudPreset | LocalPreset, ...]) -> None:
    """Validate cross-cutting preset invariants at module load.

    Catches mistakes that the per-instance Pydantic validators cannot
    see; raises :class:`ValueError` on any violation so a
    misconfiguration fails the import rather than reaching runtime.

    Args:
        presets: The merged featured + soft preset tuple.
    """
    _audit_duplicate_names(presets)
    _audit_namespace_collisions(presets)
    _audit_featured_order(presets)
