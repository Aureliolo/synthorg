"""Unit tests for the ``Plan`` / ``PlanItem`` domain invariants."""

from datetime import UTC, datetime

import pytest

from synthorg.core.plan import Plan, PlanItem
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
    )


def _plan(items: tuple[PlanItem, ...]) -> Plan:
    return Plan(
        id=as_uuid("plan"),
        project=NotBlankStr("beachhead"),
        objective_id=NotBlankStr("obj"),
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
            )

    def test_rejects_self_dependency(self) -> None:
        with pytest.raises(ValueError, match="cannot depend on itself"):
            _item("a", dependencies=(sid("a"),))

    def test_rejects_duplicate_dependencies(self) -> None:
        with pytest.raises(ValueError, match="duplicate dependencies"):
            _item("a", dependencies=(sid("b"), sid("b")))


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
