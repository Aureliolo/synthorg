"""Unit tests for cassette redaction.

Redaction scrubs the human-readable copy written to the cassette file
(defence-in-depth). It must never run on the bytes that feed the
replay key (verified in the provider-wrapper phase); here we pin the
redactor behaviour in isolation.
"""

import pytest

from synthorg.providers.cassette.redaction import (
    IMAGE_DATA_PLACEHOLDER,
    REDACTION_PLACEHOLDER,
    CassetteRedactor,
    NullRedactor,
    PatternRedactor,
)

pytestmark = pytest.mark.unit


class TestPatternRedactor:
    """The default redactor scrubs common secret shapes."""

    def test_bearer_token_scrubbed(self) -> None:
        out = PatternRedactor().redact(
            {"headers": "Authorization: Bearer abcDEF123456ghiJKL789mno"}
        )
        assert "abcDEF123456ghiJKL789mno" not in str(out)
        assert REDACTION_PLACEHOLDER in str(out)

    def test_sk_api_key_scrubbed(self) -> None:
        out = PatternRedactor().redact("my key is sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
        assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in str(out)
        assert REDACTION_PLACEHOLDER in str(out)

    def test_aws_access_key_scrubbed(self) -> None:
        out = PatternRedactor().redact("AKIAIOSFODNN7EXAMPLE here")
        assert "AKIAIOSFODNN7EXAMPLE" not in str(out)

    def test_pem_block_scrubbed(self) -> None:
        pem = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIBVgIBADANBgkqh\nkiG9w0BAQEFAASCAT\n"
            "-----END PRIVATE KEY-----"
        )
        out = PatternRedactor().redact({"key": pem})
        assert "MIIBVgIBADANBgkqh" not in str(out)
        assert REDACTION_PLACEHOLDER in str(out)

    def test_labelled_secret_value_scrubbed(self) -> None:
        out = PatternRedactor().redact('{"password": "hunter2supersecret"}')
        assert "hunter2supersecret" not in str(out)

    def test_plain_prompt_text_untouched(self) -> None:
        text = "Write a haiku about the sea and the morning sun."
        assert PatternRedactor().redact(text) == text

    def test_inline_image_data_uri_elided(self) -> None:
        # The multimodal mapper carries image bytes in image_url.url; the
        # value is a data: URI under a plain "url" key the field-name rule
        # never matches, so it must be elided by value shape.
        payload = {
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64," + "A" * 512,
                        "detail": "auto",
                    },
                },
            ],
        }
        out = PatternRedactor().redact(payload)
        assert isinstance(out, dict)
        assert "A" * 512 not in str(out)
        assert out["content"][0]["image_url"]["url"] == IMAGE_DATA_PLACEHOLDER
        # The sibling non-image field is untouched.
        assert out["content"][0]["image_url"]["detail"] == "auto"

    def test_nested_structure_traversed(self) -> None:
        payload = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "system", "content": "token=zzzSECRETtoken9999value"},
            ],
            "n": 3,
            "flag": True,
        }
        out = PatternRedactor().redact(payload)
        assert isinstance(out, dict)
        assert "zzzSECRETtoken9999value" not in str(out)
        # Non-string scalars and benign strings survive unchanged.
        assert out["n"] == 3
        assert out["flag"] is True
        assert out["messages"][0]["content"] == "hello"

    def test_does_not_mutate_input(self) -> None:
        payload = {"a": "Bearer SECRETtokenVALUE1234567890abc"}
        original = dict(payload)
        PatternRedactor().redact(payload)
        assert payload == original


class TestNullRedactor:
    """The opt-in faithful-capture redactor changes nothing."""

    def test_returns_equivalent_payload(self) -> None:
        payload = {"content": "Bearer abcDEF123456ghiJKL789mno", "n": 1}
        assert NullRedactor().redact(payload) == payload


class TestProtocolConformance:
    """Both redactors satisfy the runtime-checkable protocol; swap works."""

    def test_builtins_are_redactors(self) -> None:
        assert isinstance(PatternRedactor(), CassetteRedactor)
        assert isinstance(NullRedactor(), CassetteRedactor)

    def test_custom_redactor_is_pluggable(self) -> None:
        class UpperRedactor:
            def redact(self, payload: object) -> object:
                return str(payload).upper()

        red: CassetteRedactor = UpperRedactor()
        assert isinstance(red, CassetteRedactor)
        assert red.redact("abc") == "ABC"
