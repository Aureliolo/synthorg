# module-kind: tests
"""The entry point: plan mode spends nothing, and staging narrows honestly."""

from pathlib import Path

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
from evals.recursion_depth.manifest import Independence, load_manifest
from evals.recursion_depth.tree import SpecBrief, load_spec_brief
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.enums import AuthType

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

    def test_it_states_the_session_floor_and_the_ceiling(self) -> None:
        # A depth sweep's session count is a product of branching factors the
        # manifest cannot predict, so the figure is a floor and the ceiling is
        # what actually bounds the spend.
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        assert "at least" in plan
        assert str(manifest.max_sessions) in plan

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
