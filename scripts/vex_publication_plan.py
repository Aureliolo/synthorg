#!/usr/bin/env python3
"""Decide whether the rendered OpenVEX document may be published, and attest.

Run from the image-publish path, immediately before ``cosign attest``. It
answers two questions the publish job cannot otherwise ask:

* **Is this document generated, or was it edited by hand?** The document's
  ``@id`` is a SHA-256 over its own statements, so recomputing it detects any
  edit to what the document claims. That is the property worth checking at a
  signing boundary: everything downstream treats a signed statement as
  reviewed, and the whole arrangement rests on ``.github/vex/triage.yaml``
  being the only place a vulnerability is silenced.
* **Does it claim anything at all?** An empty document is not worth a Sigstore
  round trip per image.

What it deliberately does NOT do is re-render the ledger.
``scripts/check_vex_triage_sync.py`` owns that comparison and needs PyYAML and
the generator; putting a Python toolchain into the image-publish path to
repeat it there would add minutes and a failure mode to the job that publishes
signed images. So the residual gap is narrow and worth naming: a document
regenerated from an unreviewed ledger passes here, because it is internally
consistent. Reaching main with one takes a hand-edit, a ``--no-verify`` push
and an admin merge, each of which bypasses a gate that would have caught it.

Stdlib only, on purpose: the publish job runs the system ``python3`` with no
virtualenv.

Exit codes:
    0 -- the document is intact; the statement count is on stdout.
    1 -- the document is missing, malformed, or does not match its own ``@id``.

Usage::

    python3 scripts/vex_publication_plan.py .github/vex/synthorg.openvex.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Final

_ID_KEY: Final[str] = "@id"
_STATEMENTS_KEY: Final[str] = "statements"


class VexPublicationError(Exception):
    """The document cannot be published as it stands."""


def fingerprint(statements: list[object]) -> str:
    """Content-address a statement list.

    Mirrors ``scripts/generate_vex_documents.py``. The two must agree, which
    the generator's own tests pin from the other side by asserting the
    rendered ``@id``.
    """
    canonical = json.dumps(statements, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_document(path: Path) -> dict[str, object]:
    """Read the rendered OpenVEX document, or fail closed.

    Args:
        path: The rendered document.

    Returns:
        The parsed document.

    Raises:
        VexPublicationError: Missing, unreadable, or not a JSON object.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"{path}: {exc}"
        raise VexPublicationError(msg) from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"{path}: not valid JSON ({exc})"
        raise VexPublicationError(msg) from exc
    if not isinstance(document, dict):
        msg = f"{path}: expected a JSON object, found {type(document).__name__}"
        raise VexPublicationError(msg)
    return document


def statements_to_publish(document: dict[str, object], path: Path) -> list[object]:
    """Return the statements, having established the document was generated.

    Args:
        document: The parsed document.
        path: Where it came from, for the message.

    Returns:
        The statements the document carries.

    Raises:
        VexPublicationError: The document is shaped wrongly, or its ``@id``
            does not content-address its statements.
    """
    statements = document.get(_STATEMENTS_KEY)
    if not isinstance(statements, list):
        msg = f"{path}: '{_STATEMENTS_KEY}' is missing or not a list"
        raise VexPublicationError(msg)
    document_id = document.get(_ID_KEY)
    if not isinstance(document_id, str):
        msg = f"{path}: '{_ID_KEY}' is missing or not a string"
        raise VexPublicationError(msg)
    expected = fingerprint(statements)
    if not document_id.endswith(expected):
        msg = (
            f"{path}: '{_ID_KEY}' does not content-address these statements, so "
            f"the document was edited rather than generated. Edit "
            f".github/vex/triage.yaml and regenerate; refusing to sign a claim "
            f"that did not come from the ledger"
        )
        raise VexPublicationError(msg)
    return statements


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Verify the rendered OpenVEX document is intact and report "
        "how many statements it carries.",
    )
    parser.add_argument("document", type=Path)
    args = parser.parse_args(argv)

    try:
        document = load_document(args.document)
        statements = statements_to_publish(document, args.document)
    except VexPublicationError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    print(len(statements))
    return 0


if __name__ == "__main__":
    sys.exit(main())
