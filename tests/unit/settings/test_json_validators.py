"""Tests for the per-setting write-time JSON-shape validators."""

from collections.abc import Callable

import pytest
from pydantic import JsonValue

from synthorg.settings.json_validators import (
    _MAX_JSON_DEPTH,
    _MAX_RAW_JSON_DEPTH,
    _reject_deep_nesting,
    get_json_validator,
    reject_raw_json_over_depth,
)

pytestmark = pytest.mark.unit

_Validator = Callable[[JsonValue], None]


def _nest_lists(depth: int) -> JsonValue:
    """Return ``depth`` nested single-element lists around a scalar leaf.

    The leaf sits at nesting depth ``depth + 1`` (the outer list is depth 1),
    so ``_nest_lists(n)`` exceeds a ``max_depth`` cap of ``n`` at the leaf.
    """
    node: JsonValue = 0
    for _ in range(depth):
        node = [node]
    return node


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


class TestCompanyDepartmentsJsonValidator:
    """Write-time validation for ``company.departments``.

    Closes the generic-settings-write bypass of the ``Team`` validation the
    team CRUD path applies.
    """

    @pytest.fixture
    def validator(self) -> _Validator:
        v = get_json_validator("company", "departments")
        assert v is not None, "company/departments validator missing"
        return v

    def test_accepts_valid_departments(self, validator: _Validator) -> None:
        validator(
            [
                {"name": "Engineering", "teams": [{"name": "Core", "lead": "alice"}]},
                {"name": "Design"},
            ]
        )

    def test_rejects_non_array_payload(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="must be a JSON array"):
            validator({"name": "Engineering"})

    def test_rejects_department_without_name(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match=r"\.name must be a non-empty string"):
            validator([{"teams": []}])

    def test_rejects_blank_department_name(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match=r"\.name must be a non-empty string"):
            validator([{"name": "  "}])

    def test_rejects_non_list_teams(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="teams must be an array"):
            validator([{"name": "Engineering", "teams": {"not": "a list"}}])

    def test_rejects_invalid_team(self, validator: _Validator) -> None:
        # Missing the required ``lead`` field -> ``Team`` validation fails.
        with pytest.raises(ValueError, match="is not a valid team"):
            validator([{"name": "Engineering", "teams": [{"name": "Core"}]}])

    def test_rejects_deeply_nested_payload(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="nests deeper"):
            validator(_nest_lists(_MAX_JSON_DEPTH + 1))


class TestCompanyAgentsJsonValidator:
    """Write-time validation for ``company.agents``."""

    @pytest.fixture
    def validator(self) -> _Validator:
        v = get_json_validator("company", "agents")
        assert v is not None, "company/agents validator missing"
        return v

    def test_accepts_valid_agents(self, validator: _Validator) -> None:
        validator([{"name": "alice", "role": "engineer"}])

    def test_rejects_non_array_payload(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="must be a JSON array"):
            validator({"name": "alice"})

    def test_rejects_missing_required_keys(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="missing required keys"):
            validator([{"name": "alice"}])

    def test_rejects_blank_required_field(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="must be a non-empty string"):
            validator([{"name": "alice", "role": "  "}])


class TestAskPolicyExtraDirectivesJsonValidator:
    """Write-time validation for ``engine.ask_policy_extra_directives``."""

    @pytest.fixture
    def validator(self) -> _Validator:
        v = get_json_validator("engine", "ask_policy_extra_directives")
        assert v is not None, "ask_policy_extra_directives validator missing"
        return v

    def test_accepts_empty_array(self, validator: _Validator) -> None:
        validator([])

    def test_accepts_a_scoped_directive(self, validator: _Validator) -> None:
        validator(
            [
                {
                    "id": "x_eng",
                    "text": "Ask before a schema change.",
                    "scope": "Engineering",
                    "scope_kind": "department",
                }
            ]
        )

    def test_rejects_non_array_payload(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="must be a JSON array"):
            validator({"id": "x", "text": "y"})

    def test_rejects_missing_text(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="not a valid directive"):
            validator([{"id": "x"}])

    def test_rejects_blank_text(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="not a valid directive"):
            validator([{"id": "x", "text": "   "}])

    def test_rejects_unknown_scope_kind(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="not a valid directive"):
            validator([{"id": "x", "text": "y", "scope": "z", "scope_kind": "team"}])

    def test_rejects_inconsistent_scope_pairing(self, validator: _Validator) -> None:
        with pytest.raises(ValueError, match="not a valid directive"):
            validator([{"id": "x", "text": "y", "scope": "all", "scope_kind": "role"}])


class TestDeepNestingGuards:
    """The post-parse (``_reject_deep_nesting``) and pre-parse
    (``reject_raw_json_over_depth``) depth guards.
    """

    def test_reject_deep_nesting_accepts_at_cap(self) -> None:
        # Leaf at exactly the cap must pass.
        _reject_deep_nesting(_nest_lists(_MAX_JSON_DEPTH - 1), "departments")

    def test_reject_deep_nesting_rejects_over_cap(self) -> None:
        with pytest.raises(ValueError, match="nests deeper"):
            _reject_deep_nesting(_nest_lists(_MAX_JSON_DEPTH + 1), "departments")

    def test_raw_guard_accepts_at_cap(self) -> None:
        reject_raw_json_over_depth(
            "[" * _MAX_RAW_JSON_DEPTH + "]" * _MAX_RAW_JSON_DEPTH
        )

    def test_raw_guard_rejects_over_cap(self) -> None:
        with pytest.raises(ValueError, match="nests deeper"):
            reject_raw_json_over_depth("[" * (_MAX_RAW_JSON_DEPTH + 1))

    def test_raw_guard_ignores_brackets_inside_strings(self) -> None:
        # Brackets inside a JSON string literal are not nesting.
        reject_raw_json_over_depth('["' + "[" * 200 + '"]')

    def test_raw_guard_ignores_escaped_quote_in_string(self) -> None:
        # An escaped quote must not prematurely close the string, so the
        # bracket run after it stays inside the string and uncounted.
        reject_raw_json_over_depth('["a\\"' + "[" * 200 + '"]')


def test_unregistered_namespace_returns_none() -> None:
    assert get_json_validator("api", "definitely_not_a_setting") is None
    assert get_json_validator("missing_namespace", "csp_docs_external_origins") is None
