"""Tests for the fail-safe SSRF-violation recording seam.

The recorder turns the outbound SSRF guard's rejections into a durable
audit trail. These tests pin the two invariants that keep it from
weakening the guard: recording is a no-op when nothing is installed, and
a recording failure is swallowed so the block still fires.
"""

from collections.abc import Iterator
from typing import Final
from unittest.mock import patch

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.tools._ssrf_recording import (
    install_ssrf_violation_recorder,
    record_ssrf_violation,
)
from synthorg.tools.network_validator import DnsValidationOk, NetworkPolicy
from synthorg.tools.ssrf import resolve_outbound_target

pytestmark = pytest.mark.unit

_VALIDATE: Final[str] = "synthorg.tools.ssrf.validate_url_host"


@pytest.fixture(autouse=True)
def _clear_recorder() -> Iterator[None]:
    """Ensure each test starts and ends with no recorder installed."""
    install_ssrf_violation_recorder(None)
    try:
        yield
    finally:
        install_ssrf_violation_recorder(None)


async def test_record_is_noop_without_recorder() -> None:
    # No recorder installed -> the call must simply do nothing.
    await record_ssrf_violation(url="http://x/", hostname="x", port=80)


async def test_record_invokes_installed_recorder() -> None:
    calls: list[tuple[str, str, int]] = []

    async def _recorder(
        url: str,
        hostname: str,
        port: int,
        resolved_ip: str | None,
        blocked_range: str | None,
    ) -> None:
        calls.append((url, hostname, port))

    install_ssrf_violation_recorder(_recorder)
    await record_ssrf_violation(url="http://x/", hostname="x", port=80)
    assert calls == [("http://x/", "x", 80)]


async def test_record_swallows_recorder_failure() -> None:
    async def _boom(
        url: str,
        hostname: str,
        port: int,
        resolved_ip: str | None,
        blocked_range: str | None,
    ) -> None:
        msg = "store down"
        raise QueryError(msg)

    install_ssrf_violation_recorder(_boom)
    # Must not raise: a recording failure can never weaken the block.
    await record_ssrf_violation(url="http://x/", hostname="x", port=80)


async def test_resolve_outbound_target_records_on_rejection() -> None:
    recorded: list[tuple[str, str, int]] = []

    async def _recorder(
        url: str,
        hostname: str,
        port: int,
        resolved_ip: str | None,
        blocked_range: str | None,
    ) -> None:
        recorded.append((url, hostname, port))

    async def _blocked(url: str, policy: NetworkPolicy) -> str | DnsValidationOk:
        return "blocked: internal IP"

    install_ssrf_violation_recorder(_recorder)
    policy = NetworkPolicy()
    with (
        patch(_VALIDATE, new=_blocked),
        pytest.raises(ValueError, match="rejected by SSRF policy"),
    ):
        await resolve_outbound_target(
            "https://evil.test/path",
            field="token_url",
            policy=policy,
        )
    assert recorded == [("https://evil.test/path", "evil.test", 443)]
