"""Negative fixtures: each function exercises one modelled barrier.

CodeQL analysis with the synthorg-sanitisers extension pack loaded MUST NOT
report alerts on these functions. If any rule fires here, the pack is
under-modelling its sanitiser.
"""

import logging
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from scripts.check_image_signatures import (
    ImageTag,
    _validate_image_tag,
    _validate_repo_prefix,
)

from synthorg.observability.redaction import safe_error_description

logger = logging.getLogger(__name__)


def negative_clear_text_logging() -> None:
    """py/clear-text-logging-sensitive-data must NOT fire here."""
    try:
        raise RuntimeError("token=client_secret_value")
    except RuntimeError as exc:
        logger.warning(
            "outbound op failed",
            extra={
                "error_type": type(exc).__name__,
                "error": safe_error_description(exc),
            },
        )


def negative_path_injection_relative_to(
    user_input: str, project_root: Path
) -> str | None:
    """py/path-injection must NOT fire here.

    Pattern: resolve, then assert containment via ``Path.relative_to`` in
    a try/except block. The ValueError branch is the safe-exit.
    """
    candidate = (project_root / user_input).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return None
    return candidate.read_text()


def negative_partial_ssrf(repo_prefix: str, image: str, tag: str) -> int:
    """py/partial-ssrf must NOT fire here.

    Pattern: anchored regex validators, then percent-encode each path
    component before constructing the registry URL.
    """
    _validate_repo_prefix(repo_prefix)
    pair = ImageTag(image=image, tag=tag)
    _validate_image_tag(pair)
    safe_repo = urllib.parse.quote(f"{repo_prefix}{pair.image}", safe="/")
    safe_tag = urllib.parse.quote(pair.tag, safe="")
    url = f"https://ghcr.io/v2/{safe_repo}/manifests/{safe_tag}"
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


if __name__ == "__main__":
    negative_clear_text_logging()
    negative_path_injection_relative_to(sys.argv[1], Path(sys.argv[2]))
    negative_partial_ssrf(sys.argv[3], sys.argv[4], sys.argv[5])
