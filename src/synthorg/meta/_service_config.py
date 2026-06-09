"""Config-dump secret redaction for the self-improvement service.

Masks the dotted JSON paths in :data:`_SECRET_PATHS` so the
``get_config`` facade (and the MCP ``synthorg_meta_get_config`` tool)
can return an auditable config readout without leaking GitHub PATs or
the cross-deployment salt into telemetry.
"""

_SECRET_PATHS: frozenset[str] = frozenset(
    {
        "code_modification.github_token",
        "cross_deployment_analytics.deployment_id_salt",
    }
)
"""Dotted JSON paths whose values are redacted by ``_redact_secrets``.

Adding a new entry requires a matching test case in
``tests/unit/meta/test_service_get_config.py``; the redactor silently
ignores unknown paths, so a misspelled entry only fails in tests.
"""

_REDACTED: str = "***redacted***"


def _redact_secrets(
    dump: dict[str, object],
    paths: frozenset[str],
) -> dict[str, object]:
    """Return a copy of *dump* with each path in *paths* masked.

    Operates on a copy so the caller's source data is never mutated.
    Unknown paths are silently ignored: redaction must remain a
    safe-default operation even when the config schema is in flux.

    Returns:
        Mapping with the declared key/value types.
    """
    redacted = dict(dump)
    for path in paths:
        keys = path.split(".")
        node: dict[str, object] = redacted
        aborted = False
        # Walk down a copy at each level. Each ``cloned`` dict replaces
        # the parent's reference in ``redacted`` so the mutation stays
        # local; the corresponding nested dict on the caller's original
        # ``dump`` is never touched. This keeps ``get_config`` safe to
        # call repeatedly without re-leaking secrets across calls.
        for key in keys[:-1]:
            child = node.get(key)
            if not isinstance(child, dict):
                aborted = True
                break
            cloned: dict[str, object] = dict(child)
            node[key] = cloned
            node = cloned
        if aborted:
            continue
        leaf = keys[-1]
        if leaf in node and node[leaf] is not None:
            node[leaf] = _REDACTED
    return redacted
