"""Unit tests for the shared governed-action approval gate.

Exercises :class:`ConnectionApprovalGate` and the signature helpers
directly (the forge / chat / external-api tools all route their writes
through them): parking, consuming a matching grant, auto-approval,
explicit-approval mismatch, caller binding, and risk classification.
"""

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.tools._governed_action import (
    ActionSignature,
    ConnectionApprovalGate,
    GovernedApprovalMismatchError,
    require_governed_args,
    signature_for,
)
from synthorg.tools.base import ToolExecutionResult

pytestmark = pytest.mark.unit

_ACTION_TYPE = "comms:external"
_NS = "forge_issue"
_CONN = "forge"


class _GovernedArgs(BaseModel):
    """A minimal args model satisfying the governed-tool contract."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    action: str
    value: str = "x"

    @property
    def is_write(self) -> bool:
        return self.action != "read"


class _UngovernedArgs(BaseModel):
    """An args model missing the governed contract (no ``is_write``)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: str


class _RaisingClassifier:
    def classify(self, action_type: str) -> ApprovalRiskLevel:
        _ = action_type
        msg = "classifier unavailable"
        raise ValueError(msg)


class _LowRiskClassifier:
    def classify(self, action_type: str) -> ApprovalRiskLevel:
        _ = action_type
        return ApprovalRiskLevel.LOW


def _gate(
    *,
    store: ApprovalStore,
    autonomy: EffectiveAutonomy | None = None,
    risk_classifier: _RaisingClassifier | _LowRiskClassifier | None = None,
    agent_id: str = "agent-1",
    task_id: str | None = "task-1",
) -> ConnectionApprovalGate:
    return ConnectionApprovalGate(
        approval_store=store,
        agent_id=agent_id,
        task_id=task_id,
        action_type=_ACTION_TYPE,
        effective_autonomy=autonomy,
        risk_classifier=risk_classifier,
    )


async def _run(
    gate: ConnectionApprovalGate,
    signature: ActionSignature,
    *,
    approval_id: str | None = None,
) -> ToolExecutionResult | None:
    return await gate.gate(
        signature,
        connection=_CONN,
        approval_id=approval_id,
        title="Open issue",
        description="agent-1 wants to open an issue",
    )


def _signature(action: str = "open") -> ActionSignature:
    return signature_for(
        namespace=_NS, connection=_CONN, args=_GovernedArgs(action=action)
    )


class TestRequireGovernedArgs:
    def test_returns_conforming_model(self) -> None:
        args = _GovernedArgs(action="open")
        # Narrows in place (same object), never a copy.
        assert id(require_governed_args(args)) == id(args)

    def test_rejects_model_missing_contract(self) -> None:
        with pytest.raises(TypeError, match="governed tool args"):
            require_governed_args(_UngovernedArgs(name="x"))


class TestSignatureFor:
    def test_identical_args_sign_identically(self) -> None:
        assert _signature("open").matches(_signature("open"))

    def test_differing_action_signs_differently(self) -> None:
        assert not _signature("open").matches(_signature("comment"))

    def test_metadata_roundtrip(self) -> None:
        sig = _signature("open")
        restored = ActionSignature.from_metadata(sig.to_metadata())
        assert sig.matches(restored)

    def test_from_metadata_absent_is_none(self) -> None:
        assert ActionSignature.from_metadata({}) is None


class TestGatePark:
    async def test_no_grant_parks_pending(self) -> None:
        store = ApprovalStore()
        result = await _run(_gate(store=store), _signature())
        assert result is not None
        assert result.metadata["requires_parking"] is True
        approval_id = result.metadata["approval_id"]
        item = await store.get(str(approval_id))
        assert item is not None
        assert item.status is ApprovalStatus.PENDING
        assert item.requested_by == "agent-1"
        assert item.task_id == "task-1"

    async def test_auto_approved_action_proceeds_without_park(self) -> None:
        store = ApprovalStore()
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.FULL,
            auto_approve_actions=frozenset({_ACTION_TYPE}),
            human_approval_actions=frozenset(),
            security_agent=False,
        )
        result = await _run(_gate(store=store, autonomy=autonomy), _signature())
        assert result is None
        assert await store.list_items() == ()


class TestGateConsume:
    async def test_matching_approved_grant_is_consumed(self) -> None:
        store = ApprovalStore()
        sig = _signature()
        parked = await _run(_gate(store=store), sig)
        assert parked is not None
        approval_id = str(parked.metadata["approval_id"])
        item = await store.get(approval_id)
        assert item is not None
        await store.save(item.model_copy(update={"status": ApprovalStatus.APPROVED}))

        proceeded = await _run(_gate(store=store), sig)
        assert proceeded is None
        consumed = await store.get(approval_id)
        assert consumed is not None
        assert consumed.consumed_at is not None

    async def test_explicit_consumed_approval_id_raises(self) -> None:
        store = ApprovalStore()
        sig = _signature()
        parked = await _run(_gate(store=store), sig)
        assert parked is not None
        approval_id = str(parked.metadata["approval_id"])
        item = await store.get(approval_id)
        assert item is not None
        await store.save(item.model_copy(update={"status": ApprovalStatus.APPROVED}))
        # Consume out of band, then replay with the now-spent id.
        await store.consume_if_approved(approval_id)
        with pytest.raises(GovernedApprovalMismatchError):
            await _run(_gate(store=store), sig, approval_id=approval_id)

    async def test_explicit_approval_id_wrong_signature_raises(self) -> None:
        store = ApprovalStore()
        parked = await _run(_gate(store=store), _signature("open"))
        assert parked is not None
        approval_id = str(parked.metadata["approval_id"])
        item = await store.get(approval_id)
        assert item is not None
        await store.save(item.model_copy(update={"status": ApprovalStatus.APPROVED}))
        # Same approval id, but a call that signs to a different action.
        with pytest.raises(GovernedApprovalMismatchError):
            await _run(
                _gate(store=store),
                _signature("comment"),
                approval_id=approval_id,
            )


class TestGateBinding:
    async def test_other_principal_grant_not_consumed(self) -> None:
        store = ApprovalStore()
        sig = _signature()
        owner_parked = await _run(
            _gate(store=store, agent_id="agent-1", task_id="task-1"), sig
        )
        assert owner_parked is not None
        owner_id = str(owner_parked.metadata["approval_id"])
        owner_item = await store.get(owner_id)
        assert owner_item is not None
        await store.save(
            owner_item.model_copy(update={"status": ApprovalStatus.APPROVED})
        )

        intruder = _gate(store=store, agent_id="agent-2", task_id="task-2")
        result = await _run(intruder, sig)
        # The intruder parks its own grant rather than consuming the owner's.
        assert result is not None
        assert result.metadata["requires_parking"] is True
        assert str(result.metadata["approval_id"]) != owner_id

    async def test_other_principal_explicit_id_raises(self) -> None:
        store = ApprovalStore()
        sig = _signature()
        owner_parked = await _run(
            _gate(store=store, agent_id="agent-1", task_id="task-1"), sig
        )
        assert owner_parked is not None
        owner_id = str(owner_parked.metadata["approval_id"])
        owner_item = await store.get(owner_id)
        assert owner_item is not None
        await store.save(
            owner_item.model_copy(update={"status": ApprovalStatus.APPROVED})
        )

        intruder = _gate(store=store, agent_id="agent-2", task_id="task-2")
        with pytest.raises(GovernedApprovalMismatchError):
            await _run(intruder, sig, approval_id=owner_id)


class TestGateRiskClassification:
    async def test_no_classifier_defaults_high(self) -> None:
        store = ApprovalStore()
        parked = await _run(_gate(store=store), _signature())
        assert parked is not None
        assert parked.metadata["risk_level"] == ApprovalRiskLevel.HIGH.value

    async def test_raising_classifier_defaults_high(self) -> None:
        store = ApprovalStore()
        gate = _gate(store=store, risk_classifier=_RaisingClassifier())
        parked = await _run(gate, _signature())
        assert parked is not None
        assert parked.metadata["risk_level"] == ApprovalRiskLevel.HIGH.value

    async def test_classifier_result_used(self) -> None:
        store = ApprovalStore()
        gate = _gate(store=store, risk_classifier=_LowRiskClassifier())
        parked = await _run(gate, _signature())
        assert parked is not None
        assert parked.metadata["risk_level"] == ApprovalRiskLevel.LOW.value
