#!/usr/bin/env python3
"""Pre-push gate: keep the pinned signing identities matched to the signers.

The CLI refuses any release archive or container image whose Sigstore
certificate SAN does not match a regex compiled into the binary
(``expectedSANRegex`` in ``cli/internal/selfupdate/sigstore.go`` for release
archives, ``ExpectedSANRegex`` in ``cli/internal/verify/identity.go`` for
images). Keyless signing derives that SAN from ``job_workflow_ref``, which for
a ``workflow_call`` job is the reusable workflow's own path rather than the
caller's. So the pins name the workflow that runs the signing step, and any
move of a signing step between files silently invalidates them.

Silently, because nothing in the Go tests can notice: they assert the constant
against hand-written strings that mirror it, so the suite stays green while
every published artefact becomes unverifiable. That has now happened twice. It
surfaces as a failed ``synthorg update`` or a ``synthorg start`` that refuses
to run without ``--skip-verify``, which is exactly the moment a user cannot
fix it.

This gate closes the loop by deriving the truth from the workflow tree rather
than trusting a second hand-written list. A workflow SIGNS if it reaches an
``actions/attest-build-provenance`` step or a ``cosign sign`` invocation, in
its own steps or through any local composite action it uses, transitively. The
derived set is then held against three declarations below, and every outcome
is an error:

- a signer missing from its pin (the break this gate exists for);
- a pinned name that no longer signs and is not declared retired (unreachable
  trust surface, which is how a caller-only filename crept into both pins);
- a signer in none of the declarations (a new signing path nobody classified).

The classification itself stays a human decision, because "which artefact does
this sign, and does the CLI verify it" is not derivable from the YAML. What
the gate removes is the possibility of that decision going stale unnoticed.

Retired names are kept deliberately, not tolerated: a published signature
cannot be re-minted, so dropping the name that signed the current stable
release would leave it permanently unverifiable.

Usage::

    python scripts/check_signing_identity_pins.py
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_WORKFLOWS_ROOT: Final[Path] = _REPO_ROOT / ".github" / "workflows"
_ACTIONS_ROOT: Final[Path] = _REPO_ROOT / ".github" / "actions"

_SELFUPDATE_GO: Final[Path] = (
    _REPO_ROOT / "cli" / "internal" / "selfupdate" / "sigstore.go"
)
_VERIFY_GO: Final[Path] = _REPO_ROOT / "cli" / "internal" / "verify" / "identity.go"

# Signing steps, matched on parsed ``uses``/``run`` values only. Matching raw
# file text would count a step *name* like "Verify cosign signatures" as a
# signing step and promote a caller into the signer set.
_ATTEST_ACTION: Final[str] = "actions/attest-build-provenance"
# Anchored on the invocation, not the word: release notes composed in a
# ``run:`` block talk about "cosign signatures", and a substring match there
# would promote the workflow announcing a release into a signer.
_COSIGN_SIGN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bcosign\s+sign(-blob)?\b|\bcosign_sign_with_retry\.sh\b",
)

# ``uses`` forms that resolve to a composite action inside this repo. The
# second form is how a workflow pins one of our own actions by SHA.
_LOCAL_ACTION_PREFIX: Final[str] = "./.github/actions/"
_OWN_REPO_ACTION_PREFIX: Final[str] = "Aureliolo/synthorg/.github/actions/"

# Which pin each current signer belongs to. A signer absent from every
# declaration below fails the gate rather than defaulting into one, because
# defaulting would let a new signing path inherit trust nobody granted it.
_RELEASE_ARCHIVE_SIGNERS: Final[frozenset[str]] = frozenset({"reusable-release-cli"})
_IMAGE_SIGNERS: Final[frozenset[str]] = frozenset(
    {"reusable-publish-image", "reusable-publish-image-loaded"},
)

# Signers whose artefacts the CLI never verifies, so no pin may admit them.
_UNVERIFIED_SIGNERS: Final[dict[str, str]] = {
    "reusable-publish-apko-base": (
        "signs the apko base layers, which ImageNames() does not list, so the "
        "CLI never resolves or verifies one"
    ),
}

# Names kept in a pin after they stopped signing. A published signature cannot
# be re-minted, so removing one of these strands the artefacts it signed.
_RETIRED_RELEASE_ARCHIVE_SIGNERS: Final[dict[str, str]] = {
    "cli": "signed every release archive through v0.9.3, still the stable channel",
}
_RETIRED_IMAGE_SIGNERS: Final[dict[str, str]] = {
    "docker": "signed every image through v0.9.3, still the stable channel",
}

_STEERING_MESSAGE: Final[str] = (
    "The certificate SAN follows job_workflow_ref: for a workflow_call job "
    "that is the REUSABLE workflow's path, never the caller's. Pin the file "
    "holding the signing step.\n"
    "Verify a real artefact before changing a pin:\n"
    "  gh attestation verify oci://ghcr.io/aureliolo/synthorg-backend:<tag> "
    "-R Aureliolo/synthorg \\\n"
    "    --format json --jq "
    "'.[0].verificationResult.signature.certificate.subjectAlternativeName'"
)


class SigningScanError(Exception):
    """A workflow or action file could not be read or parsed.

    Raised so the gate fails closed: a skipped file could hide the signing
    step that decides the whole verdict.
    """


def _load_yaml(path: Path) -> object:
    """Parse a workflow or action file, failing closed on any error."""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"{path}: {exc}"
        raise SigningScanError(msg) from exc


def _iter_steps(doc: object) -> list[dict[str, object]]:
    """Return every step mapping in a workflow or composite action."""
    steps: list[dict[str, object]] = []
    if not isinstance(doc, dict):
        return steps

    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        for job in jobs.values():
            if isinstance(job, dict):
                steps.extend(s for s in _step_list(job.get("steps")) if s)

    runs = doc.get("runs")
    if isinstance(runs, dict):
        steps.extend(s for s in _step_list(runs.get("steps")) if s)

    return steps


def _step_list(raw: object) -> list[dict[str, object]]:
    """Narrow a raw ``steps:`` value to the mappings it contains."""
    if not isinstance(raw, list):
        return []
    return [step for step in raw if isinstance(step, dict)]


def _resolve_local_action(uses: str) -> Path | None:
    """Return the action.yml path for a composite action in this repo."""
    ref = uses.split("@", 1)[0].strip()
    for prefix in (_LOCAL_ACTION_PREFIX, _OWN_REPO_ACTION_PREFIX):
        if ref.startswith(prefix):
            name = ref[len(prefix) :].strip("/")
            if not name or ".." in Path(name).parts:
                return None
            for filename in ("action.yml", "action.yaml"):
                candidate = _ACTIONS_ROOT / name / filename
                if candidate.is_file():
                    return candidate
            return None
    return None


def _signs(path: Path, seen: set[Path]) -> bool:
    """Report whether this file reaches a signing step, transitively.

    Args:
        path: Workflow or composite action file to inspect.
        seen: Files already visited on this walk, so a composite action
            cycle terminates instead of recursing forever.

    Returns:
        True if the file, or any local composite action it uses, runs a
        cosign signing step or an attestation step.

    Raises:
        SigningScanError: A file in the walk could not be read or parsed.
    """
    resolved = path.resolve()
    if resolved in seen:
        return False
    seen.add(resolved)

    for step in _iter_steps(_load_yaml(path)):
        uses = step.get("uses")
        if isinstance(uses, str):
            if uses.strip().startswith(_ATTEST_ACTION):
                return True
            nested = _resolve_local_action(uses)
            if nested is not None and _signs(nested, seen):
                return True
        run = step.get("run")
        if isinstance(run, str) and _COSIGN_SIGN_PATTERN.search(run):
            return True
    return False


def discover_signers(workflows_root: Path = _WORKFLOWS_ROOT) -> set[str]:
    """Return the stem of every workflow that reaches a signing step.

    Args:
        workflows_root: Directory holding the workflow files to scan.

    Returns:
        Workflow file stems (``reusable-release-cli``, not the full path),
        matching the form a certificate SAN carries.

    Raises:
        SigningScanError: A file in the scan could not be read or parsed.
    """
    signers: set[str] = set()
    for path in sorted(workflows_root.glob("*.y*ml")):
        if _signs(path, set()):
            signers.add(path.stem)
    return signers


def extract_pinned_names(go_file: Path, const_name: str) -> list[str]:
    """Return the workflow names admitted by a Go SAN regex constant.

    Args:
        go_file: Go source holding the constant.
        const_name: Identifier assigned the SAN regex.

    Returns:
        The workflow stems in the regex's alternation group, in source order.

    Raises:
        SigningScanError: The constant is missing, unanchored, or not
            shaped as a workflow-path alternation.
    """
    try:
        source = go_file.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"{go_file}: {exc}"
        raise SigningScanError(msg) from exc

    assign = re.search(
        rf"^\s*{re.escape(const_name)}\s*=\s*`([^`]*)`",
        source,
        re.MULTILINE,
    )
    if assign is None:
        msg = f"{go_file}: no backquoted {const_name} constant"
        raise SigningScanError(msg)

    pattern = assign.group(1)
    # An unanchored or unscoped pattern would admit identities from any repo
    # or any workflow, so the alternation it lists would stop meaning
    # anything. Check before reading the names out of it.
    prefix = r"^https://github\.com/Aureliolo/synthorg/\.github/workflows/"
    if not pattern.startswith(prefix) or not pattern.endswith("$"):
        msg = (
            f"{go_file}: {const_name} must be anchored to this repo's "
            f"workflow path at both ends"
        )
        raise SigningScanError(msg)

    group = re.match(rf"{re.escape(prefix)}\(([^)]*)\)\\\.yml@", pattern)
    if group is None:
        msg = f"{go_file}: {const_name} does not expose a (name|name)\\.yml alternation"
        raise SigningScanError(msg)
    return [name for name in group.group(1).split("|") if name]


def _check_pin(
    label: str,
    pinned: list[str],
    current: frozenset[str],
    retired: dict[str, str],
    signers: set[str],
) -> list[str]:
    """Compare one pin against the discovered signers.

    Args:
        label: Human name for the pin, used in messages.
        pinned: Workflow names the pin admits.
        current: Signers that must be admitted by this pin.
        retired: Names kept after they stopped signing, mapped to why.
        signers: Every workflow discovered to sign anything.

    Returns:
        One message per problem found, empty when the pin is correct.
    """
    problems: list[str] = []
    pinned_set = set(pinned)

    problems.extend(
        f"{label}: {name}.yml signs but is not admitted -- every artefact "
        f"it signs fails verification"
        for name in sorted(current - pinned_set)
    )
    for name in pinned:
        if name in current or name in retired:
            continue
        if name in signers:
            problems.append(
                f"{label}: admits {name}.yml, which signs a different "
                f"artefact class; a pin must not vouch beyond its own",
            )
        else:
            problems.append(
                f"{label}: admits {name}.yml, which signs nothing -- drop it, "
                f"or declare it retired with the artefacts it signed",
            )
    problems.extend(
        f"{label}: {name}.yml is declared retired but still signs"
        for name in sorted(retired)
        if name in signers
    )
    return problems


def check() -> list[str]:
    """Run the full comparison.

    Returns:
        One message per problem found, empty when both pins are correct.

    Raises:
        SigningScanError: The workflow tree or a Go pin could not be read.
    """
    signers = discover_signers()
    release_pin = extract_pinned_names(_SELFUPDATE_GO, "expectedSANRegex")
    image_pin = extract_pinned_names(_VERIFY_GO, "ExpectedSANRegex")

    problems = _check_pin(
        "release-archive pin (cli/internal/selfupdate/sigstore.go)",
        release_pin,
        _RELEASE_ARCHIVE_SIGNERS,
        _RETIRED_RELEASE_ARCHIVE_SIGNERS,
        signers,
    )
    problems += _check_pin(
        "image pin (cli/internal/verify/identity.go)",
        image_pin,
        _IMAGE_SIGNERS,
        _RETIRED_IMAGE_SIGNERS,
        signers,
    )

    for name in sorted(_UNVERIFIED_SIGNERS):
        for label, pinned in (
            ("release-archive pin", release_pin),
            ("image pin", image_pin),
        ):
            if name in pinned:
                problems.append(
                    f"{label}: admits {name}.yml, declared as signing an "
                    f"artefact the CLI never verifies "
                    f"({_UNVERIFIED_SIGNERS[name]})",
                )

    classified = _RELEASE_ARCHIVE_SIGNERS | _IMAGE_SIGNERS | set(_UNVERIFIED_SIGNERS)
    for name in sorted(signers - classified):
        problems.append(
            f"{name}.yml signs but is classified nowhere -- add it to the pin "
            f"for the artefact it signs, or declare why the CLI never "
            f"verifies that artefact",
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
        print(f"FAIL (scan could not read a file): {exc}", file=sys.stderr)
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
