# module-kind: tests
"""The checks that decide whether a matrix is worth starting.

The latency probe is the one with judgement in it. Latency is a scored
dimension and the matrix records cells one after another over roughly an hour,
so a provider whose service time swings by an order of magnitude scores each
cell against its own queue rather than against the other cells.
"""

import asyncio
from datetime import UTC, datetime
from typing import Final

import pytest

from evals.errors import LoopAbProviderDegradedError, LoopAbProviderMissingError
from evals.loop_ab.manifest import LoopAbManifest, TierEntry
from evals.loop_ab.preflight import DEFAULT_LATENCY_CEILING_SECONDS, run_preflight
from synthorg.config.schema import ProviderConfig, RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_selector import registered_loop_types
from synthorg.providers.enums import AuthType

pytestmark = pytest.mark.integration

_PROVIDER: Final = "example-provider"


def _tier(tier: str, model_id: str) -> TierEntry:
    """A tier bound to the test provider.

    Returns:
        The tier entry.
    """
    return TierEntry(
        tier=NotBlankStr(tier),
        provider=NotBlankStr(_PROVIDER),
        model_id=NotBlankStr(model_id),
    )


def _manifest(*tiers: TierEntry) -> LoopAbManifest:
    """A manifest over *tiers*, with everything else at its default.

    Returns:
        The manifest.
    """
    return LoopAbManifest(
        brief_suite=NotBlankStr("evals/loop_ab/briefs"),
        repetitions=1,
        # The manifest refuses to omit a registered loop, so this is discovered
        # rather than listed: a third loop joins the comparison without an edit.
        loops=tuple(NotBlankStr(name) for name in registered_loop_types()),
        tiers=tiers or (_tier("large", "example-large-001"),),
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
        seen: list[TierEntry] = []

        async def _probe(tier: TierEntry) -> float:
            seen.append(tier)
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
        measured = iter([90.0, 2.0])

        async def _probe(_tier: TierEntry) -> float:
            return next(measured)

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

        async def _probe(_tier: TierEntry) -> float:
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
        async def _probe(_tier: TierEntry) -> float:
            return DEFAULT_LATENCY_CEILING_SECONDS + 1.0

        with pytest.raises(LoopAbProviderDegradedError) as excinfo:
            await run_preflight(
                manifest=_manifest(),
                company_config=_config(),
                check_docker=False,
                probe=_probe,
            )

        message = str(excinfo.value)
        assert "example-large-001" in message
        assert str(DEFAULT_LATENCY_CEILING_SECONDS) in message

    async def test_every_tier_is_probed_so_one_slow_model_cannot_hide(
        self,
    ) -> None:
        # The tiers are separate model pools; the small one being fast says
        # nothing about the large one, and the matrix scores all three.
        probed: list[str] = []

        async def _probe(tier: TierEntry) -> float:
            probed.append(tier.model_id)
            return 1.0

        await run_preflight(
            manifest=_manifest(
                _tier("small", "example-small-001"),
                _tier("medium", "example-medium-001"),
                _tier("large", "example-large-001"),
            ),
            company_config=_config(),
            check_docker=False,
            probe=_probe,
        )

        assert set(probed) == {
            "example-small-001",
            "example-medium-001",
            "example-large-001",
        }

    async def test_the_provider_check_still_runs_first(self) -> None:
        # A tier naming an absent provider has nothing to probe, so the older
        # check has to be the one that speaks.
        probed: list[str] = []

        async def _probe(tier: TierEntry) -> float:
            probed.append(tier.model_id)
            return 1.0

        with pytest.raises(LoopAbProviderMissingError):
            await run_preflight(
                manifest=_manifest(
                    TierEntry(
                        tier=NotBlankStr("large"),
                        provider=NotBlankStr("absent-provider"),
                        model_id=NotBlankStr("example-large-001"),
                    )
                ),
                company_config=_config(),
                check_docker=False,
                probe=_probe,
            )

        assert probed == []
