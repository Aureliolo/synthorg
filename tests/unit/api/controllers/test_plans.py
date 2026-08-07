"""Tests for the plan controller (read / rework / request-changes)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.evaluation_verdict import CriterionOutcome, CriterionVerdict
from synthorg.core.plan import MAX_PLAN_VERSION_HISTORY, Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.evaluation_report_protocol import EvaluationReportRecord
from synthorg.persistence.state import persistence_of
from tests._shared import LoopAsyncClient, as_uuid, sid
from tests.unit.api.conftest import make_auth_headers

_CREATED_AT = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
_HIRED_ON = date(2026, 1, 1)


def _agent(role: str) -> AgentIdentity:
    """An agent holding *role*, so the roster the edit path reads is non-empty."""
    return AgentIdentity(
        id=as_uuid(f"agent-{role}"),
        name=NotBlankStr(f"agent-{role}"),
        role=NotBlankStr(role),
        department=NotBlankStr("engineering"),
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=_HIRED_ON,
    )


# Plan item ids must be canonical UUID strings (dispatch rebuilds child tasks
# from them); use stable, labelled UUIDs so payloads read clearly.
_I1 = sid("item-1")
_I2 = sid("item-2")


def _plan(
    *,
    plan_id: str = "plan-001",
    status: PlanStatus = PlanStatus.PENDING_REVIEW,
    project: str = "beachhead",
    objective_id: str = "obj-001",
    failure_reason: str | None = None,
) -> Plan:
    return Plan(
        id=as_uuid(plan_id),
        project=NotBlankStr(project),
        objective_id=NotBlankStr(objective_id),
        objective_title=NotBlankStr("Ship the Tetris game"),
        parent_task_id=NotBlankStr("task-root"),
        failure_reason=NotBlankStr(failure_reason) if failure_reason else None,
        items=(
            PlanItem(
                id=NotBlankStr(_I1),
                title=NotBlankStr("Scaffold"),
                description=NotBlankStr("Set up the board"),
                acceptance_criteria=(NotBlankStr("board scaffolded"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
            PlanItem(
                id=NotBlankStr(_I2),
                title=NotBlankStr("Movement"),
                description=NotBlankStr("Drop + rotate"),
                dependencies=(NotBlankStr(_I1),),
                acceptance_criteria=(NotBlankStr("pieces drop and rotate"),),
                expected_artifacts=(NotBlankStr("src/movement.py"),),
            ),
        ),
        status=status,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _evaluation(
    plan_id: str, *, attempt: int, outcome: CriterionOutcome
) -> EvaluationReportRecord:
    return EvaluationReportRecord(
        record_id=as_uuid(f"eval-{attempt}"),
        plan_id=NotBlankStr(plan_id),
        project_id=NotBlankStr("beachhead"),
        attempt=attempt,
        summary=NotBlankStr("Read the workspace and played it."),
        verdicts=(
            CriterionVerdict(
                criterion=NotBlankStr("a person can play a full game"),
                outcome=outcome,
                evidence=NotBlankStr(
                    "the board never renders"
                    if outcome is CriterionOutcome.UNMET
                    else "played a full game"
                ),
            ),
        ),
        objective_met=outcome is CriterionOutcome.MET,
        evaluated_at=_CREATED_AT + timedelta(hours=attempt),
    )


async def _seed(client: LoopAsyncClient, plan: Plan) -> None:
    backend = persistence_of(client.app.state.app_state)
    await backend.plans.save(plan)


@pytest.mark.unit
class TestPlanController:
    async def test_list_plans_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/plans")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_get_plan_not_found(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/plans/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_seed_get_and_list(self, async_test_client: LoopAsyncClient) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        get_resp = await async_test_client.get(f"/api/v1/plans/{plan_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()["data"]
        assert data["id"] == plan_id
        assert data["status"] == "pending_review"
        assert len(data["items"]) == 2

        list_resp = await async_test_client.get("/api/v1/plans")
        assert list_resp.status_code == 200
        assert len(list_resp.json()["data"]) == 1

    async def test_get_evaluation_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/plans/nonexistent/evaluation")
        assert resp.status_code == 404

    async def test_get_evaluation_is_empty_before_any_judgement(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A plan parked without a verdict says so, rather than inventing one."""
        await _seed(async_test_client, _plan(status=PlanStatus.EVALUATING))
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.get(f"/api/v1/plans/{plan_id}/evaluation")

        assert resp.status_code == 200
        assert resp.json()["data"]["attempts"] == []

    async def test_get_evaluation_returns_verdicts_newest_first(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan(status=PlanStatus.EVALUATING))
        plan_id = str(as_uuid("plan-001"))
        backend = persistence_of(async_test_client.app.state.app_state)
        await backend.evaluation_reports.append(
            _evaluation(plan_id, attempt=1, outcome=CriterionOutcome.UNMET),
        )
        await backend.evaluation_reports.append(
            _evaluation(plan_id, attempt=2, outcome=CriterionOutcome.MET),
        )

        resp = await async_test_client.get(f"/api/v1/plans/{plan_id}/evaluation")

        assert resp.status_code == 200
        attempts = resp.json()["data"]["attempts"]
        assert [row["attempt"] for row in attempts] == [2, 1]
        assert attempts[0]["objective_met"] is True
        assert attempts[1]["verdicts"][0]["outcome"] == "unmet"
        assert attempts[1]["verdicts"][0]["evidence"] == "the board never renders"

    async def test_list_filter_by_status(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(
            async_test_client,
            _plan(plan_id="approved", status=PlanStatus.APPROVED),
        )
        await _seed(
            async_test_client,
            _plan(plan_id="pending", status=PlanStatus.PENDING_REVIEW),
        )

        resp = await async_test_client.get("/api/v1/plans?status=approved")
        assert resp.status_code == 200
        ids = {row["id"] for row in resp.json()["data"]}
        assert str(as_uuid("approved")) in ids
        assert str(as_uuid("pending")) not in ids

    async def test_list_filter_invalid_status(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/plans?status=bogus")
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert "Invalid plan status" in body["error"]

    async def test_list_pagination_walks_past_first_page(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        for i in range(3):
            await _seed(async_test_client, _plan(plan_id=f"pg-{i}"))

        first = await async_test_client.get("/api/v1/plans?limit=2")
        assert first.status_code == 200
        body = first.json()
        assert len(body["data"]) == 2
        cursor = body["pagination"]["next_cursor"]
        assert cursor is not None

        second = await async_test_client.get(f"/api/v1/plans?limit=2&cursor={cursor}")
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["data"]) == 1
        # The second page surfaces the third plan, not a repeat of page one.
        first_ids = {row["id"] for row in body["data"]}
        second_ids = {row["id"] for row in second_body["data"]}
        assert first_ids.isdisjoint(second_ids)

    async def test_edit_reworks_items_and_bumps_version(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "Reworked scaffold",
                        "description": "New scope for the board",
                        "owner": "engineering",
                        "acceptance_criteria": ["board scaffolded"],
                        "expected_artifacts": ["src/board.py"],
                    },
                ],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == 2
        assert data["status"] == "pending_review"
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == "Reworked scaffold"
        assert data["items"][0]["owner"] == "engineering"
        # The pre-edit version is snapshotted so a reviewer can diff the rework.
        assert len(data["version_history"]) == 1
        snapshot = data["version_history"][0]
        assert snapshot["version"] == 1
        assert [item["id"] for item in snapshot["items"]] == [_I1, _I2]

    async def test_edit_refuses_an_owner_the_org_does_not_staff(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        # Hand-correcting an owner to another invented role was accepted
        # without complaint, which is how the edit path could validate the
        # dependency graph and still leave every item unroutable.
        await agent_registry.clear()
        await agent_registry.register(_agent("Backend Developer"))
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "Reworked scaffold",
                        "description": "New scope for the board",
                        "owner": "Backend Engineer",
                        "acceptance_criteria": ["board scaffolded"],
                        "expected_artifacts": ["src/board.py"],
                    },
                ],
            },
            headers=make_auth_headers("ceo"),
        )

        assert resp.status_code == 422, resp.text
        assert "Backend Engineer" in resp.text
        assert "Backend Developer" in resp.text

    async def test_edit_accepts_an_owner_on_the_roster(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        await agent_registry.clear()
        await agent_registry.register(_agent("Backend Developer"))
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "Reworked scaffold",
                        "description": "New scope for the board",
                        "owner": "Backend Developer",
                        "acceptance_criteria": ["board scaffolded"],
                        "expected_artifacts": ["src/board.py"],
                    },
                ],
            },
            headers=make_auth_headers("ceo"),
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["items"][0]["owner"] == "Backend Developer"

    async def test_version_history_is_bounded(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # Reworking a plan many times must not grow version_history without
        # bound: it is capped at MAX_PLAN_VERSION_HISTORY, oldest dropping first.
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))
        payload = {
            "items": [
                {
                    "id": _I1,
                    "title": "Slice",
                    "description": "One slice",
                    "acceptance_criteria": ["it runs"],
                    "expected_artifacts": ["src/slice.py"],
                }
            ]
        }
        data = {}
        for _ in range(MAX_PLAN_VERSION_HISTORY + 2):
            resp = await async_test_client.patch(
                f"/api/v1/plans/{plan_id}",
                json=payload,
                headers=make_auth_headers("ceo"),
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
        assert len(data["version_history"]) == MAX_PLAN_VERSION_HISTORY
        versions = [snap["version"] for snap in data["version_history"]]
        # Strictly increasing and the oldest (version 1) has dropped off.
        assert versions == sorted(versions)
        assert versions[0] > 1

    async def test_edit_accepts_a_decision_item(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))
        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "Choose the stack",
                        "description": "React or Svelte",
                        "acceptance_criteria": ["decision recorded"],
                        "kind": "decision",
                        "options": [
                            {
                                "id": "react",
                                "title": "React",
                                "summary": "Mature, larger bundle",
                                "recommended": True,
                            },
                            {
                                "id": "svelte",
                                "title": "Svelte",
                                "summary": "Lean, smaller ecosystem",
                                "recommended": False,
                            },
                        ],
                    }
                ]
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        item = resp.json()["data"]["items"][0]
        assert item["kind"] == "decision"
        assert [o["id"] for o in item["options"]] == ["react", "svelte"]

    async def test_edit_rejects_decision_without_recommended(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))
        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "Choose the stack",
                        "description": "React or Svelte",
                        "acceptance_criteria": ["decision recorded"],
                        "kind": "decision",
                        "options": [
                            {"id": "react", "title": "React", "summary": "A"},
                            {"id": "svelte", "title": "Svelte", "summary": "B"},
                        ],
                    }
                ]
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    async def test_edit_rejects_work_item_without_expected_artifacts(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # A WORK item declaring no deliverable disarms the zero-artifact guard
        # on the task it dispatches, so the edit is refused at the boundary.
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "Scaffold",
                        "description": "Set up the board",
                        "acceptance_criteria": ["board scaffolded"],
                    },
                ],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    async def test_edit_rejects_unresolvable_dependency(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "Broken",
                        "description": "Depends on a ghost",
                        "dependencies": [sid("ghost")],
                        "acceptance_criteria": ["done"],
                        "expected_artifacts": ["src/broken.py"],
                    },
                ],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        assert resp.json()["success"] is False

    async def test_edit_rejects_dependency_cycle(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "A",
                        "description": "depends on B",
                        "dependencies": [_I2],
                        "acceptance_criteria": ["a done"],
                        "expected_artifacts": ["src/a.py"],
                    },
                    {
                        "id": _I2,
                        "title": "B",
                        "description": "depends on A",
                        "dependencies": [_I1],
                        "acceptance_criteria": ["b done"],
                        "expected_artifacts": ["src/b.py"],
                    },
                ],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        assert resp.json()["success"] is False

    async def test_edit_rejects_non_uuid_item_id(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": "not-a-uuid",
                        "title": "X",
                        "description": "Y",
                        "acceptance_criteria": ["done"],
                        "expected_artifacts": ["src/x.py"],
                    }
                ]
            },
            headers=make_auth_headers("ceo"),
        )
        # A malformed item payload is rejected at the request boundary (400),
        # never reaching dispatch.
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    async def test_edit_rejects_empty_items(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={"items": []},
            headers=make_auth_headers("ceo"),
        )
        # EditPlanRequest.items has min_length=1: a body-schema violation (400).
        assert resp.status_code == 400

    async def test_edit_rejects_terminal_plan(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(
            async_test_client, _plan(plan_id="decided", status=PlanStatus.APPROVED)
        )
        plan_id = str(as_uuid("decided"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "X",
                        "description": "Y",
                        "acceptance_criteria": ["done"],
                        "expected_artifacts": ["src/x.py"],
                    }
                ]
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409
        assert resp.json()["success"] is False

    async def test_edit_missing_plan_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/plans/ghost",
            json={
                "items": [
                    {
                        "id": _I1,
                        "title": "X",
                        "description": "Y",
                        "acceptance_criteria": ["done"],
                        "expected_artifacts": ["src/x.py"],
                    }
                ]
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_request_changes_drafts_plan(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.post(
            f"/api/v1/plans/{plan_id}/request-changes",
            json={"note": "Split the movement item into drop and rotate"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "draft"

    async def test_request_changes_rejects_terminal_plan(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(
            async_test_client, _plan(plan_id="decided", status=PlanStatus.REJECTED)
        )
        plan_id = str(as_uuid("decided"))

        resp = await async_test_client.post(
            f"/api/v1/plans/{plan_id}/request-changes",
            json={"note": "please revise"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409

    async def test_request_changes_missing_plan_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/plans/ghost/request-changes",
            json={"note": "please revise"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404


@pytest.mark.unit
class TestDeletePlan:
    """The route that gives a stuck plan a way out.

    Without it a plan whose parent task was deleted sat in the review
    queue forever: it could not be approved (no parent), could not be
    superseded (the items check forbids it while empty), and had no
    delete route at all.
    """

    async def test_deletes_a_plan_under_review(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan(plan_id="doomed"))
        plan_id = str(as_uuid("doomed"))

        resp = await async_test_client.delete(
            f"/api/v1/plans/{plan_id}", headers=make_auth_headers("ceo")
        )

        assert resp.status_code == 204
        follow_up = await async_test_client.get(f"/api/v1/plans/{plan_id}")
        assert follow_up.status_code == 404

    async def test_deletes_a_failed_plan(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """The status a stranded plan lands in has to be removable."""
        await _seed(
            async_test_client,
            _plan(
                plan_id="burned",
                status=PlanStatus.FAILED,
                failure_reason="dispatch failed",
            ),
        )

        resp = await async_test_client.delete(
            f"/api/v1/plans/{as_uuid('burned')}", headers=make_auth_headers("ceo")
        )

        assert resp.status_code == 204

    @pytest.mark.parametrize(
        "status",
        [
            PlanStatus.APPROVED,
            PlanStatus.EXECUTING,
            PlanStatus.INTEGRATING,
            PlanStatus.EVALUATING,
        ],
        ids=lambda value: str(value.value),
    )
    async def test_refuses_a_dispatched_plan(
        self, async_test_client: LoopAsyncClient, status: PlanStatus
    ) -> None:
        """Removing it would orphan every task already building under it."""
        await _seed(async_test_client, _plan(plan_id="building", status=status))
        plan_id = str(as_uuid("building"))

        resp = await async_test_client.delete(
            f"/api/v1/plans/{plan_id}", headers=make_auth_headers("ceo")
        )

        assert resp.status_code == 409
        # Still there: the refusal is not a partial delete.
        survivor = await async_test_client.get(f"/api/v1/plans/{plan_id}")
        assert survivor.status_code == 200

    async def test_missing_plan_404(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.delete(
            "/api/v1/plans/ghost", headers=make_auth_headers("ceo")
        )
        assert resp.status_code == 404

    async def test_a_read_only_role_cannot_delete(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """The one irreversible plan operation is not a read.

        An observer can see every plan, which is what makes the write guard
        the only thing standing between a viewer and a destroyed review
        record.
        """
        await _seed(async_test_client, _plan(plan_id="watched"))
        plan_id = str(as_uuid("watched"))

        resp = await async_test_client.delete(
            f"/api/v1/plans/{plan_id}", headers=make_auth_headers("observer")
        )

        assert resp.status_code == 403
        survivor = await async_test_client.get(f"/api/v1/plans/{plan_id}")
        assert survivor.status_code == 200
