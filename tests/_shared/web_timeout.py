"""Shared web-tool wiring for test callers of the tool factory.

Production callers of :func:`build_default_tools` /
:func:`build_default_tools_from_config` must resolve
``tools.web_request_timeout_seconds`` via ``ConfigResolver`` and pass
the result -- the factory deliberately rejects a non-positive value so
the registry resolution path is the only sanctioned way to land one in
production.

Tests have no ``ConfigResolver`` and don't care which value the web
tool actually uses; they just need *something* numeric to satisfy the
mandatory field.  This mirrors ``WebToolsConfig.request_timeout`` so the
test surface stays in sync with the documented Pydantic default without
re-importing the model at every call site.
"""

from synthorg.tools.web.config import WebToolsConfig
from synthorg.tools.web.fetch_types import WebToolsWiring

DEFAULT_TEST_WEB_REQUEST_TIMEOUT: float = WebToolsConfig().request_timeout

DEFAULT_TEST_WEB_WIRING: WebToolsWiring = WebToolsWiring(
    request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
)
