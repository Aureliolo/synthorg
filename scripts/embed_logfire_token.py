"""Embed the write-only telemetry-backend project token into the build artifact.

Run **once** during the build pipeline before ``uv build`` (or
before the Docker image's ``uv sync`` step packages the source
tree). Rewrites the sentinel string in
``src/synthorg/telemetry/reporters/_embedded_token.py`` to the
real token so the published wheel / built image carries it baked
into source. Source-tree installs and any build that does NOT run
this script keep the sentinel and run telemetry-disabled by build.

The script filename is preserved verbatim because it has to
match the build-time GHA secret name (referenced in the rotation
note below) for operator clarity in CI logs. Every other prose,
error message, and stderr line in this module uses vendor-neutral
phrasing.

Usage:
    python scripts/embed_logfire_token.py <token>

Exit codes:
    0  Token successfully embedded.
    2  Usage error (missing or empty token argument).
    3  Sentinel not found in target file (build artifact already
       embedded -- safety check, never overwrite an existing token).
    4  Target file unreadable / unwritable.

Operator-facing token rotation: bump the GitHub Actions repository
secret ``LOGFIRE_PROJECT_TOKEN`` and re-publish. The next image /
wheel build picks up the new value automatically.
"""

import sys
from pathlib import Path

_TOKEN_SENTINEL = "__SYNTHORG_TELEMETRY_TOKEN_NOT_EMBEDDED__"  # noqa: S105
_TARGET_RELATIVE = Path("src/synthorg/telemetry/reporters/_embedded_token.py")
_EXPECTED_ARGV_LEN = 2  # script name + token positional arg


def _resolve_target() -> Path:
    """Return the absolute path to ``_embedded_token.py`` from the repo root."""
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / _TARGET_RELATIVE


def embed(token: str, target: Path) -> None:
    """Rewrite the sentinel string literal in *target* to *token* in place.

    The sentinel lives inside a quoted Python string literal. A naive
    ``str.replace`` would inject the token text directly into the
    source, which breaks the literal when the token contains a quote,
    a backslash escape, or a newline. ``repr()`` produces a properly
    escaped Python string literal that drops in safely regardless of
    the token's contents -- the surrounding quotes from the existing
    sentinel literal are also replaced so the result remains
    syntactically valid.

    Raises:
        ValueError: If the quoted sentinel is not found (already
            embedded or wrong target file).
        OSError: If the file cannot be read or written.
    """
    if not token or not token.strip():
        msg = "token must be a non-empty string"
        raise ValueError(msg)
    content = target.read_text(encoding="utf-8")
    if _TOKEN_SENTINEL not in content:
        msg = (
            f"sentinel {_TOKEN_SENTINEL!r} not found in {target}; "
            "refusing to overwrite an already-embedded token"
        )
        raise ValueError(msg)
    # Match the sentinel WITH its surrounding quote so we replace the
    # entire string literal with a freshly-quoted ``repr(token)``.
    # Ruff format normalises sentinel strings to double quotes today,
    # but accept either quote style so a manual edit, a different
    # formatter rev, or a future pre-commit reorg cannot turn this
    # into a silent exit-3 noop. ``repr(token)`` always emits Python's
    # canonical quoting on the way out, so the rewritten file remains
    # syntactically valid regardless of which input quote style we
    # matched on.
    quoted_sentinel: str | None = None
    for quote in ('"', "'"):
        candidate = f"{quote}{_TOKEN_SENTINEL}{quote}"
        if candidate in content:
            quoted_sentinel = candidate
            break
    if quoted_sentinel is None:
        msg = (
            f"quoted sentinel for {_TOKEN_SENTINEL!r} not found in {target}; "
            "refusing to overwrite an already-embedded token"
        )
        raise ValueError(msg)
    # The quoted sentinel must appear in exactly one place (the
    # ``EMBEDDED_TELEMETRY_TOKEN = "..."`` constant). A repeated
    # occurrence (e.g. someone duplicated the literal into a docstring
    # or test fixture inside the target file itself) would let
    # ``replace(..., 1)`` silently land on the wrong line; refuse to
    # guess and force the operator to disambiguate.
    occurrences = content.count(quoted_sentinel)
    if occurrences != 1:
        msg = (
            f"expected exactly one {quoted_sentinel!r} occurrence in "
            f"{target}, found {occurrences}; refusing ambiguous replacement"
        )
        raise ValueError(msg)
    rewritten = content.replace(quoted_sentinel, repr(token), 1)
    target.write_text(rewritten, encoding="utf-8")


def main(argv: list[str]) -> int:
    """Entry point. Returns a process exit code (see module docstring)."""
    if len(argv) != _EXPECTED_ARGV_LEN or not argv[1].strip():
        sys.stderr.write(
            "usage: python scripts/embed_logfire_token.py <token>\n",
        )
        return 2
    token = argv[1].strip()
    target = _resolve_target()
    try:
        embed(token, target)
    except ValueError as exc:
        sys.stderr.write(f"embed_telemetry_token: {exc}\n")
        return 3
    except OSError as exc:
        sys.stderr.write(f"embed_telemetry_token: {exc}\n")
        return 4
    sys.stderr.write(f"Embedded telemetry token into {target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
