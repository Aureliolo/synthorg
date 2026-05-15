#!/usr/bin/env python3
"""Generate the three ``web/src/api/types/*.gen.ts`` DTO type files.

Pipeline:

1. Boot the Litestar app in-process (matching the env contract in
   ``scripts/export_openapi.py``: in-memory SQLite + stable pagination
   cursor secret) and export the enriched OpenAPI schema via
   ``synthorg.api.openapi.inject_rfc9457_responses``.
2. Promote every property of every response-side schema into
   ``required[]`` (see ``_promote_response_defaults_to_required``).
   ``openapi-typescript`` reads ``required[]`` literally, while
   Pydantic's default serialiser always emits every field, so without
   this step generated response types are wrongly optional.
3. Hand the post-processed schema to ``npx openapi-typescript``
   (pinned via ``web/package-lock.json``) to render the verbatim
   ``openapi.gen.ts`` (full ``paths`` + ``components`` shape).
4. Render two thin Python-side outputs from the same schema dict:
   - ``dtos.gen.ts``: a named-alias layer over
     ``components['schemas']`` so consumers can write ``AgentConfig``
     instead of ``components['schemas']['AgentConfig']``. Litestar's
     monomorphised generic names (``ApiResponse_AgentConfig_``) are
     aliased to friendlier ``AgentConfigEnvelope`` / ``AgentConfigPage``
     forms.
   - ``enum-values.gen.ts``: runtime ``*_VALUES`` tuples plus the
     derived string-union types the dashboard already relies on for
     ``<select>`` options and type guards (``openapi-typescript``
     emits types only, not runtime values).

The generator mirrors ``scripts/generate_error_codes_ts.py`` for the
argparse surface (``--check`` for byte-comparison drift detection,
``--stdout`` for previewing), file-write semantics (LF line endings
on every platform), and the paired ``check_*.py`` gate wrapper at
``scripts/check_dto_types_ts_in_sync.py``.

Usage::

    uv run python scripts/generate_dto_types_ts.py
    uv run python scripts/generate_dto_types_ts.py --check
    uv run python scripts/generate_dto_types_ts.py --stdout
"""

import argparse
import contextlib
import difflib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
WEB_DIR: Final[Path] = REPO_ROOT / "web"

OPENAPI_GEN_TS: Final[Path] = WEB_DIR / "src" / "api" / "types" / "openapi.gen.ts"
DTOS_GEN_TS: Final[Path] = WEB_DIR / "src" / "api" / "types" / "dtos.gen.ts"
ENUM_VALUES_GEN_TS: Final[Path] = (
    WEB_DIR / "src" / "api" / "types" / "enum-values.gen.ts"
)

_DRIFT_DIFF_LINE_LIMIT: Final[int] = 60

_HEADER: Final[str] = (
    "// AUTO-GENERATED: do not edit by hand.\n"
    "// Regenerate with: uv run python scripts/generate_dto_types_ts.py\n"
    "// Drift check (pre-push): "
    "uv run python scripts/check_dto_types_ts_in_sync.py\n"
    "// Source: src/synthorg/api/**/*.py "
    "(via scripts/export_openapi.py + openapi-typescript)\n"
    "// Contract: web/CLAUDE.md -> 'Generated DTO types (MANDATORY)'\n"
    "\n"
)

# Match a clean Pydantic class name (PascalCase, no underscores).
_PASCAL_CASE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Za-z0-9]*$")
# Match Litestar's monomorphised generic schema names, e.g.
# ``ApiResponse_AgentConfig_`` -> wrapper=ApiResponse, inner=AgentConfig.
_MONOMORPHISED: Final[re.Pattern[str]] = re.compile(
    r"^(?P<wrapper>[A-Z][A-Za-z0-9]*)_(?P<inner>[A-Za-z0-9_]+)_$",
)
# Strip the JSON-Schema $ref namespace prefix to recover a bare
# ``components.schemas`` key.
_COMPONENT_REF_PREFIX: Final[str] = "#/components/schemas/"


_HERMETIC_ENV_KEYS: Final[tuple[str, ...]] = (
    "SYNTHORG_DB_PATH",
    "SYNTHORG_DATABASE_URL",
    "SYNTHORG_PAGINATION_CURSOR_SECRET",
)


@contextlib.contextmanager
def _hermetic_env() -> Iterator[None]:
    """Apply the deterministic OpenAPI-export env, then restore.

    Replicates ``scripts/export_openapi.py``'s env contract: in-memory
    SQLite backend with a stable pagination cursor secret. Snapshots
    the original presence/value of each variable up-front and restores
    on exit (success or error) so the mutation is scoped to the
    ``with`` block, not leaked to anything that imports this module.

    Honours an operator-pinned ``SYNTHORG_DB_PATH``: if set, the
    function leaves both ``SYNTHORG_DB_PATH`` and
    ``SYNTHORG_DATABASE_URL`` untouched (matching the prior
    "operator wins" behaviour).
    """
    snapshot = {key: os.environ.get(key) for key in _HERMETIC_ENV_KEYS}
    try:
        if "SYNTHORG_DB_PATH" not in os.environ:
            os.environ["SYNTHORG_DB_PATH"] = ":memory:"
            os.environ.pop("SYNTHORG_DATABASE_URL", None)
        os.environ.setdefault(
            "SYNTHORG_PAGINATION_CURSOR_SECRET",
            "openapi-export-stable-cursor-secret-not-a-real-secret",
        )
        yield
    finally:
        for key, original in snapshot.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


def _collect_strenum_classes() -> dict[str, type]:
    """Map ``ClassName`` to the actual StrEnum subclass across synthorg.

    The walk inspects every loaded ``synthorg.*`` module rather than a
    pinned list so a new enum landing under any subpackage is picked
    up automatically. Conflicts (two distinct classes sharing a name)
    drop the entry to avoid a silent wrong mapping.
    """
    name_to_class: dict[str, type] = {}
    conflicts: set[str] = set()
    for module in list(sys.modules.values()):
        module_name = getattr(module, "__name__", "")
        if not isinstance(module_name, str) or not module_name.startswith(
            "synthorg.",
        ):
            continue
        for attr_name in dir(module):
            attr = getattr(module, attr_name, None)
            if (
                not isinstance(attr, type)
                or attr is StrEnum
                or not issubclass(attr, StrEnum)
            ):
                continue
            existing = name_to_class.get(attr_name)
            if existing is None:
                name_to_class[attr_name] = attr
            elif existing is not attr:
                conflicts.add(attr_name)
    for name in conflicts:
        name_to_class.pop(name, None)
    return name_to_class


def _normalise_enum_descriptions(schema: dict[str, Any]) -> dict[str, Any]:
    """Override string-enum schema descriptions with class docstrings.

    Litestar's schema generation occasionally substitutes an enum
    class's docstring with a parameter-name-derived title (e.g.
    ``Seniority level`` instead of the full class docstring); the
    choice is process-state-dependent and produces byte drift across
    otherwise-identical generator runs. Replacing the description
    with the class's own docstring (or removing it when the class has
    none) makes the export byte-stable.
    """
    name_to_class = _collect_strenum_classes()
    schemas = schema.get("components", {}).get("schemas", {})
    for schema_name, defn in schemas.items():
        if not isinstance(defn, dict):
            continue
        if defn.get("type") != "string" or not defn.get("enum"):
            continue
        cls = name_to_class.get(schema_name)
        if cls is None:
            continue
        docstring = inspect.getdoc(cls)
        if docstring:
            defn["description"] = docstring
        else:
            defn.pop("description", None)
    return schema


def _harvest_refs_under(subtree: Any, targets: set[str]) -> None:
    """Recursively harvest every ``$ref`` value into ``targets``."""
    if isinstance(subtree, dict):
        ref = subtree.get("$ref")
        if isinstance(ref, str) and ref.startswith(_COMPONENT_REF_PREFIX):
            targets.add(ref[len(_COMPONENT_REF_PREFIX) :])
        for value in subtree.values():
            _harvest_refs_under(value, targets)
    elif isinstance(subtree, list):
        for item in subtree:
            _harvest_refs_under(item, targets)


def _walk_to_key(subtree: Any, key: str, targets: set[str]) -> None:
    """Walk ``subtree`` collecting refs under every node named ``key``."""
    if isinstance(subtree, dict):
        for child_key, child_value in subtree.items():
            if child_key == key:
                _harvest_refs_under(child_value, targets)
            else:
                _walk_to_key(child_value, key, targets)
    elif isinstance(subtree, list):
        for item in subtree:
            _walk_to_key(item, key, targets)


def _collect_ref_targets(node: Any, key: str, schemas: dict[str, Any]) -> set[str]:
    """Collect the transitive ``$ref`` closure reachable under ``key``.

    Returned names are bare ``components.schemas`` keys (the
    ``#/components/schemas/`` prefix is stripped). The seed set is
    every schema referenced literally under a subtree named ``key``;
    the closure then follows every ``$ref`` inside those component
    definitions so a schema reached only indirectly (e.g. a
    ``requestBody`` wrapper that embeds ``$ref`` to ``FooOptions``)
    is still attributed to ``key``. Without the closure a nested
    request-only schema would be misclassified as response-side and
    have all its properties wrongly promoted to ``required[]``.

    The walk carries a ``seen`` set so self-referential or cyclic
    component graphs (``A -> B -> A``) terminate.
    """
    seed: set[str] = set()
    _walk_to_key(node, key, seed)
    seen: set[str] = set()
    stack = list(seed)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        defn = schemas.get(name)
        if isinstance(defn, dict):
            nested: set[str] = set()
            _harvest_refs_under(defn, nested)
            stack.extend(nested - seen)
    return seen


def _promote_response_defaults_to_required(
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Promote every property to ``required[]`` on response-side schemas.

    ``openapi-typescript`` reads JSON Schema's ``required[]`` literally
    and emits anything outside it as optional, but Pydantic's default
    response serialiser (``model_dump_json`` without ``exclude_unset``)
    always emits every model field. To match the wire, every response
    property must therefore be declared required, even those carrying a
    JSON-schema default.

    A schema is **request-only** iff it is reached via at least one
    ``paths.*.<verb>.requestBody.content.*.schema.$ref`` AND is not
    reached via any response ``$ref``. Every other schema is
    response-side; on those, every property in ``properties`` is
    appended to ``required[]``. The rule is "every property", not
    "every property with a JSON-schema ``default``", because Pydantic
    drops ``default`` from the JSON schema for ``$ref``-typed fields
    (``priority: Priority = Priority.MEDIUM``), ``Optional[X] = None``
    fields, and ``default_factory`` fields, even though the wire
    still emits them on every response.

    The function mutates ``schema`` in place and returns it for
    chaining (matching :func:`_normalise_enum_descriptions`). It is
    idempotent: ``required`` is treated as a set, sorted on write so
    re-runs are byte-stable for the drift gate.
    """
    paths = schema.get("paths", {})
    schemas = schema.get("components", {}).get("schemas", {})
    request_refs = _collect_ref_targets(paths, "requestBody", schemas)
    response_refs = _collect_ref_targets(paths, "responses", schemas)
    request_only_names = request_refs - response_refs

    for name, defn in schemas.items():
        if name in request_only_names:
            continue
        if not isinstance(defn, dict):
            continue
        properties = defn.get("properties")
        if not isinstance(properties, dict) or not properties:
            continue
        existing_required = defn.get("required")
        required = (
            set(existing_required) if isinstance(existing_required, list) else set()
        )
        all_properties = {
            prop_name
            for prop_name, prop_defn in properties.items()
            if isinstance(prop_defn, dict)
        }
        added = bool(all_properties - required)
        required |= all_properties
        if added or isinstance(existing_required, list):
            defn["required"] = sorted(required)
    return schema


def export_openapi_schema() -> dict[str, Any]:
    """Boot the app and return the enriched OpenAPI schema dict.

    Mirrors ``scripts/export_openapi.py`` step-for-step so the codegen
    and the public ``docs/openapi/openapi.json`` see the same schema.
    Additionally normalises string-enum descriptions to break a
    Litestar schema-cache non-determinism (see
    :func:`_normalise_enum_descriptions`) and promotes defaulted
    response-side properties into ``required[]`` so generated
    TypeScript matches the wire reality (see
    :func:`_promote_response_defaults_to_required`).

    The hermetic env (in-memory SQLite + stable cursor secret) is
    scoped to this call via :func:`_hermetic_env`, so an in-process
    caller's environment is never permanently mutated.
    """
    with _hermetic_env():
        # Defer imports so unit tests can patch this function without
        # paying the app-boot cost.
        from synthorg.api.app import create_app
        from synthorg.api.openapi import inject_rfc9457_responses

        app = create_app()
        schema = app.openapi_schema.to_schema()
        schema = inject_rfc9457_responses(schema)
        schema = _normalise_enum_descriptions(schema)
        return _promote_response_defaults_to_required(schema)


def run_openapi_typescript(schema_path: Path) -> str:
    """Invoke ``npx openapi-typescript`` and return stdout.

    Args:
        schema_path: Path to the OpenAPI JSON document.

    Returns:
        The rendered TypeScript module as a string (LF newlines).

    Raises:
        FileNotFoundError: ``npx`` is not on PATH.
        ChildProcessError: ``openapi-typescript`` exited non-zero.
    """
    npx = shutil.which("npx")
    if npx is None:
        msg = (
            "npx is required to run openapi-typescript; install Node.js"
            " (see web/CLAUDE.md) and run `npm --prefix web install`."
        )
        raise FileNotFoundError(msg)
    result = subprocess.run(
        [
            npx,
            "--prefix",
            str(WEB_DIR),
            "openapi-typescript",
            str(schema_path),
            "--immutable",
            "--export-type",
            "--enum=false",
            "--alphabetize",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        # Pin stdout decoding to UTF-8. ``text=True`` alone uses
        # ``locale.getpreferredencoding()``, which is ``cp1252`` on a
        # default Windows install -- ``openapi-typescript`` always emits
        # UTF-8, and decoding ``C2 A7`` (``§``) as cp1252 yields the
        # mojibake ``Â§`` that diverges from CI's Linux output and trips
        # the drift gate.
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        msg = f"openapi-typescript exited {result.returncode}\nstderr:\n{result.stderr}"
        raise ChildProcessError(msg)
    return result.stdout


def _pretty_envelope_name(raw: str) -> str | None:
    """Return a friendly alias for a Litestar monomorphised name.

    ``ApiResponse_AgentConfig_`` -> ``AgentConfigEnvelope``.
    ``PaginatedResponse_AgentConfig_`` -> ``AgentConfigPage``.
    ``ApiResponse_NoneType_`` -> ``VoidEnvelope``.

    Returns ``None`` for any other generic wrapper, leaving callers to
    reach the raw schema via ``components['schemas'][...]`` directly.
    """
    match = _MONOMORPHISED.match(raw)
    if not match:
        return None
    wrapper = match.group("wrapper")
    inner = match.group("inner")
    if inner == "NoneType":
        inner = "Void"
    if wrapper == "ApiResponse":
        return f"{inner}Envelope"
    if wrapper == "PaginatedResponse":
        return f"{inner}Page"
    return None


def _is_string_enum_schema(defn: dict[str, Any]) -> bool:
    """Return True when this schema is a Pydantic ``StrEnum`` mirror.

    Enums own a dedicated ``enum-values.gen.ts`` export with a runtime
    ``*_VALUES`` tuple plus a derived string-union type; emitting a
    second alias in ``dtos.gen.ts`` would collide with that name.
    """
    if defn.get("type") != "string":
        return False
    members = defn.get("enum")
    return (
        isinstance(members, list)
        and bool(members)
        and all(isinstance(value, str) for value in members)
    )


def render_dtos(schema: dict[str, Any]) -> str:
    """Render the ``dtos.gen.ts`` body from the OpenAPI schema dict.

    Pure: same input always produces the same output. Names are sorted
    so the file is deterministic across runs. String-enum schemas are
    intentionally skipped; ``enum-values.gen.ts`` is their canonical
    source.

    Args:
        schema: The exported OpenAPI document.

    Returns:
        The full ``dtos.gen.ts`` contents (header + import + aliases).
    """
    components = schema.get("components", {}).get("schemas", {})
    lines: list[str] = []
    seen_aliases: set[str] = set()
    for name in sorted(components):
        defn = components[name]
        if _PASCAL_CASE.match(name):
            if _is_string_enum_schema(defn):
                continue
            lines.append(
                f"export type {name} = components['schemas']['{name}']",
            )
            seen_aliases.add(name)
            continue
        pretty = _pretty_envelope_name(name)
        if pretty is None or pretty in seen_aliases:
            continue
        lines.append(
            f"export type {pretty} = components['schemas']['{name}']",
        )
        seen_aliases.add(pretty)
    body = "\n".join(lines) + ("\n" if lines else "")
    return f"{_HEADER}import type {{ components }} from './openapi.gen'\n\n{body}"


def _to_screaming_snake(name: str) -> str:
    """PascalCase -> SCREAMING_SNAKE_CASE.

    ``TaskStatus`` -> ``TASK_STATUS``; ``HTTPStatus`` -> ``HTTP_STATUS``.
    """
    intermediate = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", intermediate).upper()


_TS_SINGLE_QUOTE_ESCAPES: Final[dict[str, str]] = {
    "\\": "\\\\",
    "'": "\\'",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escape_for_ts_single_quoted(value: str) -> str:
    r"""Escape ``value`` for embedding inside a single-quoted TS literal.

    Today every Pydantic ``StrEnum`` member is a clean snake_case
    identifier so the substitution is a no-op, but a future enum that
    happens to contain a backslash, single quote, or whitespace control
    character (\n / \r / \t) would otherwise emit syntactically invalid
    TypeScript and silently break the dashboard build.
    """
    return value.translate(str.maketrans(_TS_SINGLE_QUOTE_ESCAPES))


def render_enum_values(schema: dict[str, Any]) -> str:
    """Render the ``enum-values.gen.ts`` body from the schema dict.

    Pure. Walks ``components.schemas`` for entries that are
    string-typed enums with a clean PascalCase title, and emits one
    ``*_VALUES`` tuple plus a derived type per entry. Sorted output.
    """
    components = schema.get("components", {}).get("schemas", {})
    blocks: list[str] = []
    for name in sorted(components):
        if not _PASCAL_CASE.match(name):
            continue
        defn = components[name]
        if defn.get("type") != "string":
            continue
        members = defn.get("enum")
        if not isinstance(members, list) or not members:
            continue
        if not all(isinstance(value, str) for value in members):
            continue
        snake = _to_screaming_snake(name)
        rendered_members = ",\n".join(
            f"    '{_escape_for_ts_single_quoted(value)}'" for value in members
        )
        blocks.append(
            f"export const {snake}_VALUES = [\n"
            f"{rendered_members},\n"
            f"] as const\n"
            f"export type {name} = (typeof {snake}_VALUES)[number]\n",
        )
    body = "\n".join(blocks)
    return f"{_HEADER}{body}"


def generate_all(schema: dict[str, Any] | None = None) -> tuple[str, str, str]:
    """Run the full pipeline and return the three rendered files.

    Args:
        schema: Pre-exported schema dict. When ``None``, the function
            calls :func:`export_openapi_schema` to boot the real app.
            Unit tests pass a fixture schema dict instead.

    Returns:
        ``(openapi_ts, dtos_ts, enum_values_ts)`` in that order.
    """
    if schema is None:
        schema = export_openapi_schema()
    with tempfile.TemporaryDirectory(prefix="dto-codegen-") as tmp:
        tmp_path = Path(tmp) / "openapi.json"
        tmp_path.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        raw_openapi_ts = run_openapi_typescript(tmp_path)
    openapi_ts = f"{_HEADER}{raw_openapi_ts}"
    dtos_ts = render_dtos(schema)
    enum_values_ts = render_enum_values(schema)
    return openapi_ts, dtos_ts, enum_values_ts


def _write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` with LF newlines on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _check(targets: tuple[tuple[Path, str], ...]) -> int:
    """Compare each (path, expected) pair; return non-zero on drift.

    Reads the on-disk file as bytes (no universal-newline translation)
    and compares against the freshly rendered ``expected`` UTF-8 bytes.
    ``_write`` always emits LF, so a CRLF-encoded file in the working
    tree is a real drift the gate must catch -- ``read_text`` would
    silently normalise CRLF to LF and let the divergence ship.
    """
    failures: list[str] = []
    for path, expected in targets:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not path.exists():
            failures.append(f"missing generated file: {rel}")
            continue
        actual_bytes = path.read_bytes()
        expected_bytes = expected.encode("utf-8")
        if actual_bytes != expected_bytes:
            failures.append(f"out of sync: {rel}")
            _print_drift_diff(rel, actual_bytes, expected_bytes)
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        print(
            "\nRun: uv run python scripts/generate_dto_types_ts.py",
            file=sys.stderr,
        )
        return 1
    return 0


def _print_drift_diff(
    rel: str,
    actual_bytes: bytes,
    expected_bytes: bytes,
) -> None:
    """Print a short unified diff so CI logs reveal what drifted.

    Falls back to a byte-length summary if the contents are not valid
    UTF-8 (the generator only emits UTF-8, but the on-disk file may
    have been touched by a non-UTF-8 editor).
    """
    try:
        actual_text = actual_bytes.decode("utf-8")
        expected_text = expected_bytes.decode("utf-8")
    except UnicodeDecodeError:
        print(
            f"  bytes differ: actual={len(actual_bytes)} expected={len(expected_bytes)}",
            file=sys.stderr,
        )
        return
    diff = difflib.unified_diff(
        actual_text.splitlines(keepends=True),
        expected_text.splitlines(keepends=True),
        fromfile=f"{rel} (on disk)",
        tofile=f"{rel} (regenerated)",
        n=2,
    )
    diff_lines = list(diff)
    if not diff_lines:
        print(
            "  (no line-level diff; only byte-level / line-ending drift)",
            file=sys.stderr,
        )
        return
    head = "".join(diff_lines[:_DRIFT_DIFF_LINE_LIMIT])
    sys.stderr.write(head)
    if len(diff_lines) > _DRIFT_DIFF_LINE_LIMIT:
        truncated = len(diff_lines) - _DRIFT_DIFF_LINE_LIMIT
        print(
            f"  ... ({truncated} more diff lines truncated)",
            file=sys.stderr,
        )


def _re_exec_with_fixed_hash_seed() -> int | None:
    """Re-exec with ``PYTHONHASHSEED=0`` when the caller did not pin it.

    Litestar's OpenAPI schema generation surfaces hash-seed-dependent
    output (a StrEnum's ``description`` flips between the class
    docstring and the parameter-name fallback depending on which
    controller registered the schema first). Pinning the hash seed
    serialises that ordering so the gate sees byte-identical output
    every run. Returns the child's exit code when a re-exec happened,
    or ``None`` to signal that the current process is the pinned run.
    """
    if os.environ.get("PYTHONHASHSEED") == "0":
        return None
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    result = subprocess.run(
        [sys.executable, *sys.argv],
        env=env,
        check=False,
        cwd=str(Path.cwd()),
    )
    return result.returncode


def main() -> int:
    """CLI entry point: write, check, or stream to stdout."""
    pinned_exit = _re_exec_with_fixed_hash_seed()
    if pinned_exit is not None:
        return pinned_exit
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="Compare against the committed files; exit 1 on drift.",
    )
    group.add_argument(
        "--stdout",
        action="store_true",
        help="Print all three rendered files to stdout (with markers).",
    )
    args = parser.parse_args()

    openapi_ts, dtos_ts, enum_values_ts = generate_all()
    targets: tuple[tuple[Path, str], ...] = (
        (OPENAPI_GEN_TS, openapi_ts),
        (DTOS_GEN_TS, dtos_ts),
        (ENUM_VALUES_GEN_TS, enum_values_ts),
    )

    if args.stdout:
        for path, content in targets:
            rel = path.relative_to(REPO_ROOT).as_posix()
            sys.stdout.write(f"===== {rel} =====\n")
            sys.stdout.write(content)
        return 0

    if args.check:
        return _check(targets)

    for path, content in targets:
        _write(path, content)
    print(
        "wrote "
        f"{OPENAPI_GEN_TS.relative_to(REPO_ROOT).as_posix()}, "
        f"{DTOS_GEN_TS.relative_to(REPO_ROOT).as_posix()}, "
        f"{ENUM_VALUES_GEN_TS.relative_to(REPO_ROOT).as_posix()}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
