# module-kind: code
"""OpenAI-compatible LLM gateway boundary.

The gateway fronts the in-process :class:`ProviderRegistry` with an
OpenAI-compatible HTTP surface so a process outside the runtime can point
its LLM client at ``base_url`` and still route through SynthOrg's
governance: explicit ``(provider, model)`` binding, per-run cost and
prompt attribution, a hard token-budget kill, and secret-redacted logging.
Nothing the product itself runs dispatches here, so it ships disabled
(``providers.gateway_enabled``); the recording harness under ``evals/``
turns it on to measure a run's real spend through one boundary.
"""
