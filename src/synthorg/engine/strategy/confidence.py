"""Confidence calibration formatters.

Formats :class:`~synthorg.engine.strategy.models.ConfidenceMetadata`
into prompt-injectable text in various output formats.
"""

from typing import Protocol, runtime_checkable

from synthorg.engine.strategy.models import (
    ConfidenceConfig,
    ConfidenceFormat,
    ConfidenceMetadata,
)
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import STRATEGY_CONFIDENCE_FORMATTED

logger = get_logger(__name__)


# 4 impls (Structured/Narrative/Both/Probability) + _FORMATTERS factory dict
# + get_formatter() in this file.
@runtime_checkable
class ConfidenceFormatter(Protocol):
    """Protocol for formatting confidence metadata."""

    def format(self, *, metadata: ConfidenceMetadata) -> str:
        """Format confidence metadata into text.

        Args:
            metadata: Confidence information to format.

        Returns:
            Formatted text string.
        """
        ...


class StructuredFormatter:
    """Renders confidence as labeled fields."""

    def format(self, *, metadata: ConfidenceMetadata) -> str:
        """Format as structured text block.

        Returns:
            A Markdown bullet block listing the confidence level,
            range, assumptions, and uncertainty factors.
        """
        parts = [
            f"- **Confidence**: {metadata.level:.0%}",
            f"- **Range**: {metadata.range_lower:.0%} -- {metadata.range_upper:.0%}",
        ]
        if metadata.assumptions:
            parts.append("- **Key assumptions**:")
            parts.extend(f"  - {a}" for a in metadata.assumptions)
        if metadata.uncertainty_factors:
            parts.append("- **Uncertainty factors**:")
            parts.extend(f"  - {f}" for f in metadata.uncertainty_factors)

        result = "\n".join(parts)
        logger.debug(
            STRATEGY_CONFIDENCE_FORMATTED,
            format="structured",
            level=metadata.level,
        )
        return result


class NarrativeFormatter:
    """Renders confidence as prose paragraph."""

    def format(self, *, metadata: ConfidenceMetadata) -> str:
        """Format as narrative text.

        Returns:
            A prose paragraph describing confidence, range,
            assumptions, and uncertainty factors.
        """
        pct = f"{metadata.level:.0%}"
        low = f"{metadata.range_lower:.0%}"
        high = f"{metadata.range_upper:.0%}"

        parts = [
            f"I am {pct} confident in this recommendation (range: {low} to {high}).",
        ]

        if metadata.assumptions:
            joined = "; ".join(metadata.assumptions)
            parts.append(f"Key assumptions: {joined}.")

        if metadata.uncertainty_factors:
            joined = "; ".join(metadata.uncertainty_factors)
            parts.append(f"Uncertainty factors: {joined}.")

        result = " ".join(parts)
        logger.debug(
            STRATEGY_CONFIDENCE_FORMATTED,
            format="narrative",
            level=metadata.level,
        )
        return result


class BothFormatter:
    """Renders both structured and narrative formats."""

    def __init__(self) -> None:
        """Initialize with structured and narrative sub-formatters."""
        self._structured = StructuredFormatter()
        self._narrative = NarrativeFormatter()

    def format(self, *, metadata: ConfidenceMetadata) -> str:
        """Format as structured block followed by narrative.

        Returns:
            The structured block concatenated with the narrative
            paragraph (separated by a blank line).
        """
        structured = self._structured.format(metadata=metadata)
        narrative = self._narrative.format(metadata=metadata)
        result = f"{structured}\n\n{narrative}"
        logger.debug(
            STRATEGY_CONFIDENCE_FORMATTED,
            format="both",
            level=metadata.level,
        )
        return result


class ProbabilityFormatter:
    """Renders confidence as calibrated probability ranges."""

    def format(self, *, metadata: ConfidenceMetadata) -> str:
        """Format as probability range text.

        Returns:
            A probability-of-success line with a 90% CI range,
            assumptions, and uncertainty factors.
        """
        low = f"{metadata.range_lower:.0%}"
        high = f"{metadata.range_upper:.0%}"
        level = f"{metadata.level:.0%}"

        parts = [
            f"Probability of success: {level} (90% CI: {low} -- {high})",
        ]

        if metadata.assumptions:
            parts.append("Conditional on:")
            parts.extend(f"  - {a}" for a in metadata.assumptions)

        if metadata.uncertainty_factors:
            parts.append("Uncertainty factors:")
            parts.extend(f"  - {f}" for f in metadata.uncertainty_factors)

        result = "\n".join(parts)
        logger.debug(
            STRATEGY_CONFIDENCE_FORMATTED,
            format="probability",
            level=metadata.level,
        )
        return result


_FORMATTERS: dict[ConfidenceFormat, ConfidenceFormatter] = {
    ConfidenceFormat.STRUCTURED: StructuredFormatter(),
    ConfidenceFormat.NARRATIVE: NarrativeFormatter(),
    ConfidenceFormat.BOTH: BothFormatter(),
    ConfidenceFormat.PROBABILITY: ProbabilityFormatter(),
}


def get_formatter(config: ConfidenceConfig) -> ConfidenceFormatter:
    """Factory for confidence formatters.

    Args:
        config: Confidence output configuration.

    Returns:
        Formatter matching the configured format.
    """
    return _FORMATTERS[config.format]
