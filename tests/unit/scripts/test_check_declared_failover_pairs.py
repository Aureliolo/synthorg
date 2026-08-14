"""Unit tests for ``scripts/check_declared_failover_pairs.py``.

Exercises each rejected shape (a derived target inside the failover
modules, an agent-identity reference there, an import from the memory /
embedder / gateway surfaces, and a wrapper constructed outside its one
owner), the reasoned ``# lint-allow: declared-failover`` marker, the
absence of any baseline, and the fail-closed exit on a missing source tree.

Drives the script's ``main`` entry point against a sandbox tree, matching
the ``--repo-root`` pattern used by the sibling gate tests.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_declared_failover_pairs.py"

_FAILOVER = "providers/failover.py"
_DISPATCH = "providers/failover_dispatch.py"
_OWNER = "providers/model_binding.py"


class _ScriptModule(Protocol):
    """Subset of the gate script surface the tests drive."""

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load() -> _ScriptModule:
    spec = importlib.util.spec_from_file_location(
        "check_declared_failover_pairs", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("_ScriptModule", module)


def _write(root: Path, relpath: str, body: str) -> None:
    path = root / "src" / "synthorg" / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _run(root: Path) -> int:
    """Scan *root*, seeding the failover modules the gate scopes itself by.

    The gate derives its enforced module set from ``providers/failover*.py``
    and fails closed when the glob matches nothing, so a sandbox exercising
    the import-scope or wrapper-ownership rules still has to be a tree that
    HAS the mechanism. Seeding is a no-op for a test that wrote its own.

    Returns:
        The gate's exit code.
    """
    providers = root / "src" / "synthorg" / "providers"
    if providers.is_dir() or (root / "src" / "synthorg").is_dir():
        providers.mkdir(parents=True, exist_ok=True)
        for name in (_FAILOVER, _DISPATCH):
            path = root / "src" / "synthorg" / name
            if not path.exists():
                path.write_text("", encoding="utf-8")
    return _load().main(["--repo-root", str(root)])


class TestResolutionShape:
    def test_exact_key_lookup_passes(self, tmp_path: Path) -> None:
        # The one admissible resolution: the operator's map, keyed by the
        # exact pair. Flagging it would leave the feature unbuildable.
        _write(
            tmp_path,
            _FAILOVER,
            "def alternate_for(routes, declared):\n"
            "    return routes.get(route_key(declared))\n",
        )
        assert _run(tmp_path) == 0

    def test_indexing_a_computed_sequence_is_flagged(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _FAILOVER,
            "def pick(registry):\n    return sorted(registry.candidates())[0]\n",
        )
        assert _run(tmp_path) == 1

    def test_next_iter_is_flagged(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _DISPATCH,
            "def pick(routes):\n    return next(iter(routes))\n",
        )
        assert _run(tmp_path) == 1

    def test_values_scan_is_flagged(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _DISPATCH,
            "def pick(routes):\n    return [r for r in routes.values() if r]\n",
        )
        assert _run(tmp_path) == 1

    def test_agent_identity_reference_is_flagged(self, tmp_path: Path) -> None:
        # An agent's pair is exclusive by ruling: it goes unavailable rather
        # than failing over, so the mechanism has no business knowing about one.
        _write(
            tmp_path,
            _DISPATCH,
            "def route(identity: AgentIdentity):\n    return identity\n",
        )
        assert _run(tmp_path) == 1

    def test_same_shape_outside_the_failover_modules_passes(
        self, tmp_path: Path
    ) -> None:
        # Rule 1 is about how a failover target is chosen, not about indexing:
        # an index anywhere else is ordinary code.
        _write(
            tmp_path,
            "providers/other.py",
            "def pick(registry):\n    return sorted(registry.candidates())[0]\n",
        )
        assert _run(tmp_path) == 0


class TestScope:
    def test_memory_import_is_flagged(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "memory/wiring.py",
            "from synthorg.providers.failover import parse_failover_routes\n",
        )
        assert _run(tmp_path) == 1

    def test_the_package_relative_import_form_is_flagged(self, tmp_path: Path) -> None:
        # `from <package> import <module>` puts the PACKAGE in `node.module`
        # and the module in an alias, so matching only the fully-qualified
        # forms left the whole out-of-scope rule bypassable by writing the
        # import the other way round.
        _write(
            tmp_path,
            "memory/wiring.py",
            "from synthorg.providers import failover\n",
        )
        assert _run(tmp_path) == 1

    def test_an_event_module_import_is_in_scope_too(self, tmp_path: Path) -> None:
        # The enforced set is derived from `providers/failover*.py`, which is
        # what CLAUDE.md documents. A hand-written list had already drifted
        # one module behind that pattern.
        _write(tmp_path, "providers/failover_event.py", "EVENT = 1\n")
        _write(
            tmp_path,
            "memory/wiring.py",
            "from synthorg.providers.failover_event import ProviderFailoverEvent\n",
        )
        assert _run(tmp_path) == 1

    def test_the_relative_import_form_is_flagged(self, tmp_path: Path) -> None:
        # A relative import carries a level and a partial path, so an
        # absolute comparison matches nothing: `from ..providers.failover`
        # was a silent bypass of the entire out-of-scope rule.
        _write(
            tmp_path,
            "memory/wiring.py",
            "from ..providers.failover import parse_failover_routes\n",
        )
        assert _run(tmp_path) == 1

    def test_the_relative_package_form_is_flagged(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "memory/wiring.py",
            "from ..providers import failover\n",
        )
        assert _run(tmp_path) == 1

    def test_a_packages_own_init_resolves_against_the_package(
        self, tmp_path: Path
    ) -> None:
        # `memory/__init__.py` IS `synthorg.memory`, so one dot means the
        # package itself and two means `synthorg`. Resolving an `__init__`
        # against its parent instead would land a level short and miss it.
        _write(
            tmp_path,
            "memory/__init__.py",
            "from ..providers.failover import parse_failover_routes\n",
        )
        assert _run(tmp_path) == 1

    def test_a_relative_import_elsewhere_passes(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "workers/wiring.py",
            "from ..providers.failover import parse_failover_routes\n",
        )
        assert _run(tmp_path) == 0

    def test_embedder_import_is_flagged(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "providers/embedder_factory.py",
            "from synthorg.providers.failover_dispatch import FailoverPolicy\n",
        )
        assert _run(tmp_path) == 1

    def test_gateway_import_is_flagged(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "api/gateway/dispatch.py",
            "import synthorg.providers.failover_dispatch\n",
        )
        assert _run(tmp_path) == 1

    def test_import_elsewhere_passes(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "workers/wiring.py",
            "from synthorg.providers.failover import parse_failover_routes\n",
        )
        assert _run(tmp_path) == 0


class TestWrapperOwnership:
    def test_construction_in_the_owner_passes(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _OWNER,
            "def bind(client):\n    return FailoverCompletionProvider(client)\n",
        )
        assert _run(tmp_path) == 0

    def test_construction_elsewhere_is_flagged(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "engine/agent_run.py",
            "def bind(client):\n    return FailoverCompletionProvider(client)\n",
        )
        assert _run(tmp_path) == 1

    def test_the_qualified_construction_form_is_flagged(self, tmp_path: Path) -> None:
        # Importing the module rather than the class builds the same object,
        # so recognising only the bare name left single ownership answerable
        # by an import style.
        _write(
            tmp_path,
            "engine/agent_run.py",
            "def bind(client):\n"
            "    return failover_dispatch.FailoverCompletionProvider(client)\n",
        )
        assert _run(tmp_path) == 1

    def test_the_qualified_form_in_the_owner_passes(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _OWNER,
            "def bind(client):\n"
            "    return failover_dispatch.FailoverCompletionProvider(client)\n",
        )
        assert _run(tmp_path) == 0


class TestMarker:
    def test_reasoned_marker_suppresses(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _DISPATCH,
            "def pick(routes):\n"
            "    return next(iter(routes))"
            "  # lint-allow: declared-failover -- iteration, not a target\n",
        )
        assert _run(tmp_path) == 0

    def test_the_marker_is_found_on_a_multi_line_statements_last_line(
        self, tmp_path: Path
    ) -> None:
        # A trailing comment sits on the LAST physical line of a statement,
        # while `node.lineno` is its first. Matching only the first left a
        # documented exception on a wrapped call still reported, and this
        # gate has no baseline, so the marker is the only escape hatch.
        _write(
            tmp_path,
            _DISPATCH,
            "def pick(routes):\n"
            "    return next(\n"
            "        iter(routes)\n"
            "    )  # lint-allow: declared-failover -- iteration, not a target\n",
        )
        assert _run(tmp_path) == 0

    def test_marker_without_a_reason_does_not_suppress(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _DISPATCH,
            "def pick(routes):\n"
            "    return next(iter(routes))  # lint-allow: declared-failover\n",
        )
        assert _run(tmp_path) == 1

    def test_marker_inside_a_string_does_not_suppress(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _DISPATCH,
            'NOTE = "lint-allow: declared-failover -- prose"\n'
            "def pick(routes):\n"
            "    return next(iter(routes))\n",
        )
        assert _run(tmp_path) == 1


class TestFailClosed:
    def test_missing_source_tree_is_a_configuration_error(self, tmp_path: Path) -> None:
        # A misconfigured root must not read as a clean tree: scanning zero
        # files is the one way a gate reports success having checked nothing.
        assert _run(tmp_path) == 2

    def test_missing_repo_root_is_a_configuration_error(self, tmp_path: Path) -> None:
        assert _load().main(["--repo-root", str(tmp_path / "absent")]) == 2


def test_gate_ships_no_baseline() -> None:
    # An exception here is a claim about scope, which belongs on the line
    # making it; a suppression file would let the carve-out widen quietly.
    assert not (_REPO_ROOT / "scripts" / "declared_failover_baseline.txt").exists()
