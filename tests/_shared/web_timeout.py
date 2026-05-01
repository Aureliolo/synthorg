"""Shared default ``web_request_timeout`` for test callers.

Production callers of :func:`build_default_tools` /
:func:`build_default_tools_from_config` must resolve
``tools.web_request_timeout_seconds`` via ``ConfigResolver`` and pass
the result -- the factory deliberately rejects ``None`` so the
registry resolution path is the only sanctioned way to land a value
in production.

Tests have no ``ConfigResolver`` and don't care which value the web
tool actually uses; they just need *something* numeric to satisfy the
mandatory parameter.  This constant mirrors
``WebToolsConfig.request_timeout`` so the test surface stays in sync
with the documented Pydantic default without re-importing the model
at every call site.
"""

from synthorg.tools.web.config import WebToolsConfig

DEFAULT_TEST_WEB_REQUEST_TIMEOUT: float = WebToolsConfig().request_timeout
