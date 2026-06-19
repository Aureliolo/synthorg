"""Single source for the Scalar UI ``/docs`` Content-Security-Policy origins.

The registry default (a JSON string in
:mod:`synthorg.settings.definitions.api`) and the ``ApiBridgeConfig``
tuple default both derive from :data:`CSP_DOCS_EXTERNAL_ORIGINS`, so the
two surfaces cannot drift. Operators override the resolved list at
runtime; this is only the shipped default.
"""

from typing import Final

CSP_DOCS_EXTERNAL_ORIGINS: Final[tuple[str, ...]] = (
    "https://cdn.jsdelivr.net",
    "https://fonts.scalar.com",
    "https://proxy.scalar.com",
)
