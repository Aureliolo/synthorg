"""Unit tests for ``scripts/check_charter_authorised_initiative.py``.

The gate guards one invariant: an initiative starts only from a charter the
operator approved. The shapes below are the ways a second intake path opens the
door, written out verbatim, plus the near neighbours that must NOT be flagged
and the ways the invariant itself can be hollowed out.

Tests load the script via :mod:`importlib` and call its private helpers
directly, matching the pattern in ``test_check_no_bound_pair_rewrite.py``.
"""

import importlib.util
import os
import sys
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
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_charter_authorised_initiative.py"


class _SiteView(Protocol):
    """Structural view of the script's private ``_Site`` class."""

    lineno: int
    col: int
    kind: str
    authorised: bool


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _MODEL_REL: str
    _OWNER_REL: str

    @staticmethod
    def _forcing_sites(tree: object) -> list[_SiteView]: ...
    @staticmethod
    def _invariant_faults(project_root: Path) -> list[str]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_charter_authorised_initiative",
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


def _sites(source: str) -> list[_SiteView]:
    """Parse *source* and return the forcing sites the gate finds in it.

    Returns:
        One entry per site the gate would flag or attribute to the owner.
    """
    import ast

    return _MODULE._forcing_sites(ast.parse(source))


# The classifier route, verbatim in shape: a turn decides a message looks like
# a project and provisions one, with no operator gate anywhere between the
# message and the spend.
_CLASSIFIER_INTAKE = """\
def dispatch(brief, project: str):
    return WorkItem(
        origin_adapter_id="conversational-intake",
        source=WorkSource.OBJECTIVE,
        title=brief.title,
        raw_intent=brief.raw_intent,
        project=project,
        requested_by=brief.requested_by,
        plan_required=True,
    )
"""

# The flag flipped on an already-built item. ``model_copy`` skips validation,
# so the runtime refusal never runs: this is what a second intake path looks
# like once it stops constructing one.
_COPY_REWRITE = """\
def force_a_plan(item):
    return item.model_copy(update={"plan_required": True})
"""

# The same mapping spelled as a call rather than a literal.
_COPY_DICT_CALL_FORM = """\
def force_a_plan(item):
    return item.model_copy(update=dict(plan_required=True))
"""

# The mapping built a line earlier, which a literal-only check would miss.
_COPY_NAMED_UPDATE = """\
def force_a_plan(item):
    updates = {}
    updates["plan_required"] = True
    return item.model_copy(update=updates)
"""

# A variable rather than a literal. The flag is the decision whatever computed
# it, so reading only literals would leave the rule one indirection away.
_COMPUTED_FLAG = """\
def dispatch(brief, wants_plan: bool):
    return WorkItem(title=brief.title, plan_required=wants_plan)
"""

# The charter path, which is what the owner is allowed to do.
_AUTHORISED_INTAKE = """\
def dispatch(charter):
    return WorkItem(
        origin_adapter_id="charter-entry-adapter",
        source=WorkSource.OBJECTIVE,
        title=charter.title,
        raw_intent=charter.raw_intent,
        project=charter.project,
        requested_by=charter.requested_by,
        plan_required=True,
        charter_id=charter.id,
    )
"""

# The owner keeping the flag but dropping the binding: the regression the
# runtime validator would only surface at request time.
_OWNER_WITHOUT_CHARTER = """\
def dispatch(charter):
    return WorkItem(title=charter.title, plan_required=True)
"""

# A copy carrying both halves. The charter rides inside ``update=`` because
# that is where the flag is; a check reading only top-level keywords would
# call this authorised site unauthorised.
_AUTHORISED_COPY = """\
def force_a_plan(item, charter):
    return item.model_copy(
        update={"plan_required": True, "charter_id": charter.id}
    )
"""

# Both halves written into a named mapping one key per statement, which is
# where a per-statement flag test loses the charter and calls the authorised
# site unauthorised.
_AUTHORISED_NAMED_UPDATE = """\
def force_a_plan(item, charter):
    updates = {}
    updates["plan_required"] = True
    updates["charter_id"] = charter.id
    return item.model_copy(update=updates)
"""

# One decision written two levels deep: the outer copy carries the flag and
# the inner construction is its argument.
_NESTED_FORCING = """\
def force_a_plan(item, charter):
    return item.model_copy(
        update={"plan_required": True, "charter_id": charter.id}
    ).model_copy(update={"plan_required": True, "charter_id": charter.id})
"""

# Reading the flag is not setting it; the spine does exactly this.
_READS_THE_FLAG = """\
def route(work_item):
    if work_item.plan_required:
        return RoutingVerdict.SPLITTABLE
    return RoutingVerdict.LEAF
"""

# The integration stage's own forcing flag, which opens no initiative.
_LEAF_FORCING = """\
def integrate(objective):
    return WorkItem(title=objective.title, leaf_required=True)
"""

# Copying an unrelated field is not a rewrite.
_UNRELATED_COPY = """\
def retitle(item, title: str):
    return item.model_copy(update={"title": title, "project": "growth"})
"""

_MODEL_WITH_INVARIANT = """\
class WorkItem(BaseModel):
    plan_required: bool = Field(default=False)
    charter_id: NotBlankStr | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_initiative_authorised(self) -> Self:
        if self.plan_required and self.charter_id is None:
            msg = "A work item that stands up an initiative must name a charter"
            raise ValueError(msg)
        return self
"""

_MODEL_WITHOUT_FIELD = """\
class WorkItem(BaseModel):
    plan_required: bool = Field(default=False)

    @model_validator(mode="after")
    def _validate_initiative_authorised(self) -> Self:
        if self.plan_required and self.charter_id is None:
            raise ValueError("unauthorised")
        return self
"""

_MODEL_VALIDATOR_STOPPED_RAISING = """\
class WorkItem(BaseModel):
    plan_required: bool = Field(default=False)
    charter_id: NotBlankStr | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_initiative_authorised(self) -> Self:
        if self.plan_required and self.charter_id is None:
            logger.warning("unauthorised initiative")
        return self
"""

_MODEL_CLASS_GONE = """\
class WorkBrief(BaseModel):
    plan_required: bool = Field(default=False)
"""


class TestForcingSites:
    """``_forcing_sites`` on the shapes that open an initiative."""

    @pytest.mark.parametrize(
        ("source", "kind"),
        [
            (_CLASSIFIER_INTAKE, "keyword"),
            (_COMPUTED_FLAG, "keyword"),
            (_COPY_REWRITE, "copy"),
            (_COPY_DICT_CALL_FORM, "copy"),
            (_COPY_NAMED_UPDATE, "copy"),
        ],
        ids=[
            "classifier_intake",
            "computed_flag",
            "copy_rewrite",
            "copy_dict_call_form",
            "copy_named_update",
        ],
    )
    def test_forcing_an_initiative_is_a_site(self, source: str, kind: str) -> None:
        sites = _sites(source)

        assert len(sites) == 1
        assert sites[0].kind == kind

    @pytest.mark.parametrize(
        "source",
        [_READS_THE_FLAG, _LEAF_FORCING, _UNRELATED_COPY],
        ids=["reads_the_flag", "leaf_forcing", "unrelated_copy"],
    )
    def test_a_near_neighbour_is_not_a_site(self, source: str) -> None:
        assert _sites(source) == []

    def test_a_charter_riding_along_marks_the_site_authorised(self) -> None:
        sites = _sites(_AUTHORISED_INTAKE)

        assert len(sites) == 1
        assert sites[0].authorised is True

    def test_the_flag_without_a_charter_is_unauthorised(self) -> None:
        sites = _sites(_OWNER_WITHOUT_CHARTER)

        assert len(sites) == 1
        assert sites[0].authorised is False

    def test_a_charter_inside_the_update_mapping_marks_it_authorised(self) -> None:
        # A copy writes both halves in the same place, so the charter is
        # read where the flag was rather than only at the top level.
        sites = _sites(_AUTHORISED_COPY)

        assert len(sites) == 1
        assert sites[0].kind == "copy"
        assert sites[0].authorised is True

    def test_both_halves_written_key_by_key_are_read_together(self) -> None:
        # The subscript form supplies one key per statement, so the charter
        # arrives in a later statement than the flag and only survives if
        # every key for the name is accumulated before the flag is tested.
        sites = _sites(_AUTHORISED_NAMED_UPDATE)

        assert len(sites) == 1
        assert sites[0].kind == "copy"
        assert sites[0].authorised is True

    def test_a_nest_of_forcing_calls_reports_the_outermost_once(self) -> None:
        # One decision written two levels deep. Counting it twice would
        # report a single violation as two.
        sites = _sites(_NESTED_FORCING)

        assert len(sites) == 1


class TestInvariantFaults:
    """The model must keep refusing what no static read can reach."""

    @staticmethod
    def _plant_model(root: Path, source: str) -> None:
        path = root / _MODULE._MODEL_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def test_an_intact_model_has_no_faults(self, tmp_path: Path) -> None:
        self._plant_model(tmp_path, _MODEL_WITH_INVARIANT)

        assert _MODULE._invariant_faults(tmp_path) == []

    @pytest.mark.parametrize(
        "source",
        [
            _MODEL_WITHOUT_FIELD,
            _MODEL_VALIDATOR_STOPPED_RAISING,
            _MODEL_CLASS_GONE,
        ],
        ids=["field_gone", "validator_stopped_raising", "class_gone"],
    )
    def test_a_hollowed_out_invariant_is_a_fault(
        self, tmp_path: Path, source: str
    ) -> None:
        self._plant_model(tmp_path, source)

        assert _MODULE._invariant_faults(tmp_path) != []

    def test_a_missing_model_is_a_fault(self, tmp_path: Path) -> None:
        assert _MODULE._invariant_faults(tmp_path) != []


class TestMain:
    """End-to-end runs against a sandboxed tree, never the real one."""

    @staticmethod
    def _plant(root: Path, rel: str, source: str) -> None:
        """Write *source* at *rel* under the sandbox root."""
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def _baseline(self, root: Path) -> None:
        """Plant an intact model and a live, authorised owner."""
        self._plant(root, _MODULE._MODEL_REL, _MODEL_WITH_INVARIANT)
        self._plant(root, _MODULE._OWNER_REL, _AUTHORISED_INTAKE)

    def test_a_clean_tree_passes(self, tmp_path: Path) -> None:
        self._baseline(tmp_path)
        self._plant(tmp_path, "src/synthorg/engine/pipeline/route.py", _READS_THE_FLAG)

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 0

    @pytest.mark.parametrize(
        "source",
        [_CLASSIFIER_INTAKE, _COPY_REWRITE],
        ids=["classifier_intake", "copy_rewrite"],
    )
    def test_a_second_intake_path_fails_the_gate(
        self, tmp_path: Path, source: str
    ) -> None:
        self._baseline(tmp_path)
        self._plant(tmp_path, "src/synthorg/meta/chief_of_staff/intake.py", source)

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 1

    def test_an_owner_that_dropped_the_charter_is_a_config_error(
        self, tmp_path: Path
    ) -> None:
        self._baseline(tmp_path)
        self._plant(tmp_path, _MODULE._OWNER_REL, _OWNER_WITHOUT_CHARTER)

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2

    def test_an_owner_that_stopped_forcing_is_a_config_error(
        self, tmp_path: Path
    ) -> None:
        """An unused exemption is one the next intake path inherits silently."""
        self._baseline(tmp_path)
        self._plant(tmp_path, _MODULE._OWNER_REL, _READS_THE_FLAG)

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2

    def test_a_hollowed_out_invariant_is_a_config_error(self, tmp_path: Path) -> None:
        self._baseline(tmp_path)
        self._plant(tmp_path, _MODULE._MODEL_REL, _MODEL_VALIDATOR_STOPPED_RAISING)

        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2

    def test_an_unreadable_repo_root_is_a_config_error(self, tmp_path: Path) -> None:
        assert _MODULE.main(["--repo-root", str(tmp_path / "absent")]) == 2
