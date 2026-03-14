"""Org policy quality validation heuristics.

Applies lightweight checks to detect policies that likely violate the
non-inferable principle — e.g. policies that describe codebase structure
(inferable by reading the repo) rather than actionable constraints.

Examples of **good** policies (non-inferable, actionable):

- ``"All API responses must include a correlation_id header"``
- ``"Never store PII in memory without encryption"``
- ``"Escalate budget overruns above $5 to the CFO"``

Examples of **bad** policies (inferable or non-actionable):

- ``"The project uses Python 3.14"`` — discoverable from pyproject.toml
- ``"src/api/ contains REST controllers"`` — discoverable by reading code
- ``"x"`` — too short to be actionable
"""

import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.observability import get_logger
from synthorg.observability.events.prompt import (
    PROMPT_POLICY_QUALITY_ISSUE,
    PROMPT_POLICY_VALIDATION_START,
)

logger = get_logger(__name__)

_MIN_POLICY_LENGTH: Final[int] = 10
_MAX_POLICY_LENGTH: Final[int] = 500

# Patterns that suggest inferable codebase context rather than a policy.
# Case-insensitive to catch capitalized variants like "Import json".
_CODE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:src|tests|lib|app)/[\w/]+\.py", re.IGNORECASE),  # file paths
    re.compile(r"\bfrom\s+\w+\s+import\b", re.IGNORECASE),  # Python imports
    re.compile(r"\bimport\s+\w+", re.IGNORECASE),  # bare imports
    re.compile(r"\bdef\s+\w+\s*\(", re.IGNORECASE),  # function definitions
    re.compile(r"\bclass\s+\w+[\s:(]", re.IGNORECASE),  # class definitions
)

# Directive keywords that signal an actionable policy constraint.
_ACTION_VERBS: Final[frozenset[str]] = frozenset(
    {
        "must",
        "should",
        "always",
        "never",
        "require",
        "ensure",
        "prohibit",
        "enforce",
        "restrict",
        "mandate",
        "avoid",
        "prefer",
        "escalate",
        "approve",
        "deny",
        "reject",
        "validate",
        "verify",
    }
)


class PolicyQualityIssue(BaseModel):
    """A quality issue found in an org policy.

    Attributes:
        policy: The policy text that triggered the issue.
        issue: Human-readable description of the problem.
        severity: ``"warning"`` for advisory issues; ``"error"``
            reserved for future stricter checks.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    policy: str = Field(description="The policy text that triggered the issue")
    issue: str = Field(
        min_length=1,
        description="Human-readable description of the problem",
    )
    severity: Literal["warning", "error"] = Field(
        description="Issue severity (``'error'`` reserved for future stricter checks)",
    )


def validate_policy_quality(
    policies: tuple[str, ...],
) -> tuple[PolicyQualityIssue, ...]:
    """Check org policies for non-inferable principle violations.

    Applies heuristic checks — results are advisory and never block
    prompt construction.

    Args:
        policies: Org policy texts to validate.

    Returns:
        Tuple of quality issues found (empty if all policies pass).
    """
    logger.debug(
        PROMPT_POLICY_VALIDATION_START,
        policy_count=len(policies),
    )
    issues: list[PolicyQualityIssue] = []
    for policy in policies:
        issues.extend(_check_single_policy(policy))

    for issue in issues:
        logger.warning(
            PROMPT_POLICY_QUALITY_ISSUE,
            policy_length=len(issue.policy),
            issue=issue.issue,
            severity=issue.severity,
        )

    return tuple(issues)


_ACTION_VERB_RE: re.Pattern[str] = re.compile(
    r"\b(?:" + "|".join(sorted(_ACTION_VERBS)) + r")\b",
)


def _check_single_policy(policy: str) -> list[PolicyQualityIssue]:
    """Run all heuristic checks on a single policy string.

    Args:
        policy: The policy text to validate.

    Returns:
        List of quality issues found (empty if the policy passes all checks).
    """
    return [
        *_check_length(policy),
        *_check_code_patterns(policy),
        *_check_action_keywords(policy),
    ]


def _check_length(policy: str) -> list[PolicyQualityIssue]:
    """Flag policies that are too short or too long.

    Args:
        policy: The policy text to validate.

    Returns:
        List of length-related issues (0-2 items).
    """
    found: list[PolicyQualityIssue] = []

    if len(policy) < _MIN_POLICY_LENGTH:
        found.append(
            PolicyQualityIssue(
                policy=policy,
                issue=(
                    f"Too short ({len(policy)} chars) — likely not an actionable policy"
                ),
                severity="warning",
            ),
        )

    if len(policy) > _MAX_POLICY_LENGTH:
        found.append(
            PolicyQualityIssue(
                policy=policy,
                issue=(
                    f"Too long ({len(policy)} chars) — "
                    f"may contain inferable context rather than a policy"
                ),
                severity="warning",
            ),
        )

    return found


def _check_code_patterns(policy: str) -> list[PolicyQualityIssue]:
    """Flag policies that contain inferable code patterns.

    Args:
        policy: The policy text to validate.

    Returns:
        List with one issue if a code pattern is found, else empty.
    """
    for pattern in _CODE_PATTERNS:
        if pattern.search(policy):
            return [
                PolicyQualityIssue(
                    policy=policy,
                    issue=(
                        "Contains code patterns (file paths, imports, or "
                        "definitions) — likely inferable from the codebase"
                    ),
                    severity="warning",
                ),
            ]
    return []


def _check_action_keywords(policy: str) -> list[PolicyQualityIssue]:
    """Flag policies missing directive keywords.

    Args:
        policy: The policy text to validate.

    Returns:
        List with one issue if no action keywords found, else empty.
    """
    policy_lower = policy.lower()
    if not _ACTION_VERB_RE.search(policy_lower):
        return [
            PolicyQualityIssue(
                policy=policy,
                issue=(
                    "Missing action verbs (must, should, always, never, "
                    "etc.) — may not be an actionable policy"
                ),
                severity="warning",
            ),
        ]
    return []
