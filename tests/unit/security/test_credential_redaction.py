"""Unit tests for the credential-only redaction backstop.

The redact-before-persist backstop masks secret-shaped values in chat-turn
content while leaving legitimate operator prose untouched. It is the
defence-in-depth complement to out-of-band capture.
"""

import json

import pytest

from synthorg.security.rules.credential_detector import redact_credentials

pytestmark = pytest.mark.unit

_REDACTED = "[REDACTED]"


@pytest.mark.parametrize(
    "sentinel",
    [
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD",
        "Bearer abcdefghijklmnopqrstuvwxyz012345",
        "password=hunter2hunter2hunter2",
        "api_key: sk-abcdefghijklmnop0123456789",
        "AKIAIOSFODNN7EXAMPLE",
    ],
)
def test_secret_shapes_are_redacted(sentinel: str) -> None:
    """Each credential shape is masked and its literal value removed."""
    text = f"here is my secret {sentinel} please use it"
    redacted, findings = redact_credentials(text)
    assert sentinel not in redacted
    assert _REDACTED in redacted
    assert findings


def test_clean_prose_is_untouched() -> None:
    """Ordinary operator prose is returned unchanged with no findings."""
    text = "Please connect the GitHub integration for the platform team."
    redacted, findings = redact_credentials(text)
    assert redacted == text
    assert findings == ()


def test_only_the_secret_substring_is_masked() -> None:
    """Surrounding text survives exactly; only the secret run is masked."""
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345 trailing"
    redacted, _ = redact_credentials(text)
    assert redacted == "Authorization: [REDACTED] trailing"


def test_redaction_keeps_a_json_document_parseable() -> None:
    """A secret at a JSON string boundary is masked without breaking the JSON.

    ``park_service`` redacts the *serialised* context, so a value whose match
    swallows the surrounding quote must keep the quote intact; otherwise the
    parked context can no longer be json-loaded and the run cannot resume.
    """
    doc = json.dumps(
        {
            "note": "connect the github integration",
            "token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD",
            "keep": "ordinary value",
        }
    )
    redacted, findings = redact_credentials(doc)
    assert findings
    # Must not raise: the redacted document stays valid JSON.
    reparsed = json.loads(redacted)
    assert "ghp_" not in redacted
    assert reparsed["keep"] == "ordinary value"
    assert reparsed["note"] == "connect the github integration"
    assert _REDACTED in reparsed["token"]
