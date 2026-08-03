"""Reading the SDK's running cost and token totals, defensively.

Split out of ``run_task.py`` so it can be exercised without the OpenHands SDK
installed. Everything here reaches for attributes and never imports the SDK, so
a unit test can drive it with a plain object graph; that matters because the
branch worth testing is the one that fires when the SDK's shape has moved, which
is by definition the branch the container contract test (which runs against a
real, current image) can never reproduce.

Both figures have to be read through ``get_combined_metrics()`` rather than off
the stats object: the SDK aggregates per-LLM metrics there, and it is what the
SDK itself reads for its own budget check.

A zero is indistinguishable from a run that genuinely spent nothing, and the
driving engine reads these numbers for three separate things: the per-turn
``TurnRecord`` every OpenHands run produces, the hard budget kill that ends a
run gone expensive, and the A/B token ranking (whose efficiency score treats an
observed zero as unbeatable). Only the last of those belongs to the benchmark;
the first two are how the loop behaves in production. So a shape we cannot read
is reported rather than quietly zeroed, on stderr for a human and as a flag on
the event stream for the engine's own log.
"""

import sys
from typing import Final

#: Latch so each missing-shape diagnostic is written once per run. The read
#: happens on every event and a moved shape stays moved, so repeating it would
#: bury the rest of the container's diagnostics.
_SHAPE_REPORTED: set[str] = set()

_COST_KEY: Final[str] = "accumulated cost"
_TOKENS_KEY: Final[str] = "accumulated tokens"


def reset_shape_reports() -> None:
    """Forget which diagnostics have been written.

    Exists for tests, which need each case to start from the un-latched state.
    """
    _SHAPE_REPORTED.clear()


def report_shape_once(key: str, detail: str) -> None:
    """Write a missing-metrics-shape diagnostic once per run per *key*.

    Args:
        key: Which figure could not be read.
        detail: What was found instead, to identify the moved shape.
    """
    if key in _SHAPE_REPORTED:
        return
    _SHAPE_REPORTED.add(key)
    sys.stderr.write(f"{key} unavailable: {detail}\n")


def combined_metrics(conversation: object) -> object:
    """Reach the conversation's aggregated metrics object.

    Args:
        conversation: The SDK conversation.

    Returns:
        The combined metrics object, or ``None`` when unavailable.
    """
    stats = getattr(conversation, "conversation_stats", None)
    combined = getattr(stats, "get_combined_metrics", None)
    return combined() if callable(combined) else None


def accumulated_cost(conversation: object) -> float:
    """Read the conversation's running accumulated cost.

    Args:
        conversation: The SDK conversation.

    Returns:
        The accumulated cost, or ``0.0`` when unavailable.
    """
    metrics = combined_metrics(conversation)
    cost = getattr(metrics, "accumulated_cost", None)
    if cost is None:
        report_shape_once(_COST_KEY, f"metrics={type(metrics).__name__}")
        return 0.0
    return float(cost or 0.0)


def accumulated_tokens(conversation: object) -> tuple[int, int]:
    """Read the conversation's running accumulated token usage.

    Either field missing is reported, not just both: the SDK renaming one half
    of the pair would otherwise return a half-correct total that reads as a
    real measurement, which is the same silent-zero failure with a smaller
    blast radius rather than a different one.

    Args:
        conversation: The SDK conversation.

    Returns:
        ``(prompt_tokens, completion_tokens)``, zeroed when unavailable.
    """
    metrics = combined_metrics(conversation)
    usage = getattr(metrics, "accumulated_token_usage", None)
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if prompt is None or completion is None:
        report_shape_once(
            _TOKENS_KEY,
            f"metrics={type(metrics).__name__} usage={type(usage).__name__} "
            f"prompt={prompt!r} completion={completion!r}",
        )
        return 0, 0
    return int(prompt), int(completion)


def totals(conversation: object) -> dict[str, object]:
    """Return the running accumulated figures to stamp on one event.

    ``metrics_shape_ok`` rides along so the driving engine can log a moved shape
    through its own pipeline. The stderr diagnostic alone is not enough: the
    engine sinks container stderr at DEBUG, so at any level an operator actually
    runs, a silently zeroed run would look exactly like a cheap one.

    Args:
        conversation: The SDK conversation.

    Returns:
        The ``cost`` / ``input_tokens`` / ``output_tokens`` run totals, plus the
        shape flag.
    """
    prompt_tokens, completion_tokens = accumulated_tokens(conversation)
    cost = accumulated_cost(conversation)
    return {
        "cost": cost,
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "metrics_shape_ok": not _SHAPE_REPORTED,
    }


__all__ = [
    "accumulated_cost",
    "accumulated_tokens",
    "combined_metrics",
    "report_shape_once",
    "reset_shape_reports",
    "totals",
]
