"""Conformance tests for the connection-family repositories.

The four connection repos (Connection, ConnectionSecret, OAuthState,
WebhookReceipt) ship as durable SQLite + Postgres implementations.
These tests run against both backends through the shared ``backend``
fixture so semantics stay in lock-step.

Connections own a name primary key; the other three tables FK into
``connections.name`` (oauth_states, webhook_receipts) or are
independent (connection_secrets).  Tests insert a base connection row
once per test before exercising the dependent repos.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
    OAuthState,
    SecretRef,
    WebhookReceipt,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _connection(  # noqa: PLR0913
    name: str = "github-bot",
    *,
    connection_type: ConnectionType = ConnectionType.GITHUB,
    auth_method: AuthMethod = AuthMethod.API_KEY,
    secret_refs: tuple[SecretRef, ...] = (),
    rate_limiter: RateLimiterConfig | None = None,
    metadata: dict[str, str] | None = None,
    health_status: ConnectionStatus = ConnectionStatus.UNKNOWN,
) -> Connection:
    return Connection(
        name=NotBlankStr(name),
        connection_type=connection_type,
        auth_method=auth_method,
        base_url=NotBlankStr("https://api.example.com"),
        secret_refs=secret_refs,
        rate_limiter=rate_limiter,
        health_check_enabled=True,
        health_status=health_status,
        last_health_check_at=None,
        metadata=metadata or {},
    )


class TestConnectionRepository:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        conn = _connection(metadata={"team": "platform"})
        await backend.connections.save(conn)

        fetched = await backend.connections.get(NotBlankStr("github-bot"))

        assert fetched is not None
        assert fetched.name == "github-bot"
        assert fetched.connection_type is ConnectionType.GITHUB
        assert fetched.auth_method is AuthMethod.API_KEY
        assert fetched.metadata == {"team": "platform"}
        assert fetched.health_check_enabled is True

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.connections.get(NotBlankStr("never-saved")) is None

    async def test_save_is_idempotent_upsert(self, backend: PersistenceBackend) -> None:
        await backend.connections.save(_connection(metadata={"team": "a"}))
        await backend.connections.save(_connection(metadata={"team": "b"}))

        fetched = await backend.connections.get(NotBlankStr("github-bot"))

        assert fetched is not None
        assert fetched.metadata == {"team": "b"}

    async def test_save_round_trips_secret_refs_and_rate_limiter(
        self, backend: PersistenceBackend
    ) -> None:
        secret_ref = SecretRef(
            secret_id=NotBlankStr("sec-1"),
            backend=NotBlankStr("encrypted-sqlite"),
            key_version=2,
        )
        rate_limiter = RateLimiterConfig(
            max_requests_per_minute=60,
            max_concurrent=5,
        )
        conn = _connection(
            name="github-with-extras",
            secret_refs=(secret_ref,),
            rate_limiter=rate_limiter,
        )

        await backend.connections.save(conn)
        fetched = await backend.connections.get(NotBlankStr("github-with-extras"))

        assert fetched is not None
        assert fetched.secret_refs == (secret_ref,)
        assert fetched.rate_limiter == rate_limiter

    async def test_list_all_sorted_by_name(self, backend: PersistenceBackend) -> None:
        await backend.connections.save(_connection("c-charlie"))
        await backend.connections.save(_connection("a-alpha"))
        await backend.connections.save(_connection("b-bravo"))

        rows = await backend.connections.list_all()

        names = [c.name for c in rows]
        # The fixture's database is per-test, so only our 3 names exist.
        assert names == sorted(names)
        assert {"a-alpha", "b-bravo", "c-charlie"} <= set(names)

    async def test_list_by_type_filters(self, backend: PersistenceBackend) -> None:
        await backend.connections.save(
            _connection("gh", connection_type=ConnectionType.GITHUB),
        )
        await backend.connections.save(
            _connection(
                "slack",
                connection_type=ConnectionType.SLACK,
                auth_method=AuthMethod.OAUTH2,
            ),
        )

        github_rows = await backend.connections.list_by_type(ConnectionType.GITHUB)
        slack_rows = await backend.connections.list_by_type(ConnectionType.SLACK)

        assert {c.name for c in github_rows} == {"gh"}
        assert {c.name for c in slack_rows} == {"slack"}

    async def test_delete_returns_true_when_present(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("to-delete"))

        deleted = await backend.connections.delete(NotBlankStr("to-delete"))

        assert deleted is True
        assert await backend.connections.get(NotBlankStr("to-delete")) is None

    async def test_delete_returns_false_when_missing(
        self, backend: PersistenceBackend
    ) -> None:
        deleted = await backend.connections.delete(NotBlankStr("never-existed"))

        assert deleted is False

    async def test_list_all_pagination(self, backend: PersistenceBackend) -> None:
        for name in ("a", "b", "c", "d", "e"):
            await backend.connections.save(_connection(name))

        page_one = await backend.connections.list_all(limit=2, offset=0)
        page_two = await backend.connections.list_all(limit=2, offset=2)
        unbounded = await backend.connections.list_all()

        # Sorted by name ASC; per-test database means only our 5 rows exist.
        assert [c.name for c in page_one] == ["a", "b"]
        assert [c.name for c in page_two] == ["c", "d"]
        assert [c.name for c in unbounded] == ["a", "b", "c", "d", "e"]

    async def test_list_all_offset_beyond_collection_returns_empty(
        self, backend: PersistenceBackend
    ) -> None:
        for name in ("a", "b", "c"):
            await backend.connections.save(_connection(name))

        page = await backend.connections.list_all(limit=10, offset=100)

        assert page == ()

    async def test_list_all_negative_limit_returns_empty(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("only"))

        # Both ``limit=0`` and ``limit=-1`` are documented as returning ()
        # without hitting the database.
        assert await backend.connections.list_all(limit=0) == ()
        assert await backend.connections.list_all(limit=-1) == ()

    async def test_save_round_trips_partial_zero_rate_limiter(
        self, backend: PersistenceBackend
    ) -> None:
        # Edge case the audit flagged: ``rpm > 0`` + ``concurrent == 0`` is a
        # legitimate config but the deserialization predicate
        # ``if rate_limit_rpm or rate_limit_concurrent`` would still yield
        # truthy. Confirm the round-trip preserves the partial-zero shape.
        rate_limiter = RateLimiterConfig(
            max_requests_per_minute=60,
            max_concurrent=0,
        )
        await backend.connections.save(
            _connection("partial-zero", rate_limiter=rate_limiter),
        )

        fetched = await backend.connections.get(NotBlankStr("partial-zero"))

        assert fetched is not None
        assert fetched.rate_limiter is not None
        assert fetched.rate_limiter.max_requests_per_minute == 60
        assert fetched.rate_limiter.max_concurrent == 0

    async def test_list_by_type_pagination(self, backend: PersistenceBackend) -> None:
        for name in ("a", "b", "c"):
            await backend.connections.save(
                _connection(name, connection_type=ConnectionType.GITHUB),
            )

        page = await backend.connections.list_by_type(
            ConnectionType.GITHUB,
            limit=1,
            offset=1,
        )

        assert [c.name for c in page] == ["b"]


class TestConnectionSecretRepository:
    async def test_store_and_retrieve_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connection_secrets.store(
            NotBlankStr("sec-1"),
            b"\x00\x01\xff\xfeencrypted-payload",
            key_version=1,
        )

        retrieved = await backend.connection_secrets.retrieve(NotBlankStr("sec-1"))

        assert retrieved == b"\x00\x01\xff\xfeencrypted-payload"

    async def test_retrieve_missing_returns_none(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.connection_secrets.retrieve(NotBlankStr("missing")) is None

    async def test_store_overwrites_existing(self, backend: PersistenceBackend) -> None:
        await backend.connection_secrets.store(
            NotBlankStr("sec-overwrite"),
            b"original",
            key_version=1,
        )
        await backend.connection_secrets.store(
            NotBlankStr("sec-overwrite"),
            b"rotated",
            key_version=2,
        )

        retrieved = await backend.connection_secrets.retrieve(
            NotBlankStr("sec-overwrite"),
        )

        assert retrieved == b"rotated"

    async def test_delete_returns_true_when_present(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connection_secrets.store(
            NotBlankStr("sec-delete"),
            b"payload",
            key_version=1,
        )

        deleted = await backend.connection_secrets.delete(
            NotBlankStr("sec-delete"),
        )

        assert deleted is True
        assert (
            await backend.connection_secrets.retrieve(NotBlankStr("sec-delete")) is None
        )

    async def test_delete_returns_false_when_missing(
        self, backend: PersistenceBackend
    ) -> None:
        deleted = await backend.connection_secrets.delete(
            NotBlankStr("never-stored"),
        )

        assert deleted is False


def _state(
    *,
    state_token: str = "state-abc",  # noqa: S107
    connection_name: str = "github-bot",
    expires_in: timedelta = timedelta(minutes=5),
    pkce_verifier: str | None = "verifier-xyz",
    scopes: str = "repo user",
) -> OAuthState:
    now = datetime.now(UTC)
    return OAuthState(
        state_token=NotBlankStr(state_token),
        connection_name=NotBlankStr(connection_name),
        pkce_verifier=NotBlankStr(pkce_verifier) if pkce_verifier else None,
        scopes_requested=scopes,
        redirect_uri="https://app.example.com/callback",
        created_at=now,
        expires_at=now + expires_in,
    )


class TestOAuthStateRepository:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        # FK constraint requires the parent connection.
        await backend.connections.save(_connection("github-bot"))
        state = _state()

        await backend.oauth_states.save(state)
        fetched = await backend.oauth_states.get(NotBlankStr("state-abc"))

        assert fetched is not None
        assert fetched.state_token == "state-abc"
        assert fetched.connection_name == "github-bot"
        assert fetched.pkce_verifier == "verifier-xyz"
        assert fetched.scopes_requested == "repo user"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.oauth_states.get(NotBlankStr("never-saved")) is None

    async def test_save_is_idempotent_upsert(self, backend: PersistenceBackend) -> None:
        await backend.connections.save(_connection("github-bot"))
        await backend.oauth_states.save(_state(scopes="initial"))
        await backend.oauth_states.save(_state(scopes="updated"))

        fetched = await backend.oauth_states.get(NotBlankStr("state-abc"))

        assert fetched is not None
        assert fetched.scopes_requested == "updated"

    async def test_delete_returns_true_when_present(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        await backend.oauth_states.save(_state())

        deleted = await backend.oauth_states.delete(NotBlankStr("state-abc"))

        assert deleted is True
        assert await backend.oauth_states.get(NotBlankStr("state-abc")) is None

    async def test_delete_returns_false_when_missing(
        self, backend: PersistenceBackend
    ) -> None:
        deleted = await backend.oauth_states.delete(NotBlankStr("never"))

        assert deleted is False

    async def test_cleanup_expired_removes_only_expired(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        await backend.oauth_states.save(
            _state(state_token="alive", expires_in=timedelta(hours=1)),
        )
        # Build an already-expired state directly so the assertion does
        # not depend on wall-clock sleeps; ``cleanup_expired`` then has
        # to remove this row deterministically on every run.
        now = datetime.now(UTC)
        expired = OAuthState(
            state_token=NotBlankStr("dead"),
            connection_name=NotBlankStr("github-bot"),
            pkce_verifier=NotBlankStr("verifier-xyz"),
            scopes_requested="repo user",
            redirect_uri="https://app.example.com/callback",
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        await backend.oauth_states.save(expired)

        removed = await backend.oauth_states.cleanup_expired()

        assert removed == 1
        assert await backend.oauth_states.get(NotBlankStr("alive")) is not None
        assert await backend.oauth_states.get(NotBlankStr("dead")) is None

    async def test_mark_consumed_round_trips(self, backend: PersistenceBackend) -> None:
        await backend.connections.save(_connection("github-bot"))
        await backend.oauth_states.save(_state())

        consumed_at = datetime.now(UTC)
        ok = await backend.oauth_states.mark_consumed(
            NotBlankStr("state-abc"),
            connection_name=NotBlankStr("github-bot"),
            consumed_at=consumed_at,
        )
        assert ok is True

        fetched = await backend.oauth_states.get(NotBlankStr("state-abc"))
        assert fetched is not None
        assert fetched.consumed_at is not None
        # SQLite stores ISO strings re-parsed back to UTC; compare to
        # microsecond precision via ``isoformat`` rather than equality
        # on the datetime object.
        assert (
            fetched.consumed_at.replace(microsecond=0).isoformat()
            == consumed_at.replace(microsecond=0).isoformat()
        )
        assert fetched.connection_name_returned == "github-bot"

    async def test_mark_consumed_is_idempotent(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        await backend.oauth_states.save(_state())

        first = await backend.oauth_states.mark_consumed(
            NotBlankStr("state-abc"),
            connection_name=NotBlankStr("github-bot"),
            consumed_at=datetime.now(UTC),
        )
        second = await backend.oauth_states.mark_consumed(
            NotBlankStr("state-abc"),
            connection_name=NotBlankStr("github-bot"),
            consumed_at=datetime.now(UTC),
        )
        assert first is True
        assert second is False  # already consumed

    async def test_cleanup_reaps_stale_consumed_rows(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        # Build a non-expired state that was consumed > 10 min ago so
        # the retention window has elapsed and the cleanup pass reaps
        # it alongside any expired rows.
        now = datetime.now(UTC)
        stale_consumed = OAuthState(
            state_token=NotBlankStr("stale"),
            connection_name=NotBlankStr("github-bot"),
            pkce_verifier=NotBlankStr("verifier-xyz"),
            scopes_requested="repo user",
            redirect_uri="https://app.example.com/callback",
            created_at=now - timedelta(hours=2),
            expires_at=now + timedelta(hours=1),
            consumed_at=now - timedelta(minutes=20),
            connection_name_returned=NotBlankStr("github-bot"),
        )
        fresh_consumed = OAuthState(
            state_token=NotBlankStr("fresh"),
            connection_name=NotBlankStr("github-bot"),
            pkce_verifier=NotBlankStr("verifier-xyz"),
            scopes_requested="repo user",
            redirect_uri="https://app.example.com/callback",
            created_at=now - timedelta(minutes=5),
            expires_at=now + timedelta(hours=1),
            consumed_at=now - timedelta(minutes=2),
            connection_name_returned=NotBlankStr("github-bot"),
        )
        await backend.oauth_states.save(stale_consumed)
        await backend.oauth_states.save(fresh_consumed)

        removed = await backend.oauth_states.cleanup_expired()

        assert removed == 1
        assert await backend.oauth_states.get(NotBlankStr("stale")) is None
        assert await backend.oauth_states.get(NotBlankStr("fresh")) is not None


def _receipt(
    *,
    receipt_id: str | None = None,
    connection_name: str = "github-bot",
    received_at: datetime | None = None,
    payload_json: str = '{"event":"push"}',
) -> WebhookReceipt:
    kwargs: dict[str, object] = {
        "connection_name": NotBlankStr(connection_name),
        "event_type": "push",
        "status": "received",
        "received_at": received_at or datetime.now(UTC),
        "payload_json": payload_json,
    }
    if receipt_id is not None:
        kwargs["id"] = NotBlankStr(receipt_id)
    return WebhookReceipt(**kwargs)  # type: ignore[arg-type]


class TestWebhookReceiptRepository:
    async def test_log_and_list_round_trip(self, backend: PersistenceBackend) -> None:
        await backend.connections.save(_connection("github-bot"))
        receipt = _receipt()

        await backend.webhook_receipts.log(receipt)
        rows = await backend.webhook_receipts.get_by_connection(
            NotBlankStr("github-bot"),
        )

        assert len(rows) == 1
        assert rows[0].id == receipt.id
        assert rows[0].payload_json == '{"event":"push"}'

    async def test_get_by_connection_orders_newest_first(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        now = datetime.now(UTC)
        await backend.webhook_receipts.log(
            _receipt(receipt_id="oldest", received_at=now - timedelta(seconds=20)),
        )
        await backend.webhook_receipts.log(
            _receipt(receipt_id="middle", received_at=now - timedelta(seconds=10)),
        )
        await backend.webhook_receipts.log(
            _receipt(receipt_id="newest", received_at=now),
        )

        rows = await backend.webhook_receipts.get_by_connection(
            NotBlankStr("github-bot"),
        )

        assert [r.id for r in rows] == ["newest", "middle", "oldest"]

    async def test_get_by_connection_respects_limit(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        for i in range(5):
            await backend.webhook_receipts.log(
                _receipt(receipt_id=f"r-{i}"),
            )

        rows = await backend.webhook_receipts.get_by_connection(
            NotBlankStr("github-bot"),
            limit=2,
        )

        assert len(rows) == 2

    async def test_get_by_connection_offset(self, backend: PersistenceBackend) -> None:
        await backend.connections.save(_connection("github-bot"))
        now = datetime.now(UTC)
        for i in range(4):
            await backend.webhook_receipts.log(
                _receipt(
                    receipt_id=f"r-{i}",
                    received_at=now - timedelta(seconds=i),
                ),
            )

        # Newest-first => r-0, r-1, r-2, r-3. offset=2 limit=2 -> r-2, r-3.
        rows = await backend.webhook_receipts.get_by_connection(
            NotBlankStr("github-bot"),
            limit=2,
            offset=2,
        )

        assert [r.id for r in rows] == ["r-2", "r-3"]

    async def test_get_by_connection_offset_beyond_collection(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        await backend.webhook_receipts.log(_receipt())

        rows = await backend.webhook_receipts.get_by_connection(
            NotBlankStr("github-bot"),
            limit=10,
            offset=100,
        )

        assert rows == ()

    async def test_get_by_connection_filters_by_name(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("a"))
        await backend.connections.save(_connection("b"))
        await backend.webhook_receipts.log(_receipt(connection_name="a"))
        await backend.webhook_receipts.log(_receipt(connection_name="b"))

        rows_a = await backend.webhook_receipts.get_by_connection(NotBlankStr("a"))
        rows_b = await backend.webhook_receipts.get_by_connection(NotBlankStr("b"))

        assert len(rows_a) == 1
        assert len(rows_b) == 1
        assert rows_a[0].connection_name == "a"
        assert rows_b[0].connection_name == "b"

    async def test_get_by_connection_zero_limit_returns_empty(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        await backend.webhook_receipts.log(_receipt())

        rows = await backend.webhook_receipts.get_by_connection(
            NotBlankStr("github-bot"),
            limit=0,
        )

        assert rows == ()

    async def test_cleanup_old_zero_or_negative_is_noop(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        await backend.webhook_receipts.log(_receipt())

        zero = await backend.webhook_receipts.cleanup_old_for_connection(
            NotBlankStr("github-bot"),
            0,
        )
        negative = await backend.webhook_receipts.cleanup_old_for_connection(
            NotBlankStr("github-bot"),
            -1,
        )

        assert zero == 0
        assert negative == 0
        rows = await backend.webhook_receipts.get_by_connection(
            NotBlankStr("github-bot"),
        )
        assert len(rows) == 1

    async def test_cleanup_old_removes_aged_rows_for_connection(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.connections.save(_connection("github-bot"))
        old = datetime.now(UTC) - timedelta(days=30)
        recent = datetime.now(UTC)
        await backend.webhook_receipts.log(
            _receipt(receipt_id="old", received_at=old),
        )
        await backend.webhook_receipts.log(
            _receipt(receipt_id="recent", received_at=recent),
        )

        removed = await backend.webhook_receipts.cleanup_old_for_connection(
            NotBlankStr("github-bot"),
            7,
        )

        assert removed == 1
        rows = await backend.webhook_receipts.get_by_connection(
            NotBlankStr("github-bot"),
        )
        assert {r.id for r in rows} == {"recent"}

    async def test_cleanup_old_only_touches_named_connection(
        self, backend: PersistenceBackend
    ) -> None:
        """Per-connection sweep must not delete rows for other connections."""
        await backend.connections.save(_connection("github-bot"))
        await backend.connections.save(_connection("slack-bot"))
        old = datetime.now(UTC) - timedelta(days=30)
        await backend.webhook_receipts.log(
            _receipt(
                receipt_id="github-old",
                connection_name="github-bot",
                received_at=old,
            ),
        )
        await backend.webhook_receipts.log(
            _receipt(
                receipt_id="slack-old",
                connection_name="slack-bot",
                received_at=old,
            ),
        )

        removed = await backend.webhook_receipts.cleanup_old_for_connection(
            NotBlankStr("github-bot"),
            7,
        )

        assert removed == 1
        # Slack receipt survives despite being equally old.
        slack_rows = await backend.webhook_receipts.get_by_connection(
            NotBlankStr("slack-bot"),
        )
        assert {r.id for r in slack_rows} == {"slack-old"}
