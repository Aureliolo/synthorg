"""Property-based tests for API DTO validation constraints."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from synthorg.api.dto import (
    _MAX_METADATA_KEYS,
    _MAX_METADATA_STR_LEN,
    CoordinateTaskRequest,
    CreateApprovalRequest,
)
from synthorg.core.enums import ApprovalRiskLevel

pytestmark = pytest.mark.unit

_not_blank = st.text(min_size=1, max_size=30).filter(lambda s: s.strip())
_risk_levels = st.sampled_from(ApprovalRiskLevel)

# Action types must match category:action format
_action_types = st.from_regex(r"[a-z0-9_-]{1,10}:[a-z0-9_-]{1,10}", fullmatch=True)


class TestCoordinateTaskRequestProperties:
    @given(
        names=st.lists(
            _not_blank,
            min_size=2,
            max_size=10,
        ),
    )
    @settings(max_examples=100)
    def test_case_insensitive_uniqueness(
        self,
        names: list[str],
    ) -> None:
        # Check if names have case-insensitive duplicates
        lower_names = [n.lower() for n in names]
        has_dupes = len(lower_names) != len(set(lower_names))
        if has_dupes:
            with pytest.raises(ValidationError, match="Duplicate agent name"):
                CoordinateTaskRequest(agent_names=tuple(names))
        else:
            req = CoordinateTaskRequest(agent_names=tuple(names))
            assert req.agent_names == tuple(names)

    @given(name=_not_blank)
    @settings(max_examples=50)
    def test_single_agent_always_valid(self, name: str) -> None:
        req = CoordinateTaskRequest(agent_names=(name,))
        assert req.agent_names == (name,)

    def test_none_agent_names_valid(self) -> None:
        req = CoordinateTaskRequest()
        assert req.agent_names is None

    @given(name=_not_blank)
    @settings(max_examples=50)
    def test_duplicate_same_case_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError, match="Duplicate agent name"):
            CoordinateTaskRequest(agent_names=(name, name))

    @given(
        name=st.text(
            alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz")),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=50)
    def test_duplicate_different_case_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError, match="Duplicate agent name"):
            CoordinateTaskRequest(
                agent_names=(name.lower(), name.upper()),
            )

    @given(
        max_subtasks=st.integers(min_value=1, max_value=50),
        max_conc=st.one_of(
            st.none(),
            st.integers(min_value=1, max_value=50),
        ),
        fail_fast=st.one_of(st.none(), st.booleans()),
    )
    @settings(max_examples=50)
    def test_valid_config_roundtrip(
        self,
        max_subtasks: int,
        max_conc: int | None,
        fail_fast: bool | None,
    ) -> None:
        req = CoordinateTaskRequest(
            max_subtasks=max_subtasks,
            max_concurrency_per_wave=max_conc,
            fail_fast=fail_fast,
        )
        dumped = req.model_dump()
        restored = CoordinateTaskRequest.model_validate(dumped)
        assert restored == req


class TestCreateApprovalRequestProperties:
    @given(
        action_type=_action_types,
        title=_not_blank,
        description=_not_blank,
        risk_level=_risk_levels,
    )
    @settings(max_examples=50)
    def test_valid_request_roundtrip(
        self,
        action_type: str,
        title: str,
        description: str,
        risk_level: ApprovalRiskLevel,
    ) -> None:
        # No assume() needed — _not_blank strategy has max_size=30,
        # which is always within the 256/4096 field limits.
        req = CreateApprovalRequest(
            action_type=action_type,
            title=title,
            description=description,
            risk_level=risk_level,
        )
        dumped = req.model_dump()
        restored = CreateApprovalRequest.model_validate(dumped)
        assert restored == req

    @given(
        num_keys=st.integers(
            min_value=_MAX_METADATA_KEYS + 1,
            max_value=_MAX_METADATA_KEYS + 5,
        ),
    )
    @settings(max_examples=10)
    def test_too_many_metadata_keys_rejected(self, num_keys: int) -> None:
        metadata = {f"key-{i}": f"val-{i}" for i in range(num_keys)}
        with pytest.raises(
            ValidationError,
            match=f"at most {_MAX_METADATA_KEYS} keys",
        ):
            CreateApprovalRequest(
                action_type="test:action",
                title="Test",
                description="A test",
                risk_level=ApprovalRiskLevel.LOW,
                metadata=metadata,
            )

    @given(
        key_len=st.integers(
            min_value=_MAX_METADATA_STR_LEN + 1,
            max_value=_MAX_METADATA_STR_LEN + 50,
        ),
    )
    @settings(max_examples=10)
    def test_long_metadata_key_rejected(self, key_len: int) -> None:
        long_key = "k" * key_len
        with pytest.raises(
            ValidationError,
            match=f"at most {_MAX_METADATA_STR_LEN} characters",
        ):
            CreateApprovalRequest(
                action_type="test:action",
                title="Test",
                description="A test",
                risk_level=ApprovalRiskLevel.LOW,
                metadata={long_key: "value"},
            )

    @given(
        val_len=st.integers(
            min_value=_MAX_METADATA_STR_LEN + 1,
            max_value=_MAX_METADATA_STR_LEN + 50,
        ),
    )
    @settings(max_examples=10)
    def test_long_metadata_value_rejected(self, val_len: int) -> None:
        long_val = "v" * val_len
        with pytest.raises(
            ValidationError,
            match=f"at most {_MAX_METADATA_STR_LEN} characters",
        ):
            CreateApprovalRequest(
                action_type="test:action",
                title="Test",
                description="A test",
                risk_level=ApprovalRiskLevel.LOW,
                metadata={"key": long_val},
            )

    def test_invalid_action_type_format_rejected(self) -> None:
        with pytest.raises(ValidationError, match="category:action"):
            CreateApprovalRequest(
                action_type="nocolon",
                title="Test",
                description="A test",
                risk_level=ApprovalRiskLevel.LOW,
            )

    @given(
        metadata=st.dictionaries(
            st.text(min_size=1, max_size=20, alphabet="abcdefghij"),
            st.text(min_size=1, max_size=20, alphabet="abcdefghij"),
            max_size=_MAX_METADATA_KEYS,
        ),
    )
    @settings(max_examples=50)
    def test_valid_metadata_accepted(self, metadata: dict[str, str]) -> None:
        req = CreateApprovalRequest(
            action_type="test:action",
            title="Test",
            description="A test",
            risk_level=ApprovalRiskLevel.LOW,
            metadata=metadata,
        )
        assert req.metadata == metadata
