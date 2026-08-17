#!/usr/bin/env python3
"""Pre-push gate: keep the pinned signing identities matched to the signers.

The CLI refuses any release archive or container image whose Sigstore
certificate fails a policy compiled into the binary. Half of that policy is a
SAN regex; both live in ``cli/internal/verify/identity.go``
(``ExpectedReleaseSANRegex`` for release archives, ``ExpectedSANRegex`` for
images). Keyless signing derives the SAN from ``job_workflow_ref``, which
for a ``workflow_call`` job is the reusable workflow's own path rather than
the caller's, so those regexes name the workflow that runs the signing step.
Move a signing step to another file and the pin silently stops matching
anything, while the Go tests -- which assert the constant against hand-written
strings that mirror it -- stay green. The break only surfaces as a failed
``synthorg update`` or a ``synthorg start`` that refuses to run, which is the
one moment a user cannot fix it.

This gate derives the signer set from the workflow tree instead of trusting a
second hand-written list, then holds three things against it:

- every workflow declared as a current signer must actually sign, and every
  workflow that signs must be classified somewhere;
- a name declared retired must no longer sign;
- each Go constant must equal, character for character, the pattern built from
  the declarations below.

The last check is exact rather than structural on purpose. Validating a regex
by inspecting parts of it leaves the unexamined parts free: a pin whose ref
portion was loosened to ``.*``, or one carrying a second top-level alternative
escaping the repository entirely, passes any check that only confirms the
prefix and the trailing anchor. sigstore-go matches with an unanchored search,
so such a pin would accept a certificate from an attacker-controlled
repository.

Classification stays a human decision, because "which artefact does this sign,
and does the CLI verify it" is not derivable from the YAML. What the gate
removes is the possibility of that decision going stale unnoticed.

Retired names are kept deliberately: a published signature cannot be
re-minted, so dropping the name that signed an artefact users can still
install would make it permanently unverifiable.

Usage::

    python scripts/check_signing_identity_pins.py
"""

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_WORKFLOWS_ROOT: Final[Path] = _REPO_ROOT / ".github" / "workflows"
_ACTIONS_ROOT: Final[Path] = _REPO_ROOT / ".github" / "actions"

_VERIFY_GO: Final[Path] = _REPO_ROOT / "cli" / "internal" / "verify" / "identity.go"

# This repository, as GitHub spells it. Every prefix below is built from it so
# a rename or transfer changes one line. Leaving them independent would let a
# rename update the SAN pins while a caller reference kept the old owner: the
# scan would then find no callers and report a clean tree, which is the one
# outcome this gate must never produce silently.
_REPO_SLUG: Final[str] = "Aureliolo/synthorg"

# Signing steps are matched on parsed ``uses``/``run`` values only. Matching
# raw file text would count a step *name* like "Verify cosign signatures" as a
# signing step and promote a caller into the signer set.
#
# Every action in this family mints a Fulcio certificate carrying the calling
# workflow's job_workflow_ref, so each is a signer for our purposes.
_ATTEST_ACTION_PREFIX: Final[str] = "actions/attest"

# A shell line continuation puts a backslash between the command and its
# subcommand, which no whitespace class matches, so continuations are folded
# out before the invocation pattern runs.
_LINE_CONTINUATION: Final[re.Pattern[str]] = re.compile(r"\\[ \t]*\r?\n[ \t]*")

# Shell comments routinely describe the signing they wrap, and a retry helper
# explaining which cosign calls it guards is not itself a signer. The `#` must
# start a word so parameter expansions such as ${VAR#prefix} survive.
_SHELL_COMMENT: Final[re.Pattern[str]] = re.compile(r"(?m)(?:^|(?<=\s))#.*$")

# cosign reached directly, through a path, or through a variable holding it.
# The subcommands are ordered longest-first and followed by a boundary that
# excludes word characters AND hyphens, so "cosign signatures" in prose and
# "cosign sign-tree" both fail to match while "cosign sign-blob" matches.
# Intra-line whitespace only: after continuation folding, a match spanning a
# newline would mean two unrelated commands.
_COSIGN_INVOCATION: Final[re.Pattern[str]] = re.compile(
    r"(?:\bcosign|[\"']?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?[\"']?)"
    r"[ \t]+(?:sign-blob|sign|attest-blob|attest)(?![\w-])",
)

# A run: step may delegate to a repo script that signs, and so may that
# script. Every helper is read and tested with the same pattern, so neither
# renaming a wrapper nor adding a layer of them can blind the scan.
#
# The match is any repo-relative path whose final directory is a scripts
# directory, which covers .github/scripts, the repository-root scripts, and
# any per-component one. It deliberately does not match an absolute path:
# that names a file the repository does not hold, so reading it would report
# on something other than what ships. The leading-character exclusion is what
# rules one out, so the workspace-root prefixes below are stripped first
# rather than left to be matched as though they were directories.
_SCRIPT_REFERENCE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w./-])((?:[A-Za-z0-9_.-]+/)*scripts/[A-Za-z0-9_.-]+\.(?:sh|py))",
)

# A step that has changed directory names its helper from the workspace root
# instead, and every spelling of that root IS the checkout, so the remainder
# is the repo-relative path the pattern above wants. Stripping the prefix is
# what makes the helper readable: left in, `$GITHUB_WORKSPACE/` reads as a
# directory named after the variable and fails the scan on a file that is
# plainly there, while `${{ github.workspace }}/` fails the lookbehind and is
# skipped in silence, which is the worse half -- a signing helper reached
# that way would never be read at all.
_WORKSPACE_ROOT_PREFIX: Final[re.Pattern[str]] = re.compile(
    r"(?:\$\{?GITHUB_WORKSPACE\}?|\$\{\{\s*github\.workspace\s*\}\})/",
)

# ``uses`` forms that resolve to a composite action inside this repo. GitHub
# treats owner and repository case-insensitively, so the own-repo form is
# matched that way; a case variant must not read as a third-party action.
_LOCAL_ACTION_PREFIX: Final[str] = "./.github/actions/"
_OWN_REPO_ACTION_PREFIX: Final[str] = f"{_REPO_SLUG.lower()}/.github/actions/"

# The same two forms for a reusable-workflow call, and the extensions GitHub
# accepts for one. A signer reached through either form or either extension is
# reached all the same, so the caller count must recognise all of them.
_WORKFLOW_CALL_PREFIXES: Final[tuple[str, ...]] = (
    "./.github/workflows/",
    f"{_REPO_SLUG.lower()}/.github/workflows/",
)
_WORKFLOW_EXTENSIONS: Final[frozenset[str]] = frozenset({"yml", "yaml"})

# Workflow stems are embedded in a regex verbatim, so anything outside this
# alphabet would change the pattern's meaning rather than its text.
_SAFE_STEM: Final[re.Pattern[str]] = re.compile(r"\A[a-z0-9-]+\Z")

_PATTERN_PREFIX: Final[str] = (
    rf"^https://github\.com/{re.escape(_REPO_SLUG)}/\.github/workflows/"
)

# Ref classes a certificate may carry. A release archive is only ever cut from
# a tag; an image is only ever signed on a main push, because publish jobs are
# gated to main and retagging re-points a tag at an already-signed digest
# without signing again.
_SEMVER_TAG: Final[str] = (
    r"refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?"
)
# The retired release signer is bounded to the versions it actually signed, so
# it cannot vouch for a release that does not exist yet.
_LEGACY_SEMVER_TAG: Final[str] = (
    r"refs/tags/v0\.(?:[0-8]\.[0-9]+|9\.[0-3])"
    r"(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?"
)
_MAIN_REF: Final[str] = r"refs/heads/main"


class SigningScanError(Exception):
    """The gate could not establish the facts it needs to reach a verdict.

    Raised when a workflow, composite action or script cannot be read or
    parsed, when a file's shape is not one the scanner recognises, or when a
    Go pin is missing or malformed. Each of those leaves some part of the tree
    unexamined, and an unexamined file can hide the signing step that decides
    the whole verdict, so the gate fails rather than reporting a partial scan.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class ScanRoots:
    """Where the scan looks for the files that can contain a signing step.

    Bundled rather than passed individually so every root is injected the same
    way. A root read from a module global instead would be late-bound while
    its siblings were not, which is the kind of asymmetry that makes a test
    silently exercise the real tree.

    Attributes:
        workflows: Directory holding workflow files.
        actions: Directory holding this repository's composite actions.
        repo: Root the repo-relative script references resolve against.
    """

    workflows: Path = _WORKFLOWS_ROOT
    actions: Path = _ACTIONS_ROOT
    repo: Path = _REPO_ROOT


@dataclasses.dataclass(frozen=True, slots=True)
class PinnedSigner:
    """One workflow a pin admits, with the ref class it may carry.

    Attributes:
        workflow: Workflow file stem, as it appears in a certificate SAN.
        ref_pattern: Regex fragment for the refs this signer may carry.
        retired_reason: Why a workflow that no longer signs stays admitted,
            or None for a current signer.
    """

    workflow: str
    ref_pattern: str
    retired_reason: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Pin:
    """One SAN regex constant and the signers it must admit.

    Attributes:
        label: Human name used in messages.
        go_file: Go source holding the constant.
        const_name: Identifier assigned the SAN regex.
        signers: Admitted signers, in the order the pattern lists them.
    """

    label: str
    go_file: Path
    const_name: str
    signers: tuple[PinnedSigner, ...]


_PINS: Final[tuple[Pin, ...]] = (
    Pin(
        label="release-archive pin (cli/internal/verify/identity.go)",
        go_file=_VERIFY_GO,
        const_name="ExpectedReleaseSANRegex",
        signers=(
            PinnedSigner("reusable-release-cli", _SEMVER_TAG),
            PinnedSigner(
                "cli",
                _LEGACY_SEMVER_TAG,
                retired_reason=(
                    "signed the release the stable channel still points at; a "
                    "source-built binary reports version 'dev' and always "
                    "updates, so it verifies that release"
                ),
            ),
        ),
    ),
    Pin(
        label="image pin (cli/internal/verify/identity.go)",
        go_file=_VERIFY_GO,
        const_name="ExpectedSANRegex",
        signers=(
            PinnedSigner("reusable-publish-image-loaded", _MAIN_REF),
            PinnedSigner("reusable-publish-image", _MAIN_REF),
            PinnedSigner(
                "docker",
                _MAIN_REF,
                retired_reason=(
                    "signed every image a pinned image_tag can still install"
                ),
            ),
        ),
    ),
)

# Signers whose artefacts the CLI never verifies, so no pin may admit them.
_UNVERIFIED_SIGNERS: Final[dict[str, str]] = {
    "reusable-publish-apko-base": (
        "signs the apko base layers, which ImageNames() does not list, so the "
        "CLI never resolves or verifies one"
    ),
}

_STEERING_MESSAGE: Final[str] = (
    "The certificate SAN follows job_workflow_ref: for a workflow_call job "
    "that is the REUSABLE workflow's path, never the caller's. Pin the file "
    "holding the signing step.\n"
    "Verify a real artefact before changing a pin:\n"
    "  gh attestation verify oci://ghcr.io/aureliolo/synthorg-backend:<tag> "
    "-R Aureliolo/synthorg \\\n"
    "    --format json --jq "
    "'.[0].verificationResult.signature.certificate"
    "|{subjectAlternativeName,sourceRepositoryURI}'"
)


def _read_text(path: Path) -> str:
    """Read a file as UTF-8, failing closed on any decoding or I/O error."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{path}: {exc}"
        raise SigningScanError(msg) from exc


def _load_yaml(path: Path) -> object:
    """Parse a workflow or action file, failing closed on any error."""
    try:
        return yaml.safe_load(_read_text(path))
    except yaml.YAMLError as exc:
        msg = f"{path}: {exc}"
        raise SigningScanError(msg) from exc


def _steps_of(raw: object) -> list[dict[str, object]]:
    """Narrow a raw ``steps:`` value to the mappings it contains."""
    if not isinstance(raw, list):
        return []
    return [step for step in raw if isinstance(step, dict)]


def _workflow_jobs(path: Path, doc: object) -> dict[str, object]:
    """Return the jobs mapping of a workflow file.

    Args:
        path: File the document came from, for error messages.
        doc: Parsed document.

    Returns:
        The raw ``jobs:`` mapping.

    Raises:
        SigningScanError: The document is not a workflow. A file that parses
            but has no jobs mapping would scan as having no steps, which reads
            identically to "scanned, found no signing step".
    """
    if not isinstance(doc, dict):
        msg = f"{path}: not a mapping, so it cannot be scanned for signing steps"
        raise SigningScanError(msg)
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        msg = (
            f"{path}: no 'jobs:' mapping, so its shape is not one this scan understands"
        )
        raise SigningScanError(msg)
    return jobs


def _workflow_steps(path: Path, doc: object) -> list[dict[str, object]]:
    """Return every step in a workflow file.

    Args:
        path: File the document came from, for error messages.
        doc: Parsed document.

    Returns:
        Every step mapping across all jobs.

    Raises:
        SigningScanError: The document is not a workflow.
    """
    steps: list[dict[str, object]] = []
    for job in _workflow_jobs(path, doc).values():
        if isinstance(job, dict):
            steps.extend(_steps_of(job.get("steps")))
    return steps


def _action_steps(path: Path, doc: object) -> list[dict[str, object]]:
    """Return every step in a composite action file.

    Args:
        path: File the document came from, for error messages.
        doc: Parsed document.

    Returns:
        Every step mapping under ``runs``.

    Raises:
        SigningScanError: The document has no ``runs`` mapping.
    """
    if not isinstance(doc, dict):
        msg = f"{path}: not a mapping, so it cannot be scanned for signing steps"
        raise SigningScanError(msg)
    runs = doc.get("runs")
    if not isinstance(runs, dict):
        msg = (
            f"{path}: no 'runs:' mapping, so its shape is not one this scan understands"
        )
        raise SigningScanError(msg)
    return _steps_of(runs.get("steps"))


def _resolve_local_action(uses: str, actions_root: Path, origin: Path) -> Path | None:
    """Resolve a composite action reference inside this repository.

    Args:
        uses: Raw ``uses:`` value.
        actions_root: Directory holding this repository's composite actions.
        origin: File the reference came from, for error messages.

    Returns:
        Path to the action file, or None when the reference is not local.

    Raises:
        SigningScanError: The reference is local but does not resolve. A
            reference the scanner cannot follow is indistinguishable from a
            third-party action if it returns None, so the step would be
            silently dropped from the scan.
    """
    ref = uses.split("@", 1)[0].strip()
    lowered = ref.lower()
    if lowered.startswith(_LOCAL_ACTION_PREFIX):
        name = ref[len(_LOCAL_ACTION_PREFIX) :]
    elif lowered.startswith(_OWN_REPO_ACTION_PREFIX):
        name = ref[len(_OWN_REPO_ACTION_PREFIX) :]
    else:
        return None

    name = name.strip("/")
    if name and ".." not in Path(name).parts:
        for filename in ("action.yml", "action.yaml"):
            candidate = actions_root / name / filename
            if candidate.is_file():
                return candidate
    msg = (
        f"{origin}: uses {uses!r}, which names an action in this repository "
        f"but does not resolve under {actions_root}"
    )
    raise SigningScanError(msg)


def _text_signs(body: str, origin: Path, roots: ScanRoots, seen: set[Path]) -> bool:
    """Report whether a shell body reaches a signing invocation.

    Args:
        body: Shell body of a step or of a helper script.
        origin: File the body came from, for error messages.
        roots: Directories the scan may follow references into.
        seen: Files already visited, so a helper cycle terminates.

    Returns:
        True when the body signs directly or through a repository script.

    Raises:
        SigningScanError: A referenced repository script cannot be read.
    """
    folded = _executable_text(body)
    if _COSIGN_INVOCATION.search(folded):
        return True
    for reference in _SCRIPT_REFERENCE.findall(folded):
        script = roots.repo / reference
        if not script.is_file():
            msg = (
                f"{origin}: references {reference}, which does not resolve under "
                f"{roots.repo}"
            )
            raise SigningScanError(msg)
        if _script_signs(script, roots, seen):
            return True
    return False


def _script_signs(path: Path, roots: ScanRoots, seen: set[Path]) -> bool:
    """Report whether a helper script reaches a signing invocation.

    Args:
        path: Helper script file.
        roots: Directories the scan may follow references into.
        seen: Files already visited, so a helper cycle terminates.

    Returns:
        True when the script, or one it calls, signs.

    Raises:
        SigningScanError: A file in the walk could not be read.
    """
    resolved = path.resolve()
    if resolved in seen:
        return False
    seen.add(resolved)
    return _text_signs(_read_text(path), path, roots, seen)


def _executable_text(shell: str) -> str:
    """Reduce a shell body to what actually runs.

    Comments come out first so a continuation cannot splice a commented line
    onto the command below it, then line continuations are folded so a command
    split across lines reads as one, and finally the workspace-root prefix is
    reduced to nothing so a helper named through it reads as the repo-relative
    path it is.
    """
    folded = _LINE_CONTINUATION.sub(" ", _SHELL_COMMENT.sub("", shell))
    return _WORKSPACE_ROOT_PREFIX.sub("", folded)


def _steps_sign(
    steps: list[dict[str, object]],
    origin: Path,
    roots: ScanRoots,
    seen: set[Path],
) -> bool:
    """Report whether any step reaches a signing step, transitively."""
    for step in steps:
        uses = step.get("uses")
        if isinstance(uses, str):
            if uses.strip().startswith(_ATTEST_ACTION_PREFIX):
                return True
            nested = _resolve_local_action(uses, roots.actions, origin)
            if nested is not None and _action_signs(nested, roots, seen):
                return True
        run = step.get("run")
        if isinstance(run, str) and _text_signs(run, origin, roots, seen):
            return True
    return False


def _action_signs(path: Path, roots: ScanRoots, seen: set[Path]) -> bool:
    """Report whether a composite action reaches a signing step.

    Args:
        path: Composite action file.
        roots: Directories the scan may follow references into.
        seen: Files already visited, so an action cycle terminates.

    Returns:
        True when the action, or one it uses, signs.

    Raises:
        SigningScanError: A file in the walk could not be read or parsed.
    """
    resolved = path.resolve()
    if resolved in seen:
        return False
    seen.add(resolved)
    return _steps_sign(_action_steps(path, _load_yaml(path)), path, roots, seen)


def discover_signers(roots: ScanRoots | None = None) -> set[str]:
    """Return the stem of every workflow that reaches a signing step.

    Args:
        roots: Directories the scan may follow references into.

    Returns:
        Workflow file stems, matching the form a certificate SAN carries.

    Raises:
        SigningScanError: A file in the scan could not be read or parsed.
    """
    roots = roots or ScanRoots()
    signers: set[str] = set()
    for path in sorted(roots.workflows.glob("*.y*ml")):
        steps = _workflow_steps(path, _load_yaml(path))
        if _steps_sign(steps, path, roots, set()):
            signers.add(path.stem)
    return signers


def _called_workflow_stem(uses: str) -> str | None:
    """Return the stem a job-level ``uses:`` calls in this repository.

    Args:
        uses: Raw ``uses:`` value from a job.

    Returns:
        The called workflow's stem, or None when the value names a workflow
        outside this repository.
    """
    ref = uses.split("@", 1)[0].strip()
    lowered = ref.lower()
    for prefix in _WORKFLOW_CALL_PREFIXES:
        if lowered.startswith(prefix):
            name = ref[len(prefix) :]
            break
    else:
        return None
    stem, _, extension = name.rpartition(".")
    if not stem or extension.lower() not in _WORKFLOW_EXTENSIONS:
        return None
    return stem


def calling_workflows(signer: str, roots: ScanRoots | None = None) -> set[str]:
    """Return the stems of workflows that invoke *signer* as a reusable call.

    Reads the parsed job-level ``uses:`` rather than searching the file text,
    so a path filter or a comment naming the signer is not mistaken for a
    second caller, and so both file extensions and both reference forms count.

    Args:
        signer: Workflow stem of the reusable signer.
        roots: Directories the scan may follow references into.

    Returns:
        Stems of the workflow files containing a call to it.

    Raises:
        SigningScanError: A workflow file could not be read or parsed.
    """
    roots = roots or ScanRoots()
    callers: set[str] = set()
    for path in sorted(roots.workflows.glob("*.y*ml")):
        if path.stem == signer:
            continue
        for job in _workflow_jobs(path, _load_yaml(path)).values():
            if not isinstance(job, dict):
                continue
            uses = job.get("uses")
            if isinstance(uses, str) and _called_workflow_stem(uses) == signer:
                callers.add(path.stem)
                break
    return callers


def expected_pattern(pin: Pin) -> str:
    """Build the SAN regex a pin's declarations require.

    Args:
        pin: Pin whose declarations describe the accepted identities.

    Returns:
        The exact pattern the Go constant must hold.

    Raises:
        SigningScanError: A declared workflow stem contains a character that
            would alter the pattern's meaning rather than its text.
    """
    alternatives = []
    for signer in pin.signers:
        if not _SAFE_STEM.match(signer.workflow):
            msg = f"{pin.label}: workflow stem {signer.workflow!r} is not regex-safe"
            raise SigningScanError(msg)
        alternatives.append(rf"{signer.workflow}\.yml@{signer.ref_pattern}")
    return f"{_PATTERN_PREFIX}(?:{'|'.join(alternatives)})$"


def extract_pinned_pattern(go_file: Path, const_name: str) -> str:
    """Return the raw SAN regex assigned to a Go constant.

    Args:
        go_file: Go source holding the constant.
        const_name: Identifier assigned the SAN regex.

    Returns:
        The backquoted literal's contents, verbatim.

    Raises:
        SigningScanError: The constant is absent or not a backquoted literal.
    """
    assign = re.search(
        rf"^\s*{re.escape(const_name)}\s*=\s*`([^`]*)`",
        _read_text(go_file),
        re.MULTILINE,
    )
    if assign is None:
        msg = f"{go_file}: no backquoted {const_name} constant"
        raise SigningScanError(msg)
    return assign.group(1)


def _check_declarations(signers: set[str]) -> list[str]:
    """Hold the pin declarations against the discovered signer set."""
    problems: list[str] = []
    current: dict[str, str] = {}
    retired: dict[str, str] = {}
    for pin in _PINS:
        for signer in pin.signers:
            target = retired if signer.retired_reason else current
            if signer.workflow in current or signer.workflow in retired:
                problems.append(
                    f"{signer.workflow}.yml is declared in more than one pin; "
                    f"one artefact class's signer must not vouch for another",
                )
            target[signer.workflow] = pin.label

    problems.extend(
        f"{pin_label}: {name}.yml is pinned as a current signer but signs "
        f"nothing -- the signing step moved or was removed, so find where it "
        f"went and update both the pin and this declaration"
        for name, pin_label in sorted(current.items())
        if name not in signers
    )
    problems.extend(
        f"{pin_label}: {name}.yml is declared retired but still signs"
        for name, pin_label in sorted(retired.items())
        if name in signers
    )
    problems.extend(
        f"{name}.yml signs but is classified nowhere -- add it to the pin for "
        f"the artefact it signs, or declare why the CLI never verifies that "
        f"artefact"
        for name in sorted(
            signers - set(current) - set(retired) - set(_UNVERIFIED_SIGNERS)
        )
    )
    problems.extend(
        f"{name}.yml is pinned but declared as signing an artefact the CLI "
        f"never verifies ({reason})"
        for name, reason in sorted(_UNVERIFIED_SIGNERS.items())
        if name in current or name in retired
    )
    return problems


def _check_single_caller(signers: set[str]) -> list[str]:
    """Require each current signer to be reachable from exactly one workflow.

    A reusable signer's SAN says nothing about which caller invoked it, so
    every workflow able to call one inherits the trust anchor. Keeping that to
    a single, ref-gated caller is what stops a lower-trust trigger from
    reaching the same identity.

    Zero callers fails too. A reusable workflow that signs but nothing invokes
    either is unreachable, or the scan stopped recognising the form its caller
    uses, and the second case is how this check would pass while seeing
    nothing at all.
    """
    problems = []
    for pin in _PINS:
        for signer in pin.signers:
            if signer.retired_reason or signer.workflow not in signers:
                continue
            callers = calling_workflows(signer.workflow)
            if not callers:
                problems.append(
                    f"{pin.label}: {signer.workflow}.yml signs but no workflow "
                    f"calls it; either it is unreachable, or the call form it is "
                    f"invoked through is one this scan no longer recognises",
                )
            elif len(callers) > 1:
                problems.append(
                    f"{pin.label}: {signer.workflow}.yml is called by "
                    f"{', '.join(sorted(callers))}; every caller inherits its "
                    f"signing identity, so keep it to one",
                )
    return problems


def check() -> list[str]:
    """Run the full comparison.

    Returns:
        One message per problem found, empty when both pins are correct.

    Raises:
        SigningScanError: The workflow tree or a Go pin could not be read, or
            the scan found no signers at all, which is never a valid state for
            this repository and would otherwise make every pin look correct.
    """
    signers = discover_signers()
    if not signers:
        msg = (
            "found no signing workflows at all; .github/workflows may be "
            "empty, moved, or no longer matched by the scan"
        )
        raise SigningScanError(msg)

    problems = _check_declarations(signers)
    problems += _check_single_caller(signers)
    for pin in _PINS:
        actual = extract_pinned_pattern(pin.go_file, pin.const_name)
        expected = expected_pattern(pin)
        if actual != expected:
            problems.append(
                f"{pin.label}: {pin.const_name} does not match its declaration.\n"
                f"      in Go:    {actual}\n"
                f"      declared: {expected}",
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Keep the pinned Sigstore identities matched to the "
        "workflows that actually sign.",
    )
    parser.parse_args(argv)

    try:
        problems = check()
    except SigningScanError as exc:
        print(f"FAIL (scan could not reach a verdict): {exc}", file=sys.stderr)
        return 2

    if not problems:
        return 0
    print(
        "\nSigstore identity pins do not match the signing workflows:\n",
        file=sys.stderr,
    )
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(f"\n{_STEERING_MESSAGE}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
