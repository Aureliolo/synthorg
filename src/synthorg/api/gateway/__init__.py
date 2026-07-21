# module-kind: code
"""OpenAI-compatible LLM gateway boundary.

The gateway fronts the in-process :class:`ProviderRegistry` with an
OpenAI-compatible HTTP surface so an embedded coding harness (OpenHands)
can point its LLM at ``base_url`` and still route through SynthOrg's
governance: explicit ``(provider, model)`` binding, per-run cost and
prompt attribution, a hard token-budget kill, and secret-redacted logging.
The harness reaches it only over the sandbox sidecar egress allowlist.
"""
