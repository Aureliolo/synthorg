"""Tests for the subprocess transport cleanup utility."""

from asyncio.subprocess import Process
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, PropertyMock

import pytest

from synthorg.tools._process_cleanup import close_subprocess_transport

pytestmark = pytest.mark.unit


class TestCloseSubprocessTransport:
    """close_subprocess_transport handles all transport states safely."""

    def test_noop_when_transport_is_none(self) -> None:
        """No-op when _transport attribute is absent."""
        proc = SimpleNamespace(_transport=None)
        close_subprocess_transport(cast(Process, proc))
        # Should not raise

    def test_noop_when_transport_attr_missing(self) -> None:
        """No-op when _transport attribute does not exist."""
        proc = SimpleNamespace()  # no _transport attribute
        close_subprocess_transport(cast(Process, proc))

    def test_noop_when_transport_is_closing(self) -> None:
        """No-op when transport is already closing."""
        transport = MagicMock()
        transport.is_closing.return_value = True
        proc = SimpleNamespace(_transport=transport)
        close_subprocess_transport(cast(Process, proc))
        transport.close.assert_not_called()

    def test_closes_open_transport(self) -> None:
        """Closes transport when it is open."""
        transport = MagicMock()
        transport.is_closing.return_value = False
        proc = SimpleNamespace(_transport=transport)
        close_subprocess_transport(cast(Process, proc))
        transport.close.assert_called_once()

    def test_suppresses_close_exception(self) -> None:
        """Exception from transport.close() is logged and suppressed."""
        transport = MagicMock()
        transport.is_closing.return_value = False
        transport.close.side_effect = OSError("pipe broken")
        proc = SimpleNamespace(_transport=transport)
        # Should not raise
        close_subprocess_transport(cast(Process, proc))

    def test_suppresses_is_closing_exception(self) -> None:
        """Exception from transport.is_closing() is logged and suppressed."""
        transport = MagicMock()
        transport.is_closing.side_effect = AttributeError("no method")
        proc = SimpleNamespace(_transport=transport)
        # Should not raise -- is_closing() is now inside the try/except
        close_subprocess_transport(cast(Process, proc))

    def test_suppresses_is_closing_on_non_transport(self) -> None:
        """When _transport exists but is not a real transport, no crash."""
        # Uses MagicMock so the type-level _transport PropertyMock can
        # override a string-typed value (descriptor-based attribute
        # behaviour SimpleNamespace doesn't model).
        proc = MagicMock(spec=Process)
        type(proc)._transport = PropertyMock(return_value="not-a-transport")
        # Should not raise
        close_subprocess_transport(proc)
