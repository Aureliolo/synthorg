"""Unit tests for the output-style service and boundary interceptor."""

from collections.abc import Iterator

import pytest

from synthorg.engine.output_style.errors import OutputPolicyViolationError
from synthorg.engine.output_style.exemptions import OutputContext
from synthorg.engine.output_style.interceptor import (
    enforce_output_policy,
    evaluate_output_policy,
)
from synthorg.engine.output_style.models import OutputChannel, OutputStyleConfig
from synthorg.engine.output_style.service import (
    OutputStylePolicyService,
    current_output_policy_service,
    set_output_policy_service,
)

_EM_DASH = chr(0x2014)


@pytest.fixture
def _reset_service() -> Iterator[None]:
    previous = current_output_policy_service()
    try:
        yield
    finally:
        set_output_policy_service(previous)


class TestService:
    @pytest.mark.unit
    def test_from_default_config_blocks_emdash(self) -> None:
        service = OutputStylePolicyService.from_config(OutputStyleConfig())
        verdict = service.evaluate(
            f"ship it {_EM_DASH} now", OutputContext(channel=OutputChannel.DELIVERABLE)
        )
        assert verdict.blocked is True

    @pytest.mark.unit
    def test_disabled_service_passes_through(self) -> None:
        service = OutputStylePolicyService.from_config(OutputStyleConfig(enabled=False))
        verdict = service.evaluate(
            f"ship it {_EM_DASH} now", OutputContext(channel=OutputChannel.DELIVERABLE)
        )
        assert verdict.clean is True

    @pytest.mark.unit
    def test_shadow_config_never_blocks(self) -> None:
        service = OutputStylePolicyService.from_config(
            OutputStyleConfig(shadow_mode=True)
        )
        verdict = service.evaluate(
            f"ship it {_EM_DASH} now", OutputContext(channel=OutputChannel.DELIVERABLE)
        )
        assert verdict.blocked is False
        assert verdict.findings

    @pytest.mark.unit
    def test_house_style_directives_gated(self) -> None:
        on = OutputStylePolicyService.from_config(OutputStyleConfig())
        assert on.house_style_directives()
        off = OutputStylePolicyService.from_config(
            OutputStyleConfig(house_style_enabled=False)
        )
        assert off.house_style_directives() == ()


@pytest.mark.usefixtures("_reset_service")
class TestInterceptor:
    @pytest.mark.unit
    def test_enforce_raises_on_block(self) -> None:
        set_output_policy_service(
            OutputStylePolicyService.from_config(OutputStyleConfig())
        )
        with pytest.raises(OutputPolicyViolationError):
            enforce_output_policy(
                f"ship it {_EM_DASH} now",
                OutputContext(channel=OutputChannel.MESSAGE),
            )

    @pytest.mark.unit
    def test_enforce_passes_clean(self) -> None:
        set_output_policy_service(
            OutputStylePolicyService.from_config(OutputStyleConfig())
        )
        out = enforce_output_policy(
            "ship it now", OutputContext(channel=OutputChannel.MESSAGE)
        )
        assert out == "ship it now"

    @pytest.mark.unit
    def test_enforce_passes_through_when_unwired(self) -> None:
        set_output_policy_service(None)
        text = f"unguarded {_EM_DASH} here"
        assert (
            enforce_output_policy(text, OutputContext(channel=OutputChannel.MESSAGE))
            == text
        )

    @pytest.mark.unit
    def test_evaluate_returns_none_when_unwired(self) -> None:
        set_output_policy_service(None)
        assert (
            evaluate_output_policy(
                "x", OutputContext(channel=OutputChannel.DELIVERABLE)
            )
            is None
        )

    @pytest.mark.unit
    def test_evaluate_returns_verdict_when_wired(self) -> None:
        set_output_policy_service(
            OutputStylePolicyService.from_config(OutputStyleConfig())
        )
        verdict = evaluate_output_policy(
            f"x {_EM_DASH} y", OutputContext(channel=OutputChannel.DELIVERABLE)
        )
        assert verdict is not None
        assert verdict.blocked is True
