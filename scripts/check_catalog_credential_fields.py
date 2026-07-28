"""Gate: every catalog entry maps a credential field its connection type has.

``credential_env_map`` directs a bound connection's secret into the
environment variable an MCP server expects, resolved by *exact* field name
at connect time with no aliasing. An entry that names a field the required
connection type never stores injects nothing: the server launches
unauthenticated and the only signal is a warning nobody is watching for,
followed much later by an opaque upstream auth failure.

The two sides of that contract live in different files (the entry in
``bundled.json``, the fields in ``field_metadata.py``), so nothing but this
gate notices when they drift apart.

Usage:
    uv run python scripts/check_catalog_credential_fields.py

Exit codes:
    0 -- every mapped field is a declared credential field.
    1 -- an entry names a field its connection type does not store.
    2 -- configuration error (bad ``--repo-root`` or an unreadable catalog).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Final

_CATALOG_REL: Final[str] = "src/synthorg/integrations/mcp_catalog/bundled.json"


def _declared_credential_fields(connection_type: str) -> set[str]:
    """Credential-placed field names a connection type stores.

    Returns:
        The field names, or an empty set for an unknown type (the catalog
        model already rejects those, so this gate does not restate it).

    Raises:
        SystemExit: If the metadata registry cannot be imported, which
            means the gate cannot answer and must not pass silently.
    """
    try:
        from synthorg.integrations.connections.field_metadata import (
            FieldPlacement,
            get_connection_type_metadata,
        )
        from synthorg.integrations.connections.models import (
            ConnectionType,
        )
    except ImportError as exc:  # pragma: no cover -- environment fault
        print(f"error: cannot import the field-metadata registry: {exc}")
        raise SystemExit(2) from exc
    try:
        metadata = get_connection_type_metadata(ConnectionType(connection_type))
    except ValueError:
        return set()
    return {
        field.name
        for field in metadata.fields
        if field.placement is FieldPlacement.CREDENTIAL
    }


def _check(repo_root: Path) -> list[str]:
    """Compare every entry's credential map against its connection type.

    Returns:
        A list of violation messages (empty when the two agree).
    """
    path = repo_root / _CATALOG_REL
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {_CATALOG_REL}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    violations: list[str] = []
    for server in payload.get("servers", []):
        env_map = server.get("credential_env_map") or {}
        required_type = server.get("required_connection_type")
        if not env_map or not required_type:
            continue
        declared = _declared_credential_fields(required_type)
        violations.extend(
            f"{_CATALOG_REL}: entry {server.get('id')!r} maps credential "
            f"field {field!r}, which a {required_type!r} connection never "
            f"stores (it declares {sorted(declared)}); the server would "
            f"launch unauthenticated"
            for field in sorted(env_map)
            if field not in declared
        )
    return violations


def main() -> int:
    """Run the catalog credential-field gate.

    Returns:
        The process exit code (0 clean, 1 drift, 2 config error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    violations = _check(args.repo_root)
    if violations:
        print("MCP catalog credential-field check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
