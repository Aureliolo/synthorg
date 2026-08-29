# module-kind: code
"""What a project's committed ``synthorg.env.yaml`` declares.

Both halves of the skeleton's contract with the units below it: how a fresh
clone is brought up, and what "done" means once it is. The language, lockfiles
and ordered setup commands are the first half; the gate commands, the
machine-readable report path and the pending set are the second.

The model, and nothing else. Provisioning a workspace from one of these lives
in :mod:`~synthorg.engine.workspace.environment.manifest_strategy`, and it is
genuinely a different concern: the completion-gate path reads this model on
every finishing task to decide what the project declared, and reaches none of
the provisioning machinery.
"""

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from synthorg.core.criterion_match import criterion_key
from synthorg.core.shell_semantics import trustworthy_segments
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EnvironmentConfigError
from synthorg.persistence.code_execution_protocol import CodeExecutionPurpose


class PendingTest(BaseModel):
    """One acceptance criterion, and the test that will decide it.

    Both halves are named here because the manifest is the single authority on
    what is pending. Matching a criterion to a test by reading the test's own
    name would give the name runtime meaning, and a rename nobody thought was
    load-bearing would then silently un-pend a criterion.

    Attributes:
        criterion: The criterion key, normalised through :func:`criterion_key`
            so it survives a re-spelling of the objective it came from.
        test_id: The runner's node id for the test asserting that criterion,
            matched against the machine-readable report.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    criterion: NotBlankStr
    test_id: NotBlankStr


class EnvironmentManifest(BaseModel):
    """The committed bootstrap-manifest declaration, and the project's gates.

    This file is both halves of the skeleton's contract with the units below
    it: how a fresh clone is brought up, and what "done" means once it is. A
    definition of done with nowhere to live is a definition of done nobody
    enforces, so it lives here, committed, beside the commands that produce the
    thing it judges.

    Attributes:
        language: Primary language of the deliverable (metadata).
        lockfiles: Version-pinning files hashed into the cache key.
        setup_commands: Ordered shell commands that install the toolchain
            and dependencies into the working tree, and the command that boots
            the result.
        test_command: How a fresh clone runs the project's tests.
        env: Toolchain / PATH additions applied to later tool calls.
        lint_command: How a fresh clone lints. Absent means no lint gate.
        format_command: How a fresh clone checks formatting. Absent means no
            formatting gate.
        dependency_check_command: How a fresh clone checks its own dependency
            rules. Absent means no dependency gate. A command rather than a
            list of allowed and denied names, because the project's own tooling
            already reads those and a second copy here is a second answer to
            what the project may depend on. A coverage floor is declared the
            same way, inside ``test_command``: the runner enforces it by exit
            status, and a separate number would be two owners of one figure.
        test_report_path: Where the test runner writes machine-readable
            per-test results, relative to the workspace root. Absent means the
            runner reports only an exit status, which is enough to say a run
            failed and not enough to say a pending test failed for its declared
            reason, so a project with pending criteria needs one.
        pending: The criteria whose tests are declared pending, each paired
            with the test that will decide it. A unit clears its own entry in
            the commit that makes its test pass, and that removal is the
            readiness signal, so this field is mutable committed state on
            purpose.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    language: NotBlankStr
    lockfiles: tuple[str, ...] = ()
    setup_commands: tuple[str, ...] = ()
    test_command: NotBlankStr
    env: dict[str, str] = Field(default_factory=dict)
    lint_command: NotBlankStr | None = None
    format_command: NotBlankStr | None = None
    dependency_check_command: NotBlankStr | None = None
    test_report_path: str | None = None
    pending: tuple[PendingTest, ...] = ()

    @property
    def declared_gates(self) -> Mapping[CodeExecutionPurpose, str]:
        """The gate commands this project declares, by what each one proves.

        Derived rather than listed, so a gate added to the model is a gate the
        oracle requires: a hand-written map is one field away from declaring a
        command nothing ever asks for evidence of, which is the unread knob
        this whole shape exists to refuse.

        Returns:
            One entry per declared gate; absent gates are simply not there.
        """
        declared = {
            CodeExecutionPurpose.LINT: self.lint_command,
            CodeExecutionPurpose.FORMAT: self.format_command,
            CodeExecutionPurpose.DEPENDENCY: self.dependency_check_command,
        }
        return {
            purpose: str(command)
            for purpose, command in declared.items()
            if command is not None
        }

    @model_validator(mode="after")
    def _validate_gate_commands(self) -> EnvironmentManifest:
        """Reject a gate whose exit status does not speak for its own commands.

        A gate is evidence, and the evidence is one recorded exit status. A
        declaration of ``ruff check . || true`` exits zero whatever the linter
        did, so every run of it mints a passing record for a gate that failed,
        and the badge over the work is green because nothing could ever be red.
        ``| tee lint.log`` fails the same way wherever the shell is not running
        under ``pipefail``, and both are ordinary spellings somebody writes
        without thinking about the exit status at all.

        Refused here rather than at evidence time because here is where a
        person is: the contract job's manifest goes through a review gate, so a
        declaration that cannot mean anything fails while somebody is looking
        at it, instead of silently certifying every unit below it.

        Returns:
            The validated manifest.

        Raises:
            ValueError: When a declared gate command's exit status stops
                speaking for the commands inside it.
        """
        for purpose, command in self.declared_gates.items():
            if trustworthy_segments(command) is None:
                msg = (
                    f"{purpose.value} gate {command!r} exits zero whatever its "
                    "commands did, so a run of it is not evidence; declare a "
                    "command whose exit status is the gate's own verdict"
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_pending(self) -> EnvironmentManifest:
        """Reject a pending set that cannot be matched or cannot be classified.

        Returns:
            The validated manifest.

        Raises:
            ValueError: When a criterion is not already normalised, when one
                criterion or one test is claimed twice, or when pending
                criteria are declared with no report to classify them from.
        """
        criteria: set[str] = set()
        tests: set[str] = set()
        for entry in self.pending:
            key = criterion_key(entry.criterion)
            if key != entry.criterion:
                msg = (
                    f"pending criterion {entry.criterion!r} is not normalised; "
                    f"expected {key!r}"
                )
                raise ValueError(msg)
            if key in criteria:
                msg = f"pending criterion {key!r} is declared twice"
                raise ValueError(msg)
            # Two criteria sharing one test cannot both be cleared
            # independently, so the second unit to finish would find its marker
            # already gone and read as done without having run.
            if entry.test_id in tests:
                msg = f"pending test {entry.test_id!r} is claimed by two criteria"
                raise ValueError(msg)
            criteria.add(key)
            tests.add(entry.test_id)
        if self.pending and self.test_report_path is None:
            msg = (
                "pending criteria need test_report_path: an exit status cannot "
                "separate a declared assertion failure from a collection error, "
                "so every pending test would have to classify red"
            )
            raise ValueError(msg)
        return self


def read_manifest(workspace_path: Path, *, filename: str) -> EnvironmentManifest:
    """Read and validate the committed manifest under *workspace_path*.

    Module-level so the strategy that provisions from the manifest and the
    capture path that reads its pending set share one reader: two would let a
    field the strategy accepts be one the capture path silently ignores.

    Raises:
        EnvironmentConfigError: The file is unreadable, is not a mapping, or
            does not validate.

    Returns:
        The parsed manifest.
    """
    path = workspace_path / filename
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"failed to read environment manifest {filename!r}"
        raise EnvironmentConfigError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"environment manifest {filename!r} must be a mapping"
        raise EnvironmentConfigError(msg)
    try:
        return EnvironmentManifest.model_validate(raw)
    except ValidationError as exc:
        msg = f"invalid environment manifest {filename!r}"
        raise EnvironmentConfigError(msg) from exc
