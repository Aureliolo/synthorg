"""Tests for the deterministic test-id helpers (``as_uuid`` / ``sid`` /
``coerce_id`` / ``as_pk``).

These guard the cross-shape invariants that callers rely on: a single
label must yield the same id whether it surfaces as a typed ``UUID`` PK
(``as_pk`` / ``as_uuid``) or as a canonical-string FK / wire value
(``sid`` / ``coerce_id``).
"""

from uuid import UUID

import pytest

from tests._shared import as_pk, as_uuid, coerce_id, sid

pytestmark = pytest.mark.unit


class TestAsUuidAndSid:
    def test_as_uuid_is_deterministic(self) -> None:
        assert as_uuid("task-1") == as_uuid("task-1")

    def test_distinct_labels_distinct_uuids(self) -> None:
        assert as_uuid("task-1") != as_uuid("task-2")

    def test_sid_is_canonical_string_of_as_uuid(self) -> None:
        assert sid("task-1") == str(as_uuid("task-1"))


class TestCoerceId:
    def test_label_maps_via_sid(self) -> None:
        assert coerce_id("task-1") == sid("task-1")

    def test_canonical_string_passes_through(self) -> None:
        canonical = str(as_uuid("task-1"))
        assert coerce_id(canonical) == canonical

    def test_uuid_instance_passes_through(self) -> None:
        value = as_uuid("task-1")
        assert coerce_id(value) == str(value)

    def test_non_uuid_non_str_raises(self) -> None:
        with pytest.raises(TypeError):
            coerce_id(123)


class TestAsPk:
    def test_returns_uuid_instance(self) -> None:
        assert isinstance(as_pk("task-1"), UUID)

    def test_label_matches_as_uuid(self) -> None:
        # The PK minted from a label must equal the label's ``as_uuid``,
        # so a constructor-side ``as_pk`` and an assertion-side ``as_uuid``
        # compare equal for the same label.
        assert as_pk("task-1") == as_uuid("task-1")

    def test_canonical_string_passthrough_is_idempotent(self) -> None:
        # The passthrough branch: a caller that already holds the canonical
        # string (e.g. threaded through ``sid``) must not be re-hashed.
        assert as_pk(sid("task-1")) == as_uuid("task-1")

    def test_uuid_instance_passthrough_is_idempotent(self) -> None:
        value = as_uuid("task-1")
        assert as_pk(value) == value

    def test_str_of_as_pk_equals_sid(self) -> None:
        # The PK and the FK/wire form are two shapes of one id.
        assert str(as_pk("task-1")) == sid("task-1")
