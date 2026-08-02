"""Unit tests for ``scripts/check_signing_identity_pins.py``.

Loads the script as a module so its private helpers are callable without
spawning subprocesses.

The gate exists because a signing step moved between workflow files and every
published artefact became unverifiable while the Go suite stayed green. So the
tests centre on the discovery that has to stay honest for the verdict to mean
anything:

* Signing reached only through a chain of local composite actions still counts,
  because that is how the image path signs (reusable workflow to
  ``publish-image`` to ``publish-image-loaded``). Missing it would clear a
  pin that admits nobody.
* Prose in a ``run:`` block ("cosign signatures will be attached") is not a
  signing step. A substring match here promotes the release-announcement
  workflow into a signer and fails the gate on a healthy tree.
* A caller that only invokes a reusable workflow does not sign, which is the
  exact distinction both pins got wrong.

The comparison itself is then checked in both directions: a signer missing
from its pin, and a pinned name that signs nothing.
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
    """Write a dedented YAML fixture, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_real_tree_passes(gate: ModuleType) -> None:
    """The committed pins match the committed workflows."""
    assert gate.check() == []


def test_discovers_the_real_signers(gate: ModuleType) -> None:
    """Discovery finds exactly the four reusable signers in the tree."""
    assert gate.discover_signers() == {
        "reusable-release-cli",
        "reusable-publish-image",
        "reusable-publish-image-loaded",
        "reusable-publish-apko-base",
    }


def test_attest_step_counts_as_signing(gate: ModuleType, tmp_path: Path) -> None:
    """An attestation step alone makes a workflow a signer."""
    _write(
        tmp_path / "attests.yml",
        """
        on: {workflow_call: {}}
        jobs:
          go:
            steps:
              - uses: actions/attest-build-provenance@0f67c3f # v4.1.1
        """,
    )
    assert gate.discover_signers(tmp_path) == {"attests"}


def test_signing_through_composite_chain_counts(
    gate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signing reached through nested composite actions still counts."""
    actions = tmp_path / "actions"
    _write(
        actions / "outer" / "action.yml",
        """
        runs:
          using: composite
          steps:
            - uses: ./.github/actions/inner
        """,
    )
    _write(
        actions / "inner" / "action.yml",
        """
        runs:
          using: composite
          steps:
            - run: cosign sign --yes "${IMAGE}"
              shell: bash
        """,
    )
    monkeypatch.setattr(gate, "_ACTIONS_ROOT", actions)

    workflows = tmp_path / "workflows"
    _write(
        workflows / "delegates.yml",
        """
        on: {workflow_call: {}}
        jobs:
          publish:
            steps:
              - uses: ./.github/actions/outer
        """,
    )
    assert gate.discover_signers(workflows) == {"delegates"}


def test_prose_mentioning_cosign_is_not_signing(
    gate: ModuleType,
    tmp_path: Path,
) -> None:
    """Release-note prose naming cosign does not make a workflow a signer."""
    _write(
        tmp_path / "announces.yml",
        """
        on: {push: {}}
        jobs:
          notes:
            steps:
              - run: |
                  printf 'cosign signatures and SLSA provenance follow.\\n'
                  printf 'Verify with cosign verify-blob checksums.txt\\n'
        """,
    )
    assert gate.discover_signers(tmp_path) == set()


def test_caller_of_a_reusable_workflow_is_not_a_signer(
    gate: ModuleType,
    tmp_path: Path,
) -> None:
    """A job that only calls a reusable workflow signs nothing itself."""
    _write(
        tmp_path / "caller.yml",
        """
        on: {push: {}}
        jobs:
          release:
            permissions: {id-token: write, attestations: write}
            uses: ./.github/workflows/reusable-release-cli.yml
        """,
    )
    assert gate.discover_signers(tmp_path) == set()


def test_composite_action_cycle_terminates(
    gate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A composite action cycle is walked once, not forever."""
    actions = tmp_path / "actions"
    _write(
        actions / "ping" / "action.yml",
        """
        runs:
          using: composite
          steps:
            - uses: ./.github/actions/pong
        """,
    )
    _write(
        actions / "pong" / "action.yml",
        """
        runs:
          using: composite
          steps:
            - uses: ./.github/actions/ping
        """,
    )
    monkeypatch.setattr(gate, "_ACTIONS_ROOT", actions)

    workflows = tmp_path / "workflows"
    _write(
        workflows / "loops.yml",
        """
        on: {push: {}}
        jobs:
          go:
            steps:
              - uses: ./.github/actions/ping
        """,
    )
    assert gate.discover_signers(workflows) == set()


def test_unreadable_file_fails_closed(gate: ModuleType, tmp_path: Path) -> None:
    """Unparseable YAML raises rather than passing a partial scan."""
    _write(tmp_path / "broken.yml", "jobs: [unclosed\n")
    with pytest.raises(gate.SigningScanError):
        gate.discover_signers(tmp_path)


def test_extracts_pinned_names_from_the_real_constants(gate: ModuleType) -> None:
    """Both Go constants parse into the names they admit."""
    assert gate.extract_pinned_names(gate._SELFUPDATE_GO, "expectedSANRegex") == [
        "reusable-release-cli",
        "cli",
    ]
    assert gate.extract_pinned_names(gate._VERIFY_GO, "ExpectedSANRegex") == [
        "reusable-publish-image-loaded",
        "reusable-publish-image",
        "docker",
    ]


def test_unanchored_pin_is_rejected(gate: ModuleType, tmp_path: Path) -> None:
    """A pin that does not anchor to this repo's workflow path fails."""
    go_file = tmp_path / "loose.go"
    go_file.write_text(
        "const (\n\tExpectedSANRegex = `github\\.com/.*\\.yml`\n)\n",
        encoding="utf-8",
    )
    with pytest.raises(gate.SigningScanError, match="anchored"):
        gate.extract_pinned_names(go_file, "ExpectedSANRegex")


def test_missing_constant_is_rejected(gate: ModuleType, tmp_path: Path) -> None:
    """A renamed or deleted constant fails rather than reading as empty."""
    go_file = tmp_path / "empty.go"
    go_file.write_text("package verify\n", encoding="utf-8")
    with pytest.raises(gate.SigningScanError, match="no backquoted"):
        gate.extract_pinned_names(go_file, "ExpectedSANRegex")


def test_signer_missing_from_pin_is_flagged(gate: ModuleType) -> None:
    """The break this gate exists for: a signer the pin does not admit."""
    problems = gate._check_pin(
        "image pin",
        ["docker"],
        frozenset({"reusable-publish-image"}),
        {"docker": "retired"},
        {"reusable-publish-image"},
    )
    assert len(problems) == 1
    assert "reusable-publish-image.yml signs but is not admitted" in problems[0]


def test_pinned_name_that_signs_nothing_is_flagged(gate: ModuleType) -> None:
    """A caller-only filename in a pin is unreachable trust surface."""
    problems = gate._check_pin(
        "image pin",
        ["reusable-publish-image", "build-images"],
        frozenset({"reusable-publish-image"}),
        {},
        {"reusable-publish-image"},
    )
    assert len(problems) == 1
    assert "admits build-images.yml, which signs nothing" in problems[0]


def test_pin_admitting_another_artefact_class_is_flagged(gate: ModuleType) -> None:
    """One pin must not vouch for an artefact class it does not own."""
    problems = gate._check_pin(
        "image pin",
        ["reusable-publish-image", "reusable-release-cli"],
        frozenset({"reusable-publish-image"}),
        {},
        {"reusable-publish-image", "reusable-release-cli"},
    )
    assert len(problems) == 1
    assert "signs a different artefact class" in problems[0]


def test_retired_name_that_still_signs_is_flagged(gate: ModuleType) -> None:
    """A name declared retired while it still signs is a stale declaration."""
    problems = gate._check_pin(
        "image pin",
        ["docker"],
        frozenset(),
        {"docker": "retired"},
        {"docker"},
    )
    assert len(problems) == 1
    assert "declared retired but still signs" in problems[0]


def test_retired_name_is_accepted(gate: ModuleType) -> None:
    """A retired signer stays admitted so its artefacts remain verifiable."""
    assert (
        gate._check_pin(
            "image pin",
            ["reusable-publish-image", "docker"],
            frozenset({"reusable-publish-image"}),
            {"docker": "signed through v0.9.3"},
            {"reusable-publish-image"},
        )
        == []
    )


def test_main_passes_on_the_real_tree(gate: ModuleType) -> None:
    """The gate exits 0 against the committed tree."""
    assert gate.main([]) == 0
