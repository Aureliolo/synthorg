#!/usr/bin/env python3
"""Pre-push gate: the apko lockfiles pin what actually gets built.

``docker/*/apko.lock.json`` records an exact apk URL per package, and a weekly
cron reconciles it. A lock binds a build only when four things hold, and each
is checked here.

* ``apko build`` is handed ``--lockfile``, and the path it names exists. The
  flag defaults to the empty string, which apko documents as "no additional
  constraints", and apko discovers no sibling lock on its own, so an invocation
  that omits it re-resolves every package against whatever the mirror serves at
  that moment and two builds of one commit can differ.
* Every config the workflows declare they build through
  ``.github/actions/build-apko-base`` has a lock. The expected set is derived
  from those declarations rather than from what happens to be on disk, because
  a deleted lock is otherwise indistinguishable from an image that never had
  one: ``docker/web/apko.yaml`` is deliberately unlocked, and nothing on disk
  separates the two cases.
* A lock's ``config.checksum`` equals the sha256 of the manifest it names, over
  raw bytes. apko enforces this itself and refuses to build on a mismatch, so a
  lock regenerated on a CRLF checkout stops every build on an LF one; the
  ``eol=lf`` pin in ``.gitattributes`` is what keeps the two agreeing.
* A manifest names packages that exist under that name, and so does the boot
  preflight. Wolfi resolves an unversioned alias through ``provides`` to
  whichever series it serves that week, so a bare name reads like a pin while
  tracking a moving target, and the lock records only what the alias reached.

Scope is derived, never listed. A build whose config has no sibling lock and
which no workflow declares as locked has nothing to apply and is exempt; a
config path no static read can resolve requires the flag, since a parameterised
invocation is where an omission hides.

There is deliberately no baseline and no per-line opt-out. Each of these is a
defect rather than a position to preserve.

Exit codes:
    0 -- every build applies a lock that exists, every lock matches its
         manifest, and every declared package name is the one that installs.
    1 -- a violation.
    2 -- the scan cannot be trusted: a file that will not parse, or a
         whole-tree run that found no invocation, no lock, or could not reach
         the preflight anchors.

Usage::

    python scripts/check_apko_lock_applied.py
    python scripts/check_apko_lock_applied.py --files docker/backend/apko.yaml
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _apko_lock_lib import (  # type: ignore[import-not-found]
        APKO_VERSION_RE,
        BINARY_RECORD,
        DOCKER_SUBDIR,
        LOCK_SUFFIX,
        LOCKED_BUILD_ACTION,
        LOCKFILE_FLAG,
        YAML_SUFFIXES,
        Findings,
        alias_candidates,
        bare_name,
        config_argument,
        contained,
        declared_build_configs,
        declared_specs,
        digest,
        iter_lock_files,
        iter_workflow_files,
        load_lock,
        lock_config,
        lockfile_argument,
        record_packages,
        rel,
        resolved_names,
        sibling_lock,
        split_build_commands,
        tokenise,
        workflow_roots,
    )
else:
    from scripts._apko_lock_lib import (
        APKO_VERSION_RE,
        BINARY_RECORD,
        DOCKER_SUBDIR,
        LOCK_SUFFIX,
        LOCKED_BUILD_ACTION,
        LOCKFILE_FLAG,
        YAML_SUFFIXES,
        Findings,
        alias_candidates,
        bare_name,
        config_argument,
        contained,
        declared_build_configs,
        declared_specs,
        digest,
        iter_lock_files,
        iter_workflow_files,
        load_lock,
        lock_config,
        lockfile_argument,
        record_packages,
        rel,
        resolved_names,
        sibling_lock,
        split_build_commands,
        tokenise,
        workflow_roots,
    )

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_PREFLIGHT_MODULE: Final[str] = "src/synthorg/api/lifecycle_helpers/binary_preflight.py"
_PREFLIGHT_MANIFEST: Final[str] = "docker/backend/apko.yaml"

_EXIT_OK: Final[int] = 0
_EXIT_VIOLATION: Final[int] = 1
_EXIT_CONFIG_ERROR: Final[int] = 2


def _unlocked_reason(root: Path, location: str, config: str | None) -> str | None:
    """Return why an invocation without ``--lockfile`` is wrong, else ``None``."""
    if config is None:
        return (
            f"{location}: `apko build` names no config file, so whether a lock "
            f"applies cannot be decided. Name the config, and pass "
            f"`{LOCKFILE_FLAG}` with it."
        )
    if "$" in config:
        return (
            f"{location}: `apko build {config}` resolves its config at run "
            f"time, so whether a lock exists cannot be decided here. Pass "
            f"`{LOCKFILE_FLAG}` derived from the same value."
        )
    lock = sibling_lock(root / config)
    if not lock.is_file():
        return None
    return (
        f"{location}: `apko build {config}` does not pass "
        f"`{LOCKFILE_FLAG} {rel(root, lock)}`, so the committed digests "
        f"constrain nothing and the build re-resolves against the mirror."
    )


def _check_lockfile_value(
    root: Path, location: str, value: str, findings: Findings
) -> None:
    """Check that a named lockfile path exists.

    Presence of the flag is not the same as the lock being there; apko fails on
    a missing one at build time, which is far later and far from the change
    that removed it.
    """
    if "$" in value:
        return
    named = contained(root, root / value)
    if named is None:
        findings.violations.append(
            f"{location}: `{LOCKFILE_FLAG} {value}` points outside the repository."
        )
        return
    if not named.is_file():
        findings.violations.append(
            f"{location}: `{LOCKFILE_FLAG} {value}` names a file that does "
            f"not exist, so the build will fail once it runs."
        )


def _check_build_invocations(
    root: Path, paths: Sequence[Path], findings: Findings
) -> None:
    """Check that every ``apko build`` applies a lock that exists."""
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.errors.append(f"{rel(root, path)}: unreadable ({exc})")
            continue
        for line_number, command in split_build_commands(text):
            findings.invocations += 1
            location = f"{rel(root, path)}:{line_number}"
            tokens = tokenise(command)
            if tokens is None:
                findings.errors.append(
                    f"{location}: `apko build` invocation will not tokenise, so "
                    f"whether it applies a lock cannot be decided."
                )
                continue
            value = lockfile_argument(tokens)
            if value is not None:
                _check_lockfile_value(root, location, value, findings)
                continue
            reason = _unlocked_reason(root, location, config_argument(tokens))
            if reason is not None:
                findings.violations.append(reason)


def _check_declared_locks(root: Path, findings: Findings) -> set[Path]:
    """Check every workflow-declared config carries a lock; return those locks."""
    locks: set[Path] = set()
    for config in sorted(declared_build_configs(root, findings)):
        manifest = contained(root, root / config)
        if manifest is None:
            findings.violations.append(
                f"a workflow declares `{config}` as a locked build, but that "
                f"path is outside the repository."
            )
            continue
        if not manifest.is_file():
            findings.violations.append(
                f"a workflow declares `{config}` as a locked build, but no such "
                f"manifest exists."
            )
            continue
        lock = sibling_lock(manifest)
        if not lock.is_file():
            findings.violations.append(
                f"{config}: a workflow builds this through "
                f"`{LOCKED_BUILD_ACTION}`, which applies "
                f"`{rel(root, lock)}`, but that lock does not exist. Generate "
                f"it with `apko lock {config}`."
            )
            continue
        locks.add(lock)
    return locks


def _check_alias_names(
    root: Path, manifest: Path, resolved: frozenset[str], findings: Findings
) -> None:
    """Flag every declared package name no resolved package actually carries."""
    specs = declared_specs(root, manifest, findings)
    if specs is None:
        return
    for spec in specs:
        name = bare_name(spec)
        if name != spec or name in resolved:
            continue
        candidates = alias_candidates(name, resolved)
        reached = ", ".join(candidates) if candidates else "another package"
        findings.violations.append(
            f"{rel(root, manifest)}: declares `{name}`, which no resolved "
            f"package is named; it reached {reached} through `provides`, so the "
            f"manifest tracks whatever Wolfi serves. Name the resolved package "
            f"instead."
        )


def _check_one_lock(root: Path, lock_path: Path, findings: Findings) -> None:
    """Check one lock against the manifest it names."""
    lock = load_lock(root, lock_path, findings)
    if lock is None:
        return
    named = lock_config(root, lock_path, lock, findings)
    if named is None:
        return
    name, recorded = named
    manifest = contained(root, root / name)
    if manifest is None:
        findings.violations.append(
            f"{rel(root, lock_path)}: `config.name` is `{name}`, which points "
            f"outside the repository."
        )
        return
    if not manifest.is_file():
        findings.violations.append(
            f"{rel(root, lock_path)}: names `{name}`, which does not exist, so "
            f"the lock belongs to no manifest."
        )
        return

    try:
        actual = digest(manifest.read_bytes())
    except OSError as exc:
        findings.errors.append(f"{rel(root, manifest)}: unreadable ({exc})")
        return
    if actual != recorded:
        # apko refuses to build on this mismatch, so the alias check below
        # would be comparing the current manifest against a resolved set
        # generated from a different one.
        findings.violations.append(
            f"{rel(root, lock_path)}: `config.checksum` is {recorded}, but "
            f"`{name}` hashes to {actual}. apko refuses to build against this. "
            f"Regenerate with `apko lock {name}` on an LF checkout."
        )
        return

    resolved = resolved_names(root, lock_path, lock, findings)
    if resolved is not None:
        _check_alias_names(root, manifest, resolved, findings)


def _check_locks(root: Path, paths: Sequence[Path], findings: Findings) -> None:
    """Check checksum parity, and that no declared package name is an alias."""
    for lock_path in paths:
        _check_one_lock(root, lock_path, findings)


def _check_preflight_packages(root: Path, findings: Findings) -> None:
    """Check the boot preflight names packages the backend image installs.

    The preflight's output is an operator instruction naming the package to
    install, so a name the manifest does not carry sends them after a package
    that image never had.
    """
    module = root / _PREFLIGHT_MODULE
    manifest = root / _PREFLIGHT_MANIFEST
    for anchor in (module, manifest):
        if not anchor.is_file():
            findings.errors.append(
                f"{rel(root, anchor)} is missing, so the boot-preflight check "
                f"cannot run. Repoint it rather than leaving it silent."
            )
            return
    try:
        source = module.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        findings.errors.append(f"{_PREFLIGHT_MODULE}: unreadable ({exc})")
        return
    try:
        literals, opaque = record_packages(source)
    except SyntaxError as exc:
        findings.errors.append(f"{_PREFLIGHT_MODULE}: will not parse ({exc})")
        return
    if not literals and not opaque:
        findings.errors.append(
            f"{_PREFLIGHT_MODULE}: declares no `{BINARY_RECORD}` package, so "
            f"the check found nothing to hold to the manifest."
        )
        return

    specs = declared_specs(root, manifest, findings)
    if specs is None:
        return
    installed = {bare_name(spec) for spec in specs}
    for line in opaque:
        findings.violations.append(
            f"{_PREFLIGHT_MODULE}:{line}: names its package with something other "
            f"than a string literal, so it cannot be held to "
            f"`{_PREFLIGHT_MANIFEST}`."
        )
    for line, package in literals:
        if package in installed:
            continue
        findings.violations.append(
            f"{_PREFLIGHT_MODULE}:{line}: names the `{package}` package, which "
            f"`{_PREFLIGHT_MANIFEST}` does not install, so the boot refusal "
            f"sends an operator after a package the image never had."
        )
    findings.checked_preflight = True


def _check_apko_version_parity(
    root: Path, paths: Sequence[Path], findings: Findings
) -> None:
    """Check every workflow installs the same apko version.

    One version generates the locks and another consumes them, and the pins are
    separate literals in separate files. Only Renovate's grouping currently
    keeps them together, which is a convention rather than a guarantee.
    """
    seen: dict[str, list[str]] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        for match in APKO_VERSION_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            seen.setdefault(match.group(1), []).append(f"{rel(root, path)}:{line}")
    if len(seen) > 1:
        spread = "; ".join(
            f"{version} at {', '.join(sites)}"
            for version, sites in sorted(seen.items())
        )
        findings.violations.append(
            f"apko is pinned to more than one version ({spread}). A lock minted "
            f"by one version and consumed by another is a disagreement about "
            f"the file this gate exists to trust."
        )


def _selected(
    root: Path, files: Sequence[str], findings: Findings
) -> tuple[list[Path], list[Path], bool]:
    """Split explicit ``--files`` paths into workflows, locks, and preflight."""
    workflows: list[Path] = []
    locks: list[Path] = []
    preflight = False
    roots = workflow_roots(root)
    for raw in files:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        interesting = path.name.endswith(LOCK_SUFFIX) or path.suffix in YAML_SUFFIXES
        if interesting and not path.exists():
            findings.errors.append(
                f"{rel(root, path)}: named but absent. A deleted lock or "
                f"manifest needs a whole-tree run to judge."
            )
            continue
        if rel(root, path) == _PREFLIGHT_MODULE:
            preflight = True
        elif path.name.endswith(LOCK_SUFFIX) and path.is_file():
            locks.append(path)
        elif path.suffix in YAML_SUFFIXES and path.is_file():
            if any(directory in path.parents for directory in roots):
                workflows.append(path)
            elif sibling_lock(path).is_file():
                # A manifest edit is judged through its own lock, which is
                # where both the checksum and the alias evidence live.
                locks.append(sibling_lock(path))
            if rel(root, path) == _PREFLIGHT_MANIFEST:
                preflight = True
    return workflows, locks, preflight


def _blind_scan_message(root: Path, findings: Findings, locks: int) -> str | None:
    """Return why a whole-tree scan cannot be trusted, or ``None``."""
    if findings.invocations == 0:
        directories = ", ".join(
            rel(root, directory) for directory in workflow_roots(root)
        )
        return (
            f"found no `apko build` invocation under {directories}. The scan "
            "cannot be trusted; fix the gate rather than the tree."
        )
    if locks == 0:
        return (
            f"found no `*{LOCK_SUFFIX}` under "
            f"{rel(root, root / DOCKER_SUBDIR)}. The scan cannot be "
            "trusted; fix the gate rather than the tree."
        )
    if not findings.checked_preflight:
        return (
            "the boot-preflight check did not complete, so one of the four "
            "guarantees went unverified."
        )
    return None


def _report(findings: Findings) -> None:
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
    findings = Findings()

    if whole_tree:
        workflows = list(iter_workflow_files(root))
        locks = set(iter_lock_files(root)) | _check_declared_locks(root, findings)
        preflight = True
    else:
        workflows, selected, preflight = _selected(root, args.files, findings)
        locks = set(selected)

    _check_build_invocations(root, workflows, findings)
    _check_apko_version_parity(root, workflows, findings)
    _check_locks(root, sorted(locks), findings)
    if preflight:
        _check_preflight_packages(root, findings)

    # A whole-tree run that found nothing means the scan went blind, which must
    # not read as a pass. A --files run may legitimately match nothing, and a
    # run that already has something to say plainly did not go blind: the guard
    # exists to stop a false PASS, and a violation is not one.
    if whole_tree and not findings.errors and not findings.violations:
        blind = _blind_scan_message(root, findings, len(locks))
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
