"""Tests for LLM decomposition strategy."""

import json
import re
from typing import cast, override

import pytest
from pydantic import JsonValue

from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import (
    CoordinationTopology,
    Priority,
    TaskStructure,
    TaskType,
)
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.llm import (
    LlmDecompositionConfig,
    LlmDecompositionStrategy,
)
from synthorg.engine.decomposition.llm_parse import args_to_decomposition_plan
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.errors import (
    DecompositionBudgetExhaustedError,
    DecompositionDepthError,
    DecompositionError,
    DecompositionSubtaskLimitError,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from tests._shared import as_uuid

from .conftest import MockCompletionProvider


def _make_task(
    task_id: str = "task-llm-1",
    *,
    title: str = "Build authentication",
    description: str = "Implement JWT auth for the REST API.",
) -> Task:
    """Create a minimal task for LLM decomposition tests."""
    return Task(
        id=as_uuid(task_id),
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="proj-1",
        created_by="creator",
        acceptance_criteria=(AcceptanceCriterion(description="Login returns token"),),
    )


def _make_context(
    max_subtasks: int = 10,
    max_depth: int = 3,
    current_depth: int = 0,
) -> DecompositionContext:
    """Create a decomposition context."""
    return DecompositionContext(
        max_subtasks=max_subtasks,
        max_depth=max_depth,
        current_depth=current_depth,
    )


def _valid_plan_args(
    *,
    subtask_count: int = 2,
    task_structure: str | None = "sequential",
    coordination_topology: str = "auto",
) -> dict[str, object]:
    """Build valid tool call arguments for a decomposition plan.

    ``task_structure=None`` omits the key entirely, which is what a planner
    that declared no structure actually sends.
    """
    subtasks = [
        {
            "id": f"sub-{i}",
            "title": f"Subtask {i}",
            "description": f"Do step {i}",
            "dependencies": [] if i == 0 else [f"sub-{i - 1}"],
            "estimated_complexity": "medium",
            "required_skills": ["python"],
            "acceptance_criteria": [f"step {i} verified"],
            "expected_artifacts": [f"src/step_{i}.py"],
            # The objective's own criterion, copied verbatim, on the first item
            # alone: a plan advancing none of what it decomposes is refused, and
            # a plan where every item is pure support is that plan. One claim is
            # what the rule asks for, so the rest stay untagged and the fixture
            # keeps exercising the mixed case a real plan has.
            **({"satisfies": ["Login returns token"]} if i == 0 else {}),
        }
        for i in range(subtask_count)
    ]
    args: dict[str, object] = {
        "subtasks": subtasks,
        "coordination_topology": coordination_topology,
    }
    if task_structure is not None:
        args["task_structure"] = task_structure
    return args


def _subtask_args(
    subtask_id: str,
    title: str,
    description: str,
    *,
    dependencies: list[str] | None = None,
) -> dict[str, object]:
    """One subtask with a title of its own, for the graph-reference checks.

    The shared helper names every item "Subtask N", which is the plan's house
    vocabulary and deliberately does not read as a reference; a check about
    one item naming another needs items with distinguishable subjects.
    """
    return {
        "id": subtask_id,
        "title": title,
        "description": description,
        "dependencies": dependencies or [],
        "estimated_complexity": "medium",
        "required_skills": ["python"],
        "acceptance_criteria": [f"{subtask_id} verified"],
        "expected_artifacts": [f"src/{subtask_id}.py"],
    }


def _make_tool_call_response(
    arguments: dict[str, object],
    *,
    tool_name: str = "submit_decomposition_plan",
) -> CompletionResponse:
    """Create a CompletionResponse with a tool call."""
    return CompletionResponse(
        tool_calls=(
            ToolCall(
                id="tc-1",
                name=tool_name,
                arguments=cast("dict[str, JsonValue]", arguments),
            ),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(
            input_tokens=200,
            output_tokens=100,
            cost=0.02,
        ),
        model="test-model-001",
    )


def _make_content_response(content: str) -> CompletionResponse:
    """Create a CompletionResponse with text content."""
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(
            input_tokens=200,
            output_tokens=100,
            cost=0.02,
        ),
        model="test-model-001",
    )


class TestLlmDecompositionStrategy:
    """Tests for LlmDecompositionStrategy."""

    @pytest.mark.unit
    async def test_happy_path_tool_call(self) -> None:
        """Tool call response produces a valid plan."""
        args = _valid_plan_args()
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        plan = await strategy.decompose(task, ctx)

        assert isinstance(plan, DecompositionPlan)
        assert plan.parent_task_id == str(task.id)
        assert len(plan.subtasks) == 2
        assert plan.task_structure is TaskStructure.SEQUENTIAL
        assert plan.coordination_topology is CoordinationTopology.AUTO
        assert provider.call_count == 1

    @pytest.mark.unit
    async def test_an_omitted_task_structure_stays_undeclared(self) -> None:
        """The schema does not require the field, and absence is not a choice.

        Defaulting it to sequential here would make a planner that said
        nothing indistinguishable from one that chose sequential, which is
        the distinction the classifier fallback keys off.
        """
        args = _valid_plan_args(task_structure=None)
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")

        plan = await strategy.decompose(_make_task(), _make_context())

        assert plan.task_structure is TaskStructure.AUTO

    @pytest.mark.unit
    def test_an_unmappable_task_structure_is_refused(self) -> None:
        """A value outside the schema's enum is a failed declaration.

        Degrading it to a default would pick a coordination shape nobody
        chose, and treating it as silence would hide that the planner
        answered a closed question with something that is not an answer.
        Raising makes it correctable: the strategy re-prompts.
        """
        args = _valid_plan_args(task_structure="mostly-parallel-ish")

        with pytest.raises(DecompositionError, match="Unknown task_structure"):
            args_to_decomposition_plan(
                cast("dict[str, JsonValue]", args), "task-parent-1"
            )

    @pytest.mark.unit
    def test_an_explicit_null_task_structure_is_refused(self) -> None:
        """Sending the key as ``null`` is a declaration, not an omission.

        The schema constrains the field to the enum, so ``null`` is a value
        outside it exactly like any other unmappable one. Reading it as
        absence would let a planner reach the classifier fallback by naming
        a value the schema rejects.
        """
        args = _valid_plan_args(task_structure=None)
        args["task_structure"] = None

        with pytest.raises(DecompositionError, match="Unknown task_structure"):
            args_to_decomposition_plan(
                cast("dict[str, JsonValue]", args), "task-parent-1"
            )

    @pytest.mark.unit
    def test_an_ordered_plan_with_no_edges_is_refused(self) -> None:
        """C10: the plan the run produced declared an order it never expressed.

        Six items, ``structure=mixed``, ``dependencies: []`` on every one. The
        graph and the declaration contradict each other, and discovering that
        at dispatch means an operator already approved an ordering that does
        not exist. Correctable in-session, like the roster check.
        """
        args = _valid_plan_args()
        for subtask in cast("list[dict[str, object]]", args["subtasks"]):
            subtask["dependencies"] = []

        with pytest.raises(DecompositionError, match="no dependencies"):
            args_to_decomposition_plan(
                cast("dict[str, JsonValue]", args), "task-parent-1"
            )

    @pytest.mark.unit
    def test_an_item_naming_another_it_does_not_depend_on_is_refused(self) -> None:
        """ "Integrate the renderer" cannot precede the renderer it integrates.

        The shape to refuse is an integration item that names the items it
        ties together, declares no dependency on any of them, and is therefore
        free to be dispatched first.
        """
        args: dict[str, object] = {
            "task_structure": "mixed",
            "coordination_topology": "auto",
            "subtasks": [
                _subtask_args("sub-renderer", "Renderer pipeline", "Draw the frames"),
                # Carries the plan's other edge, so stripping the integration
                # item's dependency below leaves the graph ordered and the
                # unstated reference is the only thing left to fail on.
                _subtask_args(
                    "sub-input",
                    "Input handling",
                    "Read the controls",
                    dependencies=["sub-renderer"],
                ),
                _subtask_args(
                    "sub-integrate",
                    "Integrate game loop",
                    "Tie the renderer pipeline together",
                    dependencies=["sub-renderer"],
                ),
            ],
        }
        # Sanity: with the dependency declared the plan is accepted, so the
        # failure below is the missing edge and not the titles themselves.
        args_to_decomposition_plan(cast("dict[str, JsonValue]", args), "task-parent-1")

        cast("list[dict[str, object]]", args["subtasks"])[2]["dependencies"] = []

        with pytest.raises(DecompositionError, match="Renderer pipeline"):
            args_to_decomposition_plan(
                cast("dict[str, JsonValue]", args), "task-parent-1"
            )

    @pytest.mark.unit
    def test_an_item_judged_on_a_later_item_artefact_is_refused(self) -> None:
        """A gate that cannot pass when it runs cannot pass on rework either.

        The server item's own criterion named the page item's ``index.html``,
        two waves downstream and depending on the server on nothing. The
        reviewer refused, the rework reran, the file still did not exist, and
        the second refusal cost as much as the first.
        """
        server = _subtask_args("sub-server", "HTTP server", "Serve the app")
        server["expected_artifacts"] = ["server.js"]
        page = _subtask_args(
            "sub-page", "Game page", "Lay the board out", dependencies=["sub-server"]
        )
        page["expected_artifacts"] = ["public/index.html"]
        args: dict[str, object] = {
            "task_structure": "mixed",
            "coordination_topology": "auto",
            "subtasks": [server, page],
        }
        # Sanity: the plan stands until the criterion reaches downstream, so
        # the failure below is the criterion and not the pair of items.
        args_to_decomposition_plan(cast("dict[str, JsonValue]", args), "task-parent-1")

        server["acceptance_criteria"] = ["node server.js serves index.html with a 200"]

        with pytest.raises(DecompositionError, match=re.escape("index.html")):
            args_to_decomposition_plan(
                cast("dict[str, JsonValue]", args), "task-parent-1"
            )

    @pytest.mark.unit
    def test_a_parallel_plan_with_no_edges_is_accepted(self) -> None:
        """Declaring PARALLEL and shipping no edges is coherent, not a defect."""
        args = _valid_plan_args(task_structure="parallel")
        for subtask in cast("list[dict[str, object]]", args["subtasks"]):
            subtask["dependencies"] = []

        plan = args_to_decomposition_plan(
            cast("dict[str, JsonValue]", args), "task-parent-1"
        )

        assert plan.task_structure is TaskStructure.PARALLEL
        assert all(not s.dependencies for s in plan.subtasks)

    @pytest.mark.unit
    async def test_happy_path_content_fallback(self) -> None:
        """Content-only response is parsed as JSON fallback."""
        args = _valid_plan_args(subtask_count=1)
        content = json.dumps(args)
        response = _make_content_response(content)
        provider = MockCompletionProvider([response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        plan = await strategy.decompose(task, ctx)

        assert isinstance(plan, DecompositionPlan)
        assert len(plan.subtasks) == 1

    @pytest.mark.unit
    async def test_depth_exceeded_no_provider_call(self) -> None:
        """Depth exceeded raises without calling the provider."""
        provider = MockCompletionProvider([])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context(current_depth=3, max_depth=3)

        with pytest.raises(
            DecompositionDepthError,
            match="meets or exceeds max depth",
        ):
            await strategy.decompose(task, ctx)

        assert provider.call_count == 0

    @pytest.mark.unit
    async def test_max_subtasks_exceeded_reaches_the_caller_with_both_numbers(
        self,
    ) -> None:
        """An over-limit plan is refused with the count and the ceiling intact.

        Retrying past it would replace the typed error with a bare
        retries-exhausted one, and the caller could no longer offer to raise
        the ceiling to the number the planner actually produced.
        """
        args = _valid_plan_args(subtask_count=5)
        responses = [_make_tool_call_response(args) for _ in range(3)]
        provider = MockCompletionProvider(responses)
        config = LlmDecompositionConfig(max_retries=2)
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context(max_subtasks=3)

        with pytest.raises(DecompositionSubtaskLimitError) as excinfo:
            await strategy.decompose(task, ctx)

        assert excinfo.value.produced == 5
        assert excinfo.value.limit == 3
        assert provider.call_count == 1

    @pytest.mark.unit
    async def test_malformed_json_retry_success(self) -> None:
        """Malformed response triggers retry; second attempt succeeds."""
        bad_response = _make_content_response("{invalid json")
        good_args = _valid_plan_args(subtask_count=1)
        good_response = _make_tool_call_response(good_args)
        provider = MockCompletionProvider([bad_response, good_response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        plan = await strategy.decompose(task, ctx)

        assert isinstance(plan, DecompositionPlan)
        assert provider.call_count == 2

    @pytest.mark.unit
    async def test_a_mangled_reply_does_not_spend_a_planning_attempt(self) -> None:
        """The transport flattened the list, so no plan was ever judged.

        Charging the operator's one retry for a serialisation fault upstream of
        the model is how a run that would have planned fails: with
        ``max_retries=1`` the mangled reply plus a real parse failure would
        exhaust the budget before the good plan arrives.
        """
        mangled = _make_tool_call_response(
            {"$text": "", "item": {"$text": "</item>", "item": {"$text": ""}}}
        )
        bad = _make_content_response("{invalid json")
        good = _make_tool_call_response(_valid_plan_args(subtask_count=1))
        provider = MockCompletionProvider([mangled, bad, good])
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=LlmDecompositionConfig(max_retries=1),
        )

        plan = await strategy.decompose(_make_task(), _make_context())

        assert isinstance(plan, DecompositionPlan)
        assert provider.call_count == 3

    @pytest.mark.unit
    async def test_a_mangled_reply_on_the_content_channel_is_seen_too(self) -> None:
        """This strategy reads both channels, so both can arrive mangled.

        A reply carrying no tool call is parsed from its content, and the same
        collapse there would fall through to the ordinary schema error and
        charge an attempt for a fault upstream of the model.
        """
        mangled = _make_content_response(
            '{"$text": "", "item": {"$text": "</item>", "item": {"$text": ""}}}'
        )
        good = _make_tool_call_response(_valid_plan_args(subtask_count=1))
        provider = MockCompletionProvider([mangled, good])
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=LlmDecompositionConfig(max_retries=0),
        )

        plan = await strategy.decompose(_make_task(), _make_context())

        # max_retries=0 is one attempt, so the good plan only arrives if the
        # mangled round spent none of it.
        assert isinstance(plan, DecompositionPlan)
        assert provider.call_count == 2

    @pytest.mark.unit
    async def test_prose_mentioning_the_key_is_not_a_mangled_reply(self) -> None:
        """The artefact is structural: a string is content, not a collapse."""
        provider = MockCompletionProvider(
            [_make_content_response('"a plan describing the $text field"')]
        )
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=LlmDecompositionConfig(max_retries=0),
        )

        with pytest.raises(DecompositionError, match="retries exhausted"):
            await strategy.decompose(_make_task(), _make_context())

        # One attempt, spent: nothing here bought a free round.
        assert provider.call_count == 1

    @pytest.mark.unit
    async def test_a_provider_mangling_every_reply_still_terminates(self) -> None:
        """The free rounds are bounded: a broken provider is not a loop."""
        mangled = [
            _make_tool_call_response({"$text": "", "item": {"$text": ""}})
            for _ in range(8)
        ]
        provider = MockCompletionProvider(mangled)
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=LlmDecompositionConfig(max_retries=1),
        )

        with pytest.raises(DecompositionError, match="retries exhausted"):
            await strategy.decompose(_make_task(), _make_context())

        # Two free rounds, then the two attempts the operator configured.
        assert provider.call_count == 4

    @pytest.mark.unit
    async def test_all_retries_exhausted(self) -> None:
        """All retries exhausted raises DecompositionError."""
        bad_responses = [_make_content_response("{bad}") for _ in range(3)]
        provider = MockCompletionProvider(bad_responses)
        config = LlmDecompositionConfig(max_retries=2)
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context()

        with pytest.raises(DecompositionError, match="retries exhausted"):
            await strategy.decompose(task, ctx)

        # 1 initial + 2 retries = 3 calls
        assert provider.call_count == 3

    @pytest.mark.unit
    async def test_empty_response_raises(self) -> None:
        """Response with no content and no tool calls raises."""
        # A content_filter response has no content or tool calls
        empty_response = CompletionResponse(
            finish_reason=FinishReason.CONTENT_FILTER,
            usage=TokenUsage(
                input_tokens=10,
                output_tokens=0,
                cost=0.0,
            ),
            model="test-model-001",
        )
        provider = MockCompletionProvider(
            [empty_response, empty_response, empty_response]
        )
        config = LlmDecompositionConfig(max_retries=2)
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context()

        with pytest.raises(DecompositionError):
            await strategy.decompose(task, ctx)

    @pytest.mark.unit
    async def test_truncation_is_named_and_not_retried(self) -> None:
        """A budget truncation is its own condition, and repeating it is futile.

        A reasoning model spends completion tokens on reasoning before any
        content, so a budget sized for the answer alone returns an empty string
        that reaches the JSON parser. Reported as malformed JSON it sends the
        reader to the prompt; the fix is a larger ceiling. Retrying truncates
        at the same place, so the ladder is paid to learn nothing.
        """
        truncated = CompletionResponse(
            content="",
            finish_reason=FinishReason.MAX_TOKENS,
            usage=TokenUsage(input_tokens=10, output_tokens=300, cost=0.0),
            model="test-model-001",
        )
        provider = MockCompletionProvider([truncated, truncated, truncated])
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=LlmDecompositionConfig(max_retries=2),
        )

        with pytest.raises(DecompositionBudgetExhaustedError, match="token ceiling"):
            await strategy.decompose(_make_task(), _make_context())

        assert provider.call_count == 1

    @pytest.mark.unit
    async def test_a_truncation_that_still_wrote_content_is_parsed(self) -> None:
        """Only an EMPTY truncation is the budget condition.

        A response cut off mid-JSON has content the parser can fail on for its
        own reasons, and calling that a budget error would hide a genuine
        malformed-output problem behind a ceiling nobody needs to raise.
        """
        provider = MockCompletionProvider(
            [
                CompletionResponse(
                    content='{"subtasks": [',
                    finish_reason=FinishReason.MAX_TOKENS,
                    usage=TokenUsage(input_tokens=10, output_tokens=300, cost=0.0),
                    model="test-model-001",
                )
            ]
        )
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=LlmDecompositionConfig(max_retries=0),
        )

        with pytest.raises(DecompositionError) as caught:
            await strategy.decompose(_make_task(), _make_context())

        assert not isinstance(caught.value, DecompositionBudgetExhaustedError)

    @pytest.mark.unit
    async def test_provider_error_surfaces_as_decomposition_error(self) -> None:
        """A raw provider/infra failure is wrapped as a typed DecompositionError.

        Decomposition must always terminate inside the domain error hierarchy so
        the pipeline's plan-review guard can surface it as a FAILED plan rather
        than an escaping exception (a 500). The non-DomainError raised by the
        provider is chained as ``__cause__``.
        """
        provider = MockCompletionProvider([])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        # MockCompletionProvider raises IndexError when empty; the strategy must
        # translate that into a DecompositionError rather than let it escape.
        with pytest.raises(DecompositionError) as exc_info:
            await strategy.decompose(task, ctx)
        assert isinstance(exc_info.value.__cause__, IndexError)

    @pytest.mark.unit
    async def test_a_domain_error_from_the_provider_is_not_wrapped_again(
        self,
    ) -> None:
        """``CompletionProvider`` is a Protocol, so a decorator can raise ours.

        Wrapping it a second time would bury the condition the inner error
        names under a generic call-failed message, and the caller reads only
        the outermost one.
        """
        raised = DecompositionError("the budget guard refused this call")

        class _RefusingProvider(MockCompletionProvider):
            @override
            async def complete(
                self,
                messages: list[ChatMessage],
                model: str,
                *,
                tools: list[ToolDefinition] | None = None,
                config: CompletionConfig | None = None,
            ) -> CompletionResponse:
                raise raised

        strategy = LlmDecompositionStrategy(
            provider=_RefusingProvider([]), model="test-model-001"
        )

        with pytest.raises(DecompositionError) as exc_info:
            await strategy.decompose(_make_task(), _make_context())

        assert exc_info.value is raised

    @pytest.mark.unit
    def test_protocol_conformance(self) -> None:
        """LlmDecompositionStrategy satisfies DecompositionStrategy."""
        provider = MockCompletionProvider([])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        assert isinstance(strategy, DecompositionStrategy)

    @pytest.mark.unit
    def test_strategy_name(self) -> None:
        """Strategy name is 'llm'."""
        provider = MockCompletionProvider([])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        assert strategy.get_strategy_name() == "llm"

    @pytest.mark.unit
    async def test_temperature_passed_to_provider(self) -> None:
        """Temperature from config is passed to the provider."""
        args = _valid_plan_args(subtask_count=1)
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        config = LlmDecompositionConfig(temperature=0.7)
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context()

        await strategy.decompose(task, ctx)

        recorded = provider.recorded_configs
        assert len(recorded) == 1
        assert recorded[0] is not None
        assert recorded[0].temperature == 0.7

    @pytest.mark.unit
    async def test_custom_config_values(self) -> None:
        """Custom config values are respected."""
        args = _valid_plan_args(subtask_count=1)
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        config = LlmDecompositionConfig(
            max_retries=5,
            temperature=1.0,
            max_output_tokens=2048,
        )
        strategy = LlmDecompositionStrategy(
            provider=provider,
            model="test-model-001",
            config=config,
        )
        task = _make_task()
        ctx = _make_context()

        await strategy.decompose(task, ctx)

        recorded = provider.recorded_configs
        assert recorded[0] is not None
        assert recorded[0].temperature == 1.0
        assert recorded[0].max_tokens == 2048

    @pytest.mark.unit
    async def test_model_passed_to_provider(self) -> None:
        """Model name is forwarded to the provider."""
        args = _valid_plan_args(subtask_count=1)
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-expert-001")
        task = _make_task()
        ctx = _make_context()

        await strategy.decompose(task, ctx)

        assert provider.recorded_models == ["test-expert-001"]

    @pytest.mark.unit
    async def test_tool_definition_sent_to_provider(self) -> None:
        """Tool definition is sent to the provider."""
        args = _valid_plan_args(subtask_count=1)
        response = _make_tool_call_response(args)
        provider = MockCompletionProvider([response])
        strategy = LlmDecompositionStrategy(provider=provider, model="test-model-001")
        task = _make_task()
        ctx = _make_context()

        await strategy.decompose(task, ctx)

        tools = provider.recorded_tools
        assert len(tools) == 1
        assert tools[0] is not None
        assert len(tools[0]) == 1
        assert tools[0][0].name == "submit_decomposition_plan"

    @pytest.mark.unit
    def test_blank_model_rejected(self) -> None:
        """Blank model string raises ValueError."""
        provider = MockCompletionProvider([])
        with pytest.raises(ValueError, match="non-blank"):
            LlmDecompositionStrategy(provider=provider, model="")

    @pytest.mark.unit
    def test_whitespace_model_rejected(self) -> None:
        """Whitespace-only model string raises ValueError."""
        provider = MockCompletionProvider([])
        with pytest.raises(ValueError, match="non-blank"):
            LlmDecompositionStrategy(provider=provider, model="   ")
