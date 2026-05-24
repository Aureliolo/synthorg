"""Verifies QueryParameter(max_length=...) returns 4xx, not a 500 worker crash.

Three controllers (`approvals.py`, `budget.py`, `meetings.py`) carry a
"Manual check retained" block from the pre-2.22 era. The block manually
re-validated a query param's length because Litestar 2.21's
``Parameter(max_length=...)`` on query params crashed the worker instead
of returning an RFC 9457 envelope. Litestar 2.22's
``QueryParameter(max_length=...)`` should produce a clean 4xx; this test
verifies that before removing the manual checks in #2091.

If this test fails, the upstream regression is unfixed -- keep the
manual checks and update the comments to reference ``QueryParameter``
instead of ``Parameter``.

The test mounts a minimal Litestar app with a single handler that
declares a ``str | None`` query param annotated with
``QueryParameter(max_length=QUERY_MAX_LENGTH)``. This is the exact
signature shape the three production handlers use after the
migration, so the result generalises.
"""

from typing import Annotated, Any

import pytest
from litestar import Litestar, get
from litestar.params import QueryParameter
from litestar.testing import TestClient

from synthorg.api.path_params import QUERY_MAX_LENGTH


@get("/probe")
async def _probe_handler(
    action_type: Annotated[
        str | None,
        QueryParameter(max_length=QUERY_MAX_LENGTH),
    ] = None,
) -> dict[str, Any]:
    """Echo the bound value; exists purely to exercise Litestar's binding."""
    return {"action_type": action_type}


@pytest.mark.integration
class TestQueryParamOverlongReturns4xx:
    def test_overlong_query_param_returns_4xx_not_5xx(self) -> None:
        """An over-long ``QueryParameter(max_length=N)`` value must be 4xx.

        Drives the binding through Litestar 2.22's ``QueryParameter``
        path. A 4xx response (typically 400 or 422) means the
        framework's typed validation now produces an RFC 9457 envelope
        instead of crashing the worker -- the manual length checks in
        ``approvals.py``, ``budget.py``, and ``meetings.py`` become
        redundant and can be removed.
        """
        app = Litestar(route_handlers=[_probe_handler])
        client = TestClient(app)
        overlong = "x" * (QUERY_MAX_LENGTH + 1)
        response = client.get("/probe", params={"action_type": overlong})
        assert 400 <= response.status_code < 500, (
            f"Litestar 2.22 QueryParameter(max_length={QUERY_MAX_LENGTH}) "
            f"should return 4xx for an over-long value "
            f"(got {response.status_code}). If this fails, keep the "
            "'Manual check retained' blocks in approvals.py / budget.py / "
            "meetings.py and update their comments to reference "
            "QueryParameter."
        )

    def test_within_bounds_query_param_returns_2xx(self) -> None:
        """Control case: a value at the boundary must succeed.

        Without this, the over-long assertion could pass for any
        unrelated 4xx (a route misconfiguration, an unrelated guard
        rejecting the request) and silently mask the real regression
        this test is supposed to catch.
        """
        app = Litestar(route_handlers=[_probe_handler])
        client = TestClient(app)
        at_bound = "x" * QUERY_MAX_LENGTH
        response = client.get("/probe", params={"action_type": at_bound})
        assert response.status_code == 200, (
            "QueryParameter at the max_length bound should be accepted; "
            f"got {response.status_code}. The over-long test below would "
            "pass for any unrelated 4xx and silently mask the real "
            "regression -- this control case is the negative."
        )
