"""Unit tests for ``scripts/check_no_bound_pair_rewrite.py``.

The gate guards the convention this PR establishes: an agent's bound
``(provider, model)`` pair is the operator's choice and nothing in the loop
rewrites it. The shapes below are the three deleted mechanisms written out
verbatim, plus the near neighbours that must NOT be flagged.

Tests load the script via :mod:`importlib` and call its private helpers
directly, matching the pattern in ``test_check_no_synthetic_agent_identity.py``.
"""

import importlib.util
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every ``GIT_*`` env var for the duration of each test.

    The gate's ``git ls-files`` subprocess inherits this process's
    environment. Under a pre-push hook ``GIT_DIR`` / ``GIT_WORK_TREE`` point
    at the real repo, which would let the scan escape the ``tmp_path``
    sandbox and read the live tree. A test must NEVER touch real repo data.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_bound_pair_rewrite.py"


class _HitView(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    rel: str
    lineno: int
    col: int
    kind: str

    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _BINDING_OWNER_PATHS: Mapping[str, str]

    @staticmethod
    def _scan_file(path: Path, rel: str) -> tuple[list[_HitView], int]: ...
    @staticmethod
    def _is_valid_marker(comment_token: str) -> bool: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_no_bound_pair_rewrite",
            _SCRIPT_PATH,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast(_ScriptModule, module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()


class WritePy(Protocol):
    """Callable signature of the ``write_py`` fixture."""

    def __call__(self, content: str, name: str = ...) -> Path: ...


@pytest.fixture
def write_py(tmp_path: Path) -> WritePy:
    """Helper that writes a Python source string to ``tmp_path/<name>``."""

    def _write(content: str, name: str = "sample.py") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


# Budget auto-downgrade, verbatim: the whole binding replaced at the task
# boundary, so the run executed on a model its operator never chose.
_WHOLE_BINDING_SWAP = """\
def apply_downgrade(identity, cheaper):
    return identity.model_copy(update={"model": cheaper})
"""

# The stakes router, verbatim: half the binding edited one level down, which
# is the same swap with a smaller diff.
_HALF_BINDING_SWAP = """\
def raise_to_floor(identity, model_id: str):
    return identity.model.model_copy(
        update={"model_id": model_id, "capability": "expert"}
    )
"""

# Quota degradation, verbatim: the connection swapped mid-dispatch, so the
# call hit credentials and a quota nobody named.
_PROVIDER_SWAP = """\
def apply_degradation(identity, fallback: str):
    return identity.model.model_copy(update={"provider": fallback})
"""

# The same mapping spelled as a call rather than a literal.
_DICT_CALL_FORM = """\
def apply_downgrade(identity, cheaper):
    return identity.model_copy(update=dict(model=cheaper))
"""

# The mapping built a line earlier, which a literal-only check would miss.
_NAMED_UPDATE = """\
def apply_downgrade(identity, cheaper):
    updates = {}
    updates["model"] = cheaper
    return identity.model_copy(update=updates)
"""

# A rewrite that stopped calling itself one.
_FRESH_CONSTRUCTION = """\
from synthorg.core.agent import ModelConfig


def downgrade(provider: str, model_id: str):
    return ModelConfig(provider=provider, model_id=model_id, capability="basic")
"""

# The same mint reached through an alias, which is the rename the gate must
# not be got past by.
_ALIASED_CONSTRUCTION = """\
from synthorg.core.agent import ModelConfig as Binding


def downgrade(provider: str, model_id: str):
    return Binding(provider=provider, model_id=model_id)
"""

# And through the module, which spells the class nowhere near the call.
_MODULE_PATH_CONSTRUCTION = """\
from synthorg.core import agent


def downgrade(provider: str, model_id: str):
    return agent.ModelConfig(provider=provider, model_id=model_id)
"""

# Pydantic's sharpest constructor: a whole binding out of untyped input, with
# validation skipped outright.
_MODEL_CONSTRUCT = """\
from synthorg.core.agent import ModelConfig


def rehydrate(raw):
    return ModelConfig.model_construct(**raw)
"""

_SUPPRESSED = """\
def apply_downgrade(identity, cheaper):
    return identity.model_copy(
        update={"model": cheaper}  # lint-allow: bound-pair-rewrite -- vendor shim
    )
"""

_UNJUSTIFIED_SUPPRESSION = """\
def apply_downgrade(identity, cheaper):
    return identity.model_copy(
        update={"model": cheaper}  # lint-allow: bound-pair-rewrite
    )
"""

# Copying an unrelated field is not a rewrite; flagging it would make every
# frozen model in the tree suspect.
_UNRELATED_COPY = """\
def retitle(task, title: str):
    return task.model_copy(update={"title": title, "status": "assigned"})
"""

# Selection is the answer the convention prescribes, so it must read clean.
_SELECTION_NOT_REWRITE = """\
def pick(agents, required: str):
    return [a for a in agents if a.model.capability == required]
"""

# A different class entirely, whose name merely contains the target's.
_DIFFERENT_CLASS = """\
from synthorg.config.provider_schema import ProviderModelConfig


def catalogue(model_id: str):
    return ProviderModelConfig(id=model_id)
"""


class TestScanFile:
    """``_scan_file`` on individual rewrite and construction shapes."""

    @pytest.mark.parametrize(
        ("source", "kind"),
        [
            (_WHOLE_BINDING_SWAP, "rewrite"),
            (_HALF_BINDING_SWAP, "rewrite"),
            (_PROVIDER_SWAP, "rewrite"),
            (_DICT_CALL_FORM, "rewrite"),
            (_NAMED_UPDATE, "rewrite"),
            (_FRESH_CONSTRUCTION, "construct"),
            (_ALIASED_CONSTRUCTION, "construct"),
            (_MODULE_PATH_CONSTRUCTION, "construct"),
            (_MODEL_CONSTRUCT, "construct"),
        ],
        ids=[
            "whole_binding",
            "half_binding",
            "provider_swap",
            "dict_call_form",
            "named_update",
            "fresh_construction",
            "aliased_construction",
            "module_path_construction",
            "model_construct",
        ],
    )
    def test_a_rewrite_is_flagged(
        self, write_py: WritePy, source: str, kind: str
    ) -> None:
        path = write_py(source)

        hits, count = _MODULE._scan_file(path, "src/synthorg/budget/enforcer.py")

        assert count == 1
        assert len(hits) == 1
        assert hits[0].kind == kind

    @pytest.mark.parametrize(
        "source",
        [_UNRELATED_COPY, _SELECTION_NOT_REWRITE, _DIFFERENT_CLASS],
        ids=["unrelated_field", "selection", "different_class"],
    )
    def test_a_near_neighbour_is_clean(self, write_py: WritePy, source: str) -> None:
        path = write_py(source)

        hits, count = _MODULE._scan_file(path, "src/synthorg/engine/service.py")

        assert hits == []
        assert count == 0

    def test_a_declared_owner_is_exempt_but_still_counted(
        self, write_py: WritePy
    ) -> None:
        """The count is what proves the declaration is still load-bearing."""
        path = write_py(_FRESH_CONSTRUCTION)
        declared = next(iter(_MODULE._BINDING_OWNER_PATHS))

        hits, count = _MODULE._scan_file(path, declared)

        assert hits == []
        assert count == 1

    def test_a_justified_marker_suppresses(self, write_py: WritePy) -> None:
        path = write_py(_SUPPRESSED)

        hits, count = _MODULE._scan_file(path, "src/synthorg/budget/enforcer.py")

        assert hits == []
        assert count == 1

    def test_a_marker_without_a_reason_does_not_suppress(
        self, write_py: WritePy
    ) -> None:
        """The reason is the exemption; without it there is nothing recorded."""
        path = write_py(_UNJUSTIFIED_SUPPRESSION)

        hits, _ = _MODULE._scan_file(path, "src/synthorg/budget/enforcer.py")

        assert len(hits) == 1


class TestMarkerValidation:
    @pytest.mark.parametrize(
        ("comment", "valid"),
        [
            ("# lint-allow: bound-pair-rewrite -- vendor shim", True),
            ("# lint-allow: bound-pair-rewrite --", False),
            ("# lint-allow: bound-pair-rewrite", False),
            ("# lint-allow: synthetic-agent-identity -- other gate", False),
            ("# just a comment", False),
        ],
        ids=["justified", "empty_reason", "no_dashes", "other_gate", "plain"],
    )
    def test_marker_shapes(self, comment: str, valid: bool) -> None:
        assert _MODULE._is_valid_marker(comment) is valid


class TestMain:
    """End-to-end runs against a sandboxed tree, never the real one."""

    @staticmethod
    def _plant(root: Path, rel: str, source: str) -> None:
        """Write *source* at *rel* under the sandbox root."""
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def _owners(self, root: Path) -> None:
        """Give every declared owner a live construct, so none reads stale."""
        for rel in _MODULE._BINDING_OWNER_PATHS:
            self._plant(root, rel, _FRESH_CONSTRUCTION)

    def test_a_clean_tree_passes(self, tmp_path: Path) -> None:
        self._owners(tmp_path)
        self._plant(tmp_path, "src/synthorg/engine/pick.py", _SELECTION_NOT_REWRITE)

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 0

    def test_a_rewrite_fails_the_gate(self, tmp_path: Path) -> None:
        self._owners(tmp_path)
        self._plant(tmp_path, "src/synthorg/budget/enforcer.py", _WHOLE_BINDING_SWAP)

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 1

    def test_an_owner_that_stopped_producing_a_binding_is_a_config_error(
        self, tmp_path: Path
    ) -> None:
        """An unused exemption is one the next rewrite inherits silently."""
        self._owners(tmp_path)
        stale = next(iter(_MODULE._BINDING_OWNER_PATHS))
        self._plant(tmp_path, stale, _SELECTION_NOT_REWRITE)

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2

    def test_an_unreadable_repo_root_is_a_config_error(self, tmp_path: Path) -> None:
        assert _MODULE.main(["--repo-root", str(tmp_path / "absent")]) == 2
