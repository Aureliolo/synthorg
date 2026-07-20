# module-kind: code
"""Redaction applied to every piece of text entering agent memory.

Memory is an exfiltration surface that ordinary output filtering does
not cover. Text captured from a run is persisted for the life of the
deployment and re-injected into later prompts for other tasks, so a
credential that appears once in a tool result becomes a credential
present in every future context the memory is recalled into. Prompt
fencing (``wrap_untrusted``) addresses the opposite direction: it stops
recalled text being *obeyed*, not stored secrets leaking out.

Deterministic and pattern-based, deliberately. An LLM redactor would add
a call, a cost and a non-determinism to a path that must not be able to
fail open.

Two classes of finding are masked:

* **Credentials**, via the shared :func:`redact_credentials` patterns, so
  memory and the conversation-persistence boundary agree on what a
  secret looks like.
* **Email addresses**, the one piece of personal data that reliably
  appears verbatim in task output (commit trailers, error reports,
  support transcripts) and is both high-value to an attacker and
  matchable without swallowing legitimate content. Deliberately not
  attempted: phone numbers and free-form names, whose patterns collide
  with version strings, identifiers and ordinary prose, so masking them
  would destroy the memories it was meant to protect.
"""

import re
from dataclasses import dataclass
from typing import Final

from synthorg.security.rules.credential_detector import redact_credentials

_EMAIL_REDACTED: Final[str] = "[REDACTED_EMAIL]"

_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9.\-])",
)

_EMAIL_FINDING: Final[str] = "Email address"


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Text cleared for storage, plus what had to be removed.

    Attributes:
        content: The text with every finding masked.
        findings: Names of the pattern classes that matched, sorted and
            deduplicated. Never the matched values themselves: a
            redaction report that quotes the secret it found defeats the
            redaction.
    """

    content: str
    findings: tuple[str, ...]

    @property
    def redacted(self) -> bool:
        """Whether anything was masked."""
        return bool(self.findings)


def redact_for_memory(content: str) -> RedactionResult:
    """Mask credentials and email addresses before text is remembered.

    Args:
        content: Candidate memory text, from an agent write or a capture
            hook.

    Returns:
        The storable text and the finding names, which are safe to log.
    """
    redacted, findings = redact_credentials(content)
    if _EMAIL_PATTERN.search(redacted):
        redacted = _EMAIL_PATTERN.sub(_EMAIL_REDACTED, redacted)
        findings = (*findings, _EMAIL_FINDING)
    return RedactionResult(content=redacted, findings=tuple(sorted(set(findings))))


__all__ = ["RedactionResult", "redact_for_memory"]
