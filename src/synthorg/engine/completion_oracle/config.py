# module-kind: declarative
"""Behaviour configuration for the completion-oracle peer-review gate.

Resolved from settings at wiring time and handed to the builder. The
reviewer's model is resolved to a concrete :class:`ModelConfig` by the
wiring layer, so it is not carried here; this config holds only the gate's
behavioural knobs.
"""

from pydantic import BaseModel, ConfigDict

from synthorg.core.task_enums import Stakes


class CompletionOracleConfig(BaseModel):
    """Operator-tunable behaviour of the peer-review gate.

    Attributes:
        enabled: Whether the gate is attached at all (opt-out; default on).
        shadow_mode: When true, the gate computes and surfaces its verdict
            but does not enforce it (REJECT / ESCALATE do not reroute the
            task). Lets an operator observe the oracle before it can block.
        min_stakes: The gate runs only for tasks whose stakes are at or above
            this threshold; the expensive agent-session review is skipped for
            lower-stakes work (the deterministic build/test gate still runs).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    shadow_mode: bool = False
    min_stakes: Stakes = Stakes.LOW
