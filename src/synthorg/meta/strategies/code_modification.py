"""Code modification improvement strategy.

Generates proposals for framework code changes by using LLM
analysis to synthesize concrete code modifications from signal
patterns and rule context.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final
from uuid import uuid4

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.currency import format_cost
from synthorg.budget.tracker import CostTracker
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_CONFIG_VALUE,
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import (
    CodeChange,
    CodeOperation,
    ImprovementProposal,
    OrgSignalSnapshot,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
    RuleMatch,
)
from synthorg.meta.validation.scope_validator import ScopeValidator
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_CODE_GEN_COMPLETED,
    META_CODE_GEN_FAILED,
    META_CODE_GEN_PARSE_FAILED,
    META_CODE_GEN_STARTED,
    META_CODE_SCOPE_VIOLATION,
    META_PROPOSAL_GENERATED,
)
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.errors import ProviderError

logger = get_logger(__name__)

# Confidence for an autonomous code-modification proposal.
_CONFIDENCE_CODE_MODIFICATION: Final[float] = 0.5

_SYSTEM_PROMPT = """\
You are a framework improvement analyst for SynthOrg, a framework \
for building synthetic organizations. Your task is to propose \
concrete code changes that improve the framework based on observed \
signal patterns.

Rules:
- Propose changes only to the files and modules specified.
- Follow Python 3.14+ conventions (no __future__ imports, PEP 758).
- Use type hints on all public functions.
- Use Google-style docstrings.
- Follow the pluggable protocol + strategy + factory pattern.
- Keep functions under 50 lines, files under 800 lines.
- Line length limit: 88 characters.
- All public names must use NotBlankStr for identifiers.
- Use structlog logging via synthorg.observability.get_logger.

Respond with a JSON array of code changes. Each change object:
{
  "file_path": "relative/path/from/project/root.py",
  "operation": "create" | "modify" | "delete",
  "old_content": "current file content (for modify/delete)",
  "new_content": "proposed file content (for create/modify)",
  "description": "what this change does",
  "reasoning": "why this improves the system"
}

Respond ONLY with the JSON array, no markdown fences or commentary.

""" + untrusted_content_directive((TAG_CONFIG_VALUE, TAG_TASK_DATA))


class CodeModificationStrategy:
    """Generates code modification proposals from signal patterns.

    Uses LLM calls to analyze framework code and generate concrete
    code changes (new strategies, guards, algorithms) based on
    detected signal patterns.

    Args:
        config: Self-improvement configuration.
        provider: Completion provider for LLM calls.
        scope_validator: Validates proposed file paths.
    """

    def __init__(
        self,
        *,
        config: SelfImprovementConfig,
        provider: BaseCompletionProvider,
        scope_validator: ScopeValidator,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._config = config
        self._cost_tracker = cost_tracker
        self._provider = provider
        self._scope_validator = scope_validator
        self._code_config = config.code_modification

    @property
    def altitude(self) -> ProposalAltitude:
        """This strategy produces code modification proposals.

        Returns:
            ``ProposalAltitude`` instance.
        """
        return ProposalAltitude.CODE_MODIFICATION

    async def propose(
        self,
        *,
        snapshot: OrgSignalSnapshot,
        triggered_rules: tuple[RuleMatch, ...],
    ) -> tuple[ImprovementProposal, ...]:
        """Generate code modification proposals from triggered rules.

        For each relevant rule, calls the LLM to generate concrete
        code changes, validates scope, and builds proposals.

        Args:
            snapshot: Current org-wide signal snapshot.
            triggered_rules: Rules that fired targeting code modification.

        Returns:
            Tuple of code modification proposals.
        """
        proposals: list[ImprovementProposal] = []

        for rule_match in triggered_rules:
            if self.altitude not in rule_match.suggested_altitudes:
                continue

            proposal = await self._generate_proposal(
                rule_match,
                snapshot,
            )
            if proposal is not None:
                proposals.append(proposal)
                logger.info(
                    META_PROPOSAL_GENERATED,
                    altitude="code_modification",
                    rule=rule_match.rule_name,
                    title=proposal.title,
                )

        return tuple(proposals)

    async def _generate_proposal(
        self,
        rule_match: RuleMatch,
        snapshot: OrgSignalSnapshot,
    ) -> ImprovementProposal | None:
        """Generate a single proposal via LLM call.

        Args:
            rule_match: The triggered rule match.
            snapshot: Current org signal snapshot.

        Returns:
            A code modification proposal, or None if generation
            failed or changes are out of scope.

        Raises:
            ProviderError: Raised on the corresponding failure path.
        """
        prompt = self._build_user_prompt(rule_match, snapshot)
        logger.info(
            META_CODE_GEN_STARTED,
            rule=rule_match.rule_name,
        )

        try:
            response = await self._call_llm(prompt)
        except ProviderError as exc:
            # Surface provider-wide failures with the same WARN shape as
            # the local-error branch before propagating, so the loss path
            # is observable end-to-end (provider tier + rule tier).
            logger.warning(
                META_CODE_GEN_FAILED,
                rule=rule_match.rule_name,
                reason="provider_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                META_CODE_GEN_FAILED,
                rule=rule_match.rule_name,
                reason="provider_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

        if not response:
            logger.warning(
                META_CODE_GEN_FAILED,
                rule=rule_match.rule_name,
                reason="empty_response",
            )
            return None

        # Parse response into CodeChange models.
        changes = self._parse_code_changes(response, rule_match.rule_name)
        if not changes:
            return None

        # Validate scope on the FULL parsed set -- fail-closed: reject
        # entire proposal on ANY scope violation, even if the offending
        # entry would have been truncated by the file-count limit.
        violations = self._scope_validator.validate_changes(changes)

        if violations:
            logger.error(
                META_CODE_SCOPE_VIOLATION,
                rule=rule_match.rule_name,
                violations=list(violations),
                change_count=len(changes),
            )
            return None

        # Enforce file count limit only after scope validation succeeds.
        max_files = self._code_config.max_files_per_proposal
        if len(changes) > max_files:
            changes = changes[:max_files]

        logger.info(
            META_CODE_GEN_COMPLETED,
            rule=rule_match.rule_name,
            change_count=len(changes),
        )

        proposal_id = uuid4()
        branch_name = f"{self._code_config.branch_prefix}/{str(proposal_id)[:8]}"
        return ImprovementProposal(
            id=proposal_id,
            altitude=ProposalAltitude.CODE_MODIFICATION,
            title=f"Code improvement for {rule_match.rule_name}",
            description=(
                f"LLM-generated code changes addressing "
                f"'{rule_match.rule_name}' signal pattern. "
                f"{len(changes)} file(s) modified."
            ),
            rationale=ProposalRationale(
                signal_summary=_summarize_context(
                    rule_match.signal_context,
                ),
                pattern_detected=rule_match.description,
                expected_impact=(
                    "Algorithmic improvement to address detected signal pattern"
                ),
                confidence_reasoning=(
                    "LLM analysis of signal context and framework source code"
                ),
            ),
            code_changes=changes,
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_branch",
                        target=branch_name,
                        description=(
                            f"Delete branch '{branch_name}' and close associated PR"
                        ),
                    ),
                ),
                validation_check=(
                    f"Branch '{branch_name}' deleted and no changes merged to main"
                ),
            ),
            confidence=_CONFIDENCE_CODE_MODIFICATION,
            source_rule=rule_match.rule_name,
        )

    async def _call_llm(self, user_prompt: str) -> str | None:
        """Call the LLM provider for code generation.

        Args:
            user_prompt: The user message with signal context.

        Returns:
            The LLM response text, or None if empty.
        """
        from synthorg.providers.enums import MessageRole  # noqa: PLC0415
        from synthorg.providers.models import (  # noqa: PLC0415
            ChatMessage,
            CompletionConfig,
        )

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=MessageRole.USER, content=user_prompt),
        ]
        config = CompletionConfig(
            temperature=self._code_config.temperature,
            max_tokens=self._code_config.max_tokens,
        )
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=NotBlankStr("system"),
            task_id=NotBlankStr("system:meta:code_modification"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            response = await self._provider.complete(
                messages=messages,
                model=str(self._code_config.llm_model),
                config=config,
            )
        return response.content

    def _parse_code_changes(
        self,
        response: str,
        rule_name: str,
    ) -> tuple[CodeChange, ...]:
        """Parse LLM response into CodeChange models.

        Args:
            response: Raw LLM response text (expected JSON array).
            rule_name: Rule name for logging context.

        Returns:
            Tuple of parsed CodeChange models (empty on parse failure).
        """
        data = _parse_json_array(response, rule_name)
        if data is None:
            return ()

        changes = _parse_items(data, rule_name)

        if changes and len(changes) < len(data):
            logger.info(
                META_CODE_GEN_COMPLETED,
                rule=rule_name,
                reason="partial_parse",
                valid_count=len(changes),
                total_count=len(data),
            )

        return tuple(changes)

    def _build_user_prompt(
        self,
        rule_match: RuleMatch,
        snapshot: OrgSignalSnapshot,
    ) -> str:
        """Build the user prompt for code generation.

        Args:
            rule_match: The triggered rule match.
            snapshot: Current org signal snapshot.

        Returns:
            User prompt string with signal context.
        """
        perf = snapshot.performance
        budget = snapshot.budget
        manifest = _build_file_manifest(self._code_config.allowed_paths)
        # Rule metadata (name, severity, description) is set by
        # admins / custom-rule authors and reaches the LLM verbatim;
        # signal_context values are rule-driven but may carry
        # agent-shaped payloads. ``allowed_paths`` is config-driven
        # and ``manifest`` embeds file names from that config. All
        # go inside untrusted fences declared by ``_SYSTEM_PROMPT``.
        fenced_rule_name = wrap_untrusted(TAG_CONFIG_VALUE, rule_match.rule_name)
        fenced_severity = wrap_untrusted(
            TAG_CONFIG_VALUE,
            rule_match.severity.value,
        )
        fenced_description = wrap_untrusted(
            TAG_CONFIG_VALUE,
            rule_match.description,
        )
        fenced_context = wrap_untrusted(
            TAG_TASK_DATA,
            json.dumps(rule_match.signal_context, default=str),
        )
        fenced_allowed_paths = wrap_untrusted(
            TAG_CONFIG_VALUE,
            ", ".join(self._code_config.allowed_paths),
        )
        fenced_manifest = wrap_untrusted(TAG_TASK_DATA, manifest)
        return (
            f"Signal pattern detected: {fenced_rule_name}\n"
            f"Severity: {fenced_severity}\n"
            f"Description: {fenced_description}\n"
            f"Signal context: {fenced_context}\n"
            f"\n"
            f"Org metrics:\n"
            f"- Quality: {perf.avg_quality_score}/10\n"
            f"- Success rate: {perf.avg_success_rate:.1%}\n"
            f"- Agent count: {perf.agent_count}\n"
            f"- Budget spend: {format_cost(budget.total_spend)}\n"
            f"- Coordination ratio: {budget.coordination_ratio:.1%}\n"
            f"\n"
            f"Allowed modification paths: {fenced_allowed_paths}\n"
            f"\n"
            f"{fenced_manifest}\n"
            f"\n"
            f"Propose concrete code changes to improve the framework "
            f"and address this signal pattern. For MODIFY or DELETE "
            f"operations, old_content must match the current file."
        )


def _parse_json_array(
    response: str,
    rule_name: str,
) -> list[object] | None:
    """Parse and validate the LLM response as a JSON array.

    Args:
        response: Raw LLM response text.
        rule_name: Rule name for logging context.

    Returns:
        Parsed list of dicts, or None on failure.
    """
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        logger.warning(
            META_CODE_GEN_PARSE_FAILED,
            rule=rule_name,
            reason="invalid_json",
        )
        return None

    if not isinstance(data, list):
        logger.warning(
            META_CODE_GEN_PARSE_FAILED,
            rule=rule_name,
            reason="not_a_list",
        )
        return None
    return data


def _parse_items(
    data: list[object],
    rule_name: str,
) -> list[CodeChange]:
    """Parse individual items from the JSON array into CodeChange models.

    Args:
        data: List of items from LLM response (expected dicts).
        rule_name: Rule name for logging context.

    Returns:
        List of successfully parsed CodeChange models.
    """
    changes: list[CodeChange] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            logger.warning(
                META_CODE_GEN_PARSE_FAILED,
                rule=rule_name,
                reason="non_dict_item",
                index=idx,
            )
            continue
        try:
            change = CodeChange(
                file_path=item.get("file_path", ""),
                operation=CodeOperation(item.get("operation", "")),
                old_content=item.get("old_content", ""),
                new_content=item.get("new_content", ""),
                description=item.get("description", ""),
                reasoning=item.get("reasoning", ""),
            )
            changes.append(change)
        except (ValueError, TypeError) as exc:
            logger.warning(
                META_CODE_GEN_PARSE_FAILED,
                rule=rule_name,
                reason="invalid_change_item",
                index=idx,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
    return changes


_MAX_MANIFEST_CHARS: Final[int] = 4000


def _build_file_manifest(
    allowed_paths: tuple[str, ...],
) -> str:
    """Build a manifest of existing files in allowed paths.

    Scans each allowed glob pattern directory and lists existing
    Python files so the LLM has context for MODIFY/DELETE operations.

    Args:
        allowed_paths: Glob patterns for allowed file paths.

    Returns:
        Human-readable manifest string.
    """
    cwd = Path.cwd()
    files: list[str] = []
    for pattern in allowed_paths:
        parent = Path(pattern.rsplit("/", 1)[0]) if "/" in pattern else Path()
        target = cwd / parent
        if not target.is_dir():
            continue
        for py_file in sorted(target.glob("*.py")):
            rel = py_file.relative_to(cwd)
            files.append(str(rel).replace("\\", "/"))

    if not files:
        return "Existing files in allowed paths: (none found)"

    lines = ["Existing files in allowed paths:"]
    total = 0
    for f in files:
        entry = f"- {f}"
        total += len(entry)
        if total > _MAX_MANIFEST_CHARS:
            lines.append(f"... ({len(files) - len(lines) + 1} more)")
            break
        lines.append(entry)
    return "\n".join(lines)


def _summarize_context(ctx: Mapping[str, object]) -> str:
    """Build a one-line signal summary from context dict.

    Args:
        ctx: Signal context dictionary.

    Returns:
        Comma-separated key=value summary.
    """
    if not ctx:
        return "No signal context"
    parts = [f"{k}={v}" for k, v in ctx.items()]
    return ", ".join(parts)
