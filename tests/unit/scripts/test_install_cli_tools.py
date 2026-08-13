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
_ASSETS_PER_TOOL: Final[int] = 2

# The three ceilings the ladder declares. They bound different things and none
# stands in for another: ``--retry-max-time`` decides only whether a NEW
# attempt may start, ``--max-time`` ends a transfer that opened and stalled,
# and ``--connect-timeout`` ends a connection that never opened.
_REQUIRED_CEILINGS: Final[tuple[str, ...]] = (
    "--retry-max-time",
    "--max-time",
    "--connect-timeout",
)

# The shortest job budget among the script's callers, in seconds (the
# link-check job). The download ladder has to fit inside it with room for the
# work the job actually exists to do.
_TIGHTEST_CALLER_BUDGET_SECONDS: Final[int] = 600


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


def _flag_value(flag: str) -> int:
    """Return the integer argument ``flag`` carries in the download command.

    Anchored on a preceding space so a shorter flag cannot be read off the
    tail of a longer one that ends in the same characters.
    """
    match = re.search(rf"(?<!\S){re.escape(flag)} (\d+)", _download_command())
    assert match is not None, f"{flag} is absent from the download command"
    return int(match.group(1))


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

    @pytest.mark.parametrize("flag", _REQUIRED_CEILINGS)
    def test_every_ceiling_is_positive(self, flag: str) -> None:
        # Presence is not the property under test: curl reads 0 as "no limit"
        # on all three, so a ceiling written as 0 reads like a bound in the
        # source while being the absence of one at runtime.
        assert _flag_value(flag) > 0

    def test_a_stalled_attempt_cannot_dominate_the_retry_window(self) -> None:
        # ``--retry-max-time`` governs only whether a NEW attempt may start;
        # curl runs an in-flight one to completion. So an attempt allowed to
        # last longer than the whole retry window can outlive every other
        # bound the ladder declares, and the job hangs until the runner reaps
        # it, surfacing as a cancelled job rather than the failed download.
        assert _flag_value("--max-time") <= _flag_value("--retry-max-time")

    def test_the_worst_case_fits_the_tightest_caller_budget(self) -> None:
        # Worst case per asset is the full retry window plus the one in-flight
        # attempt curl is allowed to finish past it, and a tool install fetches
        # its archive and its checksum record. That has to leave the caller
        # enough of its job to do the work it was scheduled for.
        per_asset = _flag_value("--retry-max-time") + _flag_value("--max-time")
        assert per_asset * _ASSETS_PER_TOOL < _TIGHTEST_CALLER_BUDGET_SECONDS

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
