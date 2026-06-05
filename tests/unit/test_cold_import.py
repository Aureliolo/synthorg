# module-kind: tests
"""Cold-import regression guard for the package import graph.

``synthorg`` must be importable starting from an arbitrary leaf module on a
*cold* interpreter -- one where no other ``synthorg`` module has been imported
first to prime the graph in a working order. Historically this was not true:
importing a leaf such as ``synthorg.providers.enums`` from a fresh interpreter
raised a circular-import ``ImportError``, and every real entry point hid the
defect by importing a heavy package first (the app boots through
``api.app``; the test suite primes via ``tests/conftest.py``; ``evals`` used to
import ``synthorg.persistence`` before its runner).

Each leaf below is imported in its own freshly spawned interpreter via
``subprocess`` -- the only faithful way to assert a *cold* import, since within
the pytest process the graph is already primed (see the prime in
``tests/conftest.py``). A non-zero exit means a package-level circular import
has regressed.

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

_IMPORT_TIMEOUT_SECONDS: Final[int] = 60

# Leaves proven cold-safe. The first two are the issue's acceptance criteria;
# the rest pin the three structural cuts (``core.tool_constraints`` for Cut A,
# ``config.schema`` for Cut B, the ``execution.*`` leaves plus
# ``budget.coordination_collector`` for Cut C) and a representative
# persistence leaf. ``communication.config`` is intentionally absent: it is
# blocked by a separate, unnamed ``communication`` <-> ``engine`` cycle (via
# ``meeting._prompts`` -> ``engine.prompt_safety``) that this change does not
# scope; ``config.schema`` importing cold already proves the named
# ``config.schema`` <-> ``communication.config`` cycle is broken.
COLD_IMPORT_LEAVES: Final[tuple[str, ...]] = (
    "synthorg.providers.enums",
    "synthorg.core.agent",
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
    result = subprocess.run(  # noqa: S603 -- module_name is from a hardcoded tuple
        [sys.executable, "-c", f"import {module_name}"],
        capture_output=True,
        text=True,
        timeout=_IMPORT_TIMEOUT_SECONDS,
        check=False,
    )

    assert result.returncode == 0, (
        f"Cold import of {module_name!r} failed (exit {result.returncode}); "
        f"a package-level circular import has regressed.\n"
        f"stderr tail:\n{result.stderr[-2000:]}"
    )
