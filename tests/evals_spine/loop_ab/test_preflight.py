# module-kind: tests
"""The checks that decide whether a matrix is worth starting.

The latency probe is the one with judgement in it. Latency is a scored
dimension and the matrix records cells one after another over roughly an hour,
so a provider whose service time swings by an order of magnitude scores each
cell against its own queue rather than against the other cells.
"""

import asyncio
import sys
from datetime import UTC, datetime
from typing import Final

import pytest

from evals.errors import (
    EvalToolMissingError,
    LoopAbProviderDegradedError,
    LoopAbProviderMissingError,
)
from evals.loop_ab.manifest import CapabilityEntry, LoopAbManifest
from evals.loop_ab.preflight import DEFAULT_LATENCY_CEILING_SECONDS, run_preflight
from evals.models.brief import (
    Brief,
    BriefKind,
    ExecutableChecks,
    HiddenCheckSpec,
    LimitsSpec,
)
from synthorg.config.schema import ProviderConfig, RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_selector import registered_loop_types
from synthorg.providers.enums import AuthType

pytestmark = pytest.mark.integration

_PROVIDER: Final = "example-provider"


def _tier(capability: str, model_id: str) -> CapabilityEntry:
    """A capability bound to the test provider.

    Returns:
        The capability entry.
    """
    return CapabilityEntry(
        capability=NotBlankStr(capability),
        provider=NotBlankStr(_PROVIDER),
        model_id=NotBlankStr(model_id),
    )


def _manifest(*capabilities: CapabilityEntry) -> LoopAbManifest:
    """A manifest over *capabilities*, with everything else at its default.

    Returns:
        The manifest.
    """
    return LoopAbManifest(
        brief_suite=NotBlankStr("evals/loop_ab/briefs"),
        repetitions=1,
        # The manifest refuses to omit a registered loop, so this is discovered
        # rather than listed: a third loop joins the comparison without an edit.
        loops=tuple(NotBlankStr(name) for name in registered_loop_types()),
        capabilities=capabilities or (_tier("expert", "example-expert-001"),),
    )


def _brief_checked_with(command: str) -> Brief:
    """An executable brief graded by running *command*.

    Returns:
        The brief.
    """
    return Brief(
        brief_id=NotBlankStr("preflight-tooling"),
        schema_version=1,
        kind=BriefKind.EXECUTABLE,
        title=NotBlankStr("Preflight tooling"),
        description=NotBlankStr("Graded by a command that may not exist here."),
        estimated_complexity=1,
        acceptance_criteria=(NotBlankStr("the check runs"),),
        limits=LimitsSpec(max_total_cost=1.0, max_wall_clock_seconds=60, max_turns=4),
        checks=ExecutableChecks(
            hidden_tests=(HiddenCheckSpec(cmd=(NotBlankStr(command),)),)
        ),
    )


def _config() -> RootConfig:
    """A company config carrying the test provider.

    Returns:
        The config.
    """
    return RootConfig(
        company_name=NotBlankStr("Loop A/B Preflight"),
        providers={
            _PROVIDER: ProviderConfig(
                driver=NotBlankStr("litellm"),
                auth_type=AuthType.SUBSCRIPTION,
                subscription_token=NotBlankStr("probe-token"),
                tos_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        },
    )


class TestLatencyProbe:
    async def test_a_responsive_provider_passes(self) -> None:
        seen: list[CapabilityEntry] = []

        async def _probe(capability: CapabilityEntry) -> float:
            seen.append(capability)
            return 1.5

        await run_preflight(
            manifest=_manifest(),
            company_config=_config(),
            check_docker=False,
            probe=_probe,
        )

        assert seen

    async def test_a_cold_first_call_is_warm_up_not_a_verdict(self) -> None:
        """The provider loads a model on first use, then serves it fast.

        Charging that to the band would refuse every cold provider; charging it
        to the first cell recorded is worse, because no other cell pays it.
        """
        measured = iter([90.0, 2.0, 2.1])

        async def _probe(_tier: CapabilityEntry) -> float:
            return next(measured)

        await run_preflight(
            manifest=_manifest(),
            company_config=_config(),
            check_docker=False,
            probe=_probe,
        )

    async def test_an_intermittently_degraded_provider_is_refused(self) -> None:
        """One fast answer is not evidence the provider is healthy.

        This is the condition the check exists for: the same hosted model
        answered a five-token request in 1.2s and, twenty minutes later, in
        72s. Judging the warmed attempts on their best would pass exactly
        that, so they are judged on their worst.
        """
        measured = iter([2.0, 1.2, DEFAULT_LATENCY_CEILING_SECONDS + 60.0])

        async def _probe(_tier: CapabilityEntry) -> float:
            return next(measured)

        with pytest.raises(LoopAbProviderDegradedError):
            await run_preflight(
                manifest=_manifest(),
                company_config=_config(),
                check_docker=False,
                probe=_probe,
            )

    async def test_an_attempt_that_will_not_answer_is_abandoned(self) -> None:
        """A probe must answer faster than the thing it protects against.

        One measured attempt took 311 seconds, retries included, to establish
        something its first minute had already settled.
        """
        never = asyncio.Event()

        async def _probe(_tier: CapabilityEntry) -> float:
            await never.wait()
            return 0.0

        with pytest.raises(LoopAbProviderDegradedError):
            await run_preflight(
                manifest=_manifest(),
                company_config=_config(),
                check_docker=False,
                latency_ceiling_seconds=0.01,
                probe=_probe,
            )

    async def test_a_degraded_provider_is_refused_before_anything_is_spent(
        self,
    ) -> None:
        async def _probe(_tier: CapabilityEntry) -> float:
            return DEFAULT_LATENCY_CEILING_SECONDS + 1.0

        with pytest.raises(LoopAbProviderDegradedError) as excinfo:
            await run_preflight(
                manifest=_manifest(),
                company_config=_config(),
                check_docker=False,
                probe=_probe,
            )

        message = str(excinfo.value)
        assert "example-expert-001" in message
        assert str(DEFAULT_LATENCY_CEILING_SECONDS) in message

    async def test_every_tier_is_probed_so_one_slow_model_cannot_hide(
        self,
    ) -> None:
        # The capabilities are separate model pools; the small one being fast says
        # nothing about the large one, and the matrix scores all three.
        probed: list[str] = []

        async def _probe(capability: CapabilityEntry) -> float:
            probed.append(capability.model_id)
            return 1.0

        await run_preflight(
            manifest=_manifest(
                _tier("basic", "example-basic-001"),
                _tier("capable", "example-capable-001"),
                _tier("expert", "example-expert-001"),
            ),
            company_config=_config(),
            check_docker=False,
            probe=_probe,
        )

        assert set(probed) == {
            "example-basic-001",
            "example-capable-001",
            "example-expert-001",
        }

    async def test_an_absent_grading_tool_is_refused_before_anything_is_spent(
        self,
    ) -> None:
        # A grading interpreter is a property of the machine. Discovered per
        # cell, it burns the whole matrix producing rows that say the loop was
        # unavailable when the loop ran fine and only the grader could not.
        probed: list[str] = []

        async def _probe(capability: CapabilityEntry) -> float:
            probed.append(capability.model_id)
            return 1.0

        with pytest.raises(EvalToolMissingError) as excinfo:
            await run_preflight(
                manifest=_manifest(),
                company_config=_config(),
                briefs=(_brief_checked_with("no-such-interpreter-anywhere"),),
                check_docker=False,
                probe=_probe,
            )

        assert "no-such-interpreter-anywhere" in str(excinfo.value)
        assert probed == []

    async def test_a_present_grading_tool_passes(self) -> None:
        async def _probe(_tier: CapabilityEntry) -> float:
            return 1.0

        await run_preflight(
            manifest=_manifest(),
            company_config=_config(),
            briefs=(_brief_checked_with(sys.executable),),
            check_docker=False,
            probe=_probe,
        )

    async def test_the_provider_check_still_runs_first(self) -> None:
        # A capability naming an absent provider has nothing to probe, so the older
        # check has to be the one that speaks.
        probed: list[str] = []

        async def _probe(capability: CapabilityEntry) -> float:
            probed.append(capability.model_id)
            return 1.0

        with pytest.raises(LoopAbProviderMissingError):
            await run_preflight(
                manifest=_manifest(
                    CapabilityEntry(
                        capability=NotBlankStr("expert"),
                        provider=NotBlankStr("absent-provider"),
                        model_id=NotBlankStr("example-expert-001"),
                    )
                ),
                company_config=_config(),
                check_docker=False,
                probe=_probe,
            )

        assert probed == []
