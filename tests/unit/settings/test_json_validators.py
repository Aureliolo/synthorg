"""Tests for the per-setting write-time JSON-shape validators."""

from collections.abc import Callable

import pytest
from pydantic import JsonValue

from synthorg.settings.json_validators import get_json_validator

pytestmark = pytest.mark.unit

_Validator = Callable[[JsonValue], None]


class TestCspDocsExternalOriginsJsonValidator:
    """Write-time validation for ``api.csp_docs_external_origins``.

    Reuses :class:`ApiBridgeConfig`'s field validator so /settings
    persistence cannot store a payload the runtime would later reject.
    """

    @pytest.fixture
    def validator(self) -> _Validator:
        v = get_json_validator("api", "csp_docs_external_origins")
        assert v is not None, "csp_docs_external_origins validator missing"
        return v

    def test_accepts_canonical_origins(self, validator: _Validator) -> None:
        validator(
            [
                "https://cdn.example.com",
                "https://internal-cdn.example.com:8443",
                "http://internal.example",
            ]
        )

    def test_rejects_non_array_payload(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="JSON array"):
            validator({"not": "an array"})

    def test_rejects_non_string_entry(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="must be strings"):
            validator(["https://cdn.example.com", 42])

    def test_rejects_empty_array(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="at least one trusted origin"):
            validator([])

    @pytest.mark.parametrize(
        "bad_origin",
        [
            "javascript:alert(1)",
            "ftp://example.com",
            "https://cdn.example.com/path",
            "https://cdn.example.com?q=1",
            "https://cdn.example.com#frag",
            "https://user:pw@cdn.example.com",
            "https://cdn.example.com:99999",
            "https://cdn.example.com:0",
        ],
        ids=[
            "javascript_scheme",
            "ftp_scheme",
            "with_path",
            "with_query",
            "with_fragment",
            "with_userinfo",
            "port_out_of_range",
            "port_zero",
        ],
    )
    def test_rejects_non_canonical_entry(
        self, validator: _Validator, bad_origin: str
    ) -> None:
        with pytest.raises(ValueError, match="csp_docs_external_origins"):
            validator(["https://cdn.example.com", bad_origin])


def test_unregistered_namespace_returns_none() -> None:
    assert get_json_validator("api", "definitely_not_a_setting") is None
    assert get_json_validator("missing_namespace", "csp_docs_external_origins") is None
