"""Shared helpers for built-in intake strategies."""

from synthorg.client.models import ClientRequest
from synthorg.core.types import NotBlankStr


def resolve_request_project(
    request: ClientRequest,
    default: NotBlankStr,
) -> NotBlankStr:
    """Resolve the project a request's task must be filed under.

    Work entering the pipeline carries its resolved project id in
    ``metadata["project"]`` (stamped by the pipeline's intake mapping from
    ``WorkItem.project``). A strategy must file the task under that project,
    not its own construction-time default, or a charter or objective run
    lands in the wrong project and its tasks become invisible under the one
    the caller actually created.

    Returns:
        ``metadata["project"]`` when present and non-blank, else ``default``.
    """
    raw = request.metadata.get("project")
    if isinstance(raw, str) and raw.strip():
        return NotBlankStr(raw)
    return default
