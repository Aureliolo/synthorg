"""Shared reading and parsing for the apko-lock gate.

Split from ``check_apko_lock_applied.py`` so the gate itself stays inside the
500-line ``code`` module budget. Everything here answers "what does the tree
say"; the gate decides what that means.
"""

import ast
import base64
import hashlib
import json
import re
import shlex
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import yaml

WORKFLOW_SUBDIRS: Final[tuple[tuple[str, ...], ...]] = (
    (".github", "workflows"),
    (".github", "actions"),
)
DOCKER_SUBDIR: Final[str] = "docker"
YAML_SUFFIXES: Final[frozenset[str]] = frozenset({".yml", ".yaml"})
LOCK_SUFFIX: Final[str] = ".lock.json"
CHECKSUM_PREFIX: Final[str] = "sha256-"
# Beside `Findings.exit_code`, which is the one place the verdict is decided.
EXIT_OK: Final[int] = 0
EXIT_VIOLATION: Final[int] = 1
EXIT_CONFIG_ERROR: Final[int] = 2
LOCKFILE_FLAG: Final[str] = "--lockfile"
# Any run of whitespace, because a literal single space would miss an
# invocation written with a tab or two spaces, and a missed invocation is
# checked by nothing rather than reported.
BUILD_MARKER: Final[re.Pattern[str]] = re.compile(r"\bapko\s+build\b")
# The composite action every locked base image is built through. Its callers
# name their config in `with: apko-yaml:`, which is the only place the tree
# says which images are supposed to carry a lock.
LOCKED_BUILD_ACTION: Final[str] = ".github/actions/build-apko-base"
APKO_YAML_INPUT: Final[str] = "apko-yaml"
# apko package specs may carry a version constraint or a repository selector;
# a spec naming one is already explicit and is not an alias.
SPEC_DELIMITERS: Final[str] = "=<>~@"
SEGMENT_SEPARATOR: Final[str] = "-"
BINARY_RECORD: Final[str] = "BinaryRecord"
PACKAGE_KEYWORD: Final[str] = "package"
# The version pin is written once per workflow that installs apko. A lock
# minted by one version and consumed by another is a disagreement about the
# very file this gate exists to trust, so the literals have to match.
APKO_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"""^\s*APKO_VERSION:\s*["']?(v[0-9][^"'\s]*)["']?\s*$""", re.MULTILINE
)


@dataclass(slots=True)
class Findings:
    """What a scan produced.

    Violations and errors are kept apart because they mean different things: a
    violation is the tree being wrong and is the developer's to fix, while an
    error is the gate being unable to look, which must never read as a pass.

    Attributes:
        violations: Problems in the tree, each naming a file and a remedy.
        errors: Reasons the scan itself cannot be trusted.
        invocations: How many ``apko build`` invocations were seen.
        checked_preflight: Whether the boot-preflight check ran to completion.
    """

    violations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    invocations: int = 0
    checked_preflight: bool = False

    def exit_code(self) -> int:
        """Return the verdict, with errors outranking violations.

        Both lists can be non-empty in one run, so which wins is a real
        decision and it lives here rather than in the order of two branches
        somewhere else: a scan that could not look must never be reported as a
        tree that is merely wrong.
        """
        if self.errors:
            return EXIT_CONFIG_ERROR
        if self.violations:
            return EXIT_VIOLATION
        return EXIT_OK


def digest(data: bytes) -> str:
    """Return the apko-style ``sha256-<base64>`` digest of ``data``."""
    return CHECKSUM_PREFIX + base64.b64encode(hashlib.sha256(data).digest()).decode()


def rel(root: Path, path: Path) -> str:
    """Return ``path`` relative to ``root``, for reporting."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def workflow_roots(root: Path) -> tuple[Path, ...]:
    """Return the directories holding workflow and composite-action files."""
    return tuple(root.joinpath(*parts) for parts in WORKFLOW_SUBDIRS)


def iter_workflow_files(root: Path) -> Iterator[Path]:
    """Yield every workflow and composite-action definition, sorted."""
    for directory in workflow_roots(root):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in YAML_SUFFIXES:
                yield path


def iter_lock_files(root: Path) -> Iterator[Path]:
    """Yield every apko lockfile under ``docker/``, sorted."""
    docker_root = root / DOCKER_SUBDIR
    if not docker_root.is_dir():
        return
    for path in sorted(docker_root.glob(f"*/*{LOCK_SUFFIX}")):
        if path.is_file():
            yield path


def contained(root: Path, candidate: Path) -> Path | None:
    """Return ``candidate`` resolved, or ``None`` if it escapes ``root``.

    A lockfile names its own manifest, and that name is ordinary JSON anyone
    can edit. Without this an absolute path or a ``..`` segment would send the
    gate off to read and hash a file outside the repository entirely.
    """
    try:
        resolved = candidate.resolve()
        root_resolved = root.resolve()
    except OSError, RuntimeError:
        return None
    if resolved == root_resolved or resolved.is_relative_to(root_resolved):
        return resolved
    return None


def sibling_lock(config: Path) -> Path:
    """Return the lockfile path apko writes beside ``config``."""
    return config.with_suffix("").with_suffix(LOCK_SUFFIX)


def split_build_commands(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, command)`` for each ``apko build`` invocation.

    Backslash continuations are folded so a flag on a later line still counts,
    and the fold is then split again on shell separators: two builds chained
    into one step must be judged separately, or the first one's ``--lockfile``
    would vouch for the second. Comment lines are dropped first, since YAML and
    shell spell a comment the same way here and the surrounding prose names the
    very command it describes.

    Args:
        text: Full contents of a workflow or action definition.

    Returns:
        One entry per invocation, in file order.
    """
    lines = text.splitlines()
    found: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("#") or not BUILD_MARKER.search(stripped):
            index += 1
            continue
        start_line = index + 1
        parts: list[str] = []
        while index < len(lines):
            piece = lines[index].strip()
            continued = piece.endswith("\\")
            parts.append(piece.removesuffix("\\").strip())
            index += 1
            if not continued:
                break
        found.extend(
            (start_line, segment)
            for segment in _separate(" ".join(parts))
            if BUILD_MARKER.search(segment)
        )
    return found


def _separate(command: str) -> list[str]:
    """Split a folded command on shell separators into sub-commands."""
    segments = [command]
    for separator in ("&&", "||", ";", "|"):
        nested: list[str] = []
        for segment in segments:
            nested.extend(segment.split(separator))
        segments = nested
    return [segment.strip() for segment in segments if segment.strip()]


def tokenise(command: str) -> list[str] | None:
    """Split ``command`` into shell tokens, or ``None`` if it will not parse.

    A command the shell lexer rejects is a file that will not parse, which the
    gate reserves exit 2 for. Falling back to a whitespace split would leave
    quote characters glued to the config path, so the sibling lock would not be
    found and a genuinely unlocked build would read as compliant.
    """
    try:
        return shlex.split(command)
    except ValueError:
        return None


def config_arguments(tokens: Sequence[str]) -> list[str]:
    """Return every token a build invocation could be naming its config with.

    A token carrying a shell expansion counts, because the whole point is to
    tell a run-time-resolved config apart from an absent one: the omission this
    gate exists for lived behind exactly such a token.

    Every candidate is returned rather than the first, because which one is
    the config cannot be decided without knowing which flags take a value, and
    that knowledge is one apko release out of date the moment it is written
    down. ``--sbom-path "${SBOM_DIR}"`` is the shape that makes this concrete:
    its value carries a ``$``, so a first-match rule reads it as the config and
    then judges the wrong file. The caller weighs the candidates together and
    fails closed, so a decoy can add a verdict but never hide one.

    ``--lockfile``'s value is still skipped outright, since a lock path is
    never the config it locks.
    """
    candidates: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token == LOCKFILE_FLAG:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if Path(token).suffix in YAML_SUFFIXES or "$" in token:
            candidates.append(token)
    return candidates


def lockfile_argument(tokens: Sequence[str]) -> str | None:
    """Return the value passed to ``--lockfile``, or ``None`` when absent."""
    for index, token in enumerate(tokens):
        if token.startswith(f"{LOCKFILE_FLAG}="):
            return token.split("=", 1)[1]
        if token == LOCKFILE_FLAG and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def declared_build_configs(root: Path, findings: Findings) -> set[str]:
    """Return the configs the workflows declare they build through the action.

    This is what makes a DELETED lock visible. Reading only what is on disk
    cannot tell a missing lock apart from an image that never had one, so the
    expected set is derived from the callers' own ``apko-yaml`` inputs instead.
    """
    declared: set[str] = set()
    for path in iter_workflow_files(root):
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            findings.errors.append(f"{rel(root, path)}: unreadable ({exc})")
            continue
        declared.update(_declared_in(parsed))
    return declared


def _declared_in(node: object) -> Iterator[str]:
    """Yield every ``apko-yaml`` input given to the locked build action."""
    if isinstance(node, dict):
        uses = node.get("uses")
        given = node.get("with")
        if (
            isinstance(uses, str)
            and LOCKED_BUILD_ACTION in uses
            and isinstance(given, dict)
        ):
            config = given.get(APKO_YAML_INPUT)
            if isinstance(config, str) and "$" not in config:
                yield config
        for value in node.values():
            yield from _declared_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from _declared_in(value)


def load_lock(root: Path, path: Path, findings: Findings) -> dict[str, object] | None:
    """Parse a lockfile, recording a scan error if it will not read."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        findings.errors.append(f"{rel(root, path)}: unreadable lockfile ({exc})")
        return None
    if not isinstance(parsed, dict):
        findings.errors.append(f"{rel(root, path)}: lockfile is not a JSON object")
        return None
    return parsed


def lock_config(
    root: Path, lock_path: Path, lock: dict[str, object], findings: Findings
) -> tuple[str, str] | None:
    """Return the manifest name a lock declares, plus its recorded checksum."""
    config = lock.get("config")
    if not isinstance(config, dict):
        findings.errors.append(f"{rel(root, lock_path)}: lockfile has no `config`")
        return None
    name = config.get("name")
    checksum = config.get("checksum")
    if not isinstance(name, str) or not isinstance(checksum, str):
        findings.errors.append(
            f"{rel(root, lock_path)}: `config.name` / `config.checksum` missing"
        )
        return None
    return name, checksum


def resolved_names(
    root: Path, lock_path: Path, lock: dict[str, object], findings: Findings
) -> frozenset[str] | None:
    """Return every package name the lock resolved."""
    contents = lock.get("contents")
    if not isinstance(contents, dict):
        findings.errors.append(f"{rel(root, lock_path)}: lockfile has no `contents`")
        return None
    packages = contents.get("packages")
    if not isinstance(packages, list):
        findings.errors.append(
            f"{rel(root, lock_path)}: `contents.packages` is not a list"
        )
        return None
    names = {
        entry["name"]
        for entry in packages
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    if not names:
        findings.errors.append(f"{rel(root, lock_path)}: lockfile resolved no packages")
        return None
    return frozenset(names)


def declared_contents(
    root: Path, manifest: Path, findings: Findings
) -> dict[str, object] | None:
    """Return a manifest's ``contents`` mapping."""
    try:
        parsed = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        findings.errors.append(f"{rel(root, manifest)}: unreadable manifest ({exc})")
        return None
    if not isinstance(parsed, dict):
        findings.errors.append(f"{rel(root, manifest)}: not a YAML mapping")
        return None
    contents = parsed.get("contents")
    if not isinstance(contents, dict):
        findings.errors.append(f"{rel(root, manifest)}: manifest has no `contents`")
        return None
    return contents


def declared_specs(root: Path, manifest: Path, findings: Findings) -> list[str] | None:
    """Return the package specs a manifest declares."""
    contents = declared_contents(root, manifest, findings)
    if contents is None:
        return None
    packages = contents.get("packages")
    if not isinstance(packages, list):
        findings.errors.append(
            f"{rel(root, manifest)}: `contents.packages` is not a list"
        )
        return None
    return [entry for entry in packages if isinstance(entry, str)]


def bare_name(spec: str) -> str:
    """Return the package name a spec carries, without any constraint."""
    name = spec
    for delimiter in SPEC_DELIMITERS:
        name = name.split(delimiter, 1)[0]
    return name


def alias_candidates(name: str, resolved: frozenset[str]) -> list[str]:
    """Return the resolved packages an alias plausibly reached.

    Two shapes, because Wolfi uses both: it appends the series (``glibc`` ->
    ``glibc-2.43``) and it inserts it between segments (``postgresql-client``
    -> ``postgresql-18-client``), and a plain prefix match finds only the first.
    """
    segments = name.split(SEGMENT_SEPARATOR)
    head, tail = segments[0], segments[-1]
    return sorted(
        candidate
        for candidate in resolved
        if candidate != name
        and candidate.startswith(head)
        and (candidate.endswith(tail) or candidate.startswith(f"{name}-"))
    )


def record_packages(source: str) -> tuple[list[tuple[int, str]], list[int]]:
    """Return the packages a preflight manifest names, and the lines it hides.

    Parsed rather than pattern-matched: a regex over source text cannot see a
    value written across a continuation line or held in a constant, and would
    match a ``package=`` example sitting in a docstring. The second list is the
    lines where the keyword is present but not a string literal, which the gate
    reports rather than skips.

    Args:
        source: Python source of the module declaring the records.

    Returns:
        ``(literals, opaque)`` where literals are ``(line, package)`` pairs.

    Raises:
        SyntaxError: The source does not parse.
    """
    tree = ast.parse(source)
    literals: list[tuple[int, str]] = []
    opaque: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_record(node.func):
            continue
        for keyword in node.keywords:
            if keyword.arg != PACKAGE_KEYWORD:
                continue
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                literals.append((value.lineno, value.value))
            else:
                opaque.append(value.lineno)
    return literals, opaque


def _is_record(func: ast.expr) -> bool:
    """Whether a call target names the preflight record class."""
    if isinstance(func, ast.Name):
        return func.id == BINARY_RECORD
    return isinstance(func, ast.Attribute) and func.attr == BINARY_RECORD
