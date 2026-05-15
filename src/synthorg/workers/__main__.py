"""Entry point for `python -m synthorg.workers`.

Launched from the Go CLI via ``synthorg worker start`` (see
``cli/cmd/worker_start.go``). Wires a :class:`JetStreamTaskQueue`
against the current ``NatsConfig`` and runs a pool of
:class:`Worker` instances against the HTTP-callback executor
(:class:`~synthorg.workers.executor.TaskExecutionExecutor`).

The executor POSTs to ``/api/v1/tasks/{task_id}/execute`` on the
backend; the backend dispatches to a pluggable
:class:`WorkerExecutionService` so the agent-runtime invocation is
configurable per deployment. The default
:class:`LifecycleAdvancingExecutionService` walks the task one
lifecycle step forward, which is sufficient for end-to-end claim
round-trip tests; production deployments override the service to
invoke the full agent engine.

The legacy ``_placeholder_executor`` is kept as an opt-in fallback
(``--executor placeholder``) so the dispatch plumbing can still be
smoke-tested without a running backend (e.g. NATS-only conformance
runs).
"""

import argparse
import asyncio
import os
import sys
from typing import Final

import httpx

from synthorg.communication.config import NatsConfig
from synthorg.observability import get_logger
from synthorg.observability.events.workers import (
    WORKERS_MAIN_INVALID_WORKER_COUNT,
    WORKERS_MAIN_PLACEHOLDER_EXECUTOR_INVOKED,
)
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_int
from synthorg.workers.claim import JetStreamTaskQueue, TaskClaim, TaskClaimStatus
from synthorg.workers.config import QueueConfig
from synthorg.workers.executor import TaskExecutionExecutor
from synthorg.workers.worker import TaskExecutor, run_worker_pool

logger = get_logger(__name__)


async def _placeholder_executor(claim: TaskClaim) -> TaskClaimStatus:
    """Acknowledge the claim without executing any task logic.

    Smoke-test fallback for the dispatch path; only used when the
    operator passes ``--executor placeholder`` or when no backend
    URL is configured.
    """
    logger.info(
        WORKERS_MAIN_PLACEHOLDER_EXECUTOR_INVOKED,
        task_id=claim.task_id,
        new_status=claim.new_status,
    )
    return TaskClaimStatus.SUCCESS


_DEFAULT_WORKER_COUNT: Final[int] = 1
"""Mirror of the registered ``workers.count`` default for help text and tests.

The authoritative source is the ``SettingDefinition`` in
:mod:`synthorg.settings.definitions.workers`; keep this in sync if the
registry default changes."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synthorg.workers",
        description="SynthOrg distributed task queue worker entry point.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Number of concurrent workers in this process "
            f"(default: env SYNTHORG_WORKERS or {_DEFAULT_WORKER_COUNT})."
        ),
    )
    parser.add_argument(
        "--nats-url",
        default=os.environ.get("SYNTHORG_NATS_URL", "nats://localhost:4222"),
        help="NATS server URL (default: env SYNTHORG_NATS_URL or nats://localhost:4222).",
    )
    parser.add_argument(
        "--stream-prefix",
        default=os.environ.get("SYNTHORG_NATS_STREAM_PREFIX", "SYNTHORG"),
        help="JetStream stream name prefix (default: SYNTHORG).",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("SYNTHORG_API_BASE_URL"),
        help=(
            "Backend base URL for the HTTP-callback executor "
            "(default: env SYNTHORG_API_BASE_URL)."
        ),
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("SYNTHORG_WORKER_AUTH_TOKEN"),
        help=(
            "Bearer token sent to the backend execute endpoint "
            "(default: env SYNTHORG_WORKER_AUTH_TOKEN)."
        ),
    )
    parser.add_argument(
        "--executor",
        default="http",
        choices=("http", "placeholder"),
        help=(
            "Executor implementation. ``http`` (default) calls the "
            "backend's /tasks/{id}/execute endpoint; ``placeholder`` "
            "acks every claim without any backend interaction "
            "(NATS-only smoke tests)."
        ),
    )
    return parser


def _resolve_worker_count(explicit: int | None) -> int | None:
    """Resolve the effective worker count from flag + env var.

    Precedence: explicit ``--workers`` > ``SYNTHORG_WORKERS`` env var
    > registered ``workers.count`` default. Returns ``None`` when the
    env var is set but not a valid integer so the caller can surface a
    structured usage error instead of silently masking operator intent.
    """
    if explicit is not None:
        return explicit
    env_raw = os.environ.get("SYNTHORG_WORKERS", "").strip()
    if env_raw and parse_int(env_raw) is None:
        return None
    resolved = resolve_init_value(
        SettingNamespace.WORKERS,
        "count",
        parse=parse_int,
    )
    if isinstance(resolved.value, int):
        return resolved.value
    return None


async def _async_main(argv: list[str]) -> int:
    """Parse arguments, start the queue, and run the worker pool."""
    args = _build_parser().parse_args(argv)
    resolved = _resolve_worker_count(args.workers)
    if resolved is None or resolved <= 0:
        logger.error(
            WORKERS_MAIN_INVALID_WORKER_COUNT,
            workers=resolved,
            env_value=os.environ.get("SYNTHORG_WORKERS"),
        )
        return 2
    args.workers = resolved

    queue_config = QueueConfig(enabled=True, workers=args.workers)
    nats_config = NatsConfig(
        url=args.nats_url,
        stream_name_prefix=args.stream_prefix,
    )

    task_queue = JetStreamTaskQueue(
        queue_config=queue_config,
        nats_config=nats_config,
    )
    await task_queue.start()
    executor, http_client = _resolve_executor(args)
    try:
        await run_worker_pool(
            queue_config=queue_config,
            task_queue=task_queue,
            executor=executor,
            worker_count=args.workers,
        )
    finally:
        if http_client is not None:
            await http_client.aclose()
        await task_queue.stop()
    return 0


def _resolve_executor(
    args: argparse.Namespace,
) -> tuple[TaskExecutor, httpx.AsyncClient | None]:
    """Build the configured executor and (if HTTP) the owned client.

    The HTTP executor owns an :class:`httpx.AsyncClient` for the
    lifetime of the worker pool so connection pooling persists
    across claims. The caller closes the client in the ``finally``
    block to drain in-flight requests at shutdown.
    """
    if args.executor == "placeholder":
        return _placeholder_executor, None
    if not args.api_base_url:
        msg = "--executor http requires --api-base-url (or SYNTHORG_API_BASE_URL)"
        raise SystemExit(msg)
    if not args.auth_token:
        msg = "--executor http requires --auth-token (or SYNTHORG_WORKER_AUTH_TOKEN)"
        raise SystemExit(msg)
    http_client = httpx.AsyncClient()
    executor = TaskExecutionExecutor(
        api_base_url=args.api_base_url,
        auth_token=args.auth_token,
        http_client=http_client,
    )
    return executor, http_client


def main(argv: list[str] | None = None) -> int:
    """Synchronous entry point that delegates to the asyncio runner."""
    effective = sys.argv[1:] if argv is None else argv
    return asyncio.run(_async_main(effective))


if __name__ == "__main__":
    raise SystemExit(main())
