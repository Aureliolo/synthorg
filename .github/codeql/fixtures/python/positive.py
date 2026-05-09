"""Positive fixtures: deliberate genuine leaks with no sanitiser.

CodeQL analysis MUST still alert on these functions even with the
synthorg-sanitisers extension pack loaded. If alerts do not fire, the pack
is over-suppressing.
"""

import logging
import sys
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


def positive_clear_text_logging() -> None:
    """py/clear-text-logging-sensitive-data MUST fire here."""
    try:
        raise RuntimeError("token=client_secret_value")
    except RuntimeError as exc:
        logger.warning(
            "outbound op failed",
            extra={"error": str(exc)},
        )


def positive_path_injection(user_input: str) -> str:
    """py/path-injection MUST fire here.

    No containment check, no sanitisation -- the resolved path goes
    straight into ``Path.read_text``.
    """
    return Path(user_input).read_text()


def positive_partial_ssrf(repo: str, tag: str) -> int:
    """py/partial-ssrf MUST fire here.

    No regex validation, no urllib.parse.quote -- the user-supplied
    components flow straight into the URL path.
    """
    url = f"https://ghcr.io/v2/{repo}/manifests/{tag}"
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


if __name__ == "__main__":
    positive_clear_text_logging()
    positive_path_injection(sys.argv[1])
    positive_partial_ssrf(sys.argv[2], sys.argv[3])
