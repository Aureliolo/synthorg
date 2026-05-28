"""End-to-end audit-chain coverage for connection + custom-rule events.

Wires a real :class:`AuditChainSink` with a mock signer and the
``LocalClockProvider`` so each ``security.connection.*`` and
``security.custom_rule.*`` emission produces a signed entry on the
hash chain. The chain's ``verify_integrity()`` is asserted at the end
to prove the entries are properly linked.

The unit-level test at ``tests/unit/observability/audit_chain/test_audit_chain.py``
covers HashChain mechanics in isolation; this module verifies that
controller-driven credential and control-plane mutations actually flow
into the chain.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from synthorg.api.controllers.connections import (
    ConnectionsController,
    CreateConnectionRequest,
    UpdateConnectionRequest,
)
from synthorg.api.controllers.custom_rules import (
    CreateCustomRuleRequest,
    CustomRuleController,
    UpdateCustomRuleRequest,
)
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.meta.models import ProposalAltitude, RuleSeverity
from synthorg.meta.rules.custom import Comparator, CustomRuleDefinition
from synthorg.meta.rules.service import CustomRulesService
from synthorg.observability.audit_chain.chain import HashChain
from synthorg.observability.audit_chain.protocol import (
    AuditChainSigner,
    SignedPayload,
)
from synthorg.observability.audit_chain.sink import AuditChainSink
from synthorg.observability.audit_chain.timestamping import LocalClockProvider
from synthorg.observability.events.security import (
    SECURITY_CONNECTION_CREATED,
    SECURITY_CUSTOM_RULE_CREATED,
    SECURITY_CUSTOM_RULE_DELETED,
    SECURITY_CUSTOM_RULE_TOGGLED,
    SECURITY_CUSTOM_RULE_UPDATED,
)
from synthorg.persistence.custom_rule_protocol import CustomRuleRepository
from synthorg.persistence.protocol import PersistenceBackend


def _make_signer() -> AsyncMock:
    """Mock AuditChainSigner that returns a fixed signed payload.

    Configures the spec'd AsyncMock methods in place so the
    ``signer.sign`` / ``signer.verify`` attributes stay AsyncMock
    instances bound to the protocol (replacing them with a fresh
    ``AsyncMock()`` would defeat ``spec=`` and trip the mock-spec gate).
    """
    signer = AsyncMock(spec=AuditChainSigner)
    signer.algorithm = "test-algo"
    signer.sign.return_value = SignedPayload(
        signature=b"test-sig",
        algorithm=NotBlankStr("test-algo"),
        signer_id=NotBlankStr("test-signer"),
        signed_at=datetime.now(UTC),
    )
    signer.verify.return_value = True
    return signer


def _make_conn(name: str = "gh") -> Connection:
    """Build a Connection fixture for the ConnectionCatalog mock."""
    return Connection(
        name=NotBlankStr(name),
        connection_type=ConnectionType.GITHUB,
        auth_method=AuthMethod.API_KEY,
        base_url=NotBlankStr("https://api.github.com"),
    )


def _make_rule(*, enabled: bool = True) -> CustomRuleDefinition:
    """Build a CustomRuleDefinition fixture."""
    now = datetime.now(UTC)
    return CustomRuleDefinition(
        id=uuid4(),
        name="test-rule",
        description="probe",
        metric_path="performance.avg_quality_score",
        comparator=Comparator.LT,
        threshold=5.0,
        severity=RuleSeverity.WARNING,
        target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def audit_sink(tmp_path: Path) -> Iterator[AuditChainSink]:
    """Build an ``AuditChainSink`` and wire it onto the synthorg logger.

    Calls ``configure_logging`` first so structlog bridges every event
    into stdlib (controllers emit via ``structlog`` from ``get_logger``;
    without the bridge those events never reach stdlib handlers and the
    sink is silent). Then attaches the sink to the synthorg root
    logger, yields it for inspection, and detaches on teardown.

    The autouse ``_reset_logging`` fixture in
    ``tests/integration/observability/conftest.py`` resets logging
    before AND after each test, so we don't leak handlers across runs.
    """
    from synthorg.observability.config import LogConfig, SinkConfig
    from synthorg.observability.enums import LogLevel, SinkType
    from synthorg.observability.setup import configure_logging

    # Minimal stdlib-bridged config: one cheap file sink in tmp so
    # ``configure_logging`` succeeds without touching ``./logs``.
    config = LogConfig(
        root_level=LogLevel.INFO,
        log_dir=str(tmp_path / "logs"),
        sinks=(
            SinkConfig(
                sink_type=SinkType.FILE,
                level=LogLevel.INFO,
                file_path="audit.log",
                json_format=True,
            ),
        ),
    )
    configure_logging(config)
    sink = AuditChainSink(
        signer=_make_signer(),
        timestamp_provider=LocalClockProvider(),
    )
    sink.setLevel(logging.INFO)
    root = logging.getLogger("synthorg")
    root.addHandler(sink)
    try:
        yield sink
    finally:
        root.removeHandler(sink)


def _connection_state() -> dict[str, Any]:
    """Build a controller state with a stubbed ConnectionCatalog.

    The catalog is spec'd to ``ConnectionCatalog`` so its methods are
    AsyncMocks that respect the protocol; ``return_value`` /
    ``side_effect`` assignments configure them in place rather than
    replacing the spec'd method with a bare AsyncMock.

    The catalog is wired through ``make_app_state`` into the
    :class:`IntegrationsStateSlice` so controllers reach it via
    ``app_state.slice(IntegrationsStateSlice).connection_catalog``.
    """
    from tests._shared import make_app_state

    catalog = MagicMock(spec=ConnectionCatalog)
    catalog.create.return_value = _make_conn()
    catalog.update.return_value = _make_conn()
    catalog.delete.return_value = None
    catalog.get_credentials.return_value = {"client_secret": "real-secret-value"}
    app_state = make_app_state(connection_catalog=catalog)
    return {"app_state": app_state}


def _tamper_previous_hash(snapshot: HashChain) -> None:
    """Corrupt the last entry's ``previous_hash`` on a chain snapshot.

    Centralises the private-attribute access so the tamper helper sits
    next to the integration tests that need it. ``HashChain`` does not
    expose a public mutation surface (by design: append-only, link-only).
    """
    last = snapshot._entries[-1]
    snapshot._entries[-1] = last.model_copy(
        update={"previous_hash": "tampered"},
    )


def _custom_rule_state(rule: CustomRuleDefinition) -> Any:
    """Build a controller state with a stubbed persistence layer.

    The controller reads ``state.app_state`` via attribute access, so we
    return a litestar ``State`` (which exposes both attribute and dict
    access) rather than a bare dict.
    """
    _ = rule
    from litestar.datastructures import State

    from synthorg.api.cursor import CursorSecret
    from tests._shared import make_app_state

    persistence = MagicMock(spec=PersistenceBackend)
    persistence.custom_rules = MagicMock(spec=CustomRuleRepository)
    app_state = make_app_state(
        persistence=persistence,
        cursor_secret=CursorSecret.from_key("x" * 32),
    )
    return State({"app_state": app_state})


@pytest.mark.integration
class TestConnectionAuditChainCoverage:
    """Connection mutations land signed entries on the audit chain."""

    async def test_create_lands_one_entry(
        self,
        audit_sink: AuditChainSink,
    ) -> None:
        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        before = len(audit_sink.chain.entries)
        await ctrl.create_connection.fn(
            ctrl,
            state=_connection_state(),
            data=CreateConnectionRequest.model_validate(
                {
                    "name": "gh",
                    "connection_type": "github",
                    "credentials": {"token": "t"},
                },
            ),
        )
        # ``chain`` returns a snapshot, so re-read after the emission.
        after = len(audit_sink.chain.entries)
        assert after - before == 1
        # The most recent entry's data carries the SECURITY_* event.
        last = audit_sink.chain.entries[-1]
        assert SECURITY_CONNECTION_CREATED.encode() in last.canonical_payload

    async def test_full_lifecycle_chain_stays_valid(
        self,
        audit_sink: AuditChainSink,
    ) -> None:
        """Create / update / delete / reveal-success / reveal-failed each
        appends one entry; the chain stays valid; a tampered entry breaks
        ``verify_integrity()``."""
        from synthorg.core.domain_errors import NotFoundError

        ctrl = ConnectionsController(owner=ConnectionsController)  # type: ignore[arg-type]
        before = len(audit_sink.chain.entries)
        state = _connection_state()

        await ctrl.create_connection.fn(
            ctrl,
            state=state,
            data=CreateConnectionRequest.model_validate(
                {
                    "name": "gh",
                    "connection_type": "github",
                    "credentials": {"token": "t"},
                },
            ),
        )
        await ctrl.update_connection.fn(
            ctrl,
            state=state,
            name="gh",
            data=UpdateConnectionRequest.model_validate(
                {"base_url": "https://api.github.com/v4"},
            ),
        )
        await ctrl.delete_connection.fn(
            ctrl,
            state=state,
            name="gh",
        )
        await ctrl.reveal_secret.fn(
            ctrl,
            state=state,
            name="gh",
            field="client_secret",
        )

        # reveal failure (missing field)
        from synthorg.integrations.state import IntegrationsStateSlice

        slice_catalog = state["app_state"].slice(IntegrationsStateSlice)
        slice_catalog.connection_catalog.get_credentials.return_value = {}
        with pytest.raises(NotFoundError):
            await ctrl.reveal_secret.fn(
                ctrl,
                state=state,
                name="gh",
                field="client_secret",
            )

        snapshot = audit_sink.chain
        after = len(snapshot.entries)
        expected_appends = 5
        assert after - before == expected_appends
        assert snapshot.verify_integrity() is True

        # Tamper detection: ``HashChain.append`` only links forward, so
        # there is no public mutation API for tampering. Reach into the
        # private ``_entries`` list (the same approach the unit test in
        # ``tests/unit/observability/audit_chain/test_audit_chain.py``
        # uses) to corrupt a ``previous_hash`` and confirm
        # ``verify_integrity`` reports failure. Snapshot is a copy so
        # the live chain stays clean for the rest of the suite.
        _tamper_previous_hash(snapshot)
        assert snapshot.verify_integrity() is False


@pytest.mark.integration
class TestCustomRuleAuditChainCoverage:
    """Custom-rule mutations land signed entries on the audit chain."""

    @pytest.fixture
    def patched_custom_rules_service(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> MagicMock:
        """Stub ``CustomRulesService`` so controller calls hit our mock."""
        rule = _make_rule()
        service = MagicMock(spec=CustomRulesService)
        service.create.return_value = rule
        service.update.return_value = rule
        service.toggle.return_value = _make_rule(enabled=False)
        service.delete.return_value = None
        monkeypatch.setattr(
            "synthorg.api.controllers.custom_rules.CustomRulesService",
            lambda *args, **kwargs: service,
        )
        return service

    async def test_full_lifecycle_chain_stays_valid(
        self,
        audit_sink: AuditChainSink,
        patched_custom_rules_service: MagicMock,
    ) -> None:
        """Drive create / update / toggle / delete and verify the chain."""
        _ = patched_custom_rules_service
        ctrl = CustomRuleController(owner=CustomRuleController)  # type: ignore[arg-type]
        rule = _make_rule()
        before = len(audit_sink.chain.entries)

        await ctrl.create_rule.fn(
            ctrl,
            state=_custom_rule_state(rule),
            data=CreateCustomRuleRequest(
                name="test-rule",
                description="probe",
                metric_path="performance.avg_quality_score",
                comparator=Comparator.LT,
                threshold=5.0,
                severity=RuleSeverity.WARNING,
                target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
            ),
        )
        await ctrl.update_rule.fn(
            ctrl,
            state=_custom_rule_state(rule),
            rule_id=str(rule.id),
            data=UpdateCustomRuleRequest(threshold=9.0),
        )
        await ctrl.toggle_rule.fn(
            ctrl,
            state=_custom_rule_state(rule),
            rule_id=str(rule.id),
        )
        await ctrl.delete_rule.fn(
            ctrl,
            state=_custom_rule_state(rule),
            rule_id=str(rule.id),
        )

        after = len(audit_sink.chain.entries)
        expected_appends = 4
        assert after - before == expected_appends
        assert audit_sink.chain.verify_integrity() is True

        # Each event constant lands at least one entry.
        all_data = b"".join(e.canonical_payload for e in audit_sink.chain.entries)
        for event in (
            SECURITY_CUSTOM_RULE_CREATED,
            SECURITY_CUSTOM_RULE_UPDATED,
            SECURITY_CUSTOM_RULE_TOGGLED,
            SECURITY_CUSTOM_RULE_DELETED,
        ):
            assert event.encode() in all_data


@pytest.mark.integration
class TestAuditChainEventNamespaceClosure:
    """Negative coverage: only ``security.*`` and ``tool.registry.integrity.*``
    cross into the audit chain.

    The ``AuditChainSink`` filter is the single opt-in mechanism: an event
    is signed iff its name carries one of the audited prefixes. This class
    asserts that boundary stays closed -- ``integrations.*`` operational
    events are silently ignored, and ``security.*`` emissions from anywhere
    in the synthorg logger tree route through the chain.
    """

    async def test_integrations_namespace_not_signed(
        self,
        audit_sink: AuditChainSink,
    ) -> None:
        # Manually emit an integrations.* event and confirm the chain
        # ignores it. This proves the sink filter is the only thing
        # routing events into the chain (so renaming an event into
        # security.* is the *only* way to opt in).
        before = len(audit_sink.chain.entries)
        operational_logger = logging.getLogger(
            "synthorg.api.controllers.connections",
        )
        operational_logger.info("integrations.connection.legacy_event")
        after = len(audit_sink.chain.entries)
        assert after == before

    async def test_unrelated_security_events_unaffected(
        self,
        audit_sink: AuditChainSink,
    ) -> None:
        # A direct ``security.*`` emission from anywhere else in the
        # synthorg logger tree still routes through the chain. This
        # confirms our new constants didn't accidentally narrow the
        # filter.
        before = len(audit_sink.chain.entries)
        logging.getLogger("synthorg.misc").info("security.test.unrelated")
        after = len(audit_sink.chain.entries)
        assert after - before == 1
