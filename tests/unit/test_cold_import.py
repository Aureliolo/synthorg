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
from typing import Final

import pytest

# Kept strictly below the 30s pytest-timeout wall so a genuinely hung import
# surfaces here as a clear pytest.fail rather than as the worker-level
# os.abort the pytest-timeout patch triggers at 30s.
_IMPORT_TIMEOUT_SECONDS: Final[int] = 20

# Leaves that must import cold. The first two are the primary regression
# targets (the original cold-import failure points). The rest pin each
# structural cut so any regression at the leaf boundary is caught:
# ``core.tool_constraints`` (sub-constraint types out of the ``tools`` hub),
# ``config.schema`` (no longer reached through the config-to-communication
# eager chain), and the ``execution.*`` leaves plus
# ``budget.coordination_collector`` (turn/efficiency shapes out of
# ``engine.loop_protocol``), with ``persistence._shared`` as a representative
# persistence leaf. ``communication.config`` is intentionally absent: a
# remaining ``communication`` <-> ``engine`` cycle (``meeting._prompts`` ->
# ``engine.prompt_safety`` -> ``engine/__init__`` -> ``engine.classification``
# -> ``communication.delegation`` -> ``communication.config``) blocks it from
# importing cold on its own; ``config.schema`` importing cold already proves
# the ``config.schema`` <-> ``communication.config`` edge is broken. Add a new
# leaf here whenever a new dependency-free ``core.*`` / ``execution.*`` module
# is introduced, so its cold-import safety is pinned from the start.
COLD_IMPORT_LEAVES: Final[tuple[str, ...]] = (
    "synthorg.providers.enums",
    "synthorg.core.agent",
    "synthorg.core.memory_enums",
    "synthorg.persistence._shared",
    "synthorg.core.tool_constraints",
    "synthorg.config.schema",
    "synthorg.execution.turn",
    "synthorg.execution.efficiency",
    "synthorg.execution.view",
    "synthorg.budget.coordination_collector",
)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", COLD_IMPORT_LEAVES)
def test_leaf_imports_from_cold_interpreter(module_name: str) -> None:
    """Each leaf imports successfully from a fresh, unprimed interpreter."""
    try:
        result = subprocess.run(  # noqa: S603 -- module_name is a hardcoded tuple
            [sys.executable, "-c", f"import {module_name}"],
            capture_output=True,
            text=True,
            timeout=_IMPORT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"Cold import of {module_name!r} did not finish within "
            f"{_IMPORT_TIMEOUT_SECONDS}s; a cold-import cycle (or an import-time "
            f"deadlock) is the likely cause."
        )

    assert result.returncode == 0, (
        f"Cold import of {module_name!r} failed (exit {result.returncode}); "
        f"a regressed package-level circular import is the most likely cause "
        f"(an ImportError from a typo or missing dependency also exits non-zero "
        f"-- check the stderr tail).\nstderr tail:\n{result.stderr[-2000:]}"
    )
