"""In-memory ``PersistenceBackend`` fake for tests.

Extracted from ``tests/unit/api/fakes.py`` to keep that module under
the 800-line budget.  Imports point directly at the extracted modules
(``fake_user_repository``, ``fakes``).
"""

import contextlib
from typing import TYPE_CHECKING, Literal
from unittest.mock import Mock

from pydantic import AwareDatetime, BaseModel

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.types import NotBlankStr
from synthorg.hr.training.models import TrainingPlan, TrainingPlanStatus, TrainingResult
from synthorg.meta.rules.custom import CustomRuleDefinition
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.custom_rule_protocol import CustomRuleFilterSpec
from synthorg.persistence.integration_stubs import (
    InMemoryConnectionRepository,
    InMemoryConnectionSecretRepository,
    InMemoryOAuthStateRepository,
    InMemoryWebhookReceiptRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.provider_audit_protocol import ProviderAuditFilterSpec
from synthorg.providers.management.capability_dtos import (
    PresetOverride,
    ProviderAuditEvent,
)
from synthorg.security.rules.risk_override import RiskTierOverride
from synthorg.security.ssrf_violation import SsrfViolation, SsrfViolationStatus
from synthorg.versioning.models import VersionSnapshot

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from unittest.mock import AsyncMock

    from synthorg.budget.config import BudgetConfig
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.company import Company
    from synthorg.core.role import Role
    from synthorg.hr.evaluation.config import EvaluationConfig
    from synthorg.persistence.circuit_breaker_protocol import (
        CircuitBreakerStateRecord,
    )
    from synthorg.persistence.training_protocol import TrainingPlanFilterSpec
from tests.unit.api.fake_user_repository import FakeUserRepository
from tests.unit.api.fakes import (
    FakeAgentStateRepository,
    FakeApiKeyRepository,
    FakeArtifactRepository,
    FakeAuditRepository,
    FakeCheckpointRepository,
    FakeCodebaseStructureMapRepository,
    FakeCollaborationMetricRepository,
    FakeCostRecordRepository,
    FakeDecisionRepository,
    FakeDocsRepository,
    FakeFlightRecorderFrameRepository,
    FakeHeartbeatRepository,
    FakeLifecycleEventRepository,
    FakeMessageRepository,
    FakeParkedContextRepository,
    FakePersonalityPresetRepository,
    FakeProjectEnvironmentRepository,
    FakeProjectRepository,
    FakeProjectWorkspaceRepository,
    FakeSettingsRepository,
    FakeTaskMetricRepository,
    FakeTaskRepository,
)
from tests.unit.api.fakes_workflow import (
    FakeSubworkflowRepository,
    FakeWorkflowDefinitionRepository,
    FakeWorkflowExecutionRepository,
    FakeWorkflowVersionRepository,
)
from tests.unit.knowledge._fakes import (
    FakeChunkProvenanceRepository,
    FakeKnowledgeSourceRepository,
)
from tests.unit.research._fakes import InMemoryResearchRunRepository

__all__ = [
    "FakePersistenceBackend",
    "FakeRiskOverrideRepository",
    "FakeSsrfViolationRepository",
]


class FakeRiskOverrideRepository:
    """In-memory risk override repository for tests."""

    def __init__(self) -> None:
        self._overrides: dict[str, RiskTierOverride] = {}

    async def save(self, override: RiskTierOverride) -> None:
        if override.id in self._overrides:
            msg = f"Risk override {override.id!r} already exists"
            raise DuplicateRecordError(msg)
        self._overrides[override.id] = override

    async def get(
        self,
        override_id: NotBlankStr,
    ) -> RiskTierOverride | None:
        return self._overrides.get(override_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RiskTierOverride, ...]:
        ordered = sorted(self._overrides.values(), key=lambda o: o.id)
        return tuple(ordered[offset : offset + limit])

    async def list_active(
        self,
        *,
        limit: int = 100,
    ) -> tuple[RiskTierOverride, ...]:
        limit = validate_pagination_args(limit, offset=0, event="fake.list_active")
        active = [o for o in self._overrides.values() if o.is_active]
        active.sort(key=lambda o: o.created_at, reverse=True)
        return tuple(active[:limit])

    async def delete(self, override_id: NotBlankStr) -> bool:
        return self._overrides.pop(override_id, None) is not None

    async def revoke(
        self,
        override_id: NotBlankStr,
        *,
        revoked_by: NotBlankStr,
        revoked_at: AwareDatetime,
    ) -> bool:
        ovr = self._overrides.get(override_id)
        if ovr is None or ovr.revoked_at is not None:
            return False
        self._overrides[override_id] = ovr.model_copy(
            update={"revoked_at": revoked_at, "revoked_by": revoked_by},
        )
        return True


class FakeSsrfViolationRepository:
    """In-memory SSRF violation repository for tests."""

    def __init__(self) -> None:
        self._violations: dict[str, SsrfViolation] = {}

    async def save(self, violation: SsrfViolation) -> None:
        if violation.id in self._violations:
            msg = f"SSRF violation {violation.id!r} already exists"
            raise DuplicateRecordError(msg)
        self._violations[violation.id] = violation

    async def get(
        self,
        violation_id: NotBlankStr,
    ) -> SsrfViolation | None:
        return self._violations.get(violation_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[SsrfViolation, ...]:
        ordered = sorted(self._violations.values(), key=lambda v: v.id)
        return tuple(ordered[offset : offset + limit])

    async def delete(self, violation_id: NotBlankStr) -> bool:
        return self._violations.pop(violation_id, None) is not None

    async def list_violations(
        self,
        *,
        status: SsrfViolationStatus | None = None,
        limit: int = 100,
    ) -> tuple[SsrfViolation, ...]:
        if limit <= 0:
            msg = "limit must be positive"
            raise ValueError(msg)
        items = list(self._violations.values())
        if status is not None:
            items = [v for v in items if v.status == status]
        items.sort(key=lambda v: v.timestamp, reverse=True)
        return tuple(items[:limit])

    async def update_status(
        self,
        violation_id: NotBlankStr,
        *,
        status: SsrfViolationStatus,
        resolved_by: NotBlankStr,
        resolved_at: AwareDatetime,
    ) -> bool:
        if status == SsrfViolationStatus.PENDING:
            msg = "Cannot transition a violation back to PENDING"
            raise ValueError(msg)
        v = self._violations.get(violation_id)
        if v is None or v.status != SsrfViolationStatus.PENDING:
            return False
        self._violations[violation_id] = v.model_copy(
            update={
                "status": status,
                "resolved_by": resolved_by,
                "resolved_at": resolved_at,
            },
        )
        return True


class FakeCircuitBreakerStateRepository:
    """In-memory circuit breaker state repository for tests."""

    def __init__(self) -> None:

        self._store: dict[tuple[str, str], CircuitBreakerStateRecord] = {}

    async def save(self, entity: CircuitBreakerStateRecord) -> None:
        self._store[(entity.pair_key_a, entity.pair_key_b)] = entity

    async def get(self, entity_id: tuple[str, str]) -> CircuitBreakerStateRecord | None:
        return self._store.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CircuitBreakerStateRecord, ...]:
        ordered = sorted(self._store.items(), key=lambda kv: kv[0])
        return tuple(v for _, v in ordered[offset : offset + limit])

    async def load_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CircuitBreakerStateRecord, ...]:
        ordered = sorted(self._store.items(), key=lambda kv: kv[0])
        return tuple(v for _, v in ordered[offset : offset + limit])

    async def delete(self, entity_id: tuple[str, str]) -> bool:
        if entity_id in self._store:
            del self._store[entity_id]
            return True
        return False


class FakeVersionRepository[T: BaseModel]:
    """In-memory VersionRepository[T] for tests, parametrised on the snapshot type."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, int], VersionSnapshot[T]] = {}

    async def save_version(self, version: VersionSnapshot[T]) -> bool:
        key = (version.entity_id, version.version)
        was_new = key not in self._store
        self._store.setdefault(key, version)
        return was_new

    async def get_version(
        self, entity_id: NotBlankStr, version: int
    ) -> VersionSnapshot[T] | None:
        return self._store.get((entity_id, version))

    async def get_latest_version(
        self, entity_id: NotBlankStr
    ) -> VersionSnapshot[T] | None:
        candidates = [v for (eid, _), v in self._store.items() if eid == entity_id]
        return max(candidates, key=lambda v: v.version) if candidates else None

    async def get_by_content_hash(
        self, entity_id: NotBlankStr, content_hash: NotBlankStr
    ) -> VersionSnapshot[T] | None:
        for (eid, _), v in self._store.items():
            if eid == entity_id and v.content_hash == content_hash:
                return v
        return None

    async def list_versions(
        self,
        entity_id: NotBlankStr,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[VersionSnapshot[T], ...]:
        candidates = sorted(
            (v for (eid, _), v in self._store.items() if eid == entity_id),
            key=lambda v: v.version,
            reverse=True,
        )
        return tuple(candidates[offset : offset + limit])

    async def count_versions(self, entity_id: NotBlankStr) -> int:
        return sum(1 for eid, _ in self._store if eid == entity_id)

    async def delete_versions_for_entity(self, entity_id: NotBlankStr) -> int:
        to_delete = [k for k in self._store if k[0] == entity_id]
        for k in to_delete:
            del self._store[k]
        return len(to_delete)

    def clear(self) -> None:
        """Reset all stored snapshots for test isolation."""
        self._store.clear()


class FakeTrainingPlanRepository:
    """In-memory fake for ``TrainingPlanRepository``."""

    def __init__(self) -> None:
        self._plans: dict[str, TrainingPlan] = {}

    async def save(self, entity: TrainingPlan) -> None:
        self._plans[str(entity.id)] = entity

    async def get(self, entity_id: NotBlankStr) -> TrainingPlan | None:
        return self._plans.get(str(entity_id))

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._plans.pop(str(entity_id), None) is not None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[TrainingPlan, ...]:
        ordered = sorted(self._plans.values(), key=lambda p: str(p.id))
        return tuple(ordered[offset : offset + limit])

    async def query(
        self,
        filter_spec: TrainingPlanFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[TrainingPlan, ...]:
        plans = list(self._plans.values())
        if filter_spec.agent_id is not None:
            plans = [
                p for p in plans if str(p.new_agent_id) == str(filter_spec.agent_id)
            ]
        if filter_spec.status is not None:
            plans = [p for p in plans if p.status == filter_spec.status]
        ordered = sorted(plans, key=lambda p: str(p.id))
        return tuple(ordered[offset : offset + limit])

    async def count(self, filter_spec: TrainingPlanFilterSpec) -> int:
        plans = list(self._plans.values())
        if filter_spec.agent_id is not None:
            plans = [
                p for p in plans if str(p.new_agent_id) == str(filter_spec.agent_id)
            ]
        if filter_spec.status is not None:
            plans = [p for p in plans if p.status == filter_spec.status]
        return len(plans)

    async def latest_pending(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingPlan | None:
        pending = [
            p
            for p in self._plans.values()
            if str(p.new_agent_id) == str(agent_id)
            and p.status == TrainingPlanStatus.PENDING
        ]
        if not pending:
            return None
        return max(pending, key=lambda p: p.created_at)

    async def latest_by_agent(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingPlan | None:
        plans = [
            p for p in self._plans.values() if str(p.new_agent_id) == str(agent_id)
        ]
        if not plans:
            return None
        return max(plans, key=lambda p: p.created_at)

    async def list_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        limit: int = 100,
    ) -> tuple[TrainingPlan, ...]:
        plans = [
            p for p in self._plans.values() if str(p.new_agent_id) == str(agent_id)
        ]
        plans.sort(key=lambda p: p.created_at, reverse=True)
        return tuple(plans[:limit])


class FakeTrainingResultRepository:
    """In-memory fake for ``TrainingResultRepository``."""

    def __init__(self) -> None:
        self._results: dict[str, TrainingResult] = {}

    async def save(self, entity: TrainingResult) -> None:
        plan_key = str(entity.plan_id)
        for rid, r in self._results.items():
            if str(r.plan_id) == plan_key and rid != str(entity.id):
                msg = f"UNIQUE constraint: plan_id {plan_key!r} already exists"
                raise ValueError(msg)
        self._results[str(entity.id)] = entity

    async def get(self, entity_id: NotBlankStr) -> TrainingResult | None:
        return self._results.get(str(entity_id))

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._results.pop(str(entity_id), None) is not None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[TrainingResult, ...]:
        ordered = sorted(self._results.values(), key=lambda r: str(r.id))
        return tuple(ordered[offset : offset + limit])

    async def get_by_plan(
        self,
        plan_id: NotBlankStr,
    ) -> TrainingResult | None:
        for r in self._results.values():
            if str(r.plan_id) == str(plan_id):
                return r
        return None

    async def get_latest(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingResult | None:
        agent_results = [
            r for r in self._results.values() if str(r.new_agent_id) == str(agent_id)
        ]
        if not agent_results:
            return None
        return max(agent_results, key=lambda r: r.completed_at)


class _FakeProviderAuditRepo:
    """Minimal in-memory ``ProviderAuditRepo`` stub for tests.

    Records all events under one provider key with monotonically
    increasing integer ids; supports keyset pagination on ``id``.
    """

    def __init__(self) -> None:
        self._events: list[ProviderAuditEvent] = []
        self._next_id = 1

    def clear(self) -> None:
        """Reset to a fresh, empty repo with the id counter at 1."""
        self._events = []
        self._next_id = 1

    async def record(self, event: ProviderAuditEvent) -> ProviderAuditEvent:
        saved = event.model_copy(update={"id": self._next_id})
        self._next_id += 1
        self._events.append(saved)
        return saved

    async def append(self, event: ProviderAuditEvent) -> None:
        await self.record(event)

    async def list(
        self,
        *,
        provider_name: NotBlankStr,
        after_id: int | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProviderAuditEvent, ...], bool]:
        # ``ProviderAuditEvent.id`` is ``int | None`` because the model
        # carries unsaved-state events too, but ``record()`` assigns the
        # id before appending so every event in ``self._events`` has an
        # int id at runtime. ``e.id or 0`` makes that invariant explicit
        # to the type-checker (and is safe under ``ge=1`` on the field).
        rows = sorted(
            (e for e in self._events if e.provider_name == provider_name),
            key=lambda e: e.id or 0,
            reverse=True,
        )
        if after_id is not None:
            rows = [e for e in rows if e.id is not None and e.id < after_id]
        page = rows[:limit]
        has_more = len(rows) > limit
        return tuple(page), has_more

    async def query(
        self,
        filter_spec: ProviderAuditFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- canonical ADR-0001 page size
        offset: int = 0,
    ) -> tuple[ProviderAuditEvent, ...]:
        rows = sorted(
            (e for e in self._events if e.provider_name == filter_spec.provider_name),
            key=lambda e: e.id or 0,
            reverse=True,
        )
        if filter_spec.after_id is not None:
            rows = [e for e in rows if e.id is not None and e.id < filter_spec.after_id]
        return tuple(rows[offset : offset + limit])

    async def purge_before(self, threshold: AwareDatetime) -> int:
        before = len(self._events)
        self._events = [e for e in self._events if e.occurred_at >= threshold]
        return before - len(self._events)

    async def purge_before_id(self, *, before_id: int) -> int:
        before = len(self._events)
        self._events = [
            e for e in self._events if e.id is not None and e.id >= before_id
        ]
        return before - len(self._events)


class _FakePresetOverrideRepo:
    """Minimal in-memory ``PresetOverrideRepo`` stub for tests."""

    def __init__(self) -> None:
        self._overrides: dict[str, PresetOverride] = {}

    def clear(self) -> None:
        """Reset to a fresh, empty override map."""
        self._overrides = {}

    async def get(self, preset_name: NotBlankStr) -> PresetOverride | None:
        return self._overrides.get(preset_name)

    async def save(self, override: PresetOverride) -> None:
        self._overrides[override.preset_name] = override

    async def delete(self, preset_name: NotBlankStr) -> bool:
        return self._overrides.pop(preset_name, None) is not None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- canonical ADR-0001 page size
        offset: int = 0,
    ) -> tuple[PresetOverride, ...]:
        items = sorted(self._overrides.values(), key=lambda o: o.preset_name)
        return tuple(items[offset : offset + limit])


class FakeCustomRuleRepository:
    """In-memory fake for ``CustomRuleRepository``."""

    def __init__(self) -> None:
        self._rules: dict[str, CustomRuleDefinition] = {}

    async def save(self, rule: CustomRuleDefinition) -> None:
        from synthorg.core.persistence_errors import (
            ConstraintViolationError,
        )

        for existing in self._rules.values():
            if existing.name == rule.name and existing.id != rule.id:
                msg = f"Custom rule name '{rule.name}' already exists"
                raise ConstraintViolationError(
                    msg,
                    constraint="custom_rules_name_unique",
                )
        self._rules[str(rule.id)] = rule

    async def get(
        self,
        rule_id: NotBlankStr,
    ) -> CustomRuleDefinition | None:
        return self._rules.get(str(rule_id))

    async def get_by_name(
        self,
        name: NotBlankStr,
    ) -> CustomRuleDefinition | None:
        for r in self._rules.values():
            if r.name == name:
                return r
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CustomRuleDefinition, ...]:
        from synthorg.persistence.custom_rule_protocol import (
            CustomRuleFilterSpec,
        )

        return await self.query(
            CustomRuleFilterSpec(),
            limit=limit,
            offset=offset,
        )

    async def query(
        self,
        filter_spec: CustomRuleFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CustomRuleDefinition, ...]:
        limit = validate_pagination_args(limit, offset=offset, event="fake.query")
        rules = list(self._rules.values())
        if filter_spec.enabled_only:
            rules = [r for r in rules if r.enabled]
        ordered = sorted(rules, key=lambda r: r.name)
        return tuple(ordered[offset : offset + limit])

    async def count(self, filter_spec: CustomRuleFilterSpec) -> int:
        rules = list(self._rules.values())
        if filter_spec.enabled_only:
            rules = [r for r in rules if r.enabled]
        return len(rules)

    async def delete(self, rule_id: NotBlankStr) -> bool:
        key = str(rule_id)
        if key in self._rules:
            del self._rules[key]
            return True
        return False


def _clear_attr(value: object) -> None:
    """Reset one attribute of ``FakePersistenceBackend`` in-place.

    Strategy: skip primitives that have no clear-able state, clear
    mutable containers directly, and call any nested ``clear()``
    method exposed by fake-repo objects.  Falls back to walking
    ``__dict__`` for repos that do not yet implement their own
    ``clear()``.  Extracted to a module-level helper so the
    iteration body in ``FakePersistenceBackend.clear`` stays under
    the project's complexity ceiling.
    """
    # Skip primitives that have no internal state to reset.
    if value is None or isinstance(value, (bool, str, int, float)):
        return
    # Clear mutable containers directly.
    if isinstance(value, (dict, list, set)):
        value.clear()
        return
    # Repositories that expose a dedicated ``clear()`` know how to
    # reset every piece of internal state -- including scalar
    # counters (e.g. ``_next_id``) that the generic walk below
    # cannot recognise.  Prefer that hook when available; the
    # generic walk is the legacy fallback.
    #
    # ``unittest.mock.Mock`` objects expose every attribute lookup
    # as a callable Mock (so ``getattr(stub, "clear")`` is a Mock,
    # not a real reset).  Skip them by checking for the ``Mock``
    # marker; otherwise the generic walk is what the lazy stub
    # actually expects.
    from unittest.mock import Mock

    if not isinstance(value, Mock):
        repo_clear = getattr(value, "clear", None)
        if callable(repo_clear):
            try:
                repo_clear()
            except TypeError:
                pass
            else:
                return
    # Clear internal state of fake repository objects.
    try:
        inner_vars = vars(value)
    except TypeError:
        # Objects that legitimately have no ``__dict__`` (e.g.
        # ``unittest.mock.AsyncMock`` bindings on lazy stubs).
        return
    for inner_value in inner_vars.values():
        if isinstance(inner_value, (dict, list, set)):
            inner_value.clear()


class FakePersistenceBackend(PersistenceBackend):
    """In-memory persistence backend for tests."""

    def __init__(self) -> None:
        self._artifacts = FakeArtifactRepository()
        self._projects = FakeProjectRepository()
        self._project_workspaces = FakeProjectWorkspaceRepository()
        self._codebase_structure_maps = FakeCodebaseStructureMapRepository()
        self._project_environments = FakeProjectEnvironmentRepository()
        self._project_docs = FakeDocsRepository()
        self._knowledge_sources = FakeKnowledgeSourceRepository()
        self._knowledge_provenance = FakeChunkProvenanceRepository()
        self._research_runs = InMemoryResearchRunRepository()
        self._custom_presets = FakePersonalityPresetRepository()
        self._workflow_definitions = FakeWorkflowDefinitionRepository()
        self._workflow_executions = FakeWorkflowExecutionRepository()
        self._workflow_versions = FakeWorkflowVersionRepository()
        self._subworkflows = FakeSubworkflowRepository(
            definition_repo=self._workflow_definitions,
        )
        self._identity_versions: FakeVersionRepository[AgentIdentity] = (
            FakeVersionRepository()
        )
        self._evaluation_config_versions: FakeVersionRepository[EvaluationConfig] = (
            FakeVersionRepository()
        )
        self._budget_config_versions: FakeVersionRepository[BudgetConfig] = (
            FakeVersionRepository()
        )
        self._company_versions: FakeVersionRepository[Company] = FakeVersionRepository()
        self._role_versions: FakeVersionRepository[Role] = FakeVersionRepository()
        self._risk_overrides = FakeRiskOverrideRepository()
        self._ssrf_violations = FakeSsrfViolationRepository()
        self._circuit_breaker_state = FakeCircuitBreakerStateRepository()
        self._tasks = FakeTaskRepository()
        self._cost_records = FakeCostRecordRepository()
        self._messages = FakeMessageRepository()
        self._lifecycle_events = FakeLifecycleEventRepository()
        self._task_metrics = FakeTaskMetricRepository()
        self._collaboration_metrics = FakeCollaborationMetricRepository()
        self._parked_contexts = FakeParkedContextRepository()
        self._audit_entries = FakeAuditRepository()
        self._provider_audit_events = _FakeProviderAuditRepo()
        self._preset_overrides = _FakePresetOverrideRepo()
        self._decision_records = FakeDecisionRepository()
        self._users = FakeUserRepository()
        self._api_keys = FakeApiKeyRepository()
        self._checkpoints = FakeCheckpointRepository()
        self._flight_recorder_frames = FakeFlightRecorderFrameRepository()
        self._heartbeats = FakeHeartbeatRepository()
        self._agent_states = FakeAgentStateRepository()
        self._settings_repo = FakeSettingsRepository()
        self._training_plans_repo = FakeTrainingPlanRepository()
        self._training_results_repo = FakeTrainingResultRepository()
        self._custom_rules_repo = FakeCustomRuleRepository()
        self._connections_stub = InMemoryConnectionRepository()
        self._connection_secrets_stub = InMemoryConnectionSecretRepository()
        self._oauth_states_stub = InMemoryOAuthStateRepository()
        self._webhook_receipts_stub = InMemoryWebhookReceiptRepository()
        # Legacy flat KV store for get_setting/set_setting (pre-namespaced).
        # The `settings` property returns `_settings_repo` (namespaced repo).
        self._settings: dict[str, str] = {}
        self._connected = False
        # Lazy-instantiated, cached protocol stand-ins for the A1-A6
        # consolidation repos.  Cached so repeated property access
        # returns the same mock instance (tests assert against it).
        self._sessions_stub: AsyncMock | None = None
        self._refresh_tokens_stub: AsyncMock | None = None
        self._mcp_installations_stub: AsyncMock | None = None
        self._org_facts_stub: AsyncMock | None = None
        self._ontology_entities_stub: AsyncMock | None = None
        self._ontology_drift_stub: AsyncMock | None = None
        self._project_cost_aggregates_stub: AsyncMock | None = None
        self._fine_tune_checkpoints_stub: AsyncMock | None = None
        self._fine_tune_runs_stub: AsyncMock | None = None
        self._meeting_cooldown_stub: AsyncMock | None = None
        self._ceremony_scheduler_state_stub: AsyncMock | None = None
        self._tracked_container_stub: AsyncMock | None = None
        self._idempotency_keys_stub: AsyncMock | None = None
        self._seen_claims_stub: AsyncMock | None = None
        self._principle_overrides_stub: AsyncMock | None = None

    def clear(self) -> None:
        """Reset all in-memory state for test isolation.

        Clears repository contents in-place so any services holding
        references to repository objects observe the reset.  Preserves
        object identity and the connection flag.  Lazy ``_*_stub`` fields
        are reset to ``None`` so the next property access reconstructs a
        fresh ``AsyncMock`` with no leaked ``call_count`` / ``side_effect``
        from a prior test.
        """
        for attr_name in list(vars(self)):
            if attr_name == "_connected":
                continue
            if attr_name.endswith("_stub") and isinstance(
                getattr(self, attr_name, None),
                Mock,
            ):
                setattr(self, attr_name, None)
                continue
            _clear_attr(getattr(self, attr_name))

    async def connect(self) -> None:
        self._connected = True

    def mark_connected(self) -> None:
        """Set the connected flag without awaiting.

        Sync convenience for test scaffolding that needs the backend to
        report ``is_connected`` from a sync helper (e.g. building a
        Litestar ``TestClient`` outside an event loop). Async tests
        should still ``await connect()``.
        """
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def get_db(self) -> object:
        msg = "FakePersistenceBackend does not expose a real DB"
        raise NotImplementedError(msg)

    @property
    def kind(self) -> Literal["sqlite", "postgres"]:
        # FakePersistenceBackend has no real backend kind; callers that
        # branch on ``kind`` should not be using the fake. Return
        # ``"sqlite"`` as the closest single-process analogue for the
        # in-memory store the fake actually provides.
        return "sqlite"

    def write_context(self) -> AbstractAsyncContextManager[None]:
        # The fake is in-memory and not used in any path that needs the
        # cross-statement write-context guard; yield a no-op context so
        # repository code paths that wrap mutations in ``async with
        # backend.write_context():`` still compose cleanly under tests.
        return contextlib.nullcontext()

    async def health_check(self) -> bool:
        return self._connected

    async def migrate(self) -> None:
        pass

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def backend_name(self) -> str:
        return "fake"

    @property
    def artifacts(self) -> FakeArtifactRepository:
        return self._artifacts

    @property
    def projects(self) -> FakeProjectRepository:
        return self._projects

    @property
    def project_workspaces(self) -> FakeProjectWorkspaceRepository:
        return self._project_workspaces

    @property
    def codebase_structure_maps(self) -> FakeCodebaseStructureMapRepository:
        return self._codebase_structure_maps

    @property
    def project_environments(self) -> FakeProjectEnvironmentRepository:
        return self._project_environments

    @property
    def project_docs(self) -> FakeDocsRepository:
        return self._project_docs

    @property
    def knowledge_sources(self) -> FakeKnowledgeSourceRepository:
        return self._knowledge_sources

    @property
    def knowledge_provenance(self) -> FakeChunkProvenanceRepository:
        return self._knowledge_provenance

    @property
    def research_runs(self) -> InMemoryResearchRunRepository:
        return self._research_runs

    @property
    def tasks(self) -> FakeTaskRepository:
        return self._tasks

    @property
    def cost_records(self) -> FakeCostRecordRepository:
        return self._cost_records

    @property
    def messages(self) -> FakeMessageRepository:
        return self._messages

    @property
    def lifecycle_events(self) -> FakeLifecycleEventRepository:
        return self._lifecycle_events

    @property
    def task_metrics(self) -> FakeTaskMetricRepository:
        return self._task_metrics

    @property
    def collaboration_metrics(self) -> FakeCollaborationMetricRepository:
        return self._collaboration_metrics

    @property
    def parked_contexts(self) -> FakeParkedContextRepository:
        return self._parked_contexts

    @property
    def audit_entries(self) -> FakeAuditRepository:
        return self._audit_entries

    @property
    def provider_audit_events(self) -> _FakeProviderAuditRepo:
        return self._provider_audit_events

    @property
    def preset_overrides(self) -> _FakePresetOverrideRepo:
        return self._preset_overrides

    @property
    def decision_records(self) -> FakeDecisionRepository:
        return self._decision_records

    @property
    def users(self) -> FakeUserRepository:
        return self._users

    @property
    def api_keys(self) -> FakeApiKeyRepository:
        return self._api_keys

    @property
    def checkpoints(self) -> FakeCheckpointRepository:
        return self._checkpoints

    @property
    def flight_recorder_frames(self) -> FakeFlightRecorderFrameRepository:
        return self._flight_recorder_frames

    @property
    def heartbeats(self) -> FakeHeartbeatRepository:
        return self._heartbeats

    @property
    def agent_states(self) -> FakeAgentStateRepository:
        return self._agent_states

    @property
    def settings(self) -> FakeSettingsRepository:
        return self._settings_repo

    @property
    def custom_presets(self) -> FakePersonalityPresetRepository:
        return self._custom_presets

    @property
    def workflow_definitions(self) -> FakeWorkflowDefinitionRepository:
        return self._workflow_definitions

    @property
    def workflow_executions(self) -> FakeWorkflowExecutionRepository:
        return self._workflow_executions

    @property
    def subworkflows(self) -> FakeSubworkflowRepository:
        return self._subworkflows

    @property
    def workflow_versions(self) -> FakeWorkflowVersionRepository:
        return self._workflow_versions

    @property
    def identity_versions(self) -> FakeVersionRepository[AgentIdentity]:
        return self._identity_versions

    @property
    def evaluation_config_versions(self) -> FakeVersionRepository[EvaluationConfig]:
        return self._evaluation_config_versions

    @property
    def budget_config_versions(self) -> FakeVersionRepository[BudgetConfig]:
        return self._budget_config_versions

    @property
    def company_versions(self) -> FakeVersionRepository[Company]:
        return self._company_versions

    @property
    def role_versions(self) -> FakeVersionRepository[Role]:
        return self._role_versions

    @property
    def risk_overrides(self) -> FakeRiskOverrideRepository:
        return self._risk_overrides

    @property
    def ssrf_violations(self) -> FakeSsrfViolationRepository:
        return self._ssrf_violations

    @property
    def circuit_breaker_state(self) -> FakeCircuitBreakerStateRepository:
        return self._circuit_breaker_state

    @property
    def connections(self) -> InMemoryConnectionRepository:
        """In-memory connection repository."""
        return self._connections_stub

    @property
    def connection_secrets(self) -> InMemoryConnectionSecretRepository:
        """In-memory connection secret repository."""
        return self._connection_secrets_stub

    @property
    def oauth_states(self) -> InMemoryOAuthStateRepository:
        """In-memory OAuth state repository."""
        return self._oauth_states_stub

    @property
    def webhook_receipts(self) -> InMemoryWebhookReceiptRepository:
        """In-memory webhook receipt repository."""
        return self._webhook_receipts_stub

    @property
    def training_plans(self) -> FakeTrainingPlanRepository:
        """Fake training plan repository."""
        return self._training_plans_repo

    @property
    def training_results(self) -> FakeTrainingResultRepository:
        """Fake training result repository."""
        return self._training_results_repo

    @property
    def custom_rules(self) -> FakeCustomRuleRepository:
        """Fake custom rule repository."""
        return self._custom_rules_repo

    @property
    def sessions(self) -> AsyncMock:
        """Cached fake session repository.

        Spec'd to ``SessionRepository`` so the mock surface mirrors the
        protocol; ``is_revoked`` is sync on the real protocol (auth
        hot-path) and the spec'd child mock returns ``False`` by default.
        """
        from unittest.mock import AsyncMock

        from synthorg.persistence.auth_protocol import SessionRepository

        if self._sessions_stub is None:
            stub = AsyncMock(spec=SessionRepository)
            stub.is_revoked.return_value = False
            self._sessions_stub = stub
        return self._sessions_stub

    @property
    def refresh_tokens(self) -> AsyncMock:
        """Cached fake refresh-token repository."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.auth_protocol import RefreshTokenRepository

        if self._refresh_tokens_stub is None:
            self._refresh_tokens_stub = AsyncMock(spec=RefreshTokenRepository)
        return self._refresh_tokens_stub

    @property
    def mcp_installations(self) -> AsyncMock:
        """Cached fake MCP installations repository."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.mcp_protocol import McpInstallationRepository

        if self._mcp_installations_stub is None:
            self._mcp_installations_stub = AsyncMock(spec=McpInstallationRepository)
        return self._mcp_installations_stub

    @property
    def org_facts(self) -> AsyncMock:
        """Cached fake org fact repository."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.memory_protocol import OrgFactRepository

        if self._org_facts_stub is None:
            self._org_facts_stub = AsyncMock(spec=OrgFactRepository)
        return self._org_facts_stub

    @property
    def ontology_entities(self) -> AsyncMock:
        """Cached fake ontology entity repository."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.ontology_protocol import OntologyEntityRepository

        if self._ontology_entities_stub is None:
            self._ontology_entities_stub = AsyncMock(spec=OntologyEntityRepository)
        return self._ontology_entities_stub

    @property
    def ontology_drift(self) -> AsyncMock:
        """Cached fake ontology drift-report repository."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.ontology_protocol import (
            OntologyDriftReportRepository,
        )

        if self._ontology_drift_stub is None:
            self._ontology_drift_stub = AsyncMock(spec=OntologyDriftReportRepository)
        return self._ontology_drift_stub

    @property
    def project_cost_aggregates(self) -> AsyncMock:
        """Cached fake project cost aggregate repository."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.project_cost_aggregate_protocol import (
            ProjectCostAggregateRepository,
        )

        if self._project_cost_aggregates_stub is None:
            self._project_cost_aggregates_stub = AsyncMock(
                spec=ProjectCostAggregateRepository,
            )
        return self._project_cost_aggregates_stub

    @property
    def fine_tune_checkpoints(self) -> AsyncMock:
        """Cached fake fine-tune checkpoint repository."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.fine_tune_protocol import (
            FineTuneCheckpointRepository,
        )

        if self._fine_tune_checkpoints_stub is None:
            self._fine_tune_checkpoints_stub = AsyncMock(
                spec=FineTuneCheckpointRepository,
            )
        return self._fine_tune_checkpoints_stub

    @property
    def fine_tune_runs(self) -> AsyncMock:
        """Cached fake fine-tune run repository."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.fine_tune_protocol import FineTuneRunRepository

        if self._fine_tune_runs_stub is None:
            self._fine_tune_runs_stub = AsyncMock(spec=FineTuneRunRepository)
        return self._fine_tune_runs_stub

    @property
    def meeting_cooldown(self) -> AsyncMock:
        """Cached fake meeting cooldown repository (WP-1)."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.meeting_cooldown_protocol import (
            MeetingCooldownRepository,
        )

        if self._meeting_cooldown_stub is None:
            stub = AsyncMock(spec=MeetingCooldownRepository)
            stub.load_all.return_value = ()
            self._meeting_cooldown_stub = stub
        return self._meeting_cooldown_stub

    @property
    def ceremony_scheduler_state(self) -> AsyncMock:
        """Cached fake ceremony scheduler state repository (WP-1)."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.ceremony_scheduler_state_protocol import (
            CeremonySchedulerStateRepository,
        )

        if self._ceremony_scheduler_state_stub is None:
            stub = AsyncMock(spec=CeremonySchedulerStateRepository)
            stub.get.return_value = None
            stub.list_items.return_value = ()
            self._ceremony_scheduler_state_stub = stub
        return self._ceremony_scheduler_state_stub

    @property
    def tracked_containers(self) -> AsyncMock:
        """Cached fake tracked-container repository (WP-1)."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.tracked_container_protocol import (
            TrackedContainerRepository,
        )

        if self._tracked_container_stub is None:
            stub = AsyncMock(spec=TrackedContainerRepository)
            stub.load_all.return_value = ()
            stub.list_items.return_value = ()
            self._tracked_container_stub = stub
        return self._tracked_container_stub

    @property
    def idempotency_keys(self) -> AsyncMock:
        """Cached fake idempotency-keys repository."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.idempotency_protocol import IdempotencyRepository

        if self._idempotency_keys_stub is None:
            self._idempotency_keys_stub = AsyncMock(spec=IdempotencyRepository)
        return self._idempotency_keys_stub

    @property
    def seen_claims(self) -> AsyncMock:
        """Cached fake seen-claims repository (worker claim dedup)."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository

        if self._seen_claims_stub is None:
            stub = AsyncMock(spec=SeenClaimsRepository)
            stub.is_completed.return_value = False
            stub.prune_expired.return_value = 0
            self._seen_claims_stub = stub
        return self._seen_claims_stub

    @property
    def principle_overrides(self) -> AsyncMock:
        """Cached fake principle-overrides repository (rollback overlays)."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.principle_override_protocol import (
            PrincipleOverrideRepository,
        )

        if self._principle_overrides_stub is None:
            stub = AsyncMock(spec=PrincipleOverrideRepository)
            stub.get.return_value = None
            stub.list_items.return_value = ()
            self._principle_overrides_stub = stub
        return self._principle_overrides_stub

    def build_lockouts(self, auth_config: object) -> AsyncMock:
        """Fake lockout repository builder.

        Spec'd to ``LockoutRepository`` so the mock surface mirrors the
        protocol; ``is_locked`` is sync on the real protocol (auth
        hot-path) and its spec-bound child mock returns ``False`` by
        default. ``record_failure`` returns ``False`` so invalid logins
        don't spuriously trip ``AccountLockedError``.
        ``lockout_duration_seconds`` is set to ``0`` so Retry-After
        rendering sees a plain ``int`` rather than a child mock.
        """
        from unittest.mock import AsyncMock

        from synthorg.persistence.auth_protocol import LockoutRepository

        stub = AsyncMock(spec=LockoutRepository)
        stub.is_locked.return_value = False
        stub.record_failure.return_value = False
        stub.lockout_duration_seconds = 0
        return stub

    def build_escalations(
        self,
        *,
        notify_channel: str | None = None,
    ) -> AsyncMock:
        """Fake escalation repository builder."""
        from unittest.mock import AsyncMock

        from synthorg.persistence.escalation_protocol import EscalationQueueRepository

        return AsyncMock(spec=EscalationQueueRepository)

    def build_ontology_versioning(self) -> AsyncMock:
        """Fake ontology versioning factory -- returns a mock service."""
        from unittest.mock import AsyncMock

        from synthorg.versioning.service import VersioningService

        return AsyncMock(spec=VersioningService)

    async def get_setting(self, key: str) -> str | None:
        return self._settings.get(key)

    async def set_setting(self, key: str, value: str) -> None:
        self._settings[key] = value
