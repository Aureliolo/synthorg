"""End-to-end self-extension loop (acceptance).

Drives the full deterministic loop the acceptance criterion describes:

1. The org repeatedly cannot perform a capability (recurring gap).
2. The toolsmith proposes a tool, guarded + enqueued for approval.
3. A human approves; the applier benchmark-validates the candidate (the
   authored script actually runs in a sandbox) and live-registers it.
4. A LATER task invokes the new tool through the real ``MCPToolInvoker``
   over the layered registry and succeeds.

The sandbox here runs the authored Python synchronously via
``subprocess.run`` so the script genuinely executes cross-platform
without an event-loop-policy dance; the Docker/subprocess backends are
unit-tested separately.

Deviation from the literal "validated under the simulation harness"
wording: the toolsmith operates on MCP capability gaps, not client
briefs, so driving it through ``SimulationRunner`` (clients -> intake ->
review) would be artificial. This test instead exercises the full loop
deterministically through the production ``MCPToolInvoker`` and the real
service/factory wiring, which is the meaningful end-to-end check.
"""

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast, override

import pytest

from synthorg.api.state import AppState
from synthorg.core.approval import ApprovalItem
from synthorg.core.types import NotBlankStr
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.mcp.invoker import MCPToolInvoker
from synthorg.meta.mcp.registry import DomainToolRegistry
from synthorg.meta.toolsmith.config import ToolsmithConfig, ToolValidationConfig
from synthorg.meta.toolsmith.dynamic_registry import (
    LayeredHandlerMap,
    LayeredToolRegistry,
)
from synthorg.meta.toolsmith.factory import build_toolsmith
from synthorg.meta.toolsmith.models import ToolBlueprint, ToolSandboxBackend
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared import FakeClock

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
_CAPABILITY = "textkit:slugify"

_SLUGIFY_SCRIPT = (
    "import os, json, re\n"
    'args = json.loads(os.environ["SYNTHORG_TOOL_ARGS"])\n'
    'text = str(args["text"]).lower()\n'
    'slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")\n'
    'print(json.dumps({"slug": slug}))\n'
)

_AUTHORING_RESPONSE = json.dumps(
    {
        "description": "Slugify text deterministically.",
        "action_type": "code:read",
        "parameters_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "script_body": _SLUGIFY_SCRIPT,
    }
)


class _LocalPythonSandbox:
    """SandboxBackend that runs ``python -c`` synchronously via subprocess."""

    async def execute(  # noqa: PLR0913
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Any = None,
        timeout: float | None = None,  # noqa: ASYNC109
        owner_id: Any = None,
        project_id: Any = None,
    ) -> SandboxResult:
        del cwd, owner_id, project_id
        overrides = env_overrides or {}
        # Preserve the base environment (PATH etc.) and only inject the
        # tool-args var; replacing it wholesale breaks the subprocess on
        # platforms that need inherited vars (notably Windows).
        env = dict(os.environ)
        env["SYNTHORG_TOOL_ARGS"] = overrides.get("SYNTHORG_TOOL_ARGS", "{}")
        completed = subprocess.run(  # noqa: S603, ASYNC221
            [sys.executable, *args] if command == "python" else [command, *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout or 30.0,
        )
        return SandboxResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

    async def cleanup(self) -> None:
        return None


class _FakeProvider(BaseCompletionProvider):
    """Authoring provider stub: returns the canned tool-authoring response.

    Subclasses the concrete base so the ``build_toolsmith`` boundary's
    runtime type check (``provider`` must be a ``BaseCompletionProvider``)
    is satisfied; the inherited ``complete`` wrapper drives
    ``_do_complete``.
    """

    @override
    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        del messages, tools, config
        return CompletionResponse(
            content=_AUTHORING_RESPONSE,
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=8, output_tokens=4, cost=0.0),
            model=model,
        )

    @override
    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        del messages, model, tools, config
        raise NotImplementedError

    @override
    async def _do_get_model_capabilities(self, model: str) -> ModelCapabilities:
        del model
        raise NotImplementedError


class _FakeScorecard:
    """Golden scorecard: candidate slightly improves the org (no regression)."""

    async def score(self, blueprint: ToolBlueprint) -> tuple[int, int]:
        del blueprint
        return 100, 101


class _InMemoryRepo:
    def __init__(self) -> None:
        self.rows: dict[str, ToolBlueprint] = {}

    async def save(self, entity: ToolBlueprint) -> None:
        self.rows[entity.id] = entity

    async def get(self, entity_id: str) -> ToolBlueprint | None:
        return self.rows.get(entity_id)

    async def transition_if(
        self, entity_id: str, from_state: Any, to_state: Any, **updates: Any
    ) -> bool:
        row = self.rows.get(entity_id)
        if row is None or row.state is not from_state:
            return False
        self.rows[entity_id] = row.model_copy(update={"state": to_state, **updates})
        return True


class _InMemoryApprovalStore:
    """Minimal ApprovalStoreProtocol: records enqueued approval items."""

    def __init__(self) -> None:
        self.items: dict[str, ApprovalItem] = {}

    async def add(self, item: ApprovalItem) -> None:
        self.items[str(item.id)] = item


def _config() -> SelfImprovementConfig:
    return SelfImprovementConfig(
        enabled=True,
        tool_creation_enabled=True,
        toolsmith=ToolsmithConfig(
            enabled=True,
            gap_recurrence_threshold=3,
            allowed_capabilities=(NotBlankStr(_CAPABILITY),),
            sandbox_backend=ToolSandboxBackend.SUBPROCESS,
            validation=ToolValidationConfig(
                require_golden_delta=True, min_score_margin=0
            ),
        ),
    )


class TestSelfExtensionE2E:
    async def test_full_loop_gap_to_reuse(self) -> None:
        clock = FakeClock(start=_NOW)
        repo = _InMemoryRepo()
        approvals = _InMemoryApprovalStore()
        runtime = build_toolsmith(
            si_config=_config(),
            provider=_FakeProvider(),
            repo=repo,  # type: ignore[arg-type]
            sandbox_resolver=lambda _bp: _LocalPythonSandbox(),  # type: ignore[arg-type,return-value]
            scorecard_provider=_FakeScorecard(),
            approval_store=approvals,  # type: ignore[arg-type]
            clock=clock,
        )
        service = runtime.service

        # 1. Recurring capability gap: the org cannot slugify, three times.
        for i in range(3):
            await service.record_gap(
                NotBlankStr(_CAPABILITY),
                occurred_at=_NOW + timedelta(minutes=i),
            )

        # 2. The cycle proposes + guards + enqueues exactly one proposal.
        proposals = await service.run_cycle(now=_NOW + timedelta(minutes=3))
        assert len(proposals) == 1
        proposal = proposals[0]
        assert proposal.tool_changes[0].capability == _CAPABILITY
        # The approval gate durably enqueued it for mandatory human review.
        assert len(approvals.items) == 1

        # 3. Human approves -> applier validates against the benchmark
        #    (the authored script really runs) and live-registers the tool.
        apply_result = await service.apply(proposal)
        assert apply_result.success is True
        definition = runtime.dynamic_registry.get_def("synthorg_textkit_slugify")
        assert definition is not None
        assert definition.name == "synthorg_textkit_slugify"
        assert definition.capability == _CAPABILITY

        # 4. A LATER task invokes the brand-new tool through the real
        #    MCPToolInvoker over the layered registry, and it succeeds.
        static = DomainToolRegistry()
        static.freeze()
        invoker = MCPToolInvoker(
            LayeredToolRegistry(static, runtime.dynamic_registry),
            LayeredHandlerMap({}, runtime.dynamic_registry),
        )
        result = await invoker.invoke(
            "synthorg_textkit_slugify",
            {"text": "Hello Brave World"},
            app_state=cast("AppState", None),
        )
        assert result.is_error is False
        envelope = json.loads(result.content)
        assert envelope["status"] == "ok"
        assert envelope["data"] == {"slug": "hello-brave-world"}

    async def test_no_proposal_below_recurrence_threshold(self) -> None:
        clock = FakeClock(start=_NOW)
        runtime = build_toolsmith(
            si_config=_config(),
            provider=_FakeProvider(),
            repo=_InMemoryRepo(),  # type: ignore[arg-type]
            sandbox_resolver=lambda _bp: _LocalPythonSandbox(),  # type: ignore[arg-type,return-value]
            scorecard_provider=_FakeScorecard(),
            approval_store=_InMemoryApprovalStore(),  # type: ignore[arg-type]
            clock=clock,
        )
        # Only two observations -> below the threshold of 3.
        for i in range(2):
            await runtime.service.record_gap(
                NotBlankStr(_CAPABILITY), occurred_at=_NOW + timedelta(minutes=i)
            )
        proposals = await runtime.service.run_cycle(now=_NOW + timedelta(minutes=2))
        assert proposals == ()

    async def test_regressing_tool_is_not_registered(self) -> None:
        clock = FakeClock(start=_NOW)
        repo = _InMemoryRepo()

        class _RegressingScorecard:
            async def score(self, blueprint: ToolBlueprint) -> tuple[int, int]:
                del blueprint
                return 100, 90  # candidate regresses the org

        runtime = build_toolsmith(
            si_config=_config(),
            provider=_FakeProvider(),
            repo=repo,  # type: ignore[arg-type]
            sandbox_resolver=lambda _bp: _LocalPythonSandbox(),  # type: ignore[arg-type,return-value]
            scorecard_provider=_RegressingScorecard(),
            approval_store=_InMemoryApprovalStore(),  # type: ignore[arg-type]
            clock=clock,
        )
        for i in range(3):
            await runtime.service.record_gap(
                NotBlankStr(_CAPABILITY), occurred_at=_NOW + timedelta(minutes=i)
            )
        proposals = await runtime.service.run_cycle(now=_NOW + timedelta(minutes=3))
        assert len(proposals) == 1

        apply_result = await runtime.service.apply(proposals[0])
        assert apply_result.success is False
        assert runtime.dynamic_registry.get_def("synthorg_textkit_slugify") is None
