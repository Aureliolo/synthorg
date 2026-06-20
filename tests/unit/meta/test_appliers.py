"""Unit tests for meta-loop proposal appliers (apply + dry_run)."""

from collections.abc import Awaitable, Callable

import pytest

from synthorg.config.schema import RootConfig
from synthorg.meta.appliers.architecture_applier import (
    ArchitectureApplier,
    ArchitectureApplierContext,
)
from synthorg.meta.appliers.config_applier import ConfigApplier
from synthorg.meta.appliers.prompt_applier import (
    PromptApplier,
    PromptApplierContext,
)
from synthorg.meta.models import (
    ArchitectureChange,
    ConfigChange,
    EvolutionMode,
    ImprovementProposal,
    PromptChange,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
)
from tests._shared import JsonDict

pytestmark = pytest.mark.unit


# -- Fixtures ----------------------------------------------------


def _rationale() -> ProposalRationale:
    return ProposalRationale(
        signal_summary="test",
        pattern_detected="test",
        expected_impact="test",
        confidence_reasoning="test",
    )


def _rollback() -> RollbackPlan:
    return RollbackPlan(
        operations=(
            RollbackOperation(
                operation_type="revert",
                target="x",
                description="revert x",
            ),
        ),
        validation_check="check x",
    )


def _root_config() -> RootConfig:
    return RootConfig(company_name="Test Co")


def _config_provider() -> RootConfig:
    return _root_config()


def _proposal_config(
    *changes: ConfigChange,
) -> ImprovementProposal:
    return ImprovementProposal(
        altitude=ProposalAltitude.CONFIG_TUNING,
        title="test",
        description="test",
        rationale=_rationale(),
        config_changes=changes,
        rollback_plan=_rollback(),
        confidence=0.8,
    )


def _proposal_prompt(
    *changes: PromptChange,
) -> ImprovementProposal:
    return ImprovementProposal(
        altitude=ProposalAltitude.PROMPT_TUNING,
        title="test",
        description="test",
        rationale=_rationale(),
        prompt_changes=changes,
        rollback_plan=_rollback(),
        confidence=0.8,
    )


def _proposal_architecture(
    *changes: ArchitectureChange,
) -> ImprovementProposal:
    return ImprovementProposal(
        altitude=ProposalAltitude.ARCHITECTURE,
        title="test",
        description="test",
        rationale=_rationale(),
        architecture_changes=changes,
        rollback_plan=_rollback(),
        confidence=0.8,
    )


# -- ConfigApplier ----------------------------------------------


class _FakeSettingValue:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeSettingsWriter:
    """In-memory settings double recording set() calls."""

    def __init__(
        self,
        *,
        fail_on: tuple[str, str] | None = None,
        fail_on_restore: tuple[str, str, str] | None = None,
    ) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self._fail_on = fail_on
        # Fail only when set() is called with this exact (ns, key, value).
        # A rollback restores a key to its prior value, so this lets a test
        # fail the rollback write without failing the forward apply.
        self._fail_on_restore = fail_on_restore
        self.sets: list[tuple[str, str, str]] = []

    async def get(self, namespace: str, key: str) -> object:
        return _FakeSettingValue(self.store.get((namespace, key), ""))

    async def set(self, namespace: str, key: str, value: str) -> object:
        if self._fail_on == (namespace, key):
            msg = f"boom on {namespace}/{key}"
            raise ValueError(msg)
        if self._fail_on_restore == (namespace, key, value):
            msg = f"boom restoring {namespace}/{key}"
            raise ValueError(msg)
        self.store[(namespace, key)] = value
        self.sets.append((namespace, key, value))
        return _FakeSettingValue(value)


class TestConfigApplier:
    def test_altitude(self) -> None:
        assert ConfigApplier().altitude == ProposalAltitude.CONFIG_TUNING

    async def test_apply_without_writer_rejects(self) -> None:
        applier = ConfigApplier()
        proposal = _proposal_config(
            ConfigChange(path="a.b", old_value=1, new_value=2, description="d"),
        )
        result = await applier.apply(proposal)
        assert not result.success
        assert "settings writer" in (result.error_message or "")

    async def test_apply_persists_each_change(self) -> None:
        writer = _FakeSettingsWriter()
        applier = ConfigApplier(settings_writer=writer)
        proposal = _proposal_config(
            ConfigChange(path="a.b", old_value=1, new_value=2, description="d"),
            ConfigChange(path="c.d", old_value=3, new_value=4, description="d"),
        )
        result = await applier.apply(proposal)
        assert result.success
        assert result.changes_applied == 2
        assert writer.store[("a", "b")] == "2"
        assert writer.store[("c", "d")] == "4"

    async def test_apply_rolls_back_on_failure(self) -> None:
        writer = _FakeSettingsWriter(fail_on=("c", "d"))
        writer.store[("a", "b")] = "1"
        applier = ConfigApplier(settings_writer=writer)
        proposal = _proposal_config(
            ConfigChange(path="a.b", old_value=1, new_value=2, description="d"),
            ConfigChange(path="c.d", old_value=3, new_value=4, description="d"),
        )
        result = await applier.apply(proposal)
        assert not result.success
        # The first change was applied then rolled back to its prior value.
        assert writer.store[("a", "b")] == "1"

    async def test_apply_rollback_write_failure_is_swallowed(self) -> None:
        # Forward apply of a.b succeeds (1 -> 2); apply of c.d fails,
        # triggering rollback; the rollback restore of a.b (2 -> 1) then
        # also fails. The failure must be swallowed (logged), the apply
        # reported unsuccessful, and a.b left at its un-restored value.
        writer = _FakeSettingsWriter(
            fail_on=("c", "d"),
            fail_on_restore=("a", "b", "1"),
        )
        writer.store[("a", "b")] = "1"
        applier = ConfigApplier(settings_writer=writer)
        proposal = _proposal_config(
            ConfigChange(path="a.b", old_value=1, new_value=2, description="d"),
            ConfigChange(path="c.d", old_value=3, new_value=4, description="d"),
        )
        result = await applier.apply(proposal)
        assert not result.success
        # The rollback write failed, so a.b is left at the applied value.
        assert writer.store[("a", "b")] == "2"

    async def test_apply_rejects_non_namespaced_path(self) -> None:
        applier = ConfigApplier(settings_writer=_FakeSettingsWriter())
        proposal = _proposal_config(
            ConfigChange(path="flat", new_value=1, description="d"),
        )
        result = await applier.apply(proposal)
        assert not result.success

    async def test_dry_run_without_provider_rejects(self) -> None:
        applier = ConfigApplier()
        proposal = _proposal_config(
            ConfigChange(
                path="company_name",
                new_value="New Name",
                description="rename",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "config_provider" in (result.error_message or "")

    async def test_dry_run_happy_path(self) -> None:
        applier = ConfigApplier(config_provider=_config_provider)
        proposal = _proposal_config(
            ConfigChange(
                path="company_name",
                old_value="Test Co",
                new_value="Renamed Co",
                description="rename",
            ),
        )
        result = await applier.dry_run(proposal)
        assert result.success, result.error_message
        assert result.changes_applied == 1

    async def test_dry_run_unknown_path_rejects(self) -> None:
        applier = ConfigApplier(config_provider=_config_provider)
        proposal = _proposal_config(
            ConfigChange(
                path="nonexistent.field",
                new_value=123,
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "unknown" in (result.error_message or "")

    async def test_dry_run_pydantic_validation_surfaces_errors(self) -> None:
        applier = ConfigApplier(config_provider=_config_provider)
        proposal = _proposal_config(
            ConfigChange(
                path="company_name",
                new_value="",
                description="invalid blank",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "company_name" in (result.error_message or "")

    async def test_dry_run_collects_all_errors(self) -> None:
        applier = ConfigApplier(config_provider=_config_provider)
        proposal = _proposal_config(
            ConfigChange(path="bogus.one", new_value=1, description="d"),
            ConfigChange(path="bogus.two", new_value=2, description="d"),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        message = result.error_message or ""
        assert "bogus.one" in message
        assert "bogus.two" in message

    async def test_dry_run_rejects_wrong_altitude(self) -> None:
        applier = ConfigApplier(config_provider=_config_provider)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="be concise and helpful",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "CONFIG_TUNING" in (result.error_message or "")

    async def test_dry_run_provider_failure_is_surfaced(self) -> None:
        """If the config_provider raises, dry_run must surface a safe
        error message rather than letting the exception propagate."""

        def _boom() -> RootConfig:
            msg = "provider offline"
            raise RuntimeError(msg)

        applier = ConfigApplier(config_provider=_boom)
        proposal = _proposal_config(
            ConfigChange(
                path="company_name",
                new_value="X",
                description="rename",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        message = result.error_message or ""
        assert "RuntimeError" in message
        assert "provider offline" in message


# -- PromptApplier ----------------------------------------------


class _FakePromptContext:
    def __init__(  # noqa: PLR0913
        self,
        *,
        roles: frozenset[str] = frozenset(),
        departments: frozenset[str] = frozenset(),
        existing: dict[str, frozenset[str]] | None = None,
        overridden: frozenset[str] = frozenset(),
        fail_after: int | None = None,
        fail_delete: bool = False,
        fail_refresh: bool = False,
    ) -> None:
        self._roles = roles
        self._departments = departments
        self._existing = existing or {}
        self._overridden = overridden
        self._fail_after = fail_after
        self._fail_delete = fail_delete
        self._fail_refresh = fail_refresh
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.refreshed = 0

    def known_roles(self) -> frozenset[str]:
        return self._roles

    def known_departments(self) -> frozenset[str]:
        return self._departments

    def existing_principles(self, scope: str) -> frozenset[str]:
        return self._existing.get(scope, frozenset())

    def scope_overridden(self, scope: str) -> bool:
        return scope in self._overridden

    async def create_principle(self, change: PromptChange) -> str:
        if self._fail_after is not None and len(self.created) >= self._fail_after:
            msg = "create_principle boom"
            raise ValueError(msg)
        principle_id = f"pid-{len(self.created)}-{change.target_scope}"
        self.created.append(principle_id)
        return principle_id

    async def delete_principle(self, principle_id: str) -> None:
        if self._fail_delete:
            msg = "delete_principle boom"
            raise ValueError(msg)
        self.deleted.append(principle_id)

    async def refresh_snapshot(self) -> None:
        if self._fail_refresh:
            msg = "refresh_snapshot boom"
            raise RuntimeError(msg)
        self.refreshed += 1


class TestPromptApplier:
    def test_altitude(self) -> None:
        assert PromptApplier().altitude == ProposalAltitude.PROMPT_TUNING

    def test_context_protocol_conformance(self) -> None:
        assert isinstance(_FakePromptContext(), PromptApplierContext)

    async def test_apply_without_context_rejects(self) -> None:
        applier = PromptApplier()
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="be concise and helpful",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.apply(proposal)
        assert not result.success
        assert result.changes_applied == 0
        assert "PromptApplierContext" in (result.error_message or "")

    async def test_apply_persists_each_change(self) -> None:
        context = _FakePromptContext(roles=frozenset({"engineer"}))
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="be concise and helpful",
                target_scope="all",
                description="d",
            ),
            PromptChange(
                principle_text="engineers cite source files",
                target_scope="engineer",
                description="d",
            ),
        )
        result = await applier.apply(proposal)
        assert result.success
        assert result.changes_applied == 2
        assert len(context.created) == 2
        assert context.deleted == []
        assert context.refreshed == 1

    async def test_apply_rolls_back_on_failure(self) -> None:
        context = _FakePromptContext(fail_after=1)
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="first principle text",
                target_scope="all",
                description="d",
            ),
            PromptChange(
                principle_text="second principle text",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.apply(proposal)
        assert not result.success
        assert result.changes_applied == 0
        # The one created principle is rolled back (reverse order, single item).
        assert context.deleted == list(reversed(context.created))
        assert context.refreshed == 0

    async def test_apply_rollback_delete_failure_is_swallowed(self) -> None:
        context = _FakePromptContext(fail_after=1, fail_delete=True)
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="first principle text",
                target_scope="all",
                description="d",
            ),
            PromptChange(
                principle_text="second principle text",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.apply(proposal)
        # A failing rollback delete does not abort the apply result.
        assert not result.success
        assert context.deleted == []

    async def test_apply_succeeds_when_post_commit_refresh_fails(self) -> None:
        # Principles are already durably persisted; a failed read-snapshot
        # refresh must not turn into a failure result (that would risk an
        # upstream retry re-creating the principles).
        context = _FakePromptContext(fail_refresh=True)
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="be concise and helpful",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.apply(proposal)
        assert result.success
        assert result.changes_applied == 1
        assert len(context.created) == 1
        assert context.deleted == []

    async def test_apply_rejects_wrong_altitude(self) -> None:
        context = _FakePromptContext()
        applier = PromptApplier(context=context)
        # A valid ARCHITECTURE proposal misrouted to the prompt applier.
        misrouted = _proposal_architecture(
            _arch("create_role", "r1", payload={"description": "d"}),
        )
        result = await applier.apply(misrouted)
        assert not result.success
        assert result.changes_applied == 0
        assert "altitude" in (result.error_message or "")
        assert context.created == []
        assert context.refreshed == 0

    async def test_apply_rejects_empty_proposal(self) -> None:
        context = _FakePromptContext()
        applier = PromptApplier(context=context)
        # model_copy skips validation, so this exercises the defensive guard
        # against a proposal that the model invariant would normally forbid.
        empty = _proposal_prompt(
            PromptChange(
                principle_text="be concise and helpful",
                target_scope="all",
                description="d",
            ),
        ).model_copy(update={"prompt_changes": ()})
        result = await applier.apply(empty)
        assert not result.success
        assert result.changes_applied == 0
        assert "no prompt changes" in (result.error_message or "")
        assert context.created == []
        assert context.refreshed == 0

    async def test_dry_run_without_context_rejects(self) -> None:
        applier = PromptApplier()
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="be concise and helpful",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "PromptApplierContext" in (result.error_message or "")

    async def test_dry_run_happy_path(self) -> None:
        context = _FakePromptContext(
            roles=frozenset({"engineer"}),
            departments=frozenset({"engineering"}),
        )
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="Be concise and helpful always.",
                target_scope="all",
                description="d",
            ),
            PromptChange(
                principle_text="Engineers must cite source files.",
                target_scope="engineer",
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert result.success, result.error_message
        assert result.changes_applied == 2

    async def test_dry_run_unknown_scope(self) -> None:
        context = _FakePromptContext()
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="Be concise and helpful always.",
                target_scope="unknown_role",
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "Unknown target_scope" in (result.error_message or "")

    async def test_dry_run_principle_too_short(self) -> None:
        context = _FakePromptContext()
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="too shrt",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "too short" in (result.error_message or "")

    async def test_dry_run_principle_too_long(self) -> None:
        context = _FakePromptContext()
        applier = PromptApplier(context=context)
        long_text = "x" * 5000
        proposal = _proposal_prompt(
            PromptChange(
                principle_text=long_text,
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "too long" in (result.error_message or "")

    async def test_dry_run_duplicate_in_proposal(self) -> None:
        context = _FakePromptContext()
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="Be concise and helpful.",
                target_scope="all",
                description="d",
            ),
            PromptChange(
                principle_text="  Be Concise and Helpful.  ",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "Duplicate principle_text" in (result.error_message or "")

    async def test_dry_run_duplicate_with_existing(self) -> None:
        context = _FakePromptContext(
            existing={"all": frozenset({"be concise and helpful."})},
        )
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="Be concise and helpful.",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "already exists" in (result.error_message or "")

    async def test_dry_run_override_conflict(self) -> None:
        context = _FakePromptContext(overridden=frozenset({"all"}))
        applier = PromptApplier(context=context)
        proposal = _proposal_prompt(
            PromptChange(
                principle_text="Be concise and helpful always.",
                target_scope="all",
                evolution_mode=EvolutionMode.OVERRIDE,
                description="d",
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "active OVERRIDE" in (result.error_message or "")


# -- ArchitectureApplier -----------------------------------------


class _FakeArchContext:
    def __init__(  # noqa: PLR0913
        self,
        *,
        roles: frozenset[str] = frozenset(),
        departments: frozenset[str] = frozenset(),
        workflows: frozenset[str] = frozenset(),
        roles_in_use: frozenset[str] = frozenset(),
        depts_in_use: frozenset[str] = frozenset(),
        fail_after: int | None = None,
        fail_refresh: bool = False,
    ) -> None:
        self._roles = roles
        self._departments = departments
        self._workflows = workflows
        self._roles_in_use = roles_in_use
        self._depts_in_use = depts_in_use
        self._fail_after = fail_after
        self._fail_refresh = fail_refresh
        self.applied: list[str] = []
        self.undone: list[str] = []
        self.refreshed = 0

    def has_role(self, name: str) -> bool:
        return name in self._roles

    def has_department(self, name: str) -> bool:
        return name in self._departments

    def has_workflow(self, name: str) -> bool:
        return name in self._workflows

    def role_in_use(self, name: str) -> bool:
        return name in self._roles_in_use

    def department_in_use(self, name: str) -> bool:
        return name in self._depts_in_use

    async def apply_change(
        self, change: ArchitectureChange
    ) -> Callable[[], Awaitable[None]]:
        if self._fail_after is not None and len(self.applied) >= self._fail_after:
            msg = "apply_change boom"
            raise ValueError(msg)
        self.applied.append(change.target_name)
        target = change.target_name

        async def _undo() -> None:
            self.undone.append(target)

        return _undo

    async def refresh_snapshot(self) -> None:
        if self._fail_refresh:
            msg = "refresh_snapshot boom"
            raise RuntimeError(msg)
        self.refreshed += 1


def _arch(
    operation: str,
    target_name: str,
    *,
    payload: JsonDict | None = None,
) -> ArchitectureChange:
    return ArchitectureChange(
        operation=operation,
        target_name=target_name,
        payload=payload or {},
        description="d",
    )


class TestArchitectureApplier:
    def test_altitude(self) -> None:
        assert ArchitectureApplier().altitude == ProposalAltitude.ARCHITECTURE

    def test_context_protocol_conformance(self) -> None:
        assert isinstance(_FakeArchContext(), ArchitectureApplierContext)

    async def test_apply_without_context_rejects(self) -> None:
        applier = ArchitectureApplier()
        proposal = _proposal_architecture(
            _arch("create_role", "new-role", payload={"description": "d"}),
        )
        result = await applier.apply(proposal)
        assert not result.success
        assert result.changes_applied == 0
        assert "ArchitectureApplierContext" in (result.error_message or "")

    async def test_apply_applies_each_change(self) -> None:
        context = _FakeArchContext()
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch("create_role", "new-role", payload={"description": "d"}),
            _arch("create_department", "new-dept"),
        )
        result = await applier.apply(proposal)
        assert result.success
        assert result.changes_applied == 2
        assert context.applied == ["new-role", "new-dept"]
        assert context.undone == []
        assert context.refreshed == 1

    async def test_apply_succeeds_when_post_commit_refresh_fails(self) -> None:
        # Changes are already durably committed; a failed read-snapshot refresh
        # must report success (not failure) so an upstream retry cannot re-run
        # the non-idempotent mutations.
        context = _FakeArchContext(fail_refresh=True)
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch("create_role", "new-role", payload={"description": "d"}),
        )
        result = await applier.apply(proposal)
        assert result.success
        assert result.changes_applied == 1
        assert context.applied == ["new-role"]
        assert context.undone == []

    async def test_apply_rejects_wrong_altitude(self) -> None:
        context = _FakeArchContext()
        applier = ArchitectureApplier(context=context)
        # A valid PROMPT_TUNING proposal misrouted to the architecture applier.
        misrouted = _proposal_prompt(
            PromptChange(
                principle_text="be concise and helpful",
                target_scope="all",
                description="d",
            ),
        )
        result = await applier.apply(misrouted)
        assert not result.success
        assert result.changes_applied == 0
        assert "altitude" in (result.error_message or "")
        assert context.applied == []
        assert context.refreshed == 0

    async def test_apply_rejects_empty_proposal(self) -> None:
        context = _FakeArchContext()
        applier = ArchitectureApplier(context=context)
        # model_copy skips validation, so this exercises the defensive guard
        # against a proposal that the model invariant would normally forbid.
        empty = _proposal_architecture(
            _arch("create_role", "r1", payload={"description": "d"}),
        ).model_copy(update={"architecture_changes": ()})
        result = await applier.apply(empty)
        assert not result.success
        assert result.changes_applied == 0
        assert "no architecture changes" in (result.error_message or "")
        assert context.applied == []
        assert context.refreshed == 0

    async def test_apply_rolls_back_in_reverse_on_failure(self) -> None:
        context = _FakeArchContext(fail_after=2)
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch("create_role", "role-a", payload={"description": "d"}),
            _arch("create_role", "role-b", payload={"description": "d"}),
            _arch("create_role", "role-c", payload={"description": "d"}),
        )
        result = await applier.apply(proposal)
        assert not result.success
        assert result.changes_applied == 0
        # First two applied (a, b); third fails -> undo in reverse (b, a).
        assert context.applied == ["role-a", "role-b"]
        assert context.undone == ["role-b", "role-a"]
        assert context.refreshed == 0

    async def test_dry_run_without_context_rejects(self) -> None:
        applier = ArchitectureApplier()
        proposal = _proposal_architecture(
            _arch("create_role", "new-role", payload={"description": "d"}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "ArchitectureApplierContext" in (result.error_message or "")

    async def test_dry_run_unknown_operation(self) -> None:
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch("nonsense_op", "new-role", payload={"description": "d"}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "Unknown operation" in (result.error_message or "")

    async def test_dry_run_create_role_happy_path(self) -> None:
        context = _FakeArchContext(departments=frozenset({"engineering"}))
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "senior-engineer",
                payload={
                    "description": "d",
                    "department": "engineering",
                    "required_skills": ["python"],
                },
            ),
        )
        result = await applier.dry_run(proposal)
        assert result.success, result.error_message

    async def test_dry_run_create_role_duplicate_name(self) -> None:
        context = _FakeArchContext(roles=frozenset({"engineer"}))
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch("create_role", "engineer", payload={"description": "d"}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "already exists" in (result.error_message or "")

    async def test_dry_run_create_role_missing_required_key(self) -> None:
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch("create_role", "new-role", payload={}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "missing required" in (result.error_message or "")

    async def test_dry_run_create_role_unknown_department(self) -> None:
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "new-role",
                payload={
                    "description": "d",
                    "department": "nonexistent",
                },
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "department" in (result.error_message or "")

    async def test_dry_run_create_role_non_enum_department_rejected(self) -> None:
        # A durable department whose name is not a DepartmentName enum value
        # exists in the registry, but apply (``DepartmentName(...)``) would
        # reject a role pointing at it. Dry-run must reject it too rather than
        # passing a proposal that cannot apply.
        context = _FakeArchContext(departments=frozenset({"custom-squad"}))
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "new-role",
                payload={"description": "d", "department": "custom-squad"},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "is not a known DepartmentName" in (result.error_message or "")

    async def test_dry_run_modify_workflow_missing(self) -> None:
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch("modify_workflow", "wf-1", payload={"field": "value"}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "does not exist" in (result.error_message or "")

    async def test_dry_run_modify_workflow_empty_payload(self) -> None:
        context = _FakeArchContext(workflows=frozenset({"wf-1"}))
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch("modify_workflow", "wf-1", payload={}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "no-op modify" in (result.error_message or "")

    async def test_dry_run_remove_role_in_use(self) -> None:
        context = _FakeArchContext(
            roles=frozenset({"engineer"}),
            roles_in_use=frozenset({"engineer"}),
        )
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch("remove_role", "engineer", payload={}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "still referenced" in (result.error_message or "")

    async def test_dry_run_remove_department_in_use(self) -> None:
        context = _FakeArchContext(
            departments=frozenset({"engineering"}),
            depts_in_use=frozenset({"engineering"}),
        )
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch("remove_department", "engineering", payload={}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "still referenced" in (result.error_message or "")

    async def test_dry_run_collects_all_errors(self) -> None:
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch("create_role", "a", payload={}),
            _arch("remove_role", "nonexistent", payload={}),
            _arch("modify_workflow", "nowhere", payload={"x": 1}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        message = result.error_message or ""
        assert "missing required" in message
        assert "remove_role" in message
        assert "modify_workflow" in message

    async def test_dry_run_in_proposal_dependencies_resolve(self) -> None:
        context = _FakeArchContext()
        applier = ArchitectureApplier(context=context)
        proposal = _proposal_architecture(
            _arch("create_department", "engineering", payload={}),
            _arch(
                "create_role",
                "senior-engineer",
                payload={"description": "d", "department": "engineering"},
            ),
        )
        result = await applier.dry_run(proposal)
        assert result.success, result.error_message

    async def test_dry_run_description_length_cap(self) -> None:
        """A description exceeding the 2000-char cap is rejected."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "new-role",
                payload={"description": "d" * 3_000},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "'description' exceeds" in (result.error_message or "")

    async def test_dry_run_skill_name_length_cap(self) -> None:
        """A skill name exceeding the 80-char cap is rejected."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "new-role",
                payload={
                    "description": "d",
                    "required_skills": ["x" * 200],
                },
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "required_skills[0]" in (result.error_message or "")

    async def test_dry_run_skill_count_cap(self) -> None:
        """More than 100 skills is rejected."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "new-role",
                payload={
                    "description": "d",
                    "required_skills": [f"s{i}" for i in range(150)],
                },
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "exceeds" in (result.error_message or "")

    async def test_dry_run_non_string_skill_rejected(self) -> None:
        """Non-string skill entries are rejected."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "new-role",
                payload={
                    "description": "d",
                    "required_skills": ["python", 42, "go"],
                },
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "required_skills[1]" in (result.error_message or "")
        assert "must be a string" in (result.error_message or "")

    async def test_dry_run_remove_role_rejects_payload(self) -> None:
        """remove_role must not carry payload keys."""
        applier = ArchitectureApplier(
            context=_FakeArchContext(roles=frozenset({"old-role"})),
        )
        proposal = _proposal_architecture(
            _arch(
                "remove_role",
                "old-role",
                payload={"description": "leftover"},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "remove_role: payload must be empty" in (result.error_message or "")

    async def test_dry_run_remove_department_rejects_payload(self) -> None:
        """remove_department must not carry payload keys."""
        applier = ArchitectureApplier(
            context=_FakeArchContext(departments=frozenset({"old-dept"})),
        )
        proposal = _proposal_architecture(
            _arch(
                "remove_department",
                "old-dept",
                payload={"reason": "cleanup"},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "remove_department: payload must be empty" in (
            result.error_message or ""
        )

    async def test_dry_run_collects_multiple_errors_in_one_pass(
        self,
    ) -> None:
        """A proposal with multiple independent violations surfaces
        every error in one dry-run pass so operators can fix them all
        without iterating."""
        applier = ArchitectureApplier(
            context=_FakeArchContext(roles=frozenset({"existing"})),
        )
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "existing",  # collides with existing role
                payload={
                    "description": "x" * 3_000,  # exceeds 2000-char cap
                    "required_skills": [
                        "x" * 200,  # exceeds 80-char cap
                    ],
                },
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        message = result.error_message or ""
        assert "already exists" in message
        assert "exceeds" in message

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(123, "must be a string", id="non_string"),
            pytest.param("  ", "must not be blank", id="blank"),
            pytest.param("x" * 200, "exceeds", id="too_long"),
        ],
    )
    async def test_dry_run_authority_level_rejects(
        self,
        value: object,
        expected: str,
    ) -> None:
        """Non-string, blank, and oversized authority_level are rejected."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "r1",
                payload={"description": "d", "authority_level": value},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert expected in (result.error_message or "")

    async def test_dry_run_authority_level_valid_string_passes(self) -> None:
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "r1",
                payload={"description": "d", "authority_level": "senior"},
            ),
        )
        result = await applier.dry_run(proposal)
        assert result.success, result.error_message

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("not-a-list", "must be a list or tuple", id="not_a_list"),
            pytest.param(["ok", 7], "must be a string", id="non_string_entry"),
            pytest.param(["ok", "  "], "must not be blank", id="blank_entry"),
            pytest.param(["x" * 200], "exceeds", id="name_too_long"),
            pytest.param(["ok"] * 200, "exceeds", id="too_many_tools"),
        ],
    )
    async def test_dry_run_tool_access_rejects(
        self,
        value: object,
        expected: str,
    ) -> None:
        """tool_access must be a list of non-blank bounded strings."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "r1",
                payload={"description": "d", "tool_access": value},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert expected in (result.error_message or "")

    async def test_dry_run_tool_access_valid_passes(self) -> None:
        # ``ArchitectureChange.payload`` is JSON-typed, so the
        # ``_validate_tool_access`` ``isinstance(list | tuple)`` branch
        # is unreachable for tuples in practice -- the upstream Pydantic
        # boundary rejects them with ``input was not a valid JSON
        # value`` before the applier sees the payload.  Only the list
        # path is tested because that's the only path real callers can
        # exercise.
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "r1",
                payload={
                    "description": "d",
                    "tool_access": ["git", "shell"],
                },
            ),
        )
        result = await applier.dry_run(proposal)
        assert result.success, result.error_message

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("not-a-list", "must be a list or tuple", id="not_a_list"),
            pytest.param(["ok", 7], "must be a string", id="non_string_entry"),
            pytest.param(["ok", "  "], "must not be blank", id="blank_entry"),
            pytest.param(["x" * 1_000], "exceeds", id="policy_too_long"),
            pytest.param(["ok"] * 200, "exceeds", id="too_many_policies"),
        ],
    )
    async def test_dry_run_policies_rejects(
        self,
        value: object,
        expected: str,
    ) -> None:
        """policies must be a list of non-blank bounded strings."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_department",
                "d1",
                payload={"policies": value},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert expected in (result.error_message or "")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(123, "must be a string", id="non_string"),
            pytest.param("  ", "must not be blank", id="blank"),
            pytest.param("x" * 200, "exceeds", id="too_long"),
        ],
    )
    async def test_dry_run_dept_head_rejects(
        self,
        value: object,
        expected: str,
    ) -> None:
        """head must be a non-blank bounded string if provided."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_department",
                "d1",
                payload={"head": value},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert expected in (result.error_message or "")

    async def test_dry_run_pending_department_blocks_removal(self) -> None:
        """A same-proposal ``create_role`` that references a department
        marks that department as in-use for downstream
        ``remove_department`` changes in the same proposal."""
        applier = ArchitectureApplier(
            context=_FakeArchContext(departments=frozenset({"engineering"})),
        )
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "r1",
                payload={"description": "d", "department": "engineering"},
            ),
            _arch("remove_department", "engineering"),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "still referenced" in (result.error_message or "")

    async def test_dry_run_remove_then_create_department_is_fine(self) -> None:
        """Removing a department that only existed in the context (no
        pending refs, not in use) still passes."""
        applier = ArchitectureApplier(
            context=_FakeArchContext(departments=frozenset({"dept-a"})),
        )
        proposal = _proposal_architecture(
            _arch("remove_department", "dept-a"),
        )
        result = await applier.dry_run(proposal)
        assert result.success, result.error_message

    async def test_dry_run_remove_role_blocked_by_pending_dept_head(
        self,
    ) -> None:
        """A same-proposal ``create_department(head='R1')`` followed by
        ``remove_role('R1')`` must be rejected: the department would
        dangle on an un-created role."""
        applier = ArchitectureApplier(
            context=_FakeArchContext(roles=frozenset({"r1"})),
        )
        proposal = _proposal_architecture(
            _arch(
                "create_department",
                "dept-a",
                payload={"head": "r1"},
            ),
            _arch("remove_role", "r1"),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "still referenced" in (result.error_message or "")

    async def test_dry_run_create_department_rejects_head_scheduled_for_removal(
        self,
    ) -> None:
        """A same-proposal ``remove_role('R1')`` followed by
        ``create_department(head='R1')`` must be rejected."""
        applier = ArchitectureApplier(
            context=_FakeArchContext(roles=frozenset({"r1"})),
        )
        proposal = _proposal_architecture(
            _arch("remove_role", "r1"),
            _arch(
                "create_department",
                "dept-a",
                payload={"head": "r1"},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "scheduled for removal" in (result.error_message or "")

    @pytest.mark.parametrize(
        "blank_value",
        ["", "   "],
        ids=["empty", "whitespace"],
    )
    async def test_dry_run_create_role_rejects_blank_department(
        self,
        blank_value: str,
    ) -> None:
        """A blank ``department`` must fail dry_run rather than being
        silently treated as omitted."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "r1",
                payload={"description": "d", "department": blank_value},
            ),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        assert "must be a non-blank string" in (result.error_message or "")

    @pytest.mark.parametrize(
        "value",
        [None, "", "   "],
        ids=["None", "empty", "whitespace"],
    )
    async def test_dry_run_create_role_rejects_missing_description(
        self,
        value: object,
    ) -> None:
        """create_role must reject None / blank description payloads."""
        applier = ArchitectureApplier(context=_FakeArchContext())
        proposal = _proposal_architecture(
            _arch("create_role", "r1", payload={"description": value}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        msg = result.error_message or ""
        assert "description" in msg
        assert ("not be blank" in msg) or ("not be None" in msg)

    async def test_dry_run_cancelled_create_role_releases_department_ref(
        self,
    ) -> None:
        """A create_role followed by remove_role for the same name must
        release its in-flight department reference so a later
        remove_department for that dept is not falsely blocked."""
        applier = ArchitectureApplier(
            context=_FakeArchContext(departments=frozenset({"engineering"})),
        )
        proposal = _proposal_architecture(
            _arch(
                "create_role",
                "r1",
                payload={"description": "d", "department": "engineering"},
            ),
            _arch("remove_role", "r1"),
            _arch("remove_department", "engineering"),
        )
        result = await applier.dry_run(proposal)
        assert result.success, result.error_message

    async def test_dry_run_cancelled_create_dept_releases_role_head_ref(
        self,
    ) -> None:
        """A create_department followed by remove_department must
        release its in-flight role-head reference so a later
        remove_role for that head is not falsely blocked."""
        applier = ArchitectureApplier(
            context=_FakeArchContext(roles=frozenset({"r1"})),
        )
        proposal = _proposal_architecture(
            _arch(
                "create_department",
                "dept-a",
                payload={"head": "r1"},
            ),
            _arch("remove_department", "dept-a"),
            _arch("remove_role", "r1"),
        )
        result = await applier.dry_run(proposal)
        assert result.success, result.error_message

    async def test_dry_run_context_exception_surfaces_as_validation_error(
        self,
    ) -> None:
        """A context helper that raises is funnelled into the normal
        validation error path instead of escaping ``dry_run``."""

        class _BoomContext:
            def has_role(self, name: str) -> bool:
                msg = "registry offline"
                raise RuntimeError(msg)

            def has_department(self, name: str) -> bool:
                return False

            def has_workflow(self, name: str) -> bool:
                return False

            def role_in_use(self, name: str) -> bool:
                return False

            def department_in_use(self, name: str) -> bool:
                return False

            async def apply_change(
                self, change: ArchitectureChange
            ) -> Callable[[], Awaitable[None]]:
                async def _undo() -> None: ...

                return _undo

            async def refresh_snapshot(self) -> None: ...

        applier = ArchitectureApplier(context=_BoomContext())
        proposal = _proposal_architecture(
            _arch("create_role", "r1", payload={"description": "d"}),
        )
        result = await applier.dry_run(proposal)
        assert not result.success
        message = result.error_message or ""
        assert "context raised RuntimeError" in message
        assert "registry offline" in message
