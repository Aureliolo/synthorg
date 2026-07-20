"""Tests for project controller."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
import structlog.testing

from synthorg.core.plan import Plan, PlanItem, PlanOption
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.state import persistence_of
from tests._shared import JsonDict, LoopAsyncClient, as_uuid, sid
from tests.unit.api.conftest import make_auth_headers, make_task


@pytest.mark.unit
class TestProjectController:
    async def test_list_projects_empty(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_get_project_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/projects/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    async def test_create_and_get_project(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={
                "name": "Auth System",
                "description": "Build authentication",
                "budget": 500.0,
            },
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["success"] is True
        project_id = created["data"]["id"]
        assert str(UUID(project_id)) == project_id
        assert created["data"]["name"] == "Auth System"
        assert created["data"]["budget"] == 500.0

        get_resp = await async_test_client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["id"] == project_id

    async def test_set_and_clear_autonomy_mode(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Gated Initiative"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]
        assert create_resp.json()["data"]["autonomy_mode"] is None

        set_resp = await async_test_client.patch(
            f"/api/v1/projects/{project_id}/autonomy-mode",
            json={"mode": "supervised"},
            headers=make_auth_headers("ceo"),
        )
        assert set_resp.status_code == 200
        assert set_resp.json()["data"]["autonomy_mode"] == "supervised"

        get_resp = await async_test_client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.json()["data"]["autonomy_mode"] == "supervised"

        clear_resp = await async_test_client.patch(
            f"/api/v1/projects/{project_id}/autonomy-mode",
            json={"mode": None},
            headers=make_auth_headers("ceo"),
        )
        assert clear_resp.status_code == 200
        assert clear_resp.json()["data"]["autonomy_mode"] is None

    async def test_set_autonomy_mode_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/projects/ghost/autonomy-mode",
            json={"mode": "locked"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "ghost" in body["error"]

    async def test_set_autonomy_mode_rejects_unknown_value(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Bad Mode"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]
        resp = await async_test_client.patch(
            f"/api/v1/projects/{project_id}/autonomy-mode",
            json={"mode": "omniscient"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    async def test_set_autonomy_mode_requires_mode_field(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Empty Body"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]
        # An empty body must not silently clear the override: ``mode`` carries
        # no default, so inheritance has to be selected deliberately.
        resp = await async_test_client.patch(
            f"/api/v1/projects/{project_id}/autonomy-mode",
            json={},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    async def test_set_full_mode_ceo_confirmed_audits_gate_off(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Sandbox"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]

        with structlog.testing.capture_logs() as logs:
            resp = await async_test_client.patch(
                f"/api/v1/projects/{project_id}/autonomy-mode",
                json={"mode": "full", "confirm": True},
                headers=make_auth_headers("ceo"),
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["autonomy_mode"] == "full"

        get_resp = await async_test_client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.json()["data"]["autonomy_mode"] == "full"

        audit = [
            log for log in logs if log["event"] == "api.project.autonomy_mode_changed"
        ]
        assert len(audit) == 1
        # The gate-off transition captures actor + from/to and is flagged.
        assert audit[0]["new_mode"] == "full"
        assert audit[0]["previous_mode"] is None
        assert audit[0]["gate_disabled"] is True
        assert audit[0]["log_level"] == "warning"

    async def test_set_full_mode_requires_confirm(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Unconfirmed"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]
        resp = await async_test_client.patch(
            f"/api/v1/projects/{project_id}/autonomy-mode",
            json={"mode": "full"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        assert resp.json()["success"] is False

    async def test_set_full_mode_forbidden_for_non_ceo(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Manager Attempt"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]
        # A manager holds write access but may not disable the gate.
        resp = await async_test_client.patch(
            f"/api/v1/projects/{project_id}/autonomy-mode",
            json={"mode": "full", "confirm": True},
            headers=make_auth_headers("manager"),
        )
        assert resp.status_code == 403
        # The row keeps its prior (unset) mode.
        get_resp = await async_test_client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.json()["data"]["autonomy_mode"] is None

    async def test_set_autonomy_mode_version_conflict(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Raced"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]
        # A stale expected_version simulates a concurrent write having won.
        resp = await async_test_client.patch(
            f"/api/v1/projects/{project_id}/autonomy-mode",
            json={"mode": "locked", "expected_version": 999},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409
        assert resp.json()["success"] is False
        # The rejected version-guarded write must leave the row untouched, so
        # an implementation that mutates before reporting the conflict fails.
        get_resp = await async_test_client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["autonomy_mode"] is None

    async def test_list_projects_after_create(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await async_test_client.post(
            "/api/v1/projects",
            json={"name": "P1"},
            headers=make_auth_headers("ceo"),
        )
        await async_test_client.post(
            "/api/v1/projects",
            json={"name": "P2"},
            headers=make_auth_headers("ceo"),
        )
        resp = await async_test_client.get("/api/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2

    async def test_list_projects_has_more_with_overflow(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        for i in range(4):
            await async_test_client.post(
                "/api/v1/projects",
                json={"name": f"P-page-{i:02d}"},
                headers=make_auth_headers("ceo"),
            )

        resp = await async_test_client.get("/api/v1/projects?limit=2")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["has_more"] is True
        assert body["pagination"]["next_cursor"] is not None

    async def test_create_project_with_deadline(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/projects",
            json={
                "name": "Deadline Project",
                "deadline": "2026-12-31",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["deadline"] == "2026-12-31"

    async def test_create_project_invalid_deadline(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/projects",
            json={
                "name": "Bad Deadline",
                "deadline": "not-a-date",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    async def test_oversized_project_id_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        long_id = "x" * 129
        resp = await async_test_client.get(f"/api/v1/projects/{long_id}")
        assert resp.status_code == 400

    async def test_list_projects_filter_by_invalid_status(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/projects?status=bogus")
        # ValidationError is 422 Unprocessable Entity (RFC 9457).
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert "Invalid project status" in body["error"]
        assert body["error_detail"]["error_category"] == "validation"

    async def test_create_project_with_duplicate_team(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/projects",
            json={
                "name": "Dupe Team",
                "team": ["agent-1", "agent-1"],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    async def test_delete_project_succeeds(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "To be deleted"},
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        project_id = create_resp.json()["data"]["id"]

        delete_resp = await async_test_client.delete(
            f"/api/v1/projects/{project_id}",
            headers=make_auth_headers("ceo"),
        )
        assert delete_resp.status_code == 204

        # Subsequent fetch must 404.
        get_resp = await async_test_client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.status_code == 404

    async def test_delete_project_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.delete(
            "/api/v1/projects/proj-does-not-exist",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Project 'proj-does-not-exist' not found"

    async def test_delete_project_broadcasts_ws_event(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Successful delete must publish a PROJECT_DELETED WS event.

        Regression guard: the controller's WS broadcast is easy to drop during
        refactors because it is fire-and-forget and silent on failure.
        """
        captured: list[JsonDict] = []

        def capture(
            request: object,
            event_type: object,
            channel: str,
            payload: JsonDict,
        ) -> None:
            captured.append(
                {
                    "event_type": event_type,
                    "channel": channel,
                    "payload": payload,
                },
            )

        # String-path form so the module attribute is patched by name; the
        # underlying channels.publish_ws_event is still exercised on other
        # endpoints that do not go through this test.
        monkeypatch.setattr(
            "synthorg.api.controllers.projects.publish_ws_event",
            capture,
        )

        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Doomed"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]

        with structlog.testing.capture_logs() as logs:
            delete_resp = await async_test_client.delete(
                f"/api/v1/projects/{project_id}",
                headers=make_auth_headers("ceo"),
            )
        assert delete_resp.status_code == 204

        delete_events = [
            call
            for call in captured
            if getattr(call["event_type"], "value", call["event_type"])
            == "project.deleted"
        ]
        assert len(delete_events) == 1
        event = delete_events[0]
        assert event["channel"] == "projects"
        assert event["payload"]["project_id"] == project_id
        assert event["payload"]["name"] == "Doomed"

        # Audit-trail regression guard: ProjectService.delete() must
        # emit ``api.project.deleted`` with the project_id at INFO.  If
        # the audit is silently dropped during a refactor, monitoring
        # filters that key on the event name will go quiet.
        deleted_audit = [log for log in logs if log["event"] == "api.project.deleted"]
        assert len(deleted_audit) >= 1, (
            f"expected api.project.deleted audit log; got events: "
            f"{[log['event'] for log in logs]}"
        )
        assert deleted_audit[0]["project_id"] == project_id

    async def test_delete_project_cascade_supersedes_children(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Deleting a project must resolve its live plans and open tasks.

        A project delete cannot orphan children: every non-terminal plan is
        superseded (a review decision that will now never come) and every
        non-terminal task is driven to a terminal state through its audited
        lifecycle transition. This covers the three transition shapes the
        cascade handles: a ``CREATED`` task rejects, a running task cancels,
        and a stuck task hops through ``ASSIGNED`` before cancelling.
        """
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Has children"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]

        backend = persistence_of(async_test_client.app.state.app_state)
        plan = Plan(
            id=as_uuid("plan-cascade"),
            project=NotBlankStr(project_id),
            objective_id=NotBlankStr("obj-cascade"),
            objective_title=NotBlankStr("Cascade objective"),
            parent_task_id=NotBlankStr("task-root"),
            items=(
                PlanItem(
                    id=NotBlankStr(sid("item-1")),
                    title=NotBlankStr("Scaffold"),
                    description=NotBlankStr("Set up the board"),
                    acceptance_criteria=(NotBlankStr("board scaffolded"),),
                ),
            ),
            status=PlanStatus.PENDING_REVIEW,
            created_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        )
        await backend.plans.save(plan)

        created_task = make_task(
            task_id="t-created", project=project_id, status=TaskStatus.CREATED
        )
        running_task = make_task(
            task_id="t-running",
            project=project_id,
            status=TaskStatus.ASSIGNED,
            assigned_to="alice",
        )
        stuck_task = make_task(
            task_id="t-stuck",
            project=project_id,
            status=TaskStatus.BLOCKED,
            assigned_to="alice",
        )
        for task in (created_task, running_task, stuck_task):
            await backend.tasks.save(task)

        with structlog.testing.capture_logs() as logs:
            delete_resp = await async_test_client.delete(
                f"/api/v1/projects/{project_id}",
                headers=make_auth_headers("ceo"),
            )
        assert delete_resp.status_code == 204

        superseded = await backend.plans.get(str(plan.id))
        assert superseded is not None
        assert superseded.status is PlanStatus.SUPERSEDED

        # The supersede must keep the initiating actor + reason on its audit
        # log, matching the context a task transition records.
        transitions = [
            log for log in logs if log["event"] == "api.plan.status_transitioned"
        ]
        assert len(transitions) == 1
        assert transitions[0]["reason"] == "project deleted"
        # No auth middleware in the unit harness, so the requester resolves to
        # the documented "api" fallback; the point is the context flows through.
        assert transitions[0]["requested_by"] == "api"

        async def _reloaded_status(task_id: str) -> TaskStatus:
            reloaded = await backend.tasks.get(task_id)
            assert reloaded is not None
            return reloaded.status

        assert await _reloaded_status(str(created_task.id)) is TaskStatus.REJECTED
        assert await _reloaded_status(str(running_task.id)) is TaskStatus.CANCELLED
        assert await _reloaded_status(str(stuck_task.id)) is TaskStatus.CANCELLED


@pytest.mark.unit
class TestProjectProgress:
    """The initiative view: plan items, task status, counts, critical path."""

    async def _seed(self, async_test_client: LoopAsyncClient) -> tuple[str, JsonDict]:
        """Create a project executing a three-item plan with one task done.

        Returns:
            The project id and the progress response body.
        """
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Initiative"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]
        backend = persistence_of(async_test_client.app.state.app_state)

        # b depends on a, c is independent: the critical path is a -> b.
        items = (
            PlanItem(
                id=NotBlankStr(sid("item-a")),
                title=NotBlankStr("Scaffold"),
                description=NotBlankStr("Set it up"),
                acceptance_criteria=(NotBlankStr("done"),),
            ),
            PlanItem(
                id=NotBlankStr(sid("item-b")),
                title=NotBlankStr("Build"),
                description=NotBlankStr("Build on the scaffold"),
                acceptance_criteria=(NotBlankStr("done"),),
                dependencies=(NotBlankStr(sid("item-a")),),
            ),
            PlanItem(
                id=NotBlankStr(sid("item-c")),
                title=NotBlankStr("Docs"),
                description=NotBlankStr("Write them"),
                acceptance_criteria=(NotBlankStr("done"),),
            ),
        )
        plan = Plan(
            id=as_uuid("plan-progress"),
            project=NotBlankStr(project_id),
            objective_id=NotBlankStr("obj-progress"),
            objective_title=NotBlankStr("Ship the initiative"),
            parent_task_id=NotBlankStr(sid("task-root")),
            items=items,
            status=PlanStatus.EXECUTING,
            created_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        )
        await backend.plans.save(plan)

        project = await backend.projects.get(NotBlankStr(project_id))
        assert project is not None
        await backend.projects.save(project.model_copy(update={"plan_id": plan.id}))

        # item-a passed the gate; item-b is still under review (executed but
        # unverified); item-c never dispatched.
        for label, status in (
            ("item-a", TaskStatus.COMPLETED),
            ("item-b", TaskStatus.IN_REVIEW),
        ):
            await backend.tasks.save(
                make_task(
                    task_id=label,
                    project=project_id,
                    status=status,
                    assigned_to="alice",
                ).model_copy(
                    update={"plan_id": plan.id, "plan_item_id": as_uuid(label)}
                )
            )

        resp = await async_test_client.get(f"/api/v1/projects/{project_id}/progress")
        assert resp.status_code == 200
        body: JsonDict = resp.json()["data"]
        return project_id, body

    async def test_reports_plan_and_item_state(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        _, body = await self._seed(async_test_client)

        assert body["plan_status"] == "executing"
        assert body["objective_title"] == "Ship the initiative"
        assert len(body["items"]) == 3
        by_title = {item["title"]: item for item in body["items"]}
        assert by_title["Scaffold"]["done"] is True
        assert by_title["Scaffold"]["task_status"] == "completed"
        # Executed but unverified: not done, which is what stops the project
        # completing on unverified work.
        assert by_title["Build"]["done"] is False
        assert by_title["Build"]["task_status"] == "in_review"
        # Never dispatched: no task, not done.
        assert by_title["Docs"]["task_id"] is None
        assert by_title["Docs"]["done"] is False

    async def test_reports_derived_counts(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        _, body = await self._seed(async_test_client)

        assert body["counts"] == {
            "total": 3,
            "done": 1,
            "failed": 0,
            "blocked": 0,
        }

    async def test_reports_the_critical_path(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        _, body = await self._seed(async_test_client)

        assert body["critical_path"] == [
            str(as_uuid("item-a")),
            str(as_uuid("item-b")),
        ]
        on_path = {item["title"] for item in body["items"] if item["on_critical_path"]}
        assert on_path == {"Scaffold", "Build"}

    async def test_a_decision_item_is_done_when_an_option_is_chosen(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """The assembler applies the decision rule, not just the work rule.

        Decision done-ness is derived in both the rollup and this assembler, so
        the wire shape is asserted here rather than only in the pure unit.
        """
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Deciding"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]
        backend = persistence_of(async_test_client.app.state.app_state)
        plan = Plan(
            id=as_uuid("plan-decision"),
            project=NotBlankStr(project_id),
            objective_id=NotBlankStr("obj-decision"),
            objective_title=NotBlankStr("Pick an approach"),
            parent_task_id=NotBlankStr(sid("task-root")),
            items=(
                PlanItem(
                    id=NotBlankStr(sid("item-decided")),
                    title=NotBlankStr("Choose a datastore"),
                    description=NotBlankStr("Weigh the options"),
                    acceptance_criteria=(NotBlankStr("decided"),),
                    kind=PlanItemKind.DECISION,
                    options=(
                        PlanOption(
                            id="opt-a", title="A", summary="first", recommended=True
                        ),
                        PlanOption(id="opt-b", title="B", summary="second"),
                    ),
                    chosen_option_id=NotBlankStr("opt-a"),
                ),
                PlanItem(
                    id=NotBlankStr(sid("item-open")),
                    title=NotBlankStr("Choose a queue"),
                    description=NotBlankStr("Weigh the options"),
                    acceptance_criteria=(NotBlankStr("decided"),),
                    kind=PlanItemKind.DECISION,
                    options=(
                        PlanOption(
                            id="opt-c", title="C", summary="third", recommended=True
                        ),
                        PlanOption(id="opt-d", title="D", summary="fourth"),
                    ),
                ),
            ),
            status=PlanStatus.EXECUTING,
            created_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        )
        await backend.plans.save(plan)
        project = await backend.projects.get(NotBlankStr(project_id))
        assert project is not None
        await backend.projects.save(project.model_copy(update={"plan_id": plan.id}))

        resp = await async_test_client.get(f"/api/v1/projects/{project_id}/progress")

        assert resp.status_code == 200
        body: JsonDict = resp.json()["data"]
        by_title = {item["title"]: item for item in body["items"]}
        assert by_title["Choose a datastore"]["done"] is True
        assert by_title["Choose a datastore"]["chosen_option_id"] == "opt-a"
        # A decision never dispatches a task, so it is never "not dispatched".
        assert by_title["Choose a datastore"]["task_id"] is None
        assert by_title["Choose a queue"]["done"] is False
        assert body["counts"]["done"] == 1

    async def test_project_without_a_plan_reports_empty_progress(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A project created directly renders the same shape, not a 404."""
        create_resp = await async_test_client.post(
            "/api/v1/projects",
            json={"name": "Unplanned"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]

        resp = await async_test_client.get(f"/api/v1/projects/{project_id}/progress")

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["plan_id"] is None
        assert body["project_status"] == "planning"
        assert body["items"] == []
        assert body["counts"]["total"] == 0

    async def test_unknown_project_is_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/projects/nonexistent/progress")

        assert resp.status_code == 404
        # A deleted route would also 404, so assert the structured
        # unknown-project error rather than the status alone.
        body = resp.json()
        assert body["success"] is False
        assert body["error_detail"]["error_category"] == "not_found"
        assert "Project" in body["error"]
