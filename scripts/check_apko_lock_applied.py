#!/usr/bin/env python3
"""Pre-push gate: the apko lockfiles pin what actually gets built.

``docker/*/apko.lock.json`` records the exact apk URL and digest of every
package in a base image, and a weekly cron reconciles them. None of that binds
anything unless three separate things hold, and each has already failed
silently in this tree.

* ``apko build`` must be handed ``--lockfile``. The flag defaults to the empty
  string, which apko documents as "no additional constraints", and apko does
  NOT discover a sibling lock on its own. Every base-image build ran without
  it, so the committed digests constrained nothing and two builds of one commit
  could install different packages.
* A lock's ``config.checksum`` is a sha256 over the manifest's raw bytes, so it
  must equal the manifest it names. A lock regenerated on a CRLF checkout
  records a digest CI can never reproduce, which is how one shipped.
* A manifest must name packages that exist under that name. Wolfi resolves an
  unversioned alias through ``provides`` to whichever series it currently
  serves, so ``glibc``, ``npm`` and ``postgresql-client`` each tracked a moving
  target while reading like a pin. The lock records only what the alias
  resolved to that week, so nothing downstream shows the name drifting.

Scope is derived, never listed. A build whose config has no sibling lock is
exempt because it has nothing to apply: ``docker/web/apko.yaml`` depends on a
melange package built during the workflow run, so it is deliberately unlocked
and needs no allowlist entry to stay that way. A config path this gate cannot
resolve statically requires the flag, because a parameterised invocation is
exactly where the omission hid.

There is deliberately no baseline and no per-line opt-out. A build that skips
its lock, a lock that disagrees with its manifest, and a manifest naming a
package that resolves to something else are each the defect rather than a
position to preserve; a genuine exception changes the shape instead.

Exit codes:
    0 -- every build applies its lock, and every lock matches its manifest.
    1 -- a violation.
    2 -- the scan cannot be trusted (no invocation found, no lock found, or a
         file that will not parse).

Usage::

    python scripts/check_apko_lock_applied.py
    python scripts/check_apko_lock_applied.py --files docker/backend/apko.yaml
"""

import argparse
import base64
import hashlib
import json
import re
import shlex
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_WORKFLOW_SUBDIRS: Final[tuple[tuple[str, ...], ...]] = (
    (".github", "workflows"),
    (".github", "actions"),
)
_DOCKER_SUBDIR: Final[str] = "docker"
_YAML_SUFFIXES: Final[frozenset[str]] = frozenset({".yml", ".yaml"})
_LOCK_SUFFIX: Final[str] = ".lock.json"
_CHECKSUM_PREFIX: Final[str] = "sha256-"
_LOCKFILE_FLAG: Final[str] = "--lockfile"
# apko package specs may carry a version constraint or a repository selector;
# a spec naming one is already explicit and is not an alias.
_SPEC_DELIMITERS: Final[str] = "=<>~@"
_SEGMENT_SEPARATOR: Final[str] = "-"
# The boot preflight refuses to start when one of these binaries is off PATH,
# and tells the operator which apko package supplies it. That instruction is
# only useful while it names a package the image actually installs, which is
# the claim `docker/backend/apko.yaml` makes about this module in prose.
_PREFLIGHT_MODULE: Final[tuple[str, ...]] = (
    "src",
    "synthorg",
    "api",
    "lifecycle_helpers",
    "binary_preflight.py",
)
_PREFLIGHT_MANIFEST: Final[tuple[str, ...]] = ("docker", "backend", "apko.yaml")
_PREFLIGHT_PACKAGE_RE: Final[re.Pattern[str]] = re.compile(
    r"""^\s*package=["']([^"']+)["']""", re.MULTILINE
)

_EXIT_OK: Final[int] = 0
_EXIT_VIOLATION: Final[int] = 1
_EXIT_CONFIG_ERROR: Final[int] = 2


@dataclass(slots=True)
class _Findings:
    """What a scan produced.

    Kept apart because they mean different things: a violation is the tree
    being wrong and is the developer's to fix, while an error is the gate
    being unable to look, which must never read as a pass.

    Attributes:
        violations: Problems in the tree, each naming a file and a remedy.
        errors: Reasons the scan itself cannot be trusted.
        invocations: How many ``apko build`` invocations were seen.
        locks: How many lockfiles were examined.
    """

    violations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    invocations: int = 0
    locks: int = 0


def _digest(data: bytes) -> str:
    """Return the apko-style ``sha256-<base64>`` digest of ``data``."""
    return _CHECKSUM_PREFIX + base64.b64encode(hashlib.sha256(data).digest()).decode()


def _rel(root: Path, path: Path) -> str:
    """Return ``path`` relative to ``root``, for reporting."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _workflow_roots(root: Path) -> tuple[Path, ...]:
    """Return the directories holding workflow and composite-action files."""
    return tuple(root.joinpath(*parts) for parts in _WORKFLOW_SUBDIRS)


def _iter_workflow_files(root: Path) -> Iterator[Path]:
    """Yield every workflow and composite-action definition, sorted."""
    for directory in _workflow_roots(root):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in _YAML_SUFFIXES:
                yield path


def _iter_lock_files(root: Path) -> Iterator[Path]:
    """Yield every apko lockfile under ``docker/``, sorted."""
    docker_root = root / _DOCKER_SUBDIR
    if not docker_root.is_dir():
        return
    for path in sorted(docker_root.glob(f"*/*{_LOCK_SUFFIX}")):
        if path.is_file():
            yield path


def _apko_build_commands(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, command)`` for each ``apko build`` invocation.

    Backslash continuations are folded into one string so a flag on a later
    line still counts. Comment lines are dropped first: both YAML and shell
    spell a comment the same way here, and the surrounding prose names the
    very command it is describing.

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
        if stripped.startswith("#") or "apko build" not in stripped:
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
        found.append((start_line, " ".join(parts)))
    return found


def _tokenise(command: str) -> list[str]:
    """Split ``command`` into shell tokens, tolerating unbalanced quoting."""
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _config_argument(tokens: Sequence[str]) -> str | None:
    """Return the apko config a build invocation names, if any.

    A token carrying a shell expansion counts, because the whole point is to
    tell a run-time-resolved config apart from an absent one: the omission
    this gate exists for lived behind exactly such a token.

    Flags and the value of ``--lockfile`` are skipped so a lock path is never
    mistaken for the config it locks.
    """
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token == _LOCKFILE_FLAG:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if Path(token).suffix in _YAML_SUFFIXES or "$" in token:
            return token
    return None


def _has_lockfile_flag(tokens: Sequence[str]) -> bool:
    """Report whether the invocation passes ``--lockfile`` in either spelling."""
    return any(
        token == _LOCKFILE_FLAG or token.startswith(f"{_LOCKFILE_FLAG}=")
        for token in tokens
    )


def _sibling_lock(config: Path) -> Path:
    """Return the lockfile path apko writes beside ``config``."""
    return config.with_suffix("").with_suffix(_LOCK_SUFFIX)


def _build_violation(root: Path, location: str, config: str | None) -> str | None:
    """Return why an unlocked invocation is wrong, or ``None`` if it is fine."""
    if config is None:
        return (
            f"{location}: `apko build` names no config file, so whether a lock "
            f"applies cannot be decided. Name the config, and pass "
            f"`{_LOCKFILE_FLAG}` with it."
        )
    if "$" in config:
        return (
            f"{location}: `apko build {config}` resolves its config at run "
            f"time, so whether a lock exists cannot be decided here. Pass "
            f"`{_LOCKFILE_FLAG}` derived from the same value."
        )
    lock = _sibling_lock(root / config)
    if not lock.is_file():
        return None
    return (
        f"{location}: `apko build {config}` does not pass "
        f"`{_LOCKFILE_FLAG} {_rel(root, lock)}`, so the committed digests "
        f"constrain nothing and the build re-resolves against the mirror."
    )


def _check_build_invocations(
    root: Path, paths: Sequence[Path], findings: _Findings
) -> None:
    """Check that every ``apko build`` applies its lock."""
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.errors.append(f"{_rel(root, path)}: unreadable ({exc})")
            continue
        for line_number, command in _apko_build_commands(text):
            findings.invocations += 1
            tokens = _tokenise(command)
            if _has_lockfile_flag(tokens):
                continue
            location = f"{_rel(root, path)}:{line_number}"
            violation = _build_violation(root, location, _config_argument(tokens))
            if violation is not None:
                findings.violations.append(violation)


def _load_lock(root: Path, path: Path, findings: _Findings) -> dict[str, object] | None:
    """Parse a lockfile, recording a scan error if it will not read."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        findings.errors.append(f"{_rel(root, path)}: unreadable lockfile ({exc})")
        return None
    if not isinstance(parsed, dict):
        findings.errors.append(f"{_rel(root, path)}: lockfile is not a JSON object")
        return None
    return parsed


def _manifest_for(
    root: Path, lock_path: Path, lock: dict[str, object], findings: _Findings
) -> tuple[Path, str] | None:
    """Return the manifest a lock names, plus its recorded checksum."""
    config = lock.get("config")
    if not isinstance(config, dict):
        findings.errors.append(f"{_rel(root, lock_path)}: lockfile has no `config`")
        return None
    name = config.get("name")
    checksum = config.get("checksum")
    if not isinstance(name, str) or not isinstance(checksum, str):
        findings.errors.append(
            f"{_rel(root, lock_path)}: `config.name` / `config.checksum` missing"
        )
        return None
    return root / name, checksum


def _resolved_names(
    root: Path, lock_path: Path, lock: dict[str, object], findings: _Findings
) -> frozenset[str] | None:
    """Return every package name the lock resolved."""
    contents = lock.get("contents")
    if not isinstance(contents, dict):
        findings.errors.append(f"{_rel(root, lock_path)}: lockfile has no `contents`")
        return None
    packages = contents.get("packages")
    if not isinstance(packages, list):
        findings.errors.append(
            f"{_rel(root, lock_path)}: `contents.packages` is not a list"
        )
        return None
    names = {
        entry["name"]
        for entry in packages
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    if not names:
        findings.errors.append(
            f"{_rel(root, lock_path)}: lockfile resolved no packages"
        )
        return None
    return frozenset(names)


def _declared_specs(
    root: Path, manifest: Path, findings: _Findings
) -> list[str] | None:
    """Return the package specs a manifest declares."""
    try:
        parsed = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        findings.errors.append(f"{_rel(root, manifest)}: unreadable manifest ({exc})")
        return None
    if not isinstance(parsed, dict):
        findings.errors.append(f"{_rel(root, manifest)}: not a YAML mapping")
        return None
    contents = parsed.get("contents")
    if not isinstance(contents, dict):
        findings.errors.append(f"{_rel(root, manifest)}: manifest has no `contents`")
        return None
    packages = contents.get("packages")
    if not isinstance(packages, list):
        findings.errors.append(
            f"{_rel(root, manifest)}: `contents.packages` is not a list"
        )
        return None
    return [entry for entry in packages if isinstance(entry, str)]


def _bare_name(spec: str) -> str:
    """Return the package name a spec carries, without any constraint."""
    name = spec
    for delimiter in _SPEC_DELIMITERS:
        name = name.split(delimiter, 1)[0]
    return name


def _alias_candidates(name: str, resolved: frozenset[str]) -> list[str]:
    """Return the resolved packages an alias plausibly reached.

    Wolfi inserts the series between the name's segments rather than appending
    it (``postgresql-client`` becomes ``postgresql-18-client``), so a prefix
    match alone finds nothing for the very case that motivated this check.
    """
    segments = name.split(_SEGMENT_SEPARATOR)
    head, tail = segments[0], segments[-1]
    return sorted(
        candidate
        for candidate in resolved
        if candidate != name
        and candidate.startswith(head)
        and (candidate.endswith(tail) or candidate.startswith(f"{name}-"))
    )


def _check_alias_names(
    root: Path, manifest: Path, resolved: frozenset[str], findings: _Findings
) -> None:
    """Flag every declared package name no resolved package actually carries."""
    specs = _declared_specs(root, manifest, findings)
    if specs is None:
        return
    for spec in specs:
        name = _bare_name(spec)
        if name != spec or name in resolved:
            continue
        candidates = _alias_candidates(name, resolved)
        reached = ", ".join(candidates) if candidates else "another package"
        findings.violations.append(
            f"{_rel(root, manifest)}: declares `{name}`, which no resolved "
            f"package is named; it reached {reached} through `provides`, so the "
            f"manifest tracks whatever Wolfi serves. Name the resolved package "
            f"instead."
        )


def _check_locks(root: Path, paths: Sequence[Path], findings: _Findings) -> None:
    """Check checksum parity, and that no declared package name is an alias."""
    for lock_path in paths:
        findings.locks += 1
        lock = _load_lock(root, lock_path, findings)
        if lock is None:
            continue
        named = _manifest_for(root, lock_path, lock, findings)
        if named is None:
            continue
        manifest, recorded = named
        if not manifest.is_file():
            findings.violations.append(
                f"{_rel(root, lock_path)}: names `{_rel(root, manifest)}`, which "
                f"does not exist, so the lock belongs to no manifest."
            )
            continue

        actual = _digest(manifest.read_bytes())
        if actual != recorded:
            findings.violations.append(
                f"{_rel(root, lock_path)}: `config.checksum` is {recorded}, but "
                f"`{_rel(root, manifest)}` hashes to {actual}. Regenerate with "
                f"`apko lock {_rel(root, manifest)}` on an LF checkout."
            )

        resolved = _resolved_names(root, lock_path, lock, findings)
        if resolved is not None:
            _check_alias_names(root, manifest, resolved, findings)


def _check_preflight_packages(root: Path, findings: _Findings) -> None:
    """Check the boot preflight names packages the backend image installs.

    The preflight's whole output is an operator instruction naming the package
    to install, so a name the manifest does not carry sends them after a
    package that image never had. Renaming one side is exactly how it breaks,
    and only the sandbox half of this question had a gate before.
    """
    module = root.joinpath(*_PREFLIGHT_MODULE)
    manifest = root.joinpath(*_PREFLIGHT_MANIFEST)
    if not module.is_file() or not manifest.is_file():
        return
    try:
        source = module.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        findings.errors.append(f"{_rel(root, module)}: unreadable ({exc})")
        return
    declared = _declared_specs(root, manifest, findings)
    if declared is None:
        return
    installed = {_bare_name(spec) for spec in declared}
    for match in _PREFLIGHT_PACKAGE_RE.finditer(source):
        package = match.group(1)
        if package in installed:
            continue
        line = source.count("\n", 0, match.start()) + 1
        findings.violations.append(
            f"{_rel(root, module)}:{line}: names the `{package}` package, which "
            f"`{_rel(root, manifest)}` does not install, so the boot refusal "
            f"sends an operator after a package the image never had."
        )


def _selected(root: Path, files: Sequence[str]) -> tuple[list[Path], list[Path]]:
    """Split explicit ``--files`` paths into workflow definitions and locks."""
    workflows: list[Path] = []
    locks: list[Path] = []
    roots = _workflow_roots(root)
    for raw in files:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            continue
        if path.name.endswith(_LOCK_SUFFIX):
            locks.append(path)
        elif path.suffix in _YAML_SUFFIXES:
            if any(directory in path.parents for directory in roots):
                workflows.append(path)
            elif _sibling_lock(path).is_file():
                # A manifest edit is judged through its own lock, which is
                # where both the checksum and the alias evidence live.
                locks.append(_sibling_lock(path))
    return workflows, locks


def _blind_scan_message(root: Path, findings: _Findings) -> str | None:
    """Return why a whole-tree scan cannot be trusted, or ``None``."""
    if findings.invocations == 0:
        directories = ", ".join(
            _rel(root, directory) for directory in _workflow_roots(root)
        )
        return (
            f"found no `apko build` invocation under {directories}. The scan "
            "cannot be trusted; fix the gate rather than the tree."
        )
    if findings.locks == 0:
        return (
            f"found no `*{_LOCK_SUFFIX}` under "
            f"{_rel(root, root / _DOCKER_SUBDIR)}. The scan cannot be trusted; "
            "fix the gate rather than the tree."
        )
    return None


def _report(findings: _Findings) -> None:
    """Print every violation, with the remedy on each line."""
    print("apko lockfiles do not constrain what is built:\n", file=sys.stderr)
    for violation in findings.violations:
        print(f"  {violation}", file=sys.stderr)
    print(
        f"\n{len(findings.violations)} violation(s). There is no opt-out marker: "
        "regenerate the lock, pass --lockfile, or name the resolved package.",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` clean, ``1`` on a violation, ``2`` when the scan cannot be
        trusted.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="Restrict the scan to these paths (agent-time use).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root to scan (defaults to this checkout).",
    )
    args = parser.parse_args(argv)
    root: Path = args.repo_root.resolve()
    whole_tree = args.files is None

    if whole_tree:
        workflows = list(_iter_workflow_files(root))
        locks = list(_iter_lock_files(root))
    else:
        workflows, locks = _selected(root, args.files)

    findings = _Findings()
    _check_build_invocations(root, workflows, findings)
    _check_locks(root, locks, findings)
    if whole_tree:
        _check_preflight_packages(root, findings)

    # A whole-tree run finding nothing means the scan went blind -- apko was
    # renamed, or the images moved -- which must not read as a pass. A --files
    # run is legitimately allowed to match neither.
    if whole_tree and not findings.errors:
        blind = _blind_scan_message(root, findings)
        if blind is not None:
            findings.errors.append(blind)

    if findings.errors:
        for error in findings.errors:
            print(f"check_apko_lock_applied: {error}", file=sys.stderr)
        return _EXIT_CONFIG_ERROR

    if findings.violations:
        _report(findings)
        return _EXIT_VIOLATION
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
