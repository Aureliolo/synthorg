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

Each leaf is one parametrized test that spawns exactly one fresh interpreter, so
the file fans out no wider than a single subprocess at a time and every case
finishes well inside the 30s global timeout (the heaviest cold import is a few
seconds). Keeping one import per test is what lets the guard stay under the
global timeout without a per-test override and without oversubscribing a shared
CI runner with a burst of concurrent interpreters.

The cycle is driven by eager re-export side effects in package ``__init__``
hubs, not by simple module-to-module edges, so a ``grimp`` / import-linter
chain contract cannot see it (it reports ``KEPT`` even when the runtime cycle
is live). This subprocess smoke test is therefore the primary guard; the
direct-edge ``forbidden`` contracts in ``.importlinter`` are a secondary,
documentation-grade backstop. See
``docs/decisions/0012-cold-import-cycle-break.md``.
"""

import subprocess
import sys
from typing import Final, NamedTuple

import pytest

# Kept strictly below the 30s pytest-timeout wall so a genuinely hung import
# surfaces here as a clear pytest.fail rather than as the worker-level
# os.abort the pytest-timeout patch triggers at 30s.
_IMPORT_TIMEOUT_SECONDS: Final[int] = 20

# Leaves that must import cold. Each pins a structural cut so any regression at
# the leaf boundary is caught:
# ``core.tool_constraints`` (sub-constraint types out of the ``tools`` hub),
# ``config.schema`` (the settings-schema aggregator; imports cold despite
# transitively referencing engine section configs), ``engine.quality.models``
# (pins the lazy ``quality/__init__`` so ``loop_protocol`` -> ``quality.models``
# cannot regress into eagerly pulling ``quality.classifier`` -> ``loop_protocol``
# mid-init), and the ``execution.*`` leaves plus
# ``budget.coordination_collector`` (turn/efficiency shapes out of
# ``engine.loop_protocol``), with ``persistence._shared`` as a representative
# persistence leaf. Add a new leaf here whenever a new dependency-free
# ``core.*`` / ``execution.*`` module is introduced, so its cold-import safety
# is pinned from the start. The same applies to a dependency-free enum leaf
# placed inside a feature package whose ``__init__`` is heavy
# (``approval.enums`` / ``security.autonomy.enums`` / ``security.timeout.enums``
# / ``templates.enums`` / ``engine.workspace.enums`` /
# ``engine.intervention.enums``): every consumer of those enums forces the
# package ``__init__`` to run, so the cold path through that init must be
# pinned. Dependency-free ``core.*`` foundation leaves whose consumers annotate
# against them at module level: ``core.completion_enums`` (the ``FinishReason``
# completion-outcome enum), ``core.effective_autonomy`` and
# ``core.redteam_review_input`` (resolved-autonomy and red-team gate-input value
# objects), and ``core.approval`` (the human-approval value object). Each imports
# only ``core.*`` / pydantic / stdlib, so adding a heavier import to any of them
# regresses this gate. ``budget.cost_record`` is pinned because it sources
# ``FinishReason`` from the ``core`` leaf, so importing it does not pull the heavy
# ``providers`` package (whose eager init imports ``budget.cost_record`` back).
#
# The ``communication`` / ``engine`` cluster below was the historical cold-cycle
# blind spot. Two independent cuts opened it up: (1) the heavy package hubs
# (``communication`` / ``engine`` / ``providers`` / ``security`` /
# ``templates``) now resolve their re-exports lazily (PEP 562), so importing a
# light leaf no longer drags the whole subgraph; and (2) the genuine
# interface<->implementation cycle where ``communication.bus_protocol`` imported
# ``QuadraticAlertSink`` from the concrete ``communication.bus`` package while
# ``bus/__init__`` re-exported ``MessageBus`` from ``bus_protocol`` was broken by
# moving the ``QuadraticAlertSink`` protocol up into ``bus_protocol`` (the
# interface module), so the concrete package depends on the interface and never
# the reverse (ADR-0012). ``bus_protocol`` / ``bus`` / ``engine.context`` pin
# that cut directly; ``communication.config`` / ``communication.conversation.enums``
# / ``meta.charter.enums`` were previously excluded because they could not import
# cold at all, and are pinned here now that both cuts hold. They remain heavier
# than the ``core.*`` leaves (they still transitively reach eager engine
# sub-hubs owned elsewhere); aggregate drift is the suite-timing gate's job, not
# a per-leaf wall-clock assert that would be load-sensitive and flaky.
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
    "synthorg.communication.bus_protocol",
    "synthorg.communication.bus",
    "synthorg.engine.context",
    "synthorg.communication.config",
    "synthorg.communication.conversation.enums",
    "synthorg.meta.charter.enums",
)


# Floor on the leaf count so an accidental deletion (a bad merge, a careless
# edit) fails the guard instead of silently shrinking coverage. Raise this in
# lock-step whenever leaves are added; only lower it with a deliberate,
# explained edit when a pinned module is genuinely removed from the tree.
_MIN_LEAF_COUNT: Final[int] = 32


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


@pytest.mark.unit
def test_leaf_set_is_well_formed() -> None:
    """The leaf set stays unique, module-shaped, and never silently shrinks.

    A cold-import cycle is only caught for a module that is actually in the set,
    so a leaf dropped by a bad merge would erase coverage without any test going
    red. This guard makes such a removal fail loudly: it pins a floor on the
    count and rejects duplicates or non-``synthorg`` entries.
    """
    assert len(COLD_IMPORT_LEAVES) >= _MIN_LEAF_COUNT, (
        f"COLD_IMPORT_LEAVES shrank to {len(COLD_IMPORT_LEAVES)} entries, below "
        f"the {_MIN_LEAF_COUNT} floor; a leaf was likely removed by accident. "
        f"If a pinned module was genuinely deleted, lower _MIN_LEAF_COUNT in the "
        f"same edit with a note explaining which leaf went and why."
    )
    duplicates = sorted(
        name for name in set(COLD_IMPORT_LEAVES) if COLD_IMPORT_LEAVES.count(name) > 1
    )
    assert not duplicates, f"Duplicate cold-import leaves: {duplicates}"
    malformed = [
        name for name in COLD_IMPORT_LEAVES if not name.startswith("synthorg.")
    ]
    assert not malformed, f"Cold-import leaves must be synthorg modules: {malformed}"


@pytest.mark.unit
@pytest.mark.parametrize("module_name", COLD_IMPORT_LEAVES)
def test_leaf_imports_from_cold_interpreter(module_name: str) -> None:
    """Each leaf imports successfully from a fresh, unprimed interpreter.

    One import per test (its own subprocess), so the case finishes inside the
    30s global timeout and no burst of concurrent interpreters oversubscribes
    the runner. The 20s subprocess timeout still fails a hung import fast.
    """
    outcome = _import_leaf_cold(module_name)
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
