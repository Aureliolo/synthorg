"""Setup controller helpers, split between agent-side and company-side concerns.

Direct imports only:
``from synthorg.api.controllers.setup.agent_helpers import ...`` or
``from synthorg.api.controllers.setup.company_helpers import ...``.
This package's ``__init__`` deliberately stays empty so the boundary
between agent-bootstrap helpers and company-metadata helpers is
explicit at every call site.
"""
