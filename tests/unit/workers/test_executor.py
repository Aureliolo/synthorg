"""Unit coverage for :class:`TaskExecutionExecutor`.

The executor turns each :class:`TaskClaim` into a typed HTTP POST
against the backend's ``/api/v1/tasks/{id}/execute`` endpoint. These
tests pin the outcome-mapping contract documented in the module
docstring: every HTTP shape lands on exactly one
:class:`TaskClaimStatus` so worker ack/nack behaviour is predictable
from logs alone.
"""

from typing import Any

import httpx
import pytest

from synthorg.workers.claim import TaskClaim, TaskClaimStatus
from synthorg.workers.executor import TaskExecutionExecutor

pytestmark = pytest.mark.unit


def _claim() -> TaskClaim:
    return TaskClaim(task_id="task-A", new_status="assigned")


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_terminal_completed_returns_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"id": "task-A", "status": "completed"}},
        )

    async with _client(httpx.MockTransport(handler)) as http:
        executor = TaskExecutionExecutor(
            api_base_url="http://backend",
            auth_token="t",
            http_client=http,
        )
        outcome = await executor(_claim())
    assert outcome is TaskClaimStatus.SUCCESS


async def test_terminal_failed_returns_success() -> None:
    """A 2xx response with terminal ``failed`` status still acks the claim.

    The worker's claim outcome reflects whether the execution attempt
    completed end-to-end, not whether the task itself succeeded. A task
    that terminated in ``failed`` status is recorded on the task; the
    JetStream claim should be acked so the dispatcher does not redeliver.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"id": "task-A", "status": "failed"}},
        )

    async with _client(httpx.MockTransport(handler)) as http:
        executor = TaskExecutionExecutor(
            api_base_url="http://backend",
            auth_token="t",
            http_client=http,
        )
        outcome = await executor(_claim())
    assert outcome is TaskClaimStatus.SUCCESS


async def test_non_terminal_200_returns_retry() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"id": "task-A", "status": "in_progress"}},
        )

    async with _client(httpx.MockTransport(handler)) as http:
        executor = TaskExecutionExecutor(
            api_base_url="http://backend",
            auth_token="t",
            http_client=http,
        )
        outcome = await executor(_claim())
    assert outcome is TaskClaimStatus.RETRY


async def test_404_returns_failed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    async with _client(httpx.MockTransport(handler)) as http:
        executor = TaskExecutionExecutor(
            api_base_url="http://backend",
            auth_token="t",
            http_client=http,
        )
        outcome = await executor(_claim())
    assert outcome is TaskClaimStatus.FAILED


async def test_409_returns_failed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={})

    async with _client(httpx.MockTransport(handler)) as http:
        executor = TaskExecutionExecutor(
            api_base_url="http://backend",
            auth_token="t",
            http_client=http,
        )
        outcome = await executor(_claim())
    assert outcome is TaskClaimStatus.FAILED


async def test_500_returns_retry() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    async with _client(httpx.MockTransport(handler)) as http:
        executor = TaskExecutionExecutor(
            api_base_url="http://backend",
            auth_token="t",
            http_client=http,
        )
        outcome = await executor(_claim())
    assert outcome is TaskClaimStatus.RETRY


async def test_transport_error_returns_retry() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("")  # noqa: EM101 -- minimal test stub

    async with _client(httpx.MockTransport(handler)) as http:
        executor = TaskExecutionExecutor(
            api_base_url="http://backend",
            auth_token="t",
            http_client=http,
        )
        outcome = await executor(_claim())
    assert outcome is TaskClaimStatus.RETRY


async def test_timeout_returns_retry() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")  # noqa: EM101 -- minimal test stub

    async with _client(httpx.MockTransport(handler)) as http:
        executor = TaskExecutionExecutor(
            api_base_url="http://backend",
            auth_token="t",
            http_client=http,
        )
        outcome = await executor(_claim())
    assert outcome is TaskClaimStatus.RETRY


async def test_bearer_token_header_sent() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"data": {"id": "task-A", "status": "completed"}},
        )

    async with _client(httpx.MockTransport(handler)) as http:
        executor = TaskExecutionExecutor(
            api_base_url="http://backend",
            auth_token="sekret",
            http_client=http,
        )
        await executor(_claim())
    assert captured["auth"] == "Bearer sekret"


async def test_rejects_empty_api_base_url() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="api_base_url"):
            TaskExecutionExecutor(
                api_base_url="",
                auth_token="t",
                http_client=http,
            )


async def test_rejects_empty_token() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="auth_token"):
            TaskExecutionExecutor(
                api_base_url="http://backend",
                auth_token="",
                http_client=http,
            )
