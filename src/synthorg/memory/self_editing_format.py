# module-kind: code
"""Human-readable rendering for the self-editing memory tools.

Split from :mod:`synthorg.memory.self_editing` so the strategy module
stays within its size budget; these are the two pure formatters its tool
handlers return text through, with no dependency on the strategy itself.
"""

from synthorg.engine.prompt_safety import TAG_MEMORY_ENTRY, wrap_untrusted
from synthorg.memory.models import MemoryEntry


def format_self_editing_error(err: object) -> str:
    """Render a single Pydantic ``errors()`` entry as ``loc: msg``.

    Strips the ``tool`` discriminator from ``loc`` (it's a dispatch
    concern not surfaced to the LLM caller).

    Returns:
        Result of type ``str``.
    """
    if not isinstance(err, dict):
        return "<arguments>: invalid"
    loc_raw = err.get("loc", ())
    loc_parts = loc_raw if isinstance(loc_raw, tuple) else ()
    loc = ".".join(str(p) for p in loc_parts if p != "tool") or "<arguments>"
    msg = err.get("msg", "")
    return f"{loc}: {msg}" if isinstance(msg, str) else f"{loc}: invalid"


def format_entries(entries: tuple[MemoryEntry, ...]) -> str:
    """Format memory entries as human-readable tool response text.

    Args:
        entries: Memory entries to format.

    Returns:
        Formatted multi-line string, or ``"No memories found."`` if empty.
    """
    if not entries:
        return "No memories found."
    # Stored content is model-writable, so a prior write could have planted
    # instructions; fence each entry as untrusted before it reaches the
    # model that reads this back (SEC-1).
    return "\n".join(
        f"[{e.category.value}] (id={e.id}) "
        f"{wrap_untrusted(TAG_MEMORY_ENTRY, e.content)}"
        for e in entries
    )


__all__ = ["format_entries", "format_self_editing_error"]
