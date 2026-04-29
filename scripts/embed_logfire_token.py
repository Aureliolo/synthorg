"""Embed the Logfire write-only project token into the build artifact.

Run **once** during the build pipeline before ``uv build`` (or
before the Docker image's ``uv sync`` step packages the source
tree). Rewrites the sentinel string in
``src/synthorg/telemetry/reporters/_embedded_token.py`` to the
real token so the published wheel / built image carries it baked
into source. Source-tree installs and any build that does NOT run
this script keep the sentinel and run telemetry-disabled by build.

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

_TOKEN_SENTINEL = "__SYNTHORG_LOGFIRE_TOKEN_NOT_EMBEDDED__"  # noqa: S105
_TARGET_RELATIVE = Path("src/synthorg/telemetry/reporters/_embedded_token.py")
_EXPECTED_ARGV_LEN = 2  # script name + token positional arg


def _resolve_target() -> Path:
    """Return the absolute path to ``_embedded_token.py`` from the repo root."""
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / _TARGET_RELATIVE


def embed(token: str, target: Path) -> None:
    """Rewrite the sentinel in *target* to *token* in place.

    Raises:
        ValueError: If the sentinel is not found (already embedded
            or wrong target file).
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
    rewritten = content.replace(_TOKEN_SENTINEL, token, 1)
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
        sys.stderr.write(f"embed_logfire_token: {exc}\n")
        return 3
    except OSError as exc:
        sys.stderr.write(f"embed_logfire_token: {exc}\n")
        return 4
    sys.stderr.write(f"Embedded Logfire token into {target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
