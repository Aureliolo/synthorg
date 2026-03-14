"""Property-based tests for budget configuration validation constraints."""

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from synthorg.budget.config import (
    AutoDowngradeConfig,
    BudgetAlertConfig,
    BudgetConfig,
)

pytestmark = pytest.mark.unit

_pct = st.integers(min_value=0, max_value=100)
_not_blank = st.text(min_size=1, max_size=20).filter(lambda s: s.strip())


class TestBudgetAlertConfigProperties:
    @given(warn=_pct, critical=_pct, hard_stop=_pct)
    @settings(max_examples=200)
    def test_threshold_ordering_invariant(
        self,
        warn: int,
        critical: int,
        hard_stop: int,
    ) -> None:
        if warn < critical < hard_stop:
            cfg = BudgetAlertConfig(
                warn_at=warn,
                critical_at=critical,
                hard_stop_at=hard_stop,
            )
            assert cfg.warn_at < cfg.critical_at < cfg.hard_stop_at
        else:
            with pytest.raises(ValidationError):
                BudgetAlertConfig(
                    warn_at=warn,
                    critical_at=critical,
                    hard_stop_at=hard_stop,
                )

    @given(
        data=st.lists(
            st.integers(min_value=0, max_value=100),
            min_size=3,
            max_size=3,
        )
        .map(sorted)
        .filter(lambda xs: xs[0] < xs[1] < xs[2]),
    )
    @settings(max_examples=100)
    def test_valid_config_roundtrip(self, data: list[int]) -> None:
        warn, critical, hard_stop = data
        cfg = BudgetAlertConfig(
            warn_at=warn,
            critical_at=critical,
            hard_stop_at=hard_stop,
        )
        dumped = cfg.model_dump()
        restored = BudgetAlertConfig.model_validate(dumped)
        assert restored == cfg


class TestAutoDowngradeConfigProperties:
    @given(
        enabled=st.booleans(),
        threshold=_pct,
    )
    @settings(max_examples=50)
    def test_basic_config_roundtrip(
        self,
        enabled: bool,
        threshold: int,
    ) -> None:
        cfg = AutoDowngradeConfig(enabled=enabled, threshold=threshold)
        dumped = cfg.model_dump()
        restored = AutoDowngradeConfig.model_validate(dumped)
        assert restored == cfg

    @given(source=_not_blank)
    @settings(max_examples=50)
    def test_self_downgrade_rejected(self, source: str) -> None:
        with pytest.raises(ValidationError, match="Self-downgrade"):
            AutoDowngradeConfig(
                enabled=True,
                downgrade_map=((source, source),),
            )

    @given(source=_not_blank, target1=_not_blank, target2=_not_blank)
    @settings(max_examples=50)
    def test_duplicate_source_rejected(
        self,
        source: str,
        target1: str,
        target2: str,
    ) -> None:
        assume(source != target1)
        assume(source != target2)
        with pytest.raises(ValidationError, match="Duplicate source"):
            AutoDowngradeConfig(
                enabled=True,
                downgrade_map=((source, target1), (source, target2)),
            )

    @given(
        pairs=st.lists(
            st.tuples(_not_blank, _not_blank),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=100)
    def test_valid_map_accepted_or_expected_rejection(
        self,
        pairs: list[tuple[str, str]],
    ) -> None:
        has_self_downgrade = any(s == t for s, t in pairs)
        sources = [s for s, _ in pairs]
        has_dup_source = len(sources) != len(set(sources))

        if has_self_downgrade or has_dup_source:
            with pytest.raises(ValidationError):
                AutoDowngradeConfig(
                    enabled=True,
                    downgrade_map=tuple(pairs),
                )
        else:
            cfg = AutoDowngradeConfig(
                enabled=True,
                downgrade_map=tuple(pairs),
            )
            out_sources = [s for s, _ in cfg.downgrade_map]
            assert len(out_sources) == len(set(out_sources))
            for s, t in cfg.downgrade_map:
                assert s != t


class TestBudgetConfigProperties:
    @given(
        total=st.floats(
            min_value=0.0,
            max_value=10_000.0,
            allow_nan=False,
        ),
        per_task=st.floats(
            min_value=0.0,
            max_value=10_000.0,
            allow_nan=False,
        ),
        per_agent=st.floats(
            min_value=0.0,
            max_value=10_000.0,
            allow_nan=False,
        ),
        reset_day=st.integers(min_value=1, max_value=28),
    )
    @settings(max_examples=100)
    def test_budget_config_validation(
        self,
        total: float,
        per_task: float,
        per_agent: float,
        reset_day: int,
    ) -> None:
        valid_task = total == 0 or per_task <= total
        valid_agent = total == 0 or per_agent <= total
        if valid_task and valid_agent:
            cfg = BudgetConfig(
                total_monthly=total,
                per_task_limit=per_task,
                per_agent_daily_limit=per_agent,
                reset_day=reset_day,
            )
            assert cfg.total_monthly == total
            assert cfg.per_task_limit == per_task
            assert cfg.per_agent_daily_limit == per_agent
        else:
            with pytest.raises(ValidationError):
                BudgetConfig(
                    total_monthly=total,
                    per_task_limit=per_task,
                    per_agent_daily_limit=per_agent,
                    reset_day=reset_day,
                )

    @given(
        total=st.floats(min_value=10.0, max_value=1000.0, allow_nan=False),
        reset_day=st.integers(min_value=1, max_value=28),
    )
    @settings(max_examples=50)
    def test_valid_budget_roundtrip(
        self,
        total: float,
        reset_day: int,
    ) -> None:
        cfg = BudgetConfig(
            total_monthly=total,
            per_task_limit=min(5.0, total),
            per_agent_daily_limit=min(10.0, total),
            reset_day=reset_day,
        )
        dumped = cfg.model_dump()
        restored = BudgetConfig.model_validate(dumped)
        assert restored == cfg
