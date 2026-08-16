"""Which security config a runtime rebuild composes against.

The agent -> SynthOrg-MCP bridge is composed once when the engine is built and
held for its lifetime, so an operator opening it reaches no agent until a
rebuild. `security.mcp_self_consumer_mode` therefore triggers one, and the
rebuild has to read the value that triggered it: composing against the boot
snapshot would rebuild the engine and hand it the mode the process started
with, which looks exactly like the toggle doing nothing.

The holder swap is covered by the security-bridge subscriber's own tests and
the trigger by the runtime-reload subscriber's; what is pinned here is the
read between them.
"""

import pytest

from synthorg.security.runtime_config import MutableSecurityConfig
from synthorg.workers._engine_assembly import _live_security
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


class TestLiveSecurity:
    def test_a_swapped_config_is_what_a_rebuild_sees(self) -> None:
        app_state = make_app_state()
        boot = app_state.config.security
        written = boot.model_copy(
            update={
                "mcp_self_consumer": boot.mcp_self_consumer.model_copy(
                    update={"mode": "trust_scoped"}
                )
            }
        )

        app_state.security_runtime_config.swap(written)

        assert _live_security(app_state).mcp_self_consumer.mode == "trust_scoped"

    def test_the_boot_config_answers_before_any_write(self) -> None:
        # Nothing to prefer yet, and the boot config is what the holder was
        # seeded with, so this is a fallback with no second owner.
        app_state = make_app_state()

        assert _live_security(app_state) is app_state.security_runtime_config.current

    def test_an_absent_holder_falls_back_rather_than_returning_none(self) -> None:
        """A rebuild must always have a config to compose against.

        ``current`` is ``None`` only when the process booted with no security
        config at all; returning that to the bridge builder would swap a
        posture question for a crash during the rebuild.
        """
        app_state = make_app_state()
        app_state.security_runtime_config = MutableSecurityConfig(None)

        assert _live_security(app_state) is app_state.config.security
