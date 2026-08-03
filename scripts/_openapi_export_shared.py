"""Shared OpenAPI export: build the schema once, verify it before reuse.

Two pre-push hooks needed the same schema and each booted the app to get
it, so a controller-touching push paid ``create_app()`` twice for one
answer. This module is the single producer, plus the freshness check
that lets the second consumer trust the first one's file.

Trust is not granted by ordering. A consumer that simply read
``docs/openapi/openapi.json`` would pass against a stale artefact left by
an earlier tree state, which is a gate silently under-enforcing: exactly
the failure it exists to prevent. So the producer records a fingerprint
of the sources the schema is derived from, and a consumer recomputes it
and falls back to booting the app whenever it does not match. The
fallback direction is the safe one, so a missing, stale, truncated or
hand-edited artefact costs time, never correctness.

The fingerprint hashes the sources themselves. Stat metadata is cheaper
but not sufficient: a length-preserving edit within one timestamp tick
is invisible to it, and "stale unless you edited slowly" is not a
guarantee worth stating. Reading the tree is a fraction of the boot it
avoids.

State lives in a sibling file rather than inside the schema, so the
published ``openapi.json`` stays a pristine OpenAPI document with no
build metadata in it.
"""

import contextlib
import hashlib
import inspect
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import Final, TypedDict, cast

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
OUTPUT_DIR: Final[Path] = REPO_ROOT / "docs" / "openapi"
SCHEMA_FILE: Final[Path] = OUTPUT_DIR / "openapi.json"
EXPORT_STATE_FILE: Final[Path] = OUTPUT_DIR / ".export-state.json"

# Every module under here can contribute a DTO, an enum or a route to the
# schema, so the whole package is the derivation input.
_SOURCE_ROOT: Final[Path] = REPO_ROOT / "src" / "synthorg"
_SOURCE_GLOB: Final[str] = "**/*.py"
_STATE_VERSION: Final[int] = 1

HERMETIC_ENV_KEYS: Final[tuple[str, ...]] = (
    "SYNTHORG_DB_PATH",
    "SYNTHORG_DATABASE_URL",
    "SYNTHORG_PAGINATION_CURSOR_SECRET",
)
# create_app refuses to boot on an ephemeral pagination cursor secret, so a
# build-time export has to supply one. Not a credential: it signs nothing that
# outlives the export process.
STABLE_CURSOR_SECRET: Final[str] = (
    "openapi-export-stable-cursor-secret-not-a-real-secret"  # noqa: S105
)


@contextlib.contextmanager
def hermetic_env() -> Iterator[None]:
    """Apply the deterministic OpenAPI-export env, then restore it.

    Without a persistence backend the app skips whole controller groups,
    producing a partial schema that differs between environments, so the
    export pins an in-memory backend and a stable cursor secret. An
    operator who pinned ``SYNTHORG_DB_PATH`` themselves wins.

    Yields:
        None, with the export environment in force.
    """
    snapshot = {key: os.environ.get(key) for key in HERMETIC_ENV_KEYS}
    try:
        if "SYNTHORG_DB_PATH" not in os.environ:
            os.environ["SYNTHORG_DB_PATH"] = ":memory:"
            os.environ.pop("SYNTHORG_DATABASE_URL", None)
        os.environ.setdefault("SYNTHORG_PAGINATION_CURSOR_SECRET", STABLE_CURSOR_SECRET)
        yield
    finally:
        for key, original in snapshot.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


def re_exec_with_fixed_hash_seed() -> int | None:
    """Re-exec with ``PYTHONHASHSEED=0`` when the caller did not pin it.

    Litestar's schema generation surfaces hash-seed-dependent output (a
    StrEnum's description flips between the class docstring and a
    parameter-name fallback depending on which controller registered the
    schema first). Pinning the seed serialises that ordering so the
    export is byte-identical run to run, which is what makes one run's
    artefact usable as another run's input.

    Returns:
        The child's exit code when a re-exec happened, or None when this
        process is already the pinned run.
    """
    if os.environ.get("PYTHONHASHSEED") == "0":
        return None
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    result = subprocess.run(
        [sys.executable, *sys.argv], env=env, check=False, cwd=str(Path.cwd())
    )
    return result.returncode


def as_dict(value: object) -> dict[str, object]:
    """Narrow an arbitrary JSON value to a string-keyed dict (``{}`` if not)."""
    if isinstance(value, dict):
        return value
    return {}


def collect_strenum_classes() -> dict[str, type[StrEnum]]:
    """Map ``ClassName`` to the actual StrEnum subclass across synthorg.

    The walk inspects every loaded ``synthorg.*`` module rather than a
    pinned list so a new enum landing under any subpackage is picked up
    automatically. Conflicts (two distinct classes sharing a name) drop
    the entry to avoid a silent wrong mapping.

    Returns:
        Class name mapped to the class, for every unambiguous StrEnum.
    """
    name_to_class: dict[str, type[StrEnum]] = {}
    conflicts: set[str] = set()
    for module in list(sys.modules.values()):
        module_name = getattr(module, "__name__", "")
        if not isinstance(module_name, str) or not module_name.startswith("synthorg."):
            continue
        for attr_name in dir(module):
            # Nine synthorg packages re-export lazily through a module-level
            # ``__getattr__``, so this getattr can run a real import. Its
            # default only swallows AttributeError, and an absent optional
            # extra would otherwise abort the whole export from inside a
            # cosmetic docstring walk.
            try:
                attr = getattr(module, attr_name, None)
            except ImportError:
                continue
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


def normalise_enum_descriptions(schema: dict[str, object]) -> dict[str, object]:
    """Override string-enum schema descriptions with class docstrings.

    Litestar occasionally substitutes an enum class's docstring with a
    parameter-name-derived title; the choice is process-state-dependent
    and produces byte drift across otherwise-identical runs. Replacing
    the description with the class's own docstring (or removing it when
    the class has none) makes the export byte-stable.

    Args:
        schema: The schema to normalise, mutated in place.

    Returns:
        The same schema, for chaining.
    """
    name_to_class = collect_strenum_classes()
    schemas = as_dict(as_dict(schema.get("components")).get("schemas"))
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


def build_openapi_schema() -> dict[str, object]:
    """Boot the app and return the normalised OpenAPI schema.

    Returns:
        The schema every consumer of the export shares.
    """
    with hermetic_env():
        # Deferred so importing this module stays cheap for a caller that
        # only wants the freshness helpers.
        from synthorg.api.app import create_app
        from synthorg.api.openapi import inject_rfc9457_responses

        app = create_app()
        enriched = inject_rfc9457_responses(app.openapi_schema.to_schema())
        return normalise_enum_descriptions(cast("dict[str, object]", enriched))


def source_fingerprint() -> str:
    """Fingerprint every source file the schema is derived from.

    Content rather than ``(size, mtime)``. Two files can differ in
    neither: an edit that preserves length, landing inside whatever
    timestamp granularity the filesystem and clock happen to provide,
    is invisible to stat metadata, and "stale unless you edited slowly"
    is not a guarantee worth stating for a gate whose one job is
    refusing a stale artefact. Reading the tree costs well under a
    second against the ``create_app()`` boot it exists to skip, and it
    also makes a fresh checkout (every mtime rewritten, every byte the
    same) correctly read as fresh.

    Returns:
        A hex digest over each source's path and contents.
    """
    digest = hashlib.blake2b(digest_size=16)
    for path in sorted(_SOURCE_ROOT.glob(_SOURCE_GLOB)):
        rel = path.relative_to(REPO_ROOT).as_posix()
        digest.update(f"{rel}:{path.stat().st_size}\n".encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class _ExportState(TypedDict):
    """What the state file records about one export.

    Named so the writer and the reader below share one declaration.
    Without it they agree by convention across two functions, and a
    renamed key produces a permanent cache miss rather than an error:
    the export silently stops being reused and only shows up as the
    push getting slower again.
    """

    version: int
    sources: str
    schema_sha256: str


def write_export_state(schema_json: str) -> None:
    """Record what the just-written schema was derived from.

    Args:
        schema_json: The exact text written to :data:`SCHEMA_FILE`.
    """
    state: _ExportState = {
        "version": _STATE_VERSION,
        "sources": source_fingerprint(),
        "schema_sha256": hashlib.sha256(schema_json.encode("utf-8")).hexdigest(),
    }
    EXPORT_STATE_FILE.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_verified_schema() -> dict[str, object] | None:
    """Return the exported schema only when it is provably current.

    Returns:
        The parsed schema, or None when no trustworthy export exists and
        the caller must build one itself.
    """
    if not (SCHEMA_FILE.is_file() and EXPORT_STATE_FILE.is_file()):
        return None
    try:
        state = json.loads(EXPORT_STATE_FILE.read_text(encoding="utf-8"))
        schema_text = SCHEMA_FILE.read_text(encoding="utf-8")
    except OSError, ValueError:
        return None
    if not isinstance(state, dict) or state.get("version") != _STATE_VERSION:
        return None
    if state.get("sources") != source_fingerprint():
        return None
    actual = hashlib.sha256(schema_text.encode("utf-8")).hexdigest()
    if state.get("schema_sha256") != actual:
        return None
    try:
        schema = json.loads(schema_text)
    except ValueError:
        return None
    return schema if isinstance(schema, dict) else None
