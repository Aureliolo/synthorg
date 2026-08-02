"""Unit tests for ``scripts/check_signing_identity_pins.py``.

Loads the script as a module so its private helpers are callable without
spawning subprocesses.

The gate guards a trust anchor, so the tests are weighted towards the ways it
could report success without having checked anything:

* Discovery must see a signing step however it is expressed -- through a
  composite-action chain, a shell helper, a line continuation, or a variable
  holding the binary -- because anything it misses becomes a pin nobody
  validates.
* Discovery must NOT see one in prose or in a comment describing the signing
  it wraps, or a healthy tree fails and the gate gets muted.
* An ambiguous reference must raise rather than read as "not a signer".
* The declarations must be held against the discovered set in both
  directions, and an empty discovery must be an error rather than a pass.
* The pattern comparison must be exact, since a partially-validated regex
  leaves the unvalidated part free.
"""

import importlib.util
import textwrap
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_signing_identity_pins.py"


def _load_script_module() -> ModuleType:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_signing_identity_pins",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    """The gate module under test."""
    return _load_script_module()


def _write(path: Path, body: str) -> Path:
    """Write a dedented fixture, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _roots(gate: ModuleType, tmp_path: Path) -> object:
    """Build a ScanRoots pointing entirely inside tmp_path."""
    return gate.ScanRoots(
        workflows=tmp_path / "workflows",
        actions=tmp_path / "actions",
        scripts=tmp_path / "scripts",
    )


def _workflow(tmp_path: Path, name: str, steps: str) -> None:
    """Write a minimal one-job workflow whose steps are *steps*."""
    _write(
        tmp_path / "workflows" / name,
        f"""
        on: {{workflow_call: {{}}}}
        jobs:
          go:
            steps:
{textwrap.indent(textwrap.dedent(steps), " " * 14)}
        """,
    )


# --------------------------------------------------------------------------
# The committed tree
# --------------------------------------------------------------------------


def test_real_tree_passes(gate: ModuleType) -> None:
    """The committed pins match the committed workflows."""
    assert gate.check() == []


def test_main_passes_on_the_real_tree(gate: ModuleType) -> None:
    """The gate exits 0 against the committed tree."""
    assert gate.main([]) == 0


def test_discovers_the_real_signers(gate: ModuleType) -> None:
    """Discovery finds exactly the four reusable signers in the tree."""
    assert gate.discover_signers() == {
        "reusable-release-cli",
        "reusable-publish-image",
        "reusable-publish-image-loaded",
        "reusable-publish-apko-base",
    }


def test_expected_pattern_matches_the_real_constants(gate: ModuleType) -> None:
    """Each declaration builds exactly the pattern committed in Go."""
    for pin in gate._PINS:
        actual = gate.extract_pinned_pattern(pin.go_file, pin.const_name)
        assert actual == gate.expected_pattern(pin), pin.label


# --------------------------------------------------------------------------
# Discovery: forms that DO sign
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "step"),
    [
        ("attest_build_provenance", "- uses: actions/attest-build-provenance@0f67c3f"),
        ("attest_sbom", "- uses: actions/attest-sbom@0f67c3f"),
        ("cosign_sign", '- run: cosign sign --yes "${IMAGE}"'),
        ("cosign_sign_blob", "- run: cosign sign-blob checksums.txt"),
        ("cosign_attest", "- run: cosign attest --predicate p.json img"),
        ("cosign_attest_blob", "- run: cosign attest-blob checksums.txt"),
        ("cosign_via_path", "- run: /usr/local/bin/cosign sign img"),
        ("cosign_via_variable", '- run: |\n    "$COSIGN" sign img'),
        ("cosign_line_continuation", "- run: |\n    cosign \\\n      sign img"),
    ],
)
def test_signing_forms_are_detected(
    gate: ModuleType,
    tmp_path: Path,
    label: str,
    step: str,
) -> None:
    """Every shape a real signing step takes counts as signing."""
    _workflow(tmp_path, "signs.yml", step)
    assert gate.discover_signers(_roots(gate, tmp_path)) == {"signs"}, label


def test_signing_through_composite_chain_counts(
    gate: ModuleType,
    tmp_path: Path,
) -> None:
    """Signing reached through nested composite actions still counts.

    This is the real image path: a reusable workflow delegates to
    publish-image, which delegates again to publish-image-loaded, which signs.
    """
    _write(
        tmp_path / "actions" / "outer" / "action.yml",
        """
        runs:
          using: composite
          steps:
            - uses: ./.github/actions/inner
        """,
    )
    _write(
        tmp_path / "actions" / "inner" / "action.yml",
        """
        runs:
          using: composite
          steps:
            - run: cosign sign --yes "${IMAGE}"
              shell: bash
        """,
    )
    _workflow(tmp_path, "delegates.yml", "- uses: ./.github/actions/outer")
    assert gate.discover_signers(_roots(gate, tmp_path)) == {"delegates"}


def test_own_repo_action_prefix_resolves_case_insensitively(
    gate: ModuleType,
    tmp_path: Path,
) -> None:
    """GitHub treats owner/repo case-insensitively, so the scan must too."""
    _write(
        tmp_path / "actions" / "signer" / "action.yml",
        """
        runs:
          using: composite
          steps:
            - run: cosign sign img
              shell: bash
        """,
    )
    _workflow(
        tmp_path,
        "pinned.yml",
        "- uses: AURELIOLO/SynthOrg/.github/actions/signer@abc123",
    )
    assert gate.discover_signers(_roots(gate, tmp_path)) == {"pinned"}


def test_signing_through_repo_script_counts(gate: ModuleType, tmp_path: Path) -> None:
    """A run: delegating to a repo shell helper that signs counts.

    Detection keys on the script's contents, not its name, so renaming the
    wrapper cannot blind the scan.
    """
    _write(
        tmp_path / "scripts" / "some_wrapper.sh",
        """
        #!/usr/bin/env bash
        exec cosign sign-blob "$@"
        """,
    )
    _workflow(
        tmp_path,
        "delegates.yml",
        "- run: bash .github/scripts/some_wrapper.sh sign-blob checksums.txt",
    )
    assert gate.discover_signers(_roots(gate, tmp_path)) == {"delegates"}


# --------------------------------------------------------------------------
# Discovery: forms that do NOT sign
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "step"),
    [
        ("prose", "- run: printf 'cosign signatures follow\\n'"),
        ("verify_not_sign", "- run: cosign verify-blob checksums.txt"),
        ("hyphenated_lookalike", "- run: cosign sign-tree whatever"),
        ("shell_comment", "- run: |\n    # cosign sign happens elsewhere\n    echo hi"),
        ("third_party_action", "- uses: sigstore/cosign-installer@6f9f177"),
    ],
)
def test_non_signing_forms_are_not_detected(
    gate: ModuleType,
    tmp_path: Path,
    label: str,
    step: str,
) -> None:
    """Text that merely mentions signing does not make a workflow a signer."""
    _workflow(tmp_path, "innocent.yml", step)
    assert gate.discover_signers(_roots(gate, tmp_path)) == set(), label


def test_caller_of_a_reusable_workflow_is_not_a_signer(
    gate: ModuleType,
    tmp_path: Path,
) -> None:
    """A job that only calls a reusable workflow signs nothing itself."""
    _write(
        tmp_path / "workflows" / "caller.yml",
        """
        on: {push: {}}
        jobs:
          release:
            permissions: {id-token: write}
            uses: ./.github/workflows/reusable-release-cli.yml
        """,
    )
    assert gate.discover_signers(_roots(gate, tmp_path)) == set()


def test_composite_action_cycle_terminates(gate: ModuleType, tmp_path: Path) -> None:
    """A composite action cycle is walked once, not forever."""
    for name, other in (("ping", "pong"), ("pong", "ping")):
        _write(
            tmp_path / "actions" / name / "action.yml",
            f"""
            runs:
              using: composite
              steps:
                - uses: ./.github/actions/{other}
            """,
        )
    _workflow(tmp_path, "loops.yml", "- uses: ./.github/actions/ping")
    assert gate.discover_signers(_roots(gate, tmp_path)) == set()


# --------------------------------------------------------------------------
# Fail-closed behaviour
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uses",
    [
        pytest.param("./.github/actions/does-not-exist", id="missing_action"),
        pytest.param("./.github/actions/../../etc/passwd", id="path_traversal"),
    ],
)
def test_local_reference_that_does_not_resolve_raises(
    gate: ModuleType,
    tmp_path: Path,
    uses: str,
) -> None:
    """A local-looking reference the scan cannot follow must not read as safe.

    Covers a plainly missing action and a traversal attempt out of the actions
    directory. Returning None for either would be indistinguishable from a
    third-party action, silently dropping the step from the scan.
    """
    _workflow(tmp_path, "broken.yml", f"- uses: {uses}")
    with pytest.raises(gate.SigningScanError, match="does not resolve"):
        gate.discover_signers(_roots(gate, tmp_path))


def test_missing_referenced_script_raises(gate: ModuleType, tmp_path: Path) -> None:
    """A run: naming a repo script that is absent leaves the scan blind."""
    _workflow(tmp_path, "ghost.yml", "- run: bash .github/scripts/gone.sh")
    with pytest.raises(gate.SigningScanError, match=r"not in \.github/scripts"):
        gate.discover_signers(_roots(gate, tmp_path))


def test_unparseable_yaml_raises(gate: ModuleType, tmp_path: Path) -> None:
    """Unparseable YAML raises rather than passing a partial scan."""
    _write(tmp_path / "workflows" / "broken.yml", "jobs: [unclosed\n")
    with pytest.raises(gate.SigningScanError):
        gate.discover_signers(_roots(gate, tmp_path))


def test_workflow_without_jobs_raises(gate: ModuleType, tmp_path: Path) -> None:
    """A file whose shape the scan does not understand is not 'no steps'."""
    _write(tmp_path / "workflows" / "shapeless.yml", "on: {push: {}}\n")
    with pytest.raises(gate.SigningScanError, match="no 'jobs:' mapping"):
        gate.discover_signers(_roots(gate, tmp_path))


def test_non_mapping_workflow_raises(gate: ModuleType, tmp_path: Path) -> None:
    """A document that is not a mapping cannot be scanned."""
    _write(tmp_path / "workflows" / "list.yml", "- a\n- b\n")
    with pytest.raises(gate.SigningScanError, match="not a mapping"):
        gate.discover_signers(_roots(gate, tmp_path))


def test_invalid_utf8_raises(gate: ModuleType, tmp_path: Path) -> None:
    """Undecodable bytes fail closed with the gate's own error type.

    UnicodeDecodeError is a ValueError, so an OSError-only guard would let it
    escape as an unhandled traceback instead of a clean verdict.
    """
    path = tmp_path / "workflows" / "bad.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jobs:\n  go:\n    steps: []\n# \xff\xfe invalid\n")
    with pytest.raises(gate.SigningScanError):
        gate.discover_signers(_roots(gate, tmp_path))


def test_empty_signer_set_raises(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tree where nothing signs is never valid, so it must not read as OK."""
    monkeypatch.setattr(gate, "discover_signers", lambda *_a, **_k: set())
    with pytest.raises(gate.SigningScanError, match="no signing workflows"):
        gate.check()


def test_main_reports_scan_error_as_exit_2(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan that cannot reach a verdict exits 2, distinct from a breach."""
    monkeypatch.setattr(gate, "discover_signers", lambda *_a, **_k: set())
    assert gate.main([]) == 2


def test_main_reports_problems_as_exit_1(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real mismatch exits 1."""
    monkeypatch.setattr(gate, "check", lambda: ["something drifted"])
    assert gate.main([]) == 1


# --------------------------------------------------------------------------
# Declarations held against discovery
# --------------------------------------------------------------------------


def test_declared_signer_that_stopped_signing_is_flagged(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vacuous pass: a pinned current signer that no longer signs.

    Comparing the pin only against the hand-written declarations would report
    green here, which is the exact regression this gate exists to catch.
    """
    surviving = gate.discover_signers() - {"reusable-release-cli"}
    monkeypatch.setattr(gate, "discover_signers", lambda *_a, **_k: surviving)
    problems = gate.check()
    assert any(
        "reusable-release-cli.yml is pinned as a current signer" in p for p in problems
    )


def test_unclassified_new_signer_is_flagged(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signing step landing in a brand-new file must be classified."""
    extra = gate.discover_signers() | {"reusable-publish-something-new"}
    monkeypatch.setattr(gate, "discover_signers", lambda *_a, **_k: extra)
    problems = gate.check()
    assert any(
        "reusable-publish-something-new.yml signs but is classified nowhere" in p
        for p in problems
    )


def test_retired_name_that_still_signs_is_flagged(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name declared retired while it still signs is a stale declaration."""
    resurrected = gate.discover_signers() | {"docker"}
    monkeypatch.setattr(gate, "discover_signers", lambda *_a, **_k: resurrected)
    problems = gate.check()
    assert any("docker.yml is declared retired but still signs" in p for p in problems)
    # It is already classified, so it must not also be reported as unclassified:
    # that would send the reader to add a pin entry that is already there.
    assert not any("docker.yml signs but is classified nowhere" in p for p in problems)


def test_apko_base_signer_stays_unpinned(gate: ModuleType) -> None:
    """The base-layer signer is discovered but must never be admitted."""
    assert "reusable-publish-apko-base" in gate.discover_signers()
    pinned = {s.workflow for pin in gate._PINS for s in pin.signers}
    assert "reusable-publish-apko-base" not in pinned


def test_single_caller_invariant_holds_on_the_real_tree(gate: ModuleType) -> None:
    """Each current signer is reachable from exactly one calling workflow.

    Every caller inherits the signer's identity, so a second one would widen
    the trust anchor without changing any pin.
    """
    assert gate._check_single_caller(gate.discover_signers()) == []
    assert gate.calling_workflows("reusable-release-cli") == {"verify-cli"}
    assert gate.calling_workflows("reusable-publish-image") == {"build-images"}


# --------------------------------------------------------------------------
# Pattern construction and comparison
# --------------------------------------------------------------------------


def test_missing_constant_raises(gate: ModuleType, tmp_path: Path) -> None:
    """A renamed or deleted constant fails rather than reading as empty."""
    go_file = _write(tmp_path / "empty.go", "package verify\n")
    with pytest.raises(gate.SigningScanError, match="no backquoted"):
        gate.extract_pinned_pattern(go_file, "ExpectedSANRegex")


def test_unsafe_stem_is_rejected(gate: ModuleType) -> None:
    """A stem carrying regex metacharacters would change the pattern."""
    pin = gate.Pin(
        label="test pin",
        go_file=gate._VERIFY_GO,
        const_name="ExpectedSANRegex",
        signers=(gate.PinnedSigner("evil.*", gate._MAIN_REF),),
    )
    with pytest.raises(gate.SigningScanError, match="not regex-safe"):
        gate.expected_pattern(pin)


@pytest.mark.parametrize(
    ("label", "pattern"),
    [
        (
            "wildcard_ref",
            r"^https://github\.com/Aureliolo/synthorg/\.github/workflows/(?:docker\.yml@.*)$",
        ),
        (
            "second_top_level_alternative",
            r"^https://github\.com/Aureliolo/synthorg/\.github/workflows/(?:docker\.yml@refs/heads/main)$|^https://github\.com/evil/x/.*$",
        ),
        (
            "pull_ref_admitted",
            r"^https://github\.com/Aureliolo/synthorg/\.github/workflows/(?:docker\.yml@refs/.+)$",
        ),
    ],
)
def test_loosened_pattern_is_rejected(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    pattern: str,
) -> None:
    """A pin loosened in any part is caught, not just its prefix and anchor."""
    monkeypatch.setattr(gate, "extract_pinned_pattern", lambda *_a, **_k: pattern)
    problems = gate.check()
    assert any("does not match its declaration" in p for p in problems), label
