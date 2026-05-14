#!/usr/bin/env python3
"""Generate the three ``web/src/api/types/*.gen.ts`` DTO type files.

Pipeline:

1. Boot the Litestar app in-process (matching the env contract in
   ``scripts/export_openapi.py``: in-memory SQLite + stable pagination
   cursor secret) and export the enriched OpenAPI schema via
   ``synthorg.api.openapi.inject_rfc9457_responses``.
2. Hand the schema to ``npx openapi-typescript`` (pinned via
   ``web/package-lock.json``) to render the verbatim
   ``openapi.gen.ts`` (full ``paths`` + ``components`` shape).
3. Render two thin Python-side outputs from the same schema dict:
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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
WEB_DIR: Final[Path] = REPO_ROOT / "web"

OPENAPI_GEN_TS: Final[Path] = WEB_DIR / "src" / "api" / "types" / "openapi.gen.ts"
DTOS_GEN_TS: Final[Path] = WEB_DIR / "src" / "api" / "types" / "dtos.gen.ts"
ENUM_VALUES_GEN_TS: Final[Path] = (
    WEB_DIR / "src" / "api" / "types" / "enum-values.gen.ts"
)

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


def _hermetic_env_setdefaults() -> None:
    """Replicate ``scripts/export_openapi.py`` env contract.

    The OpenAPI export is deterministic only when the app boots
    against an in-memory SQLite backend with a stable pagination
    cursor secret. If the operator has not pinned a backend we force
    the in-memory contract and clear ``SYNTHORG_DATABASE_URL`` so a
    pre-set Postgres URL cannot silently steer the export onto a
    different wiring path.
    """
    if "SYNTHORG_DB_PATH" not in os.environ:
        os.environ["SYNTHORG_DB_PATH"] = ":memory:"
        os.environ.pop("SYNTHORG_DATABASE_URL", None)
    os.environ.setdefault(
        "SYNTHORG_PAGINATION_CURSOR_SECRET",
        "openapi-export-stable-cursor-secret-not-a-real-secret",
    )


def _collect_strenum_classes() -> dict[str, type]:
    """Map ``ClassName`` to the actual StrEnum subclass across synthorg.

    The walk inspects every loaded ``synthorg.*`` module rather than a
    pinned list so a new enum landing under any subpackage is picked
    up automatically. Conflicts (two distinct classes sharing a name)
    drop the entry to avoid a silent wrong mapping.
    """
    import sys
    from enum import StrEnum

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
    import inspect

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
            defn["description"] = f"{docstring}\n"
        else:
            defn.pop("description", None)
    return schema


def export_openapi_schema() -> dict[str, Any]:
    """Boot the app and return the enriched OpenAPI schema dict.

    Mirrors ``scripts/export_openapi.py`` step-for-step so the codegen
    and the public ``docs/openapi/openapi.json`` see the same schema.
    Additionally normalises string-enum descriptions to break a
    Litestar schema-cache non-determinism (see
    :func:`_normalise_enum_descriptions`).
    """
    _hermetic_env_setdefaults()
    # Defer imports so unit tests can patch this function without
    # paying the app-boot cost.
    from synthorg.api.app import create_app
    from synthorg.api.openapi import inject_rfc9457_responses

    app = create_app()
    schema = app.openapi_schema.to_schema()
    schema = inject_rfc9457_responses(schema)
    return _normalise_enum_descriptions(schema)


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
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
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
        rendered_members = ",\n".join(f"    '{value}'" for value in members)
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
    """Compare each (path, expected) pair; return non-zero on drift."""
    failures: list[str] = []
    for path, expected in targets:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not path.exists():
            failures.append(f"missing generated file: {rel}")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            failures.append(f"out of sync: {rel}")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        print(
            "\nRun: uv run python scripts/generate_dto_types_ts.py",
            file=sys.stderr,
        )
        return 1
    return 0


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
