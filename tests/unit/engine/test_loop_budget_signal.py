"""The turn-boundary signal reporting live spend against a run's ceiling.

The system prompt declares the ceiling once, at zero spend, which is true for
the life of the session; only the turn boundary can report what has actually
been spent. This fires at declared steps rather than every turn, because an
injected line on all 130 turns of a real merge is real, avoidable spend on a
connection with no prompt caching.
"""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_budget_signal import (
    BudgetSignalConfig,
    check_budget_signal,
    resolve_budget_signal_config,
)
from synthorg.providers.enums import MessageRole
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid("signal-agent"),
        name="Signal Agent",
        role="engineer",
        department="engineering",
        model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
        hiring_date=date(2026, 1, 1),
    )


def _ctx(*, token_ceiling: int | None, spent: int) -> AgentContext:
    ctx = AgentContext.from_identity(_identity(), token_ceiling=token_ceiling)
    return ctx.model_copy(
        update={
            "accumulated_cost": ctx.accumulated_cost.model_copy(
                update={"input_tokens": spent}
            ),
        }
    )


_CONFIG = BudgetSignalConfig(step_percent=25, terminal_percent=90)


class TestNoCeilingNoSignal:
    def test_a_run_with_no_ceiling_gets_no_signal(self) -> None:
        ctx = _ctx(token_ceiling=None, spent=0)
        assert check_budget_signal(ctx, _CONFIG) is None

    def test_step_percent_zero_disables_the_signal_below_terminal(self) -> None:
        ctx = _ctx(token_ceiling=1_000, spent=500)
        config = BudgetSignalConfig(step_percent=0, terminal_percent=90)
        assert check_budget_signal(ctx, config) is None

    def test_step_percent_zero_still_fires_the_terminal_warning(self) -> None:
        # Disabling the periodic signal is not disabling the terminal one:
        # the two are separate knobs, and silence at the ceiling is the one
        # failure mode this issue exists to end.
        ctx = _ctx(token_ceiling=1_000, spent=950)
        config = BudgetSignalConfig(step_percent=0, terminal_percent=90)
        result = check_budget_signal(ctx, config)
        assert result is not None


class TestStepCrossing:
    def test_below_the_first_step_is_silent(self) -> None:
        ctx = _ctx(token_ceiling=1_000, spent=100)
        assert check_budget_signal(ctx, _CONFIG) is None

    def test_crossing_a_step_fires_once(self) -> None:
        ctx = _ctx(token_ceiling=1_000, spent=300)
        result = check_budget_signal(ctx, _CONFIG)
        assert result is not None
        user_messages = [
            m.content for m in result.conversation if m.role is MessageRole.USER
        ]
        assert user_messages
        assert "25%" in (user_messages[-1] or "") or "300" in (user_messages[-1] or "")
        assert result.budget_signal_last_step_percent == 25

    def test_the_same_step_does_not_fire_twice(self) -> None:
        ctx = _ctx(token_ceiling=1_000, spent=300)
        first = check_budget_signal(ctx, _CONFIG)
        assert first is not None
        # Still within the same 25-49% band: already announced.
        second_ctx = first.model_copy(
            update={
                "accumulated_cost": first.accumulated_cost.model_copy(
                    update={"input_tokens": 400}
                ),
            }
        )
        assert check_budget_signal(second_ctx, _CONFIG) is None

    def test_crossing_the_next_step_fires_again(self) -> None:
        ctx = _ctx(token_ceiling=1_000, spent=300)
        first = check_budget_signal(ctx, _CONFIG)
        assert first is not None
        assert first.budget_signal_last_step_percent == 25
        next_ctx = first.model_copy(
            update={
                "accumulated_cost": first.accumulated_cost.model_copy(
                    update={"input_tokens": 550}
                ),
            }
        )
        second = check_budget_signal(next_ctx, _CONFIG)
        assert second is not None
        assert second.budget_signal_last_step_percent == 50

    def test_the_remainder_strictly_decreases_step_to_step(self) -> None:
        remainders: list[int] = []
        ctx = _ctx(token_ceiling=1_000, spent=300)
        for spend in (300, 550, 800):
            ctx = ctx.model_copy(
                update={
                    "accumulated_cost": ctx.accumulated_cost.model_copy(
                        update={"input_tokens": spend}
                    ),
                }
            )
            result = check_budget_signal(ctx, _CONFIG)
            if result is not None:
                ctx = result
                remainders.append(ctx.token_ceiling - ctx.accumulated_cost.total_tokens)  # type: ignore[operator]
        assert remainders == sorted(remainders, reverse=True)
        assert len(set(remainders)) == len(remainders)


class TestTerminalWarning:
    def test_at_or_past_terminal_fires_every_turn(self) -> None:
        ctx = _ctx(token_ceiling=1_000, spent=900)
        first = check_budget_signal(ctx, _CONFIG)
        assert first is not None
        # A second call at the same (unchanged) spend still fires: unlike a
        # step, the terminal warning is not a once-per-run event.
        second = check_budget_signal(first, _CONFIG)
        assert second is not None

    def test_terminal_wording_names_the_ceiling(self) -> None:
        ctx = _ctx(token_ceiling=1_000, spent=950)
        result = check_budget_signal(ctx, _CONFIG)
        assert result is not None
        user_messages = [
            m.content for m in result.conversation if m.role is MessageRole.USER
        ]
        assert any("ceiling" in (m or "").lower() for m in user_messages)


class TestResolveBudgetSignalConfig:
    async def test_no_resolver_falls_back_to_defaults(self) -> None:
        config = await resolve_budget_signal_config(None)
        assert config.step_percent == 25
        assert config.terminal_percent == 90

    async def test_reads_the_live_settings(self) -> None:
        values = {
            "budget_signal_step_percent": 10,
            "budget_signal_terminal_percent": 80,
        }
        resolver = mock_of[ConfigResolverProtocol]()
        resolver.get_int.side_effect = lambda namespace, key: values[key]

        config = await resolve_budget_signal_config(resolver)

        assert config.step_percent == 10
        assert config.terminal_percent == 80
