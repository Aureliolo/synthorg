# module-kind: tests
"""The entry point: plan mode spends nothing, and staging narrows honestly."""

from pathlib import Path

import pytest
from scripts.record_recursion_depth import (
    check_declared_families,
    describe_plan,
    main,
    narrow,
)

from evals.errors import RecursionDepthJudgeNotIndependentError
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
                _config(executor_family="qwen-coder", reviewer_family="qwen-coder"),
            )

    def test_real_families_that_differ_satisfy_the_placeholder_claim(self) -> None:
        # The names never match the manifest's vendor-agnostic placeholders and
        # are not required to. What is checked is that the two pairs differ,
        # which is the whole content of a cross_family claim.
        check_declared_families(
            load_manifest(_MANIFEST),
            _config(executor_family="qwen-coder", reviewer_family="deepseek-v"),
        )

    def test_a_config_declaring_no_family_leaves_the_manifest_the_only_claim(
        self,
    ) -> None:
        # Not an error: the config saying nothing is not the config disagreeing.
        check_declared_families(
            load_manifest(_MANIFEST),
            _config(executor_family=None, reviewer_family=None),
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
