"""Golden positive / negative cases for the Litestar 2.22 parameter gate.

The gate (``scripts/check_litestar_path_parameter.py``) catches:

1. PATH-bound handler params using bare ``Parameter(...)`` instead of
   ``PathParameter(...)``;
2. ``Parameter(query=/header=/cookie=)`` deprecation shorthand;
3. ``Path*`` module-level aliases that don't wrap ``PathParameter``.

Each test writes a minimal synthetic source file into a tmp directory
that mimics the structure ``_iter_scanned_files`` walks, then drives
``_run`` against it. The synthetic files exercise one rule per fixture
so a future refactor that breaks one rule does not silently mask the
others.
"""

from pathlib import Path

import pytest
from scripts import check_litestar_path_parameter as gate


def _write_controller(repo_root: Path, name: str, source: str) -> None:
    """Drop a synthetic controller file under the gate's scanned path."""
    target = repo_root / "src" / "synthorg" / "api" / "controllers" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


def _write_alias_module(repo_root: Path, source: str) -> None:
    """Drop a synthetic ``path_params.py`` under the gate's alias path."""
    target = repo_root / "src" / "synthorg" / "api" / "path_params.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


@pytest.mark.unit
class TestRule1PathBoundParameter:
    def test_path_bound_with_bare_parameter_fails(self, tmp_path: Path) -> None:
        """A handler matching a route placeholder must NOT use Parameter."""
        _write_controller(
            tmp_path,
            "regression.py",
            """
from typing import Annotated
from litestar import Controller, get
from litestar.params import Parameter


class RegressionController(Controller):
    path = "/things"

    @get("/{thing_id:str}")
    async def get_thing(
        self,
        thing_id: Annotated[str, Parameter(min_length=1)],
    ) -> dict[str, str]:
        return {"thing_id": thing_id}
""",
        )
        assert gate._run(tmp_path) == 1

    def test_path_bound_with_path_parameter_passes(self, tmp_path: Path) -> None:
        """The migrated shape -- typed marker on a PATH-bound param."""
        _write_controller(
            tmp_path,
            "ok.py",
            """
from typing import Annotated
from litestar import Controller, get
from litestar.params import PathParameter


class OkController(Controller):
    path = "/things"

    @get("/{thing_id:str}")
    async def get_thing(
        self,
        thing_id: Annotated[str, PathParameter(min_length=1)],
    ) -> dict[str, str]:
        return {"thing_id": thing_id}
""",
        )
        assert gate._run(tmp_path) == 0


@pytest.mark.unit
class TestRule2DeprecatedKwargs:
    def test_parameter_query_kwarg_fails(self, tmp_path: Path) -> None:
        """``Parameter(query="X")`` triggers a 2.22 DeprecationWarning."""
        _write_controller(
            tmp_path,
            "regression.py",
            """
from typing import Annotated
from litestar import Controller, get
from litestar.params import Parameter


class RegressionController(Controller):
    path = "/things"

    @get("/")
    async def list_things(
        self,
        kind: Annotated[
            str | None,
            Parameter(query="type", description="filter"),
        ] = None,
    ) -> dict[str, str | None]:
        return {"kind": kind}
""",
        )
        assert gate._run(tmp_path) == 1

    def test_parameter_header_kwarg_fails(self, tmp_path: Path) -> None:
        """``Parameter(header="X")`` is also deprecated in 2.22."""
        _write_controller(
            tmp_path,
            "regression.py",
            """
from typing import Annotated
from litestar import Controller, post
from litestar.params import Parameter


class RegressionController(Controller):
    path = "/things"

    @post("/")
    async def create_thing(
        self,
        idempotency_key: Annotated[
            str,
            Parameter(header="Idempotency-Key"),
        ],
    ) -> dict[str, str]:
        return {"key": idempotency_key}
""",
        )
        assert gate._run(tmp_path) == 1

    def test_query_parameter_name_kwarg_passes(self, tmp_path: Path) -> None:
        """``QueryParameter(name="X")`` is the migrated shape."""
        _write_controller(
            tmp_path,
            "ok.py",
            """
from typing import Annotated
from litestar import Controller, get
from litestar.params import QueryParameter


class OkController(Controller):
    path = "/things"

    @get("/")
    async def list_things(
        self,
        kind: Annotated[
            str | None,
            QueryParameter(name="type", description="filter"),
        ] = None,
    ) -> dict[str, str | None]:
        return {"kind": kind}
""",
        )
        assert gate._run(tmp_path) == 0


@pytest.mark.unit
class TestRule3PathAliasModule:
    def test_path_alias_with_parameter_fails(self, tmp_path: Path) -> None:
        """``PathFoo = Annotated[..., Parameter(...)]`` is forbidden."""
        _write_alias_module(
            tmp_path,
            """
from typing import Annotated
from litestar.params import Parameter

PathFoo = Annotated[str, Parameter(min_length=1, max_length=64)]
""",
        )
        assert gate._run(tmp_path) == 1

    def test_path_alias_with_path_parameter_passes(self, tmp_path: Path) -> None:
        """The migrated shape -- alias name with the typed PATH marker."""
        _write_alias_module(
            tmp_path,
            """
from typing import Annotated
from litestar.params import PathParameter

PathFoo = Annotated[str, PathParameter(min_length=1, max_length=64)]
""",
        )
        assert gate._run(tmp_path) == 0


@pytest.mark.unit
class TestOptOutMarker:
    def test_marker_suppresses_violation(self, tmp_path: Path) -> None:
        """Per-line opt-out lets a rare exemption land without failing.

        Includes a non-empty reason so a reviewer can audit the
        bypass; the gate does not silence the violation without a
        documented reason.
        """
        _write_controller(
            tmp_path,
            "exempt.py",
            """
from typing import Annotated
from litestar import Controller, get
from litestar.params import Parameter


class ExemptController(Controller):
    path = "/things"

    @get("/{thing_id:str}")
    async def get_thing(
        self,
        thing_id: Annotated[
            str,
            Parameter(min_length=1),  # lint-allow: litestar-path-parameter -- legacy
        ],
    ) -> dict[str, str]:
        return {"thing_id": thing_id}
""",
        )
        assert gate._run(tmp_path) == 0


@pytest.mark.unit
class TestQueryParamWithBareParameterIsAllowed:
    """A query-bound handler param using bare ``Parameter()`` (no
    ``query=`` kwarg) is NOT a regression -- the bare form still works
    and only the explicit ``query=`` kwarg is deprecated. The plan
    explicitly opted to keep the query-side enforcement narrow."""

    def test_query_bound_bare_parameter_does_not_fail(
        self,
        tmp_path: Path,
    ) -> None:
        _write_controller(
            tmp_path,
            "ok.py",
            """
from typing import Annotated
from litestar import Controller, get
from litestar.params import Parameter


class OkController(Controller):
    path = "/things"

    @get("/")
    async def list_things(
        self,
        kind: Annotated[
            str | None,
            Parameter(max_length=64),
        ] = None,
    ) -> dict[str, str | None]:
        return {"kind": kind}
""",
        )
        # The route placeholder set is empty (no ``{...}`` in the
        # path), so ``kind`` is unambiguously query-bound; Rule 1
        # does NOT apply. Rule 2 only fires on explicit ``query=`` /
        # ``header=`` / ``cookie=`` kwargs. The gate stays silent.
        assert gate._run(tmp_path) == 0
