# module-kind: code
"""Publishing a decomposition's progress, without ever failing the run.

Split from the service because the swallow is the whole substance: the
decision that a reporter which raises must not take the tree down with it is
one rule, and it belongs somewhere a reader can find it rather than inline
beside the recursion it protects.
"""

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.decomposition._recursion import TreeSessionLedger
from synthorg.engine.decomposition.progress_protocol import (
    DecompositionProgressReporter,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_PROGRESS_UNRECORDED,
)

logger = get_logger(__name__)


async def publish_progress(
    ledger: TreeSessionLedger,
    *,
    reporter: DecompositionProgressReporter | None,
    clock: Clock,
) -> None:
    """Publish how far the tree has got, without ever failing it.

    Best-effort by contract: a decomposition is minutes to hours of real
    provider spend, and losing the progress line costs an operator a refresh
    while losing the tree costs the run. So a reporter that raises is logged
    and dropped here rather than at each call site.

    Args:
        ledger: The tree's running totals.
        reporter: Where to publish, or ``None`` when nothing is wired.
        clock: Time source for the snapshot's own stamp.
    """
    if reporter is None or ledger.objective_task_id is None:
        return
    try:
        await reporter.report(
            objective_task_id=ledger.objective_task_id,
            progress=ledger.progress(now=clock.now()),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- describing a run must not fail it
        reraise_critical(exc)
        # Named, because several trees report concurrently and "some
        # decomposition somewhere stopped being described" is not something an
        # operator can act on.
        logger.warning(
            DECOMPOSITION_PROGRESS_UNRECORDED,
            objective_task_id=ledger.objective_task_id,
            deepest_level=ledger.deepest_level,
            units_planned=ledger.units_planned,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = ["publish_progress"]
