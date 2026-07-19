# module-kind: tests
"""Cold-import regression guard for the package import graph.

Each leaf below must import successfully on a *cold* interpreter: one where no
other ``synthorg`` module has been imported first to prime the graph in a
working order. Any entry point that imports a heavy hub first (``api.app``, or
``tests/conftest.py`` for the suite) masks a latent circular import in leaf
consumers that never load that hub, so a leaf imported in isolation is the only
faithful check. Each leaf is therefore imported in its own freshly spawned
interpreter via ``subprocess``; within the pytest process the graph is already
primed (see the prime in ``tests/conftest.py``), so an in-process import would
not exercise the cold path. A non-zero exit most likely means a package-level
circular import has regressed.

The per-leaf imports are independent and dominated by interpreter startup plus
a one-off cold import of heavy transitive dependencies, so they are spawned
concurrently in a bounded pool (each still in its own pristine interpreter).
That collapses the file's wall-clock from the serial sum of every leaf to the
slowest single import, without weakening the guarantee that each leaf loads on
an unprimed graph.

The cycle is driven by eager re-export side effects in package ``__init__``
hubs, not by simple module-to-module edges, so a ``grimp`` / import-linter
chain contract cannot see it (it reports ``KEPT`` even when the runtime cycle
is live). This subprocess smoke test is therefore the primary guard; the
direct-edge ``forbidden`` contracts in ``.importlinter`` are a secondary,
documentation-grade backstop. See
``docs/decisions/0012-cold-import-cycle-break.md``.
"""

import concurrent.futures
import subprocess
import sys
from typing import Final, NamedTuple

import pytest

# Kept strictly below the 30s pytest-timeout wall so a genuinely hung import
# surfaces here as a clear pytest.fail rather than as the worker-level
# os.abort the pytest-timeout patch triggers at 30s.
_IMPORT_TIMEOUT_SECONDS: Final[int] = 20

# Upper bound on concurrent fresh interpreters. Cold-import lands on a single
# xdist worker (``--dist=loadfile`` keeps a file together), so this caps the
# transient memory/process spike from spawning every leaf at once while still
# running enough in parallel to hide the serial startup cost.
_MAX_CONCURRENT_COLD_IMPORTS: Final[int] = 16

# Leaves that must import cold. The first two are the primary regression
# targets (the original cold-import failure points). The rest pin each
# structural cut so any regression at the leaf boundary is caught:
# ``core.tool_constraints`` (sub-constraint types out of the ``tools`` hub),
# ``config.schema`` (no longer reached through the config-to-communication
# eager chain), ``engine.quality.models`` (pins the lazy ``quality/__init__``
# so ``loop_protocol`` -> ``quality.models`` cannot regress into eagerly
# pulling ``quality.classifier`` -> ``loop_protocol`` mid-init), and the
# ``execution.*`` leaves plus
# ``budget.coordination_collector`` (turn/efficiency shapes out of
# ``engine.loop_protocol``), with ``persistence._shared`` as a representative
# persistence leaf. ``communication.config`` is intentionally absent: a
# remaining ``communication`` <-> ``engine`` cycle (``communication/__init__``
# -> ``meeting._prompts`` -> ``engine.prompt_safety`` -> ``engine/__init__`` ->
# ``agent_engine`` -> ``engine.context`` -> ``communication.async_tasks`` ->
# ``communication.config``) blocks it from importing cold on its own. The
# former ``engine.classification`` -> ``communication.delegation`` ->
# ``communication.config`` edge no longer contributes: the delegation request /
# result / record value objects moved to the ``core.delegation_types`` leaf, so
# the classification loaders reference them without importing the
# ``communication`` hub. ``config.schema`` importing cold already proves
# the ``config.schema`` <-> ``communication.config`` edge is broken. Add a new
# leaf here whenever a new dependency-free ``core.*`` / ``execution.*`` module
# is introduced, so its cold-import safety is pinned from the start. The same
# applies to a dependency-free enum leaf placed inside a feature package whose
# ``__init__`` is heavy (``approval.enums`` / ``security.autonomy.enums`` /
# ``security.timeout.enums`` / ``templates.enums`` /
# ``engine.workspace.enums`` / ``engine.intervention.enums``): every consumer
# of those enums now forces the package ``__init__`` to run, so the cold path
# through that init must be pinned. ``communication.conversation.enums`` and
# ``meta.charter.enums`` are intentionally absent: both reach the heavy
# ``communication`` init, which still cold-cycles (the same ``communication``
# <-> ``engine`` edge that keeps ``communication.config`` out).
# ``communication.conversation.enums`` triggers it directly via its parent
# package; ``meta.charter.enums`` reaches it indirectly
# (``meta.charter.__init__`` -> ``meta.charter.service`` ->
# ``communication.conversation.enums`` -> ``communication.__init__``). Neither
# can import cold on its own, and no cold leaf consumes those enums.
# Dependency-free ``core.*`` foundation leaves whose consumers annotate against
# them at module level: ``core.completion_enums`` (the ``FinishReason``
# completion-outcome enum), ``core.effective_autonomy`` and
# ``core.redteam_review_input`` (resolved-autonomy and red-team gate-input value
# objects), and ``core.approval`` (the human-approval value object). Each imports
# only ``core.*`` / pydantic / stdlib, so adding a heavier import to any of them
# regresses this gate. ``budget.cost_record`` is pinned because it sources
# ``FinishReason`` from the ``core`` leaf, so importing it does not pull the heavy
# ``providers`` package (whose eager init imports ``budget.cost_record`` back).
COLD_IMPORT_LEAVES: Final[tuple[str, ...]] = (
    "synthorg.providers.enums",
    "synthorg.core.agent",
    "synthorg.core.memory_enums",
    "synthorg.core.completion_enums",
    "synthorg.core.effective_autonomy",
    "synthorg.core.redteam_review_input",
    "synthorg.core.approval",
    "synthorg.persistence._shared",
    "synthorg.core.tool_constraints",
    "synthorg.core.boundary",
    "synthorg.core.text_estimation",
    "synthorg.core.slug",
    "synthorg.config.schema",
    "synthorg.execution.turn",
    "synthorg.execution.efficiency",
    "synthorg.execution.view",
    "synthorg.execution.parked_context",
    "synthorg.engine.quality.models",
    "synthorg.budget.coordination_collector",
    "synthorg.budget.cost_record",
    "synthorg.approval.enums",
    "synthorg.security.autonomy.enums",
    "synthorg.security.timeout.enums",
    "synthorg.templates.enums",
    "synthorg.engine.workspace.enums",
    "synthorg.engine.intervention.enums",
)


class _ColdImportOutcome(NamedTuple):
    """Result of importing one leaf in a fresh interpreter."""

    returncode: int
    stderr: str
    timed_out: bool


def _import_leaf_cold(module_name: str) -> _ColdImportOutcome:
    """Import ``module_name`` in a fresh, unprimed interpreter."""
    try:
        result = subprocess.run(  # noqa: S603 -- module_name is a hardcoded tuple
            [sys.executable, "-c", f"import {module_name}"],
            capture_output=True,
            text=True,
            timeout=_IMPORT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _ColdImportOutcome(returncode=-1, stderr="", timed_out=True)
    return _ColdImportOutcome(
        returncode=result.returncode, stderr=result.stderr, timed_out=False
    )


@pytest.fixture(scope="module")
def cold_import_outcomes() -> dict[str, _ColdImportOutcome]:
    """Import every leaf concurrently, each in its own fresh interpreter.

    Module-scoped so the concurrent sweep runs once per worker; each
    parametrized test then asserts on its own leaf's cached outcome, keeping
    per-leaf failure reporting while paying the wall-clock of only the slowest
    single import.
    """
    max_workers = min(len(COLD_IMPORT_LEAVES), _MAX_CONCURRENT_COLD_IMPORTS)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(
            zip(
                COLD_IMPORT_LEAVES,
                pool.map(_import_leaf_cold, COLD_IMPORT_LEAVES),
                strict=True,
            )
        )


@pytest.mark.unit
@pytest.mark.parametrize("module_name", COLD_IMPORT_LEAVES)
def test_leaf_imports_from_cold_interpreter(
    module_name: str, cold_import_outcomes: dict[str, _ColdImportOutcome]
) -> None:
    """Each leaf imports successfully from a fresh, unprimed interpreter."""
    outcome = cold_import_outcomes[module_name]
    if outcome.timed_out:
        pytest.fail(
            f"Cold import of {module_name!r} did not finish within "
            f"{_IMPORT_TIMEOUT_SECONDS}s; a cold-import cycle (or an import-time "
            f"deadlock) is the likely cause."
        )

    assert outcome.returncode == 0, (
        f"Cold import of {module_name!r} failed (exit {outcome.returncode}); "
        f"a regressed package-level circular import is the most likely cause "
        f"(an ImportError from a typo or missing dependency also exits non-zero "
        f"-- check the stderr tail).\nstderr tail:\n{outcome.stderr[-2000:]}"
    )
