"""Positive fixtures: deliberate genuine leaks with no sanitiser.

CodeQL analysis MUST still alert on these functions even with the
synthorg-* custom queries loaded. If alerts do not fire, a custom query
is over-suppressing genuine leaks.
"""

import logging
import sys
import urllib.request

logger = logging.getLogger(__name__)


def positive_clear_text_logging() -> None:
    """synthorg/clear-text-logging-sensitive-data MUST fire here."""
    try:
        raise RuntimeError("token=client_secret_value")
    except RuntimeError as exc:
        logger.warning(
            "outbound op failed",
            extra={"error": str(exc)},
        )


def positive_partial_ssrf(repo: str, tag: str) -> int:
    """synthorg/partial-ssrf MUST fire here.

    No regex validation, no urllib.parse.quote -- the user-supplied
    components flow straight into the URL path.
    """
    url = f"https://ghcr.io/v2/{repo}/manifests/{tag}"
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


if __name__ == "__main__":
    positive_clear_text_logging()
    positive_partial_ssrf(sys.argv[1], sys.argv[2])
