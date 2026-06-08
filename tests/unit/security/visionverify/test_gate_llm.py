# mypy: disable-error-code="explicit-any"
"""Unit tests for the vision gate and the llm_vision verifier."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.visionverify.gate import VisionVerifierGateService
from synthorg.security.visionverify.models import (
    VisionReviewInput,
    VisionScreenshotRef,
    VisionVerdict,
)
from synthorg.security.visionverify.verifiers.llm_vision import LLMVisionVerifier
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_SHA = "c" * 64
_SCREENSHOT_REL = ".synthorg/desktop/screenshots/shot.png"


def _write_png(workspace: Path) -> VisionScreenshotRef:
    path = workspace / _SCREENSHOT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (200, 30, 30)).save(path)
    return VisionScreenshotRef(workspace_path=_SCREENSHOT_REL, sha256=_SHA)


def _input(ref: VisionScreenshotRef) -> VisionReviewInput:
    return VisionReviewInput(
        task_id="t1",
        execution_id="e1",
        brief="a blue submit button labelled Submit",
        acceptance_criteria=("button is blue", "label reads Submit"),
        screenshots=(ref,),
        generator_agent_id="gen",
        evaluator_agent_id="vision",
        autonomy=AutonomyLevel.FULL,
    )


def _capabilities(*, supports_vision: bool) -> ModelCapabilities:
    return ModelCapabilities(
        model_id="example-medium-001",
        provider="example-provider",
        max_context_tokens=128_000,
        max_output_tokens=4096,
        supports_vision=supports_vision,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
    )


def _response(arguments: dict[str, Any]) -> CompletionResponse:
    return CompletionResponse(
        tool_calls=(
            ToolCall(id="c1", name="record_vision_verdict", arguments=arguments),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=10, cost=0.0),
        model="example-medium-001",
    )


def _provider(response: CompletionResponse, *, supports_vision: bool = True) -> Any:
    return mock_of[CompletionProvider](
        complete=AsyncMock(spec=CompletionProvider.complete, return_value=response),
        get_model_capabilities=AsyncMock(
            spec=CompletionProvider.get_model_capabilities,
            return_value=_capabilities(supports_vision=supports_vision),
        ),
    )


class TestLLMVisionVerifier:
    async def test_parses_block_finding(self, tmp_path: Path) -> None:
        ref = _write_png(tmp_path)
        response = _response(
            {
                "confidence": 0.9,
                "summary": "Button is red, not blue.",
                "findings": [
                    {
                        "category": "requirements_mismatch",
                        "severity": "high",
                        "description": "Submit button is red, brief requires blue",
                        "evidence": ["button fill is red"],
                    },
                ],
            },
        )
        verifier = LLMVisionVerifier(
            provider=_provider(response),
            model_id="example-medium-001",
            workspace=tmp_path,
        )
        report = await verifier.verify(_input(ref))
        assert len(report.findings) == 1
        assert report.model_id == "example-medium-001"

    async def test_sends_images_and_wraps_brief(self, tmp_path: Path) -> None:
        ref = _write_png(tmp_path)
        response = _response(
            {"confidence": 1.0, "summary": "matches", "findings": []},
        )
        provider = _provider(response)
        verifier = LLMVisionVerifier(
            provider=provider,
            model_id="example-medium-001",
            workspace=tmp_path,
        )
        await verifier.verify(_input(ref))
        messages: list[ChatMessage] = provider.complete.await_args.kwargs["messages"]
        user = messages[-1]
        assert len(user.image_parts) == 1
        # The untrusted brief is fenced as data, not interpolated raw.
        assert "<task-data>" in (user.content or "")

    async def test_unsupported_model_raises(self, tmp_path: Path) -> None:
        from synthorg.security.visionverify.errors import VisionModelUnsupportedError

        ref = _write_png(tmp_path)
        response = _response({"confidence": 1.0, "summary": "x", "findings": []})
        verifier = LLMVisionVerifier(
            provider=_provider(response, supports_vision=False),
            model_id="example-medium-001",
            workspace=tmp_path,
        )
        with pytest.raises(VisionModelUnsupportedError):
            await verifier.verify(_input(ref))

    async def test_malformed_response_degrades(self, tmp_path: Path) -> None:
        ref = _write_png(tmp_path)
        response = _response({"unexpected": True})
        verifier = LLMVisionVerifier(
            provider=_provider(response),
            model_id="example-medium-001",
            workspace=tmp_path,
        )
        report = await verifier.verify(_input(ref))
        # Degrades to a non-blocking INFO finding rather than raising.
        assert report.confidence == 0.0
        assert all(f.severity.value == "info" for f in report.findings)


class _RaisingVerifier:
    @property
    def kind(self) -> str:
        return "heuristic"

    async def verify(self, review_input: VisionReviewInput) -> Any:
        msg = "boom"
        raise RuntimeError(msg)


class TestVisionGate:
    async def test_block_verdict_routes(self, tmp_path: Path) -> None:
        ref = _write_png(tmp_path)
        response = _response(
            {
                "confidence": 0.9,
                "summary": "mismatch",
                "findings": [
                    {
                        "category": "requirements_mismatch",
                        "severity": "high",
                        "description": "wrong colour",
                        "evidence": ["red not blue"],
                    },
                ],
            },
        )
        verifier = LLMVisionVerifier(
            provider=_provider(response),
            model_id="example-medium-001",
            workspace=tmp_path,
        )
        gate = VisionVerifierGateService(verifier=verifier)
        result = await gate.evaluate(_input(ref))
        assert result.verdict is VisionVerdict.BLOCK

    async def test_fail_open_on_verifier_exception(self, tmp_path: Path) -> None:
        ref = _write_png(tmp_path)
        gate = VisionVerifierGateService(verifier=_RaisingVerifier())
        result = await gate.evaluate(_input(ref))
        # Fail-OPEN: a verifier fault degrades to a non-blocking verdict.
        assert result.verdict is not VisionVerdict.BLOCK
        assert len(result.report.findings) == 1
