"""Hypothesis property tests for the TSA response parser.

Goal: random byte strings must either decode into a structured TSA
response or raise a :class:`TsaError` subclass within a bounded
time. The parser must never hang, crash with an unhandled exception
type, or allocate unbounded memory.
"""

from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthorg.observability.audit_chain.tsa_client import (
    TsaError,
    _decode_response,
)

pytestmark = pytest.mark.unit


@given(data=st.binary(min_size=0, max_size=8192))
@settings(deadline=timedelta(seconds=1))
def test_decode_never_raises_unhandled_exception(data: bytes) -> None:
    """Parser handles arbitrary bytes with a bounded exception set."""
    try:
        _decode_response(data)
    except TsaError:
        # Expected: malformed / invalid / etc.
        pass
    except MemoryError, RecursionError:
        # Documented pass-through for resource exhaustion.
        raise
    except Exception as exc:  # pragma: no cover - failure mode
        pytest.fail(
            f"Parser raised unexpected exception type {type(exc).__name__}: {exc}"
        )
