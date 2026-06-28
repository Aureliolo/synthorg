"""Step-level quality signals for engine execution.

Provides ternary step classification (correct/neutral/incorrect),
accuracy-effort ratio computation, and a pluggable classifier protocol.

Re-exports are lazy (PEP 562 ``__getattr__``): importing this package
does not eagerly pull ``classifier`` (which imports
``engine.loop_protocol``). ``loop_protocol`` carries ``quality_signals``
typed as ``StepQualitySignal`` from ``quality.models``; were this init
eager, ``loop_protocol`` -> ``quality.models`` -> ``quality/__init__``
-> ``classifier`` -> ``loop_protocol`` would close a partial-init cycle.
The lazy form lets a module that only needs ``quality.models`` import it
on a cold interpreter without dragging the loop-protocol graph in.
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synthorg.engine.quality.classifier import (
        RuleBasedStepClassifier,
        StepQualityClassifier,
    )
    from synthorg.engine.quality.effort import compute_accuracy_effort
    from synthorg.engine.quality.models import (
        AccuracyEffortRatio,
        StepQuality,
        StepQualitySignal,
    )

__all__ = [
    "AccuracyEffortRatio",
    "RuleBasedStepClassifier",
    "StepQuality",
    "StepQualityClassifier",
    "StepQualitySignal",
    "compute_accuracy_effort",
]

_LAZY_EXPORTS: dict[str, str] = {
    "RuleBasedStepClassifier": "synthorg.engine.quality.classifier",
    "StepQualityClassifier": "synthorg.engine.quality.classifier",
    "compute_accuracy_effort": "synthorg.engine.quality.effort",
    "AccuracyEffortRatio": "synthorg.engine.quality.models",
    "StepQuality": "synthorg.engine.quality.models",
    "StepQualitySignal": "synthorg.engine.quality.models",
}


def __getattr__(name: str) -> object:
    """Lazily import a re-exported symbol from its defining submodule.

    Returns:
        The requested attribute.

    Raises:
        AttributeError: If ``name`` is not a re-exported symbol.
    """
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return getattr(importlib.import_module(module_path), name)
