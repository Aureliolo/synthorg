"""First-run setup controllers and their agent / company helpers.

The setup wizard surface is split per sub-domain across sibling
controller modules -- ``status`` (status probe + template listing),
``company`` (company creation), ``agents`` (agent CRUD), ``locales``
(name-locale configuration), and ``completion`` (the completion flow
plus its prerequisite-validation helpers) -- alongside the shared
agent-side helper modules ``_status_checks`` (read-only probes),
``_runtime_wiring`` (provider reload / hot-swap / setup locks), and
``_embedder_setup`` (template-agent creation / embedder selection), plus
``company_helpers`` (company-metadata).

Direct imports only:
``from synthorg.api.controllers.setup.<module> import ...``.
This package's ``__init__`` deliberately stays empty so the boundary
between each sub-domain controller and the helper modules is
explicit at every call site.
"""
