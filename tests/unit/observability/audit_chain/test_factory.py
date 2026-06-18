"""Tests for the audit-chain sink factory + installer."""

import logging
from collections.abc import Iterator

import pytest

from synthorg.observability.audit_chain.config import AuditChainConfig
from synthorg.observability.audit_chain.factory import (
    build_audit_chain_sink,
    install_audit_chain_sink,
)
from synthorg.observability.audit_chain.sink import AuditChainSink

pytestmark = pytest.mark.unit


@pytest.fixture
def _root_handlers_restored() -> Iterator[None]:
    """Snapshot + restore the root logger handlers around a test."""
    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        yield
    finally:
        for handler in list(root.handlers):
            if handler not in saved:
                root.removeHandler(handler)


def test_build_returns_none_when_disabled() -> None:
    assert build_audit_chain_sink(AuditChainConfig(enabled=False)) is None


def test_build_returns_sink_when_enabled() -> None:
    sink = build_audit_chain_sink(AuditChainConfig(enabled=True))
    assert isinstance(sink, AuditChainSink)


@pytest.mark.usefixtures("_root_handlers_restored")
def test_install_attaches_sink_when_enabled() -> None:
    sink = install_audit_chain_sink(AuditChainConfig(enabled=True))
    assert isinstance(sink, AuditChainSink)
    assert sink in logging.getLogger().handlers


@pytest.mark.usefixtures("_root_handlers_restored")
def test_install_is_idempotent() -> None:
    first = install_audit_chain_sink(AuditChainConfig(enabled=True))
    second = install_audit_chain_sink(AuditChainConfig(enabled=True))
    assert first is second
    attached = [
        h for h in logging.getLogger().handlers if isinstance(h, AuditChainSink)
    ]
    assert len(attached) == 1


@pytest.mark.usefixtures("_root_handlers_restored")
def test_install_returns_none_when_disabled() -> None:
    assert install_audit_chain_sink(AuditChainConfig(enabled=False)) is None
    attached = [
        h for h in logging.getLogger().handlers if isinstance(h, AuditChainSink)
    ]
    assert attached == []
