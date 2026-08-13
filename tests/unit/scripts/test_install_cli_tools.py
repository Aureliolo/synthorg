"""Unit tests for ``scripts/install_cli_tools.sh``.

The script provisions the pre-push toolchain for CI and for local clones, and
every binary it installs is fetched from the same github.com release CDN. That
CDN answers 503 for tens of seconds at a stretch, so the retry ladder in front
of it decides whether an outage produces a slow job or a red one. A ladder is
only as strong as its weakest call site: the script runs under ``set -e``, so
one download that gives up early aborts the whole install.

These assertions read the script as text rather than running it. The ladder is
declarative -- a fixed set of curl flags -- so executing an install would spend
a real network round trip to observe something already stated in the source.
"""

import re
from pathlib import Path
from typing import Final

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH: Final[Path] = _REPO_ROOT / "scripts" / "install_cli_tools.sh"
_HELPER: Final[str] = "fetch_release_asset"

# Waiting floor before the final attempt, in seconds. curl's default backoff
# doubles from 1s, so N retries wait ``2**N - 1`` seconds across the sequence.
# A CDN 503 episode outlasts a single-digit window, which is the shape this
# floor exists to reject.
_MIN_BACKOFF_SECONDS: Final[int] = 60

# The four assets the script fetches, keyed by the URL variable each call
# site passes. Two tools, each with its binary archive and the checksum
# record that proves it.
_ASSET_URL_VARS: Final[frozenset[str]] = frozenset(
    {"${download_url}", "${sha_url}", "${checksums_url}"}
)
_ASSET_COUNT: Final[int] = 4


def _script() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def _folded_lines(text: str) -> list[str]:
    """Return the script's lines with continuations folded and runs collapsed.

    The curl invocation spreads its flags across three continued lines, so a
    per-line read would see each flag in isolation and none of them together.
    """
    return [" ".join(line.split()) for line in text.replace("\\\n", " ").splitlines()]


def _download_command() -> str:
    """Return the single curl invocation that writes an asset to disk.

    Asserting uniqueness here rather than in one dedicated test keeps every
    flag assertion below honest: each one would otherwise pass on a script
    carrying a second, weaker downloader beside the compliant one.
    """
    downloads = [
        line
        for line in _folded_lines(_script())
        if line.startswith("curl ") and "--output" in line
    ]
    assert len(downloads) == 1
    return downloads[0]


class TestReleaseDownloadLadder:
    """Every release asset is fetched through one shared retry ladder."""

    def test_the_ladder_outlasts_a_cdn_outage_window(self) -> None:
        retries = re.search(r"--retry (\d+)", _download_command())
        assert retries is not None
        assert 2 ** int(retries.group(1)) - 1 >= _MIN_BACKOFF_SECONDS

    def test_the_ladder_backs_off_exponentially(self) -> None:
        # ``--retry-delay`` pins every wait to the same value, collapsing the
        # doubling sequence to N*delay. Three retries two seconds apart is a
        # six-second ladder wearing the word "retry".
        assert "--retry-delay" not in _download_command()

    def test_the_ladder_is_bounded(self) -> None:
        # Without a ceiling a sustained outage holds the job until the runner
        # reaps it, which surfaces as a cancelled job rather than as the
        # failed download it is.
        assert "--retry-max-time" in _download_command()

    def test_the_ladder_covers_curl_level_failures(self) -> None:
        # The same CDN episode presents as a reset connection about as often
        # as a 503, and only ``--retry-all-errors`` retries the former.
        assert "--retry-all-errors" in _download_command()

    def test_the_download_fails_closed(self) -> None:
        # Without ``--fail``, curl writes the CDN's error page to the output
        # path and exits 0, and the install goes on to checksum an HTML
        # document.
        assert "--fail" in _download_command()


class TestEveryAssetUsesTheLadder:
    """No asset reaches the network outside the shared helper."""

    @staticmethod
    def _calls() -> list[str]:
        return [
            line for line in _folded_lines(_script()) if line.startswith(f"{_HELPER} ")
        ]

    def test_every_asset_is_fetched_through_the_helper(self) -> None:
        # Counted, not merely sampled: a call site dropped back to a bare
        # curl would still leave the surviving ones matching by name.
        assert len(self._calls()) == _ASSET_COUNT

    @pytest.mark.parametrize("url_var", sorted(_ASSET_URL_VARS))
    def test_each_asset_url_reaches_the_helper(self, url_var: str) -> None:
        assert any(url_var in call for call in self._calls())
