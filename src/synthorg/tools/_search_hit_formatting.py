# module-kind: code
"""Shared rendering for scored search-tool hits.

The living-docs and project-brain search tools render hits identically:
a ``[label] identifier (score):`` header followed by the chunk body, with
a blank line between hits. Both feed agent-authored content (brain
entries and living-doc chunks alike are attacker-influenceable, since an
upstream agent may have been prompt-injected when it wrote them), so each
caller passes the fence ``wrap_tag`` appropriate to its source. This
helper is the single source of truth for that layout.

The knowledge search tool uses a distinct citation-anchored layout and
keeps its own formatter.
"""

from collections.abc import Iterable

from synthorg.engine.prompt_safety import wrap_untrusted


def format_scored_hits(
    rows: Iterable[tuple[str, str, float, str]],
    *,
    empty_msg: str,
    wrap_tag: str | None = None,
) -> str:
    """Render scored hits as ``[label] id (score):`` blocks.

    Args:
        rows: ``(label, identifier, score, body)`` per hit.
        empty_msg: Returned verbatim when there are no rows.
        wrap_tag: When set, the body is fenced via
            :func:`~synthorg.engine.prompt_safety.wrap_untrusted` under
            this tag (untrusted, attacker-influenceable content).

    Returns:
        A blank-line-separated multi-line summary, or ``empty_msg``.
    """
    materialised = list(rows)
    if not materialised:
        return empty_msg
    lines: list[str] = []
    for label, identifier, score, body in materialised:
        lines.append(f"[{label}] {identifier} (score={score:.2f}):")
        lines.append(wrap_untrusted(wrap_tag, body) if wrap_tag else body)
        lines.append("")
    return "\n".join(lines).rstrip()
