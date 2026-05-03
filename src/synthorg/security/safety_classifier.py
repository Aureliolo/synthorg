"""Two-stage safety classifier for approval gates.

Stage 1: ``InformationStripper`` removes PII, secrets, internal IDs,
and email addresses from the reviewer-facing description.  The
original text is preserved for execution.

Stage 2: ``SafetyClassifier`` sends the stripped text to an LLM from
a different provider family for classification as safe, suspicious,
or blocked.  Blocked actions are auto-rejected (configurable).
Suspicious actions receive a warning badge in the reviewer UI.

Design invariants:
    - The LLM classifier only sees the stripped text -- agent
      rationalizations containing PII/secrets cannot influence it.
    - Classification errors default to SUSPICIOUS (fail-safe):
      neither auto-rejects nor marks as safe.
    - Cross-family provider selection follows the same pattern as
      ``LlmSecurityEvaluator``.
"""

import asyncio
import html
import re
import secrets
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.enums import ApprovalRiskLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr

# ``prompt_safety`` is imported lazily inside this module's helpers
# to avoid a circular boot path: ``synthorg.engine.__init__`` imports
# ``AgentEngine`` -> ``_security_factory`` -> ``SecOpsService`` ->
# ``service_safety`` -> ``safety_classifier``, so a top-level
# ``from synthorg.engine.prompt_safety import ...`` here would be
# resolved against a partially-initialised ``synthorg.engine``
# package.  ``prompt_safety`` itself has no dependency on the rest
# of ``engine/``; the deferred import is correctness-preserving.
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_INFO_STRIP_COMPLETE,
    SECURITY_SAFETY_CLASSIFY_COMPLETE,
    SECURITY_SAFETY_CLASSIFY_ERROR,
    SECURITY_SAFETY_CLASSIFY_START,
    SECURITY_TIER_CLASSIFIED,
    SECURITY_TIER_SAFE_TOOL,
)

# ``cost_recording_scope`` is imported lazily inside the call site
# (``_run_classifier``) to break a latent circular boot path:
# ``synthorg.providers.cost_recording`` -> ``synthorg.budget.cost_record``
# -> ``synthorg.ontology`` -> ``synthorg.persistence`` ->
# ``synthorg.persistence.audit_protocol`` -> ``synthorg.security`` ->
# ``synthorg.security.safety_classifier``.  Hoisting the import back to
# module scope reopens the cycle when ``synthorg.security`` is the
# first package walked at boot (covered by
# ``tests/unit/security/test_safety_classifier_circular_boot.py``).
from synthorg.providers.enums import MessageRole
from synthorg.providers.family import get_family, providers_excluding_family
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.security.config import SafetyClassifierConfig  # noqa: TC001
from synthorg.security.rules.credential_detector import CREDENTIAL_PATTERNS
from synthorg.security.rules.data_leak_detector import PII_PATTERNS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.budget.tracker import CostTracker
    from synthorg.config.schema import ProviderConfig
    from synthorg.providers.base import BaseCompletionProvider
    from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)

# ── Information stripping patterns ───────────────────────────────

# Reuse existing credential and PII patterns from detectors.
_CREDENTIAL_STRIP_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    CREDENTIAL_PATTERNS
)
_PII_STRIP_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = PII_PATTERNS

# Additional patterns for UUIDs, emails, and internal IDs.
_UUID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
)
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
)
_INTERNAL_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:agent|task)-[A-Za-z0-9][A-Za-z0-9\-]*\b",
)

# Placeholder tokens.
_CREDENTIAL_PLACEHOLDER: Final[str] = "[CREDENTIAL]"
_PII_PLACEHOLDER: Final[str] = "[PII]"
_ID_PLACEHOLDER: Final[str] = "[ID]"
_EMAIL_PLACEHOLDER: Final[str] = "[EMAIL]"

# Maximum length for LLM-returned reason string.
_MAX_REASON_LENGTH: Final[int] = 300

# Regex to strip control and formatting characters from LLM-returned
# reason.  Best-effort coverage: ASCII control (C0/DEL), Unicode bidi
# overrides, zero-width chars, line/paragraph separators, and known
# invisible characters used in prompt injection payloads.
_CONTROL_CHAR_RE: Final[re.Pattern[str]] = re.compile(
    r"[\x00-\x1f\x7f"
    r"\u200b-\u200f"  # zero-width and bidi marks
    r"\u2028-\u2029"  # line / paragraph separators
    r"\u202a-\u202e"  # bidi embedding/override
    r"\u2066-\u2069"  # bidi isolate
    r"\u2800"  # braille blank (invisible)
    r"\u3164"  # hangul filler (invisible)
    r"\ufeff"  # BOM / zero-width no-break space
    r"]",
)


# ── Enums and models ─────────────────────────────────────────────


class SafetyClassification(StrEnum):
    """Safety classification for an escalated action.

    Members:
        SAFE: Action appears safe for human review.
        SUSPICIOUS: Action has concerning elements -- reviewer
            sees a warning badge.
        BLOCKED: Action is unsafe -- auto-rejected when
            ``auto_reject_blocked`` is enabled.
    """

    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    BLOCKED = "blocked"


class PermissionTier(StrEnum):
    """Permission tier for safety classifier routing.

    Determines how much scrutiny an action receives before
    approval.

    Members:
        SAFE_TOOL: Action type is in the safe-tool allowlist;
            bypass the classifier entirely.
        IN_PROJECT: In-project operation -- lighter review
            (reserved for future use; currently falls through
            to CLASSIFIER_GATED).
        CLASSIFIER_GATED: Full classifier scrutiny (external
            operations, shell commands, network calls).
    """

    SAFE_TOOL = "safe_tool"
    IN_PROJECT = "in_project"
    CLASSIFIER_GATED = "classifier_gated"


class SafetyClassifierResult(BaseModel):
    """Result of the two-stage safety classification.

    Attributes:
        classification: The safety classification verdict.
        stripped_description: Description with PII, secrets, and
            internal IDs replaced by tagged placeholders.
        reason: Human-readable explanation for the classification.
        classification_duration_ms: Time taken for classification.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    classification: SafetyClassification
    stripped_description: str
    reason: NotBlankStr
    classification_duration_ms: float = Field(ge=0.0)


# ── InformationStripper ─────────────────────────────────────────


class InformationStripper:
    """Strip PII, secrets, UUIDs, emails, and internal IDs from text.

    Reuses credential patterns from ``credential_detector`` and PII
    patterns from ``data_leak_detector``, plus additional patterns
    for UUIDs, email addresses, and internal ID formats.  Each
    category is replaced with a distinct tagged placeholder.
    """

    def strip(self, text: str) -> str:
        """Replace sensitive data with tagged placeholders.

        Args:
            text: The input text to sanitize.

        Returns:
            The text with sensitive patterns replaced by
            ``[CREDENTIAL]``, ``[PII]``, ``[ID]``, or ``[EMAIL]``.
        """
        if not text:
            return text

        result = text

        # Credentials first (most specific patterns).
        for _label, pattern in _CREDENTIAL_STRIP_PATTERNS:
            result = pattern.sub(_CREDENTIAL_PLACEHOLDER, result)

        # PII patterns.
        for _label, pattern in _PII_STRIP_PATTERNS:
            result = pattern.sub(_PII_PLACEHOLDER, result)

        # UUIDs.
        result = _UUID_PATTERN.sub(_ID_PLACEHOLDER, result)

        # Internal IDs (agent-xxx, task-xxx).
        result = _INTERNAL_ID_PATTERN.sub(_ID_PLACEHOLDER, result)

        # Emails (after credentials to avoid double-matching
        # patterns that look like email-with-token).
        result = _EMAIL_PATTERN.sub(_EMAIL_PLACEHOLDER, result)

        # Strip bidi overrides and zero-width characters that
        # could hide prompt injection payloads.
        result = _CONTROL_CHAR_RE.sub(" ", result)

        logger.debug(
            SECURITY_INFO_STRIP_COMPLETE,
            original_length=len(text),
            stripped_length=len(result),
        )
        return result


# ── LLM tool schema ──────────────────────────────────────────────

_VALID_CLASSIFICATIONS = frozenset(v.value for v in SafetyClassification)

_SAFETY_VERDICT_TOOL = ToolDefinition(
    name="safety_classification_verdict",
    description=(
        "Submit your safety classification for the proposed agent "
        "action.  You MUST call this tool with your assessment."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "classification": {
                "type": "string",
                "enum": sorted(_VALID_CLASSIFICATIONS),
                "description": (
                    "Safety classification: safe (action appears "
                    "safe), suspicious (concerning elements), or "
                    "blocked (unsafe, should be auto-rejected)."
                ),
            },
            # "concerns" is optional in the schema so the LLM can
            # reason about safety concerns, but the value is not
            # consumed by _parse_tool_call -- it exists only to
            # improve classification quality via chain-of-thought.
            "concerns": {
                "type": "string",
                "description": "List of specific safety concerns found.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Brief explanation for the classification (1-2 sentences)."
                ),
            },
        },
        "required": ["classification", "reason"],
        "additionalProperties": False,
    },
)

_SYSTEM_PROMPT_HEAD: Final[str] = (
    "You are a safety classifier for an AI agent orchestration "
    "system.  An agent action has been escalated for human approval. "
    "Your job is to classify the action to help the human reviewer.\n\n"
    "Classify the action as:\n"
    "- safe: The action appears legitimate and safe for review.\n"
    "- suspicious: The action has concerning elements (unusual "
    "patterns, potential data exfiltration, scope creep).\n"
    "- blocked: The action is clearly unsafe (destructive, "
    "credential theft, unauthorized access).\n\n"
    "IMPORTANT: The field values below have been sanitized.  Some "
    "data has been replaced with placeholders like [CREDENTIAL], "
    "[PII], [ID], [EMAIL].\n\n"
    "You MUST call the safety_classification_verdict tool with "
    "your assessment.  Do not respond with text -- only use the tool."
)
"""Static head of the safety-classifier system prompt.

The ``untrusted_content_directive`` suffix is appended at message-
build time via :func:`_system_prompt` so the lazy
``synthorg.engine.prompt_safety`` import (see module docstring above)
does not run at module-import time.
"""


def _system_prompt() -> str:
    """Return the full system prompt with the directive appended.

    Computed lazily so the ``synthorg.engine.prompt_safety`` import
    does not run during ``synthorg.engine.__init__`` boot, which
    would create a circular import via the security service.

    A failed import here surfaces as ``ImportError`` to the caller
    (the ``classify`` request path). That is intentional: the
    classifier is a security-critical surface, and falling back
    silently on a missing prompt-safety helper would weaken the
    untrusted-content fence rather than surface the boot bug. The
    call site in ``classify`` already has a fail-safe (``SUSPICIOUS``
    verdict on any unhandled exception); a hard ``ImportError`` is
    the correct route for "the build is broken" vs "the LLM said
    no".
    """
    from synthorg.engine.prompt_safety import (  # noqa: PLC0415
        TAG_TASK_DATA,
        untrusted_content_directive,
    )

    return f"{_SYSTEM_PROMPT_HEAD}\n\n{untrusted_content_directive((TAG_TASK_DATA,))}"


# ── SafetyClassifier ─────────────────────────────────────────────


class SafetyClassifier:
    """Two-stage safety classifier for approval gate actions.

    Stage 1: strip PII, secrets, and internal IDs via
    ``InformationStripper``.  Stage 2: classify the stripped action
    via an LLM from a different provider family.

    Args:
        provider_registry: Registry of provider drivers.
        provider_configs: Provider config dict for family lookup.
        config: Safety classifier configuration.
    """

    def __init__(
        self,
        *,
        provider_registry: ProviderRegistry,
        provider_configs: Mapping[str, ProviderConfig],
        config: SafetyClassifierConfig,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._registry = provider_registry
        self._configs = provider_configs
        self._config = config
        self._cost_tracker = cost_tracker
        self._stripper = InformationStripper()

    def classify_tier(self, action_type: str) -> PermissionTier:
        """Determine the permission tier for an action type.

        Args:
            action_type: The action type (``category:action``).

        Returns:
            The permission tier governing classifier behavior.
        """
        if action_type in self._config.safe_tool_categories:
            logger.debug(
                SECURITY_TIER_SAFE_TOOL,
                action_type=action_type,
            )
            return PermissionTier.SAFE_TOOL

        logger.debug(
            SECURITY_TIER_CLASSIFIED,
            action_type=action_type,
            tier=PermissionTier.CLASSIFIER_GATED.value,
        )
        return PermissionTier.CLASSIFIER_GATED

    async def classify(
        self,
        description: str,
        action_type: str,
        tool_name: str,
        risk_level: ApprovalRiskLevel,
    ) -> SafetyClassifierResult:
        """Run two-stage safety classification.

        Args:
            description: The escalation reason / action description.
            action_type: The action type (``category:action``).
            tool_name: The tool being invoked.
            risk_level: The risk level from the security verdict.

        Returns:
            A ``SafetyClassifierResult`` with the classification,
            stripped description, and reason.
        """
        start = time.monotonic()
        logger.info(
            SECURITY_SAFETY_CLASSIFY_START,
            tool_name=tool_name,
            action_type=action_type,
            risk_level=risk_level.value,
        )

        # Stage 1: information stripping.
        stripped = self._stripper.strip(description)

        # Stage 2: LLM classification.
        try:
            return await self._classify_via_llm(
                stripped,
                action_type,
                tool_name,
                risk_level,
                start,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000
            logger.exception(
                SECURITY_SAFETY_CLASSIFY_ERROR,
                tool_name=tool_name,
                action_type=action_type,
                duration_ms=duration_ms,
            )
            return SafetyClassifierResult(
                classification=SafetyClassification.SUSPICIOUS,
                stripped_description=stripped,
                reason="Safety classification failed (fail-safe: suspicious)",
                classification_duration_ms=duration_ms,
            )

    async def _classify_via_llm(
        self,
        stripped_description: str,
        action_type: str,
        tool_name: str,
        risk_level: ApprovalRiskLevel,
        start: float,
    ) -> SafetyClassifierResult:
        """Send stripped description to LLM for classification."""
        provider_name, driver = self._select_provider()
        if provider_name is None or driver is None:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning(
                SECURITY_SAFETY_CLASSIFY_ERROR,
                note="No provider available for safety classification",
            )
            return SafetyClassifierResult(
                classification=SafetyClassification.SUSPICIOUS,
                stripped_description=stripped_description,
                reason="No provider available for safety classification",
                classification_duration_ms=duration_ms,
            )

        model = self._select_model(provider_name)
        messages = self._build_messages(
            stripped_description,
            action_type,
            tool_name,
            risk_level,
        )

        # Lazy import: see module-level note for the boot-cycle
        # rationale.
        from synthorg.providers.cost_recording import (  # noqa: PLC0415
            cost_recording_scope,
        )

        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=NotBlankStr("system"),
            task_id=NotBlankStr("system:security:safety_classifier"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            response = await asyncio.wait_for(
                driver.complete(
                    messages,
                    model,
                    tools=[_SAFETY_VERDICT_TOOL],
                    config=CompletionConfig(
                        temperature=0.0,
                        max_tokens=256,
                    ),
                ),
                timeout=self._config.timeout_seconds,
            )

        return self._parse_response(
            response,
            stripped_description,
            start,
        )

    def _select_provider(
        self,
    ) -> tuple[str | None, BaseCompletionProvider | None]:
        """Select a provider for safety classification.

        Prefers a cross-family provider.  Falls back to the first
        available provider if no cross-family option exists.
        """
        available = self._registry.list_providers()
        if not available:
            return None, None

        # Try cross-family selection with randomization to avoid
        # always hitting the same external provider.
        all_cross: list[str] = []
        for name in available:
            family = get_family(name, self._configs)
            candidates = providers_excluding_family(family, self._configs)
            all_cross.extend(p for p in candidates if p in available)
        if all_cross:
            selected = secrets.choice(list(set(all_cross)))
            return selected, self._registry.get(selected)

        # Fallback: use first available (same-family).
        name = available[0]
        return name, self._registry.get(name)

    def _select_model(self, provider_name: str) -> str:
        """Select the model for classification."""
        if self._config.model is not None:
            return self._config.model

        config = self._configs.get(provider_name)
        if config is not None and config.models:
            first = config.models[0]
            return first.alias or first.id

        logger.warning(
            SECURITY_SAFETY_CLASSIFY_ERROR,
            note=(
                f"No model configured for provider {provider_name!r}, "
                "using provider name as model hint"
            ),
            provider_name=provider_name,
        )
        return provider_name

    def _build_messages(
        self,
        stripped_description: str,
        action_type: str,
        tool_name: str,
        risk_level: ApprovalRiskLevel,
    ) -> list[ChatMessage]:
        """Build prompt messages from the stripped context.

        ``tool_name``, ``action_type``, and ``risk_level`` are bounded
        registry / enum strings (not attacker-controllable) and are
        emitted as ``html.escape``d label fields. The free-form
        ``description`` is the only attacker-controllable input and is
        wrapped via :func:`wrap_untrusted` under :data:`TAG_TASK_DATA`;
        the system prompt's ``untrusted_content_directive`` instructs
        the classifier LLM to ignore directives embedded in the body.
        """
        from synthorg.engine.prompt_safety import (  # noqa: PLC0415
            TAG_TASK_DATA,
            wrap_untrusted,
        )

        safe_tool = html.escape(self._stripper.strip(tool_name))
        safe_type = html.escape(self._stripper.strip(action_type))
        safe_risk = html.escape(risk_level.value)
        # Truncate BEFORE wrapping so we never cut inside the fence
        # boundary string returned by ``wrap_untrusted``.
        max_desc_chars = self._config.max_input_tokens * 4
        desc_text = stripped_description
        if len(desc_text) > max_desc_chars:
            desc_text = desc_text[:max_desc_chars] + "... [truncated]"
        wrapped_desc = wrap_untrusted(TAG_TASK_DATA, desc_text)

        user_content = (
            "<action>\n"
            f"  <tool>{safe_tool}</tool>\n"
            f"  <type>{safe_type}</type>\n"
            f"  <risk_level>{safe_risk}</risk_level>\n"
            f"  <description>\n{wrapped_desc}\n  </description>\n"
            "</action>"
        )

        return [
            ChatMessage(role=MessageRole.SYSTEM, content=_system_prompt()),
            ChatMessage(role=MessageRole.USER, content=user_content),
        ]

    def _parse_response(
        self,
        response: CompletionResponse,
        stripped_description: str,
        start: float,
    ) -> SafetyClassifierResult:
        """Parse LLM response into a SafetyClassifierResult."""
        duration_ms = (time.monotonic() - start) * 1000

        for tc in response.tool_calls:
            if tc.name == "safety_classification_verdict":
                return self._parse_tool_call(
                    tc.arguments,
                    stripped_description,
                    duration_ms,
                )

        logger.warning(
            SECURITY_SAFETY_CLASSIFY_ERROR,
            note="LLM did not call safety_classification_verdict tool",
        )
        return SafetyClassifierResult(
            classification=SafetyClassification.SUSPICIOUS,
            stripped_description=stripped_description,
            reason="LLM did not call the classification tool",
            classification_duration_ms=duration_ms,
        )

    def _parse_tool_call(
        self,
        args: dict[str, object],
        stripped_description: str,
        duration_ms: float,
    ) -> SafetyClassifierResult:
        """Parse tool call arguments into a result."""
        raw_classification = str(args.get("classification", ""))
        raw_reason = args.get("reason", "")

        if raw_classification not in _VALID_CLASSIFICATIONS:
            logger.warning(
                SECURITY_SAFETY_CLASSIFY_ERROR,
                note=f"Invalid classification: {raw_classification!r}",
            )
            return SafetyClassifierResult(
                classification=SafetyClassification.SUSPICIOUS,
                stripped_description=stripped_description,
                reason=(f"Invalid classification from LLM: {raw_classification!r}"),
                classification_duration_ms=duration_ms,
            )

        # Strip control chars first, then whitespace -- a reason
        # composed entirely of control chars becomes empty after
        # substitution, which would violate NotBlankStr.
        reason_clean = _CONTROL_CHAR_RE.sub(
            " ",
            str(raw_reason) if raw_reason else "",
        ).strip()
        reason = (
            reason_clean[:_MAX_REASON_LENGTH]
            if reason_clean
            else "Safety classification"
        )

        classification = SafetyClassification(raw_classification)
        logger.info(
            SECURITY_SAFETY_CLASSIFY_COMPLETE,
            classification=classification.value,
            duration_ms=duration_ms,
        )
        return SafetyClassifierResult(
            classification=classification,
            stripped_description=stripped_description,
            reason=reason,
            classification_duration_ms=duration_ms,
        )
