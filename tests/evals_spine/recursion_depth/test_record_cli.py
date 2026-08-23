# module-kind: tests
"""The entry point: plan mode spends nothing, and staging narrows honestly."""

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import record_recursion_depth as record_module
from scripts.record_recursion_depth import (
    _reclaim_workspaces,
    _recording_slug,
    check_declared_families,
    describe_plan,
    main,
    narrow,
)

from evals.errors import (
    HarnessProviderMissingError,
    RecursionDepthJudgeNotIndependentError,
)
from evals.harness.binding import HarnessBinder
from evals.harness.host import RecordingGatewayHost
from evals.recursion_depth.manifest import Independence, load_manifest
from evals.recursion_depth.runner import planned_cells
from evals.recursion_depth.tree import SpecBrief, load_spec_brief
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.enums import AuthType
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_MANIFEST = (
    Path(__file__).resolve().parents[3] / "evals" / "recursion_depth" / "manifest.yaml"
)


def _spec() -> SpecBrief:
    """Load the committed specification.

    Returns:
        The brief.
    """
    manifest = load_manifest(_MANIFEST)
    return load_spec_brief(Path(manifest.spec_dir))


def _config(*, executor_family: str | None, reviewer_family: str | None) -> RootConfig:
    """Build a company config aliasing the manifest's two placeholder pairs.

    Args:
        executor_family: Family the config claims for the executor's model.
        reviewer_family: Family the config claims for the reviewer's model.

    Returns:
        The config.
    """

    return _config_declaring(
        executor_family=executor_family,
        reviewer_family=reviewer_family,
        connection_family=None,
    )


def _config_declaring(
    *,
    executor_family: str | None,
    reviewer_family: str | None,
    connection_family: str | None,
) -> RootConfig:
    """Build a config declaring families at the model and connection levels.

    Args:
        executor_family: Family the config claims for the executor's model.
        reviewer_family: Family the config claims for the reviewer's model.
        connection_family: Family the CONNECTION declares, which every model
            that declares none of its own inherits.

    Returns:
        The config.
    """

    def _model(alias: str, family: str | None) -> ProviderModelConfig:
        return ProviderModelConfig(
            id=NotBlankStr(f"real-{alias}"),
            alias=NotBlankStr(alias),
            metadata=ModelMetadata(family=family),
        )

    return RootConfig(
        company_name=NotBlankStr("Recursion Depth Sweep"),
        providers={
            "example-provider": ProviderConfig(
                auth_type=AuthType.CUSTOM_HEADER,
                custom_header_name=NotBlankStr("Authorization"),
                custom_header_value=NotBlankStr("Bearer test-key"),
                family=connection_family,
                models=(
                    _model("example-capable-001", executor_family),
                    _model("example-expert-001", reviewer_family),
                ),
            )
        },
    )


class TestDeclaredFamiliesMatchWhatAnswers:
    """The manifest claims decorrelation; the config picks who actually runs."""

    def test_a_config_putting_both_pairs_in_one_family_is_refused(self) -> None:
        # The loophole the manifest alone cannot close: every check there
        # passes on the declared strings, and the models that answer are both
        # from one organisation.
        with pytest.raises(
            RecursionDepthJudgeNotIndependentError, match="nobody achieved"
        ):
            check_declared_families(
                load_manifest(_MANIFEST),
                _config(
                    executor_family="bound-family-a",
                    reviewer_family="bound-family-a",
                ),
            )

    def test_bound_families_that_differ_satisfy_the_placeholder_claim(self) -> None:
        # The bound names never match the manifest's own placeholders and are
        # not required to. What is checked is that the two pairs differ, which
        # is the whole content of a cross_family claim.
        check_declared_families(
            load_manifest(_MANIFEST),
            _config(
                executor_family="bound-family-a",
                reviewer_family="bound-family-b",
            ),
        )

    def test_a_config_declaring_no_family_leaves_the_manifest_the_only_claim(
        self,
    ) -> None:
        # Not an error: the config saying nothing is not the config disagreeing.
        check_declared_families(
            load_manifest(_MANIFEST),
            _config(executor_family=None, reviewer_family=None),
        )

    def test_a_family_inherited_from_the_connection_is_still_a_family(self) -> None:
        """Reading only the model half turns a collision into two silences.

        A config that declares the family once on the connection and lets both
        models inherit it puts both pairs in ONE organisation. Resolved from
        the model alone that reads as two undeclared families, which the check
        waves through by saying nothing rather than by differing, and the run
        records a correlated judge as independent.
        """
        with pytest.raises(
            RecursionDepthJudgeNotIndependentError, match="nobody achieved"
        ):
            check_declared_families(
                load_manifest(_MANIFEST),
                _config_declaring(
                    executor_family=None,
                    reviewer_family=None,
                    connection_family="bound-family-a",
                ),
            )

    def test_a_models_own_family_still_wins_over_its_connections(self) -> None:
        """The connection is the fallback, never an override."""
        check_declared_families(
            load_manifest(_MANIFEST),
            _config_declaring(
                executor_family="bound-family-a",
                reviewer_family="bound-family-b",
                connection_family="bound-family-a",
            ),
        )


class TestPlanMode:
    """The default path boots nothing and states the bill."""

    def test_it_exits_clean_without_recording(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # No --record, so no gateway, no port, no container and no spend.
        assert main([]) == 0
        assert "Recursion-depth recording plan" in capsys.readouterr().out

    def test_it_names_the_scenario_and_the_ceiling(self) -> None:
        # A depth sweep's session count is a product of branching factors the
        # manifest cannot predict, so the figure is what a FULL tree costs at
        # the declared branching rather than a bound in either direction, and
        # the ceiling is what actually bounds the spend. Presenting it as a
        # floor invites an operator to read a number the run can exceed.
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        assert "full tree" in plan
        assert "at least" not in plan
        assert str(manifest.max_sessions) in plan

    def test_the_projection_is_derived_from_the_tree_each_cap_admits(self) -> None:
        """The whole tree is counted, not summarised as a sentence about it.

        A figure that scales only with the number of runs is the one an
        operator sizes ``max_sessions`` from and then loses a paid run to: the
        tree, not the matrix, is where a depth sweep's sessions come from.
        """
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        projected = sum(
            manifest.projected_sessions(cell.depth_cap)
            for cell in planned_cells(manifest)
        )
        per_run = len(planned_cells(manifest)) * (1 + manifest.merge_attempts * 2)

        assert projected > per_run * 10
        assert f"{projected:,}" in plan

    def test_the_projection_prints_the_assumption_it_rests_on(self) -> None:
        """A model whose input is hidden reads as a measurement."""
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        assert f"{manifest.projected_branching} subtasks per planning session" in plan

    def test_the_per_cell_cost_grows_with_the_cap(self) -> None:
        """The deep end is where a ceiling is chosen wrong and money is lost."""
        manifest = load_manifest(_MANIFEST)

        per_cell = [manifest.projected_sessions(cap) for cap in (1, 2, 3)]

        assert per_cell == sorted(per_cell)
        assert per_cell[0] < per_cell[2]

    def test_the_model_reproduces_the_shape_a_real_tree_had(self) -> None:
        """Checked against the measured run the projection models.

        A cap-3 tree held 85 leaves across 25 nodes that planned and cost about
        158 sessions. The declared branching is rounded DOWN from the ~4.4 that
        implies, so the projection stays a floor: it must not read HIGHER than
        what was actually measured, or an operator would size a ceiling off a
        number no run has ever reached.
        """
        manifest = load_manifest(_MANIFEST)

        projected = manifest.projected_sessions(3)

        assert projected < 158
        assert projected > 100

    def test_it_states_the_equal_attempt_budget(self) -> None:
        # Repair only in the gated arm would let it win by spending more, so
        # the operator reading the plan is told the budget is shared.
        plan = describe_plan(load_manifest(_MANIFEST), _spec())

        assert "the SAME in both arms" in plan

    def test_it_states_a_token_bound_the_money_bound_cannot_give(self) -> None:
        # A flat-rate connection attributes 0.0 to every call, so the money
        # ceiling never fires there and the money worst case reads 0.00
        # however long the sweep runs. Tokens are counted on every provider,
        # so the plan states a bound that holds without the reader first
        # knowing how they are billed.
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        assert f"{manifest.max_sessions * manifest.unit_token_ceiling:,}" in plan
        assert "flat-rate" in plan

    def test_the_shipped_manifest_needs_no_independence_caveat(self) -> None:
        plan = describe_plan(load_manifest(_MANIFEST), _spec())

        assert "CAVEAT" not in plan

    def test_a_weakened_judge_puts_its_caveat_on_the_plan(self) -> None:
        # The operator is told before spending, not after reading the chart.
        shipped = load_manifest(_MANIFEST)
        weakened = shipped.model_copy(
            update={
                "reviewer": shipped.reviewer.model_copy(
                    update={"family": shipped.executor.family}
                ),
                "independence": Independence.SAME_FAMILY,
            }
        )

        plan = describe_plan(weakened, _spec())

        assert "CAVEAT" in plan
        assert "share a model family" in plan


class TestPreflightGuardsTheBoot:
    """Nothing is stood up until everything knowable has been settled."""

    async def test_a_failing_preflight_stops_before_the_host_is_built(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The ordering IS the finding: a probe after the boot buys nothing.

        Every condition preflight checks is a property of the configuration or
        the machine, and none of it becomes truer once a scratch database, a
        gateway and a container are standing. Asserted on the collaborator
        never being reached, because the two calls type-check in either order
        and only the sequence decides whether an operator waits.
        """
        built: list[object] = []

        refusal = "no such provider"
        built_early = "the host was built before preflight passed"

        async def _refuse(**kwargs: object) -> None:
            del kwargs
            raise HarnessProviderMissingError(refusal)

        def _host(config: object) -> object:
            built.append(config)
            raise AssertionError(built_early)

        monkeypatch.setattr(record_module, "run_preflight", _refuse)
        monkeypatch.setattr(record_module, "RecordingGatewayHost", _host)

        with pytest.raises(HarnessProviderMissingError):
            await record_module._record(
                record_module._parse_args(["--record", "--work-root", str(tmp_path)]),
                manifest=load_manifest(_MANIFEST),
                spec=_spec(),
                company_config=_config(
                    executor_family="bound-family-a",
                    reviewer_family="bound-family-b",
                ),
            )

        assert not built


class TestStaging:
    """A large bill is paid in instalments, and never for a cap nobody asked."""

    def test_depths_narrows_to_the_named_caps(self) -> None:
        narrowed = narrow(load_manifest(_MANIFEST), "1,2")

        assert narrowed.depths == (1, 2)

    def test_no_depths_keeps_the_manifest(self) -> None:
        manifest = load_manifest(_MANIFEST)

        assert narrow(manifest, None).depths == manifest.depths

    def test_max_sessions_reaches_the_plan_the_operator_reads(self) -> None:
        # The ceiling is what turns "at least 182 sessions" into a decision, so
        # applying the override only to the run would print the manifest's own
        # figure beside the flag that was meant to lower it.
        narrowed = narrow(load_manifest(_MANIFEST), None, 30)

        plan = describe_plan(narrowed, _spec())

        assert narrowed.max_sessions == 30
        assert "3000 sessions" not in plan
        assert "30 sessions" in plan

    def test_max_sessions_survives_a_depth_narrowing(self) -> None:
        narrowed = narrow(load_manifest(_MANIFEST), "1,2", 30)

        assert narrowed.depths == (1, 2)
        assert narrowed.max_sessions == 30

    def test_a_cap_the_manifest_does_not_carry_is_refused(self) -> None:
        # Silently recording nothing for it would leave a gap in the curve that
        # reads as a measured zero.
        with pytest.raises(ValueError, match="does not carry"):
            narrow(load_manifest(_MANIFEST), "1,4,9")


def _record_args(tmp_path: Path) -> argparse.Namespace:
    """Build the argument bundle ``_record`` reads.

    Returns:
        The namespace.
    """
    return argparse.Namespace(
        out_dir=tmp_path / "out",
        work_root=tmp_path / "work",
        keep_workspaces=False,
        manifest=_MANIFEST,
        resume=False,
        max_sessions=None,
    )


def _recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sweep: Exception | None,
    release: Exception | None = None,
) -> Path:
    """Stub everything ``_record`` reaches for, and seed the tree it builds.

    Every collaborator between the preflight and the report is a gateway, a
    container or a provider call, so each is replaced with the smallest thing
    that lets the lifecycle run. What is left unstubbed is the part under
    test: which scratch root is chosen, and whether it survives.

    Args:
        tmp_path: The test's directory.
        monkeypatch: Patching seam.
        sweep: Raised by the sweep, or ``None`` for one that measured a cell.
        release: Raised when the containers are released, or ``None`` for a
            release that succeeds.

    Returns:
        The scratch root the recorder will build under, already populated so
        its removal is observable.
    """
    root = tmp_path / "work" / f"run-{_recording_slug(tmp_path / 'out')}"
    (root / "unit").mkdir(parents=True)

    host = mock_of[RecordingGatewayHost](
        # The three addresses the start log states, which is everything the
        # lifecycle under test reads off a host.
        container_gateway_url="http://gateway.invalid/v1",
        container_mcp_url="http://gateway.invalid/mcp",
        port=0,
    )
    host.__aenter__.return_value = host
    host.__aexit__.return_value = False
    binder = mock_of[HarnessBinder]()
    binder.release_tool_sandboxes.side_effect = release

    async def _no_preflight(**_kwargs: object) -> None:
        return None

    async def _swept(*_args: object, **_kwargs: object) -> object:
        if sweep is not None:
            raise sweep
        return SimpleNamespace(measured_cells=("one",))

    monkeypatch.setattr(record_module, "run_preflight", _no_preflight)
    monkeypatch.setattr(record_module, "RecordingGatewayHost", lambda _c: host)
    monkeypatch.setattr(record_module, "HarnessBinder", lambda **_k: binder)
    monkeypatch.setattr(record_module, "_host_config", lambda *a, **k: None)
    monkeypatch.setattr(record_module, "_build_context", _built_context)
    monkeypatch.setattr(record_module, "capture_provenance", lambda **_k: None)
    monkeypatch.setattr(record_module, "run_sweep", _swept)
    monkeypatch.setattr(record_module, "write_report", lambda *_a: (tmp_path / "r",))
    return root


async def _built_context(*_args: object, **_kwargs: object) -> None:
    """Stand in for the context build, which needs a live gateway.

    Returns:
        Nothing the lifecycle under test reads.
    """
    return


class TestTheScratchRootAResumeContinuesWith:
    """A journal buys nothing if the trees it indexes move every run."""

    def test_the_same_output_directory_names_the_same_root(
        self, tmp_path: Path
    ) -> None:
        # A resume rebuilds each unit's tree path from the run root, so a root
        # it cannot predict leaves every cell finding nothing and paying again
        # for what the last attempt already built.
        assert _recording_slug(tmp_path / "out") == _recording_slug(tmp_path / "out")

    def test_two_output_directories_name_different_roots(self, tmp_path: Path) -> None:
        # Each unit's provisioning removes and re-copies a whole tree, which is
        # race-free only within one process, so two recordings running at once
        # must never share a root.
        assert _recording_slug(tmp_path / "one") != _recording_slug(tmp_path / "two")

    def test_the_root_is_one_path_segment(self, tmp_path: Path) -> None:
        # An output directory is an absolute path carrying separators, and on
        # this platform a drive letter; embedding it would build a tree nobody
        # asked for.
        slug = _recording_slug(tmp_path / "out")

        assert "/" not in slug
        assert "\\" not in slug

    async def test_an_unfinished_run_keeps_its_trees(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Reclaiming on the way out of a failure destroys exactly what the
        # next --resume continues with.
        root = tmp_path / "run-abc"
        (root / "unit").mkdir(parents=True)

        await _reclaim_workspaces(root, keep=True)

        assert root.is_dir()
        assert "--resume" in capsys.readouterr().out

    async def test_a_finished_run_reclaims_them(self, tmp_path: Path) -> None:
        root = tmp_path / "run-abc"
        (root / "unit").mkdir(parents=True)

        await _reclaim_workspaces(root, keep=False)

        assert not root.exists()


class TestWhatTheRecorderDoesWithTheTreesItBuilt:
    """Which trees survive is decided by the recorder, not by its helper.

    The two cases above pin what ``_reclaim_workspaces`` does when told; these
    pin what it is told, which is the half a resume actually depends on. A
    ``completed`` flag set one statement too early reads correctly in both
    branches of the helper and still deletes what the next attempt needed.
    """

    async def test_a_sweep_that_raised_leaves_its_trees_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _recorded(tmp_path, monkeypatch, sweep=OSError("the gateway died"))

        with pytest.raises(OSError, match="the gateway died"):
            await record_module._record(
                _record_args(tmp_path),
                manifest=load_manifest(_MANIFEST),
                spec=_spec(),
                company_config=_config(
                    executor_family="bound-family-a", reviewer_family="bound-family-b"
                ),
            )

        assert root.is_dir()

    async def test_a_release_failure_still_reclaims_a_finished_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Releasing the containers and reclaiming the trees are two
        # obligations, not a sequence: run as one, the first one raising
        # silently drops the second and the disk grows for ever.
        root = _recorded(
            tmp_path, monkeypatch, sweep=None, release=OSError("the daemon went away")
        )

        with pytest.raises(OSError, match="the daemon went away"):
            await record_module._record(
                _record_args(tmp_path),
                manifest=load_manifest(_MANIFEST),
                spec=_spec(),
                company_config=_config(
                    executor_family="bound-family-a", reviewer_family="bound-family-b"
                ),
            )

        assert not root.exists()

    async def test_a_sweep_that_wrote_its_report_reclaims_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _recorded(tmp_path, monkeypatch, sweep=None)

        assert (
            await record_module._record(
                _record_args(tmp_path),
                manifest=load_manifest(_MANIFEST),
                spec=_spec(),
                company_config=_config(
                    executor_family="bound-family-a", reviewer_family="bound-family-b"
                ),
            )
            == 0
        )

        assert not root.exists()
