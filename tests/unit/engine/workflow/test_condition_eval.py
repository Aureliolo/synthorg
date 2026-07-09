"""Tests for safe workflow condition expression evaluator."""

import pytest

from synthorg.engine.workflow.condition_eval import evaluate_condition


class TestEvaluateCondition:
    """Safe condition expression evaluator."""

    # ── Boolean literals ──────────────────────────────────────────

    @pytest.mark.unit
    def test_true_literal(self) -> None:
        assert evaluate_condition("true", {}) is True

    @pytest.mark.unit
    def test_false_literal(self) -> None:
        assert evaluate_condition("false", {}) is False

    @pytest.mark.unit
    def test_true_case_insensitive(self) -> None:
        assert evaluate_condition("True", {}) is True

    @pytest.mark.unit
    def test_false_case_insensitive(self) -> None:
        assert evaluate_condition("FALSE", {}) is False

    # ── Key lookup (truthy check) ─────────────────────────────────

    @pytest.mark.unit
    def test_key_truthy_string(self) -> None:
        assert evaluate_condition("has_budget", {"has_budget": "yes"}) is True

    @pytest.mark.unit
    def test_key_truthy_true(self) -> None:
        assert evaluate_condition("enabled", {"enabled": True}) is True

    @pytest.mark.unit
    def test_key_truthy_nonzero(self) -> None:
        assert evaluate_condition("count", {"count": 5}) is True

    @pytest.mark.unit
    def test_key_falsy_false(self) -> None:
        assert evaluate_condition("enabled", {"enabled": False}) is False

    @pytest.mark.unit
    def test_key_falsy_zero(self) -> None:
        assert evaluate_condition("count", {"count": 0}) is False

    @pytest.mark.unit
    def test_key_falsy_empty_string(self) -> None:
        assert evaluate_condition("name", {"name": ""}) is False

    @pytest.mark.unit
    def test_key_falsy_none(self) -> None:
        assert evaluate_condition("val", {"val": None}) is False

    @pytest.mark.unit
    def test_missing_key_returns_false(self) -> None:
        assert evaluate_condition("missing", {}) is False

    # ── Equality comparison ───────────────────────────────────────

    @pytest.mark.unit
    def test_equality_match(self) -> None:
        assert (
            evaluate_condition(
                "priority == high",
                {"priority": "high"},
            )
            is True
        )

    @pytest.mark.unit
    def test_equality_mismatch(self) -> None:
        assert (
            evaluate_condition(
                "priority == high",
                {"priority": "low"},
            )
            is False
        )

    @pytest.mark.unit
    def test_equality_missing_key(self) -> None:
        assert evaluate_condition("env == prod", {}) is False

    @pytest.mark.unit
    def test_equality_extra_spaces(self) -> None:
        assert (
            evaluate_condition(
                "  env  ==  staging  ",
                {"env": "staging"},
            )
            is True
        )

    # ── Inequality comparison ─────────────────────────────────────

    @pytest.mark.unit
    def test_inequality_match(self) -> None:
        assert (
            evaluate_condition(
                "env != prod",
                {"env": "staging"},
            )
            is True
        )

    @pytest.mark.unit
    def test_inequality_mismatch(self) -> None:
        assert (
            evaluate_condition(
                "env != prod",
                {"env": "prod"},
            )
            is False
        )

    @pytest.mark.unit
    def test_inequality_missing_key(self) -> None:
        assert evaluate_condition("env != prod", {}) is True

    # ── Whitespace handling ───────────────────────────────────────

    @pytest.mark.unit
    def test_whitespace_stripped(self) -> None:
        assert evaluate_condition("  true  ", {}) is True

    @pytest.mark.unit
    def test_key_whitespace_stripped(self) -> None:
        assert evaluate_condition("  enabled  ", {"enabled": True}) is True

    # ── Empty and invalid expressions ─────────────────────────────

    @pytest.mark.unit
    def test_empty_expression_returns_false(self) -> None:
        assert evaluate_condition("", {}) is False

    @pytest.mark.unit
    def test_whitespace_only_returns_false(self) -> None:
        assert evaluate_condition("   ", {}) is False

    # ── Safety: no code execution ─────────────────────────────────

    @pytest.mark.unit
    def test_import_expression_rejected_never_executed(self) -> None:
        """A dangerous expression is rejected as malformed, never executed.

        The parenthesised call shape parses as a structurally malformed
        compound expression (trailing tokens after the atom), so it fails
        loud rather than silently resolving to a key lookup -- and the
        argument is never passed to ``eval``/``exec``.
        """
        with pytest.raises(ValueError, match=r"[Mm]alformed"):
            evaluate_condition("__import__('os')", {})

    @pytest.mark.unit
    def test_eval_expression_rejected_never_executed(self) -> None:
        with pytest.raises(ValueError, match=r"[Mm]alformed"):
            evaluate_condition("eval('1+1')", {})

    @pytest.mark.unit
    def test_bare_dangerous_identifier_is_inert_key_lookup(self) -> None:
        """A parenless dangerous-looking token is an inert missing-key lookup."""
        assert evaluate_condition("__import__", {}) is False


@pytest.mark.unit
class TestCompoundConditions:
    """Compound expression evaluation (AND / OR / NOT / parentheses)."""

    @pytest.mark.parametrize(
        ("expression", "ctx", "expected"),
        [
            pytest.param(
                "priority == high AND env == prod",
                {"priority": "high", "env": "prod"},
                True,
                id="and-both-true",
            ),
            pytest.param(
                "priority == high AND env == prod",
                {"priority": "low", "env": "prod"},
                False,
                id="and-left-false",
            ),
            pytest.param(
                "priority == high AND env == staging2",
                {"priority": "high", "env": "staging"},
                False,
                id="and-right-false",
            ),
            pytest.param("a == 1 AND b == 2", {}, False, id="and-both-false"),
        ],
    )
    def test_and(self, expression: str, ctx: dict[str, str], expected: bool) -> None:
        assert evaluate_condition(expression, ctx) is expected

    @pytest.mark.parametrize(
        ("expression", "ctx", "expected"),
        [
            pytest.param(
                "a == 1 OR b == 2", {"a": "1", "b": "2"}, True, id="or-both-true"
            ),
            pytest.param("a == 1 OR b == 2", {"a": "1"}, True, id="or-left-true"),
            pytest.param("a == 1 OR b == 2", {"b": "2"}, True, id="or-right-true"),
            pytest.param("a == 1 OR b == 2", {}, False, id="or-both-false"),
        ],
    )
    def test_or(self, expression: str, ctx: dict[str, str], expected: bool) -> None:
        assert evaluate_condition(expression, ctx) is expected

    @pytest.mark.parametrize(
        ("expression", "ctx", "expected"),
        [
            pytest.param("NOT true", {}, False, id="not-true"),
            pytest.param("NOT false", {}, True, id="not-false"),
            pytest.param("NOT enabled", {"enabled": True}, False, id="not-key-truthy"),
            pytest.param("NOT enabled", {"enabled": False}, True, id="not-key-falsy"),
            pytest.param(
                "NOT priority == low",
                {"priority": "high"},
                True,
                id="not-comparison-true",
            ),
            pytest.param(
                "NOT priority == low",
                {"priority": "low"},
                False,
                id="not-comparison-false",
            ),
        ],
    )
    def test_not(self, expression: str, ctx: dict[str, object], expected: bool) -> None:
        assert evaluate_condition(expression, ctx) is expected

    @pytest.mark.parametrize(
        ("expression", "ctx", "expected"),
        [
            pytest.param(
                "a == 1 OR b == 2 AND c == 3",
                {"a": "1"},
                True,
                id="and-binds-tighter-true",
            ),
            pytest.param(
                "a == 1 OR b == 2 AND c == 3",
                {"b": "2"},
                False,
                id="and-binds-tighter-false",
            ),
            pytest.param(
                "(a == 1 OR b == 2) AND c == 3",
                {"a": "1"},
                False,
                id="parens-override-false",
            ),
            pytest.param(
                "(a == 1 OR b == 2) AND c == 3",
                {"a": "1", "c": "3"},
                True,
                id="parens-override-true",
            ),
        ],
    )
    def test_precedence_and_parens(
        self, expression: str, ctx: dict[str, str], expected: bool
    ) -> None:
        assert evaluate_condition(expression, ctx) is expected

    def test_nested_parens(self) -> None:
        ctx = {"a": "1", "b": "2", "c": "3"}
        result = evaluate_condition("NOT (a == 1 AND (b == 2 OR c == 99))", ctx)
        # a==1 AND (b==2 OR c==99) -> True AND True -> True; NOT True -> False
        assert result is False

    def test_nested_parens_inner_false(self) -> None:
        ctx: dict[str, str] = {"a": "1"}
        result = evaluate_condition("NOT (a == 1 AND (b == 2 OR c == 99))", ctx)
        # a==1 AND (False OR False) -> True AND False -> False; NOT False -> True
        assert result is True

    @pytest.mark.parametrize(
        ("expression", "ctx", "expected"),
        [
            pytest.param(
                "a == 1 and b == 2",
                {"a": "1", "b": "2"},
                True,
                id="lowercase",
            ),
            pytest.param("a == 1 Or not b", {"a": "1"}, True, id="mixed-case"),
        ],
    )
    def test_case_insensitive_operators(
        self, expression: str, ctx: dict[str, str], expected: bool
    ) -> None:
        assert evaluate_condition(expression, ctx) is expected

    @pytest.mark.parametrize(
        ("expression", "ctx"),
        [
            pytest.param("brand == ORLANDO", {"brand": "ORLANDO"}, id="and"),
            pytest.param("city == YORK", {"city": "YORK"}, id="or"),
            pytest.param("tag == NOTICE", {"tag": "NOTICE"}, id="not"),
        ],
    )
    def test_keyword_in_value(self, expression: str, ctx: dict[str, str]) -> None:
        assert evaluate_condition(expression, ctx) is True

    @pytest.mark.parametrize(
        ("expression", "ctx"),
        [
            pytest.param("() AND true", {}, id="empty-parens"),
            pytest.param("AND AND", {}, id="double-operator"),
            pytest.param("true AND", {}, id="trailing-operator"),
            pytest.param("AND true", {}, id="leading-operator"),
            pytest.param(
                "(a == 1 AND b == 2",
                {"a": "1", "b": "2"},
                id="unclosed-paren",
            ),
        ],
    )
    def test_malformed_raises(self, expression: str, ctx: dict[str, str]) -> None:
        """A structurally malformed expression fails loud, not silently False.

        The workflow-activation caller wraps this ``ValueError`` in a typed
        ``WorkflowConditionEvalError`` rather than silently taking the
        condition-not-met edge.
        """
        with pytest.raises(ValueError, match=r"[Mm]alformed|operand|paren|Unexpected"):
            evaluate_condition(expression, ctx)

    def test_double_not(self) -> None:
        assert evaluate_condition("NOT NOT true", {}) is True

    @pytest.mark.parametrize(
        ("expression", "ctx", "expected"),
        [
            pytest.param(
                "has_budget AND (priority == high OR priority == critical)"
                " AND NOT env == prod",
                {"has_budget": "yes", "priority": "critical", "env": "staging"},
                True,
                id="complex-true",
            ),
            pytest.param(
                "has_budget AND (priority == high OR priority == critical)"
                " AND NOT env == prod",
                {"has_budget": "yes", "priority": "low", "env": "staging"},
                False,
                id="complex-false",
            ),
        ],
    )
    def test_complex_expression(
        self, expression: str, ctx: dict[str, str], expected: bool
    ) -> None:
        assert evaluate_condition(expression, ctx) is expected

    # ── Backward compatibility ────────────────────────────────────

    def test_simple_expressions_still_work(self) -> None:
        """Ensure the quick-path for simple expressions is intact."""
        assert evaluate_condition("true", {}) is True
        assert evaluate_condition("false", {}) is False
        assert evaluate_condition("x == 1", {"x": "1"}) is True
        assert evaluate_condition("missing", {}) is False
        assert evaluate_condition("", {}) is False
