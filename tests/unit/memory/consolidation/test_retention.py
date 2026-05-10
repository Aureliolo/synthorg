"""Tests for RetentionEnforcer."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.core.enums import MemoryCategory
from synthorg.memory.consolidation.config import RetentionConfig
from synthorg.memory.consolidation.models import RetentionRule
from synthorg.memory.consolidation.retention import RetentionEnforcer
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability.events.consolidation import (
    RETENTION_AGENT_OVERRIDE_APPLIED,
)

_NOW = datetime.now(UTC)
_AGENT_ID = "test-agent"


def _make_entry(entry_id: str, category: MemoryCategory) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=_AGENT_ID,
        category=category,
        content=f"Content {entry_id}",
        metadata=MemoryMetadata(),
        created_at=_NOW - timedelta(days=60),
    )


@pytest.mark.unit
class TestRetentionEnforcer:
    """RetentionEnforcer cleanup behaviour."""

    async def test_no_rules_no_deletions(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        config = RetentionConfig()
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 0
        backend.retrieve.assert_not_called()

    async def test_cleanup_per_category(self) -> None:
        expired_entry = _make_entry("m1", MemoryCategory.WORKING)
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=(expired_entry,))
        backend.delete = AsyncMock(return_value=True)

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 1

    async def test_default_retention_applies_to_all(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())
        backend.delete = AsyncMock(return_value=True)

        config = RetentionConfig(default_retention_days=30)
        enforcer = RetentionEnforcer(config=config, backend=backend)
        await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert backend.retrieve.call_count == len(MemoryCategory)

    async def test_no_expired_entries(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.EPISODIC,
                    retention_days=7,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 0

    async def test_mixed_categories(self) -> None:
        working_entry = _make_entry("w1", MemoryCategory.WORKING)
        # e1 is created but not expired -- no assignment needed

        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(
            side_effect=lambda *a, **kw: (
                (working_entry,)
                if MemoryCategory.WORKING in kw.get("query", a[-1]).categories
                else ()
            ),
        )
        backend.delete = AsyncMock(return_value=True)

        config = RetentionConfig(
            rules=(
                RetentionRule(category=MemoryCategory.WORKING, retention_days=30),
                RetentionRule(category=MemoryCategory.EPISODIC, retention_days=90),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 1

    async def test_continues_on_per_category_failure(self) -> None:
        """Item 11: failure in one category does not block the rest."""
        _make_entry("w1", MemoryCategory.WORKING)  # used indirectly in mock
        episodic_entry = _make_entry("e1", MemoryCategory.EPISODIC)

        call_count = 0

        async def _mock_retrieve(
            agent_id: str,
            query: MemoryQuery,
        ) -> tuple[MemoryEntry, ...]:
            nonlocal call_count
            call_count += 1
            cats = query.categories or frozenset()
            if MemoryCategory.WORKING in cats:
                msg = "working store unavailable"
                raise RuntimeError(msg)
            if MemoryCategory.EPISODIC in cats:
                return (episodic_entry,)
            return ()

        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(side_effect=_mock_retrieve)
        backend.delete = AsyncMock(return_value=True)

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
                RetentionRule(
                    category=MemoryCategory.EPISODIC,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        # Working failed, but episodic should still succeed
        assert deleted == 1
        backend.delete.assert_called_once()

    async def test_delete_returns_false_not_counted(self) -> None:
        expired_entry = _make_entry("m1", MemoryCategory.WORKING)
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=(expired_entry,))
        backend.delete = AsyncMock(return_value=False)

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 0


@pytest.mark.unit
class TestResolveCategories:
    """Unit tests for _resolve_categories static method."""

    def test_agent_override_replaces_company_rule(self) -> None:
        """Agent per-category rule overrides company per-category rule."""
        base = ((MemoryCategory.WORKING, 30),)
        agent_overrides: dict[MemoryCategory, int] = {
            MemoryCategory.WORKING: 90,
        }
        result = RetentionEnforcer._resolve_categories(
            base,
            agent_overrides=agent_overrides,
            agent_default_days=None,
            company_default_days=None,
        )
        result_dict = dict(result)
        assert result_dict[MemoryCategory.WORKING] == 90

    def test_agent_override_adds_new_category(self) -> None:
        """Agent can add a rule for a category not in company config."""
        base = ((MemoryCategory.WORKING, 30),)
        agent_overrides: dict[MemoryCategory, int] = {
            MemoryCategory.SEMANTIC: 365,
        }
        result = RetentionEnforcer._resolve_categories(
            base,
            agent_overrides=agent_overrides,
            agent_default_days=None,
            company_default_days=None,
        )
        result_dict = dict(result)
        assert result_dict[MemoryCategory.WORKING] == 30
        assert result_dict[MemoryCategory.SEMANTIC] == 365

    def test_agent_default_fills_gaps(self) -> None:
        """Agent default_retention_days fills categories without rules."""
        base = ()  # no company rules
        result = RetentionEnforcer._resolve_categories(
            base,
            agent_overrides={},
            agent_default_days=60,
            company_default_days=None,
        )
        result_dict = dict(result)
        # All categories should get the agent default
        for cat in MemoryCategory:
            assert result_dict[cat] == 60

    def test_agent_rule_beats_agent_default(self) -> None:
        """Agent per-category rule takes priority over agent default."""
        base = ()
        agent_overrides: dict[MemoryCategory, int] = {
            MemoryCategory.EPISODIC: 180,
        }
        result = RetentionEnforcer._resolve_categories(
            base,
            agent_overrides=agent_overrides,
            agent_default_days=60,
            company_default_days=None,
        )
        result_dict = dict(result)
        assert result_dict[MemoryCategory.EPISODIC] == 180
        # Other categories get agent default
        assert result_dict[MemoryCategory.WORKING] == 60

    def test_company_rule_beats_agent_default(self) -> None:
        """Company per-category rule beats agent global default."""
        base = ((MemoryCategory.WORKING, 7),)
        result = RetentionEnforcer._resolve_categories(
            base,
            agent_overrides={},
            agent_default_days=365,
            company_default_days=None,
        )
        result_dict = dict(result)
        # Company rule for WORKING wins over agent default
        assert result_dict[MemoryCategory.WORKING] == 7
        # Agent default fills other categories
        assert result_dict[MemoryCategory.SEMANTIC] == 365

    def test_company_default_used_when_no_agent_default(self) -> None:
        """Company default fills gaps when no agent default is set."""
        base = ((MemoryCategory.WORKING, 7),)
        result = RetentionEnforcer._resolve_categories(
            base,
            agent_overrides={},
            agent_default_days=None,
            company_default_days=30,
        )
        result_dict = dict(result)
        assert result_dict[MemoryCategory.WORKING] == 7
        assert result_dict[MemoryCategory.SEMANTIC] == 30

    def test_no_overrides_returns_base(self) -> None:
        """When no agent overrides, result matches base plus defaults."""
        base = (
            (MemoryCategory.WORKING, 30),
            (MemoryCategory.EPISODIC, 90),
        )
        result = RetentionEnforcer._resolve_categories(
            base,
            agent_overrides={},
            agent_default_days=None,
            company_default_days=None,
        )
        result_dict = dict(result)
        assert result_dict[MemoryCategory.WORKING] == 30
        assert result_dict[MemoryCategory.EPISODIC] == 90
        # Categories with no rule at all are not included
        assert MemoryCategory.SOCIAL not in result_dict

    def test_full_resolution_chain(self) -> None:
        """Test all 5 resolution levels in a single scenario.

        - WORKING: agent per-category (7) beats company per-category (30)
        - EPISODIC: company per-category (90) beats agent default (60)
        - SEMANTIC: agent per-category (365) -- no company rule
        - PROCEDURAL: agent default (60) -- no rules, no company rule
        - SOCIAL: agent default (60) -- no rules, agent default > co default
        """
        # Only explicit company per-category rules (not company default)
        explicit_rules = (
            (MemoryCategory.WORKING, 30),
            (MemoryCategory.EPISODIC, 90),
        )
        agent_overrides: dict[MemoryCategory, int] = {
            MemoryCategory.WORKING: 7,
            MemoryCategory.SEMANTIC: 365,
        }
        result = RetentionEnforcer._resolve_categories(
            explicit_rules,
            agent_overrides=agent_overrides,
            agent_default_days=60,
            company_default_days=45,
        )
        result_dict = dict(result)
        assert result_dict[MemoryCategory.WORKING] == 7  # 1. agent rule
        assert result_dict[MemoryCategory.EPISODIC] == 90  # 2. company rule
        assert result_dict[MemoryCategory.SEMANTIC] == 365  # 1. agent rule
        assert result_dict[MemoryCategory.PROCEDURAL] == 60  # 3. agent default
        assert result_dict[MemoryCategory.SOCIAL] == 60  # 3. agent default

    def test_company_default_used_when_agent_default_absent(self) -> None:
        """Priority 4 (company default) used when no agent default."""
        explicit_rules = ((MemoryCategory.WORKING, 30),)
        result = RetentionEnforcer._resolve_categories(
            explicit_rules,
            agent_overrides={},
            agent_default_days=None,
            company_default_days=45,
        )
        result_dict = dict(result)
        assert result_dict[MemoryCategory.WORKING] == 30  # 2. company rule
        assert result_dict[MemoryCategory.SOCIAL] == 45  # 4. company default

    def test_empty_everything_returns_empty(self) -> None:
        """No rules, no defaults -- returns empty tuple."""
        result = RetentionEnforcer._resolve_categories(
            (),
            agent_overrides={},
            agent_default_days=None,
            company_default_days=None,
        )
        assert result == ()


@pytest.mark.unit
class TestRetentionEnforcerAgentOverrides:
    """Cleanup behaviour with per-agent retention overrides."""

    async def test_no_overrides_unchanged_behavior(self) -> None:
        """Without overrides, cleanup behaves identically to base."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 0
        # Only WORKING queried (same as base behavior)
        assert backend.retrieve.call_count == 1

    async def test_cleanup_with_agent_category_override(self) -> None:
        """Agent override changes the cutoff date for a category."""
        expired_entry = _make_entry("m1", MemoryCategory.WORKING)
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=(expired_entry,))
        backend.delete = AsyncMock(return_value=True)

        # Company says WORKING = 30 days
        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)

        # Agent overrides WORKING to 90 days -- entry at 60 days old
        # should NOT be expired (60 < 90)
        await enforcer.cleanup_expired(
            _AGENT_ID,
            now=_NOW,
            agent_category_overrides={MemoryCategory.WORKING: 90},
        )
        # The query cutoff should be now - 90 days, so the 60-day-old
        # entry should NOT be returned by a correct cutoff query.
        # But our mock always returns the entry -- the key assertion
        # is that the query uses the agent's 90-day cutoff.
        retrieve_call = backend.retrieve.call_args
        query: MemoryQuery = retrieve_call[0][1]
        expected_cutoff = _NOW - timedelta(days=90)
        assert query.until == expected_cutoff

    async def test_cleanup_with_agent_default_adds_categories(self) -> None:
        """Agent default_retention_days causes all categories to be checked."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())

        # Company has no rules at all
        config = RetentionConfig()
        enforcer = RetentionEnforcer(config=config, backend=backend)

        # Without overrides: no categories checked
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 0
        assert backend.retrieve.call_count == 0

        backend.reset_mock()

        # With agent default: all categories checked
        deleted = await enforcer.cleanup_expired(
            _AGENT_ID,
            now=_NOW,
            agent_default_retention_days=60,
        )
        assert backend.retrieve.call_count == len(MemoryCategory)

    async def test_agent_default_cutoff_correctness(self) -> None:
        """Agent default_retention_days produces correct cutoff dates."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())

        config = RetentionConfig()
        enforcer = RetentionEnforcer(config=config, backend=backend)

        await enforcer.cleanup_expired(
            _AGENT_ID,
            now=_NOW,
            agent_default_retention_days=60,
        )
        # Verify each query uses the agent default cutoff
        expected_cutoff = _NOW - timedelta(days=60)
        for call in backend.retrieve.call_args_list:
            query: MemoryQuery = call[0][1]
            assert query.until == expected_cutoff

    async def test_agent_default_beats_company_default(self) -> None:
        """Agent default overrides company default for non-rule categories."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())

        # Company has default_retention_days=30 (no explicit rules)
        config = RetentionConfig(default_retention_days=30)
        enforcer = RetentionEnforcer(config=config, backend=backend)

        # Agent default is 90 -- should override company default
        await enforcer.cleanup_expired(
            _AGENT_ID,
            now=_NOW,
            agent_default_retention_days=90,
        )
        expected_cutoff = _NOW - timedelta(days=90)
        for call in backend.retrieve.call_args_list:
            query: MemoryQuery = call[0][1]
            assert query.until == expected_cutoff

    async def test_override_log_event_fires(self) -> None:
        """RETENTION_AGENT_OVERRIDE_APPLIED log event fires with overrides."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())

        config = RetentionConfig()
        enforcer = RetentionEnforcer(config=config, backend=backend)

        with structlog.testing.capture_logs() as logs:
            await enforcer.cleanup_expired(
                _AGENT_ID,
                now=_NOW,
                agent_default_retention_days=30,
            )
        override_logs = [
            log for log in logs if log.get("event") == RETENTION_AGENT_OVERRIDE_APPLIED
        ]
        assert len(override_logs) == 1
        assert override_logs[0]["agent_id"] == _AGENT_ID
        assert override_logs[0]["resolved_category_count"] == len(
            MemoryCategory,
        )

    async def test_fast_path_returns_precomputed_categories(self) -> None:
        """Without overrides, _resolve_for_agent returns cached tuple."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)

        resolved = enforcer._resolve_for_agent(_AGENT_ID, None, None)
        assert resolved is enforcer._categories_to_check

    async def test_cleanup_with_both_overrides_simultaneously(self) -> None:
        """Both agent_category_overrides and agent_default combined."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)

        await enforcer.cleanup_expired(
            _AGENT_ID,
            now=_NOW,
            agent_category_overrides={MemoryCategory.WORKING: 7},
            agent_default_retention_days=60,
        )
        # All categories checked: WORKING via agent override,
        # the rest via agent default
        assert backend.retrieve.call_count == len(MemoryCategory)
        # Verify WORKING uses agent override (7 days), not company (30)
        working_calls = [
            call
            for call in backend.retrieve.call_args_list
            if MemoryCategory.WORKING in call[0][1].categories
        ]
        assert len(working_calls) == 1
        query: MemoryQuery = working_calls[0][0][1]
        assert query.until == _NOW - timedelta(days=7)


@pytest.mark.unit
class TestRetentionRuleParity:
    """Ensure AgentRetentionRule and RetentionRule stay in sync."""

    def test_field_parity(self) -> None:
        """Both models must have the same field names and types."""
        from synthorg.core.agent import AgentRetentionRule
        from synthorg.memory.consolidation.models import RetentionRule

        agent_fields = AgentRetentionRule.model_fields
        retention_fields = RetentionRule.model_fields
        assert set(agent_fields) == set(retention_fields)
        for name in agent_fields:
            assert agent_fields[name].annotation == retention_fields[name].annotation, (
                f"Field {name!r} type mismatch"
            )
