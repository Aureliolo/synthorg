"""Conformance tests for ``EscalationQueueStore.subscribe_notifications``.

Postgres arm emits a ``NOTIFY`` via a second pool connection and
verifies the subscriber receives the payload within a short window.
SQLite arm asserts the context manager enters + exits cleanly on
cancellation without yielding (the protocol explicitly allows a noop
iterator for single-process backends).
"""

import asyncio
import contextlib

import pytest

from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


class TestEscalationNotifyConformance:
    async def test_postgres_emits_and_receives(
        self, backend: PersistenceBackend
    ) -> None:  # lint-allow: dual-backend-parity -- LISTEN/NOTIFY is Postgres-only
        """Postgres LISTEN/NOTIFY delivers payloads to the subscriber."""
        if backend.backend_name != "postgres":
            pytest.skip("Postgres-only: exercises LISTEN/NOTIFY semantics")

        repo = backend.build_escalations(notify_channel="conformance_channel")
        payload_queue: asyncio.Queue[str] = asyncio.Queue()
        received_first = asyncio.Event()
        listener_ready = asyncio.Event()

        async def _listen() -> None:
            async with repo.subscribe_notifications("conformance_channel") as gen:
                # LISTEN has registered by the time the context manager
                # yields; signal the producer to fire the NOTIFY.
                listener_ready.set()
                async for payload in gen:
                    await payload_queue.put(payload)
                    received_first.set()
                    return

        listener = asyncio.create_task(_listen())
        try:
            await asyncio.wait_for(listener_ready.wait(), timeout=5.0)
            pool = repo.pool  # type: ignore[attr-defined]
            async with pool.connection() as conn, conn.cursor() as cur:
                await conn.set_autocommit(True)
                await cur.execute(
                    "SELECT pg_notify(%s, %s)",
                    ("conformance_channel", "esc-001:decided"),
                )
            await asyncio.wait_for(received_first.wait(), timeout=5.0)
            assert await payload_queue.get() == "esc-001:decided"
        finally:
            listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener

    async def test_postgres_publishes_one_payload_per_id_on_batch(
        self, backend: PersistenceBackend
    ) -> None:  # lint-allow: dual-backend-parity -- LISTEN/NOTIFY is Postgres-only
        """``_publish_notifies`` over N ids emits N distinct payloads.

        Verifies the post-#1597 batching collapse preserves the
        per-id payload contract subscribers depend on.
        """
        if backend.backend_name != "postgres":
            pytest.skip("Postgres-only: exercises LISTEN/NOTIFY semantics")

        repo = backend.build_escalations(notify_channel="batch_channel")
        seen: list[str] = []
        ready = asyncio.Event()
        target = 3

        async def _listen() -> None:
            async with repo.subscribe_notifications("batch_channel") as gen:
                ready.set()
                async for payload in gen:
                    seen.append(payload)
                    if len(seen) >= target:
                        return

        listener = asyncio.create_task(_listen())
        try:
            await asyncio.wait_for(ready.wait(), timeout=5.0)
            # Reach into the private batch publisher to verify the
            # observable contract: N ids -> N payloads on the wire.
            await repo._publish_notifies(  # type: ignore[attr-defined]
                ("esc-a", "esc-b", "esc-c"),
                "expired",
            )
            await asyncio.wait_for(
                asyncio.shield(listener),
                timeout=5.0,
            )
            assert sorted(seen) == [
                "esc-a:expired",
                "esc-b:expired",
                "esc-c:expired",
            ]
        finally:
            listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener

    async def test_sqlite_subscription_is_noop(
        self,
        # lint-allow: dual-backend-parity -- single-process noop subscription
        backend: PersistenceBackend,
    ) -> None:
        """SQLite (single-process) yields an iterator that never emits."""
        if backend.backend_name != "sqlite":
            pytest.skip("SQLite-only: single-process noop subscription")

        repo = backend.build_escalations()
        # Deterministic readiness signal: the consumer sets this once the
        # context manager has actually entered. Using ``asyncio.sleep`` as
        # a readiness check was scheduler-dependent under ``-n 8`` and
        # could race the assertion on busy workers.
        entered = asyncio.Event()

        async def _consume() -> None:
            async with repo.subscribe_notifications("conformance_channel") as gen:
                entered.set()
                async for _ in gen:
                    # Should never yield on SQLite.
                    msg = "sqlite subscribe_notifications yielded unexpectedly"
                    raise AssertionError(msg)

        consumer = asyncio.create_task(_consume())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5.0)
            assert not consumer.done()
        finally:
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
