"""Regression test: app boot must not raise `Kwarg resolution ambiguity`.

Litestar 2.22 tightened kwarg resolution so PATH-bound handler params
declared with bare ``Parameter(...)`` raise
``ImproperlyConfiguredException: Kwarg resolution ambiguity detected``
at ``create_app`` time. PR #2091 migrated every PATH-bound site to
``PathParameter(...)`` and every deprecated ``Parameter(query=...)``
to ``QueryParameter(name=...)``.

This guard does the smallest possible thing that catches a regression:
build the app and assert no ``ImproperlyConfiguredException`` surfaces.
The error message names the offending key, so a future PR that adds a
PATH-bound param with the wrong marker will fail here pointing at the
exact parameter name before any other test runs.
"""

from typing import Any

import pytest
from litestar import Litestar
from litestar.exceptions import ImproperlyConfiguredException

from synthorg.api.app import create_app


@pytest.mark.unit
class TestAppBootHasNoKwargAmbiguity:
    def test_create_app_does_not_raise_kwarg_ambiguity(
        self,
        fake_persistence: Any,
        fake_message_bus: Any,
        cost_tracker: Any,
        root_config: Any,
    ) -> None:
        """Building the app must not trip Litestar 2.22's PATH-vs-query check.

        The legacy failure mode was:

            ImproperlyConfiguredException:
                Kwarg resolution ambiguity detected for the following
                keys: version_num. Make sure to use distinct keys for
                your dependencies, path parameters, and aliased
                parameters.

        If this test fails, the failure message names the offending
        param. Migrate that handler's ``Parameter(...)`` to
        ``PathParameter(...)`` (PEP 593 marker class from
        ``litestar.params``).
        """
        try:
            app = create_app(
                config=root_config,
                persistence=fake_persistence,
                message_bus=fake_message_bus,
                cost_tracker=cost_tracker,
            )
        except ImproperlyConfiguredException as exc:
            pytest.fail(
                "Litestar refused to build the app -- a PATH-bound handler "
                "param is still using bare Parameter(...). Migrate it to "
                f"PathParameter(...): {exc}"
            )

        assert isinstance(app, Litestar)
        # Sanity floor: the app should have registered hundreds of
        # routes after the migration -- a near-zero count would mean
        # the controllers tree never registered.
        assert len(app.routes) > 100
