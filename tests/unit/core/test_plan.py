"""Unit tests for the ``Plan`` / ``PlanItem`` domain invariants."""

from datetime import UTC, datetime

import pytest

from synthorg.core.plan import Plan, PlanItem, PlanOption
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_CREATED_AT = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def _item(label: str, *, dependencies: tuple[str, ...] = ()) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Item {label}"),
        description=NotBlankStr(f"Description for {label}"),
        dependencies=tuple(NotBlankStr(d) for d in dependencies),
        acceptance_criteria=(NotBlankStr(f"{label} is done"),),
    )


def _plan(items: tuple[PlanItem, ...]) -> Plan:
    return Plan(
        id=as_uuid("plan"),
        project=NotBlankStr("beachhead"),
        objective_id=NotBlankStr("obj"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr("root"),
        items=items,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


class TestPlanItemInvariants:
    def test_rejects_non_uuid_id(self) -> None:
        with pytest.raises(ValueError, match="canonical UUID"):
            PlanItem(
                id=NotBlankStr("not-a-uuid"),
                title=NotBlankStr("X"),
                description=NotBlankStr("Y"),
                acceptance_criteria=(NotBlankStr("done"),),
            )

    def test_rejects_empty_acceptance_criteria(self) -> None:
        with pytest.raises(ValueError, match="acceptance_criteria"):
            PlanItem(
                id=NotBlankStr(sid("a")),
                title=NotBlankStr("X"),
                description=NotBlankStr("Y"),
                acceptance_criteria=(),
            )

    def test_rejects_self_dependency(self) -> None:
        with pytest.raises(ValueError, match="cannot depend on itself"):
            _item("a", dependencies=(sid("a"),))

    def test_rejects_duplicate_dependencies(self) -> None:
        with pytest.raises(ValueError, match="duplicate dependencies"):
            _item("a", dependencies=(sid("b"), sid("b")))


def _decision_item(
    label: str,
    *,
    options: tuple[PlanOption, ...],
    chosen_option_id: str | None = None,
) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Decide {label}"),
        description=NotBlankStr(f"Choice for {label}"),
        acceptance_criteria=(NotBlankStr("decision recorded"),),
        kind=PlanItemKind.DECISION,
        options=options,
        chosen_option_id=chosen_option_id,
    )


def _opt(oid: str, *, recommended: bool = False) -> PlanOption:
    return PlanOption(
        id=NotBlankStr(oid),
        title=NotBlankStr(f"Option {oid}"),
        summary=NotBlankStr(f"Tradeoffs for {oid}"),
        recommended=recommended,
    )


class TestDecisionItem:
    def test_accepts_a_well_formed_decision(self) -> None:
        item = _decision_item(
            "a", options=(_opt("react", recommended=True), _opt("svelte"))
        )
        assert item.kind is PlanItemKind.DECISION
        assert len(item.options) == 2

    def test_work_item_rejects_options(self) -> None:
        with pytest.raises(ValueError, match="WORK but carries decision options"):
            PlanItem(
                id=NotBlankStr(sid("a")),
                title=NotBlankStr("X"),
                description=NotBlankStr("Y"),
                acceptance_criteria=(NotBlankStr("done"),),
                options=(_opt("o1", recommended=True), _opt("o2")),
            )

    def test_decision_needs_two_options(self) -> None:
        with pytest.raises(ValueError, match="at least two options"):
            _decision_item("a", options=(_opt("only", recommended=True),))

    def test_decision_needs_exactly_one_recommended(self) -> None:
        with pytest.raises(ValueError, match="exactly one recommended"):
            _decision_item("a", options=(_opt("o1"), _opt("o2")))

    def test_decision_rejects_duplicate_option_ids(self) -> None:
        with pytest.raises(ValueError, match="duplicate option ids"):
            _decision_item(
                "a",
                options=(_opt("dup", recommended=True), _opt("dup")),
            )

    def test_decision_rejects_unknown_chosen_option(self) -> None:
        with pytest.raises(ValueError, match="chose an unknown option"):
            _decision_item(
                "a",
                options=(_opt("o1", recommended=True), _opt("o2")),
                chosen_option_id="ghost",
            )

    def test_resolved_option_prefers_the_chosen_pick(self) -> None:
        item = _decision_item(
            "a",
            options=(_opt("o1", recommended=True), _opt("o2")),
            chosen_option_id="o2",
        )
        resolved = item.resolved_option()
        assert resolved is not None
        assert resolved.id == "o2"

    def test_resolved_option_falls_back_to_recommended(self) -> None:
        item = _decision_item("a", options=(_opt("o1"), _opt("o2", recommended=True)))
        resolved = item.resolved_option()
        assert resolved is not None
        assert resolved.id == "o2"

    def test_resolved_option_is_none_for_work_item(self) -> None:
        assert _item("a").resolved_option() is None


class TestPlanInvariants:
    def test_rejects_empty_items(self) -> None:
        with pytest.raises(ValueError, match="at least one item"):
            _plan(())

    def test_rejects_duplicate_item_ids(self) -> None:
        with pytest.raises(ValueError, match="duplicate plan item ids"):
            _plan((_item("a"), _item("a")))

    def test_rejects_unresolvable_dependency(self) -> None:
        with pytest.raises(ValueError, match="unknown items"):
            _plan((_item("a", dependencies=(sid("ghost"),)),))

    def test_rejects_two_node_cycle(self) -> None:
        with pytest.raises(ValueError, match="dependency cycle"):
            _plan(
                (
                    _item("a", dependencies=(sid("b"),)),
                    _item("b", dependencies=(sid("a"),)),
                )
            )

    def test_rejects_three_node_cycle(self) -> None:
        with pytest.raises(ValueError, match="dependency cycle"):
            _plan(
                (
                    _item("a", dependencies=(sid("b"),)),
                    _item("b", dependencies=(sid("c"),)),
                    _item("c", dependencies=(sid("a"),)),
                )
            )

    def test_accepts_valid_dag(self) -> None:
        plan = _plan(
            (
                _item("a"),
                _item("b", dependencies=(sid("a"),)),
                _item("c", dependencies=(sid("a"), sid("b"))),
            )
        )
        assert len(plan.items) == 3
