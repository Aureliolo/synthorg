"""Sandbox execution result model."""

from pydantic import BaseModel, ConfigDict, computed_field


class SandboxResult(BaseModel):
    """Immutable result of a sandboxed command execution.

    Attributes:
        stdout: Captured standard output.
        stderr: Captured standard error.
        returncode: Process exit code.
        timed_out: Whether the process was killed due to timeout.
        success: Computed — ``True`` when returncode is 0 and not timed out.
    """

    model_config = ConfigDict(frozen=True)

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        """Whether the execution succeeded."""
        return self.returncode == 0 and not self.timed_out
