# module-kind: code
"""What bounds the built-in tools at construction.

One object rather than a parameter each, matching ``WebToolsWiring`` and
``TerminalWiring``: these answer the same question, and a factory threading
them separately grows an argument list rather than a concept.

Resolved once by the caller because their consumers read them when the tool
is built. A ceiling a tool re-reads PER CALL does not belong here: it travels
with the wiring of the tool that reads it, so there is one place to look for
who keeps it live.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

#: Commits ``git_log`` returns when nothing resolves a bound.
DEFAULT_GIT_LOG_MAX_COUNT: Final[int] = 100

#: Characters of captured stdout/stderr kept on a test record when nothing
#: resolves a bound. Applied by ``code_runner`` and ``shell_command`` alike,
#: because both write the same record and a limit applied to one producer and
#: not the other means the retune half-lands.
DEFAULT_CODE_RUNNER_OUTPUT_TAIL_LIMIT: Final[int] = 2000

#: Live (pending/running) background jobs one sandbox lifecycle owner may
#: hold at once when nothing resolves a bound.
DEFAULT_BACKGROUND_MAX_CONCURRENT_JOBS: Final[int] = 5

#: Bytes of a background job's output captured at write time when nothing
#: resolves a bound.
DEFAULT_BACKGROUND_OUTPUT_BYTE_CAP: Final[int] = 1_000_000


class ToolCeilings(BaseModel):
    """Bounds the built-in tools are constructed under.

    Attributes:
        git_log_max_count: Upper bound on the commits ``git_log`` returns,
            resolved from ``tools.git_log_max_count``.
        code_runner_output_tail_limit: Maximum characters of captured
            stdout/stderr kept on a test record, resolved from
            ``tools.code_runner_output_tail_limit``.
        background_max_concurrent_jobs: Live background jobs one sandbox
            lifecycle owner may hold at once, resolved from
            ``tools.shell_command_background_max_concurrent_jobs``.
        background_output_byte_cap: Bytes of a background job's output
            captured at write time, resolved from
            ``tools.shell_command_background_output_byte_cap``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    git_log_max_count: int = Field(
        default=DEFAULT_GIT_LOG_MAX_COUNT,
        gt=0,
        description="Upper bound on commits git_log returns",
    )
    code_runner_output_tail_limit: int = Field(
        default=DEFAULT_CODE_RUNNER_OUTPUT_TAIL_LIMIT,
        gt=0,
        description="Captured output characters kept on a test record",
    )
    background_max_concurrent_jobs: int = Field(
        default=DEFAULT_BACKGROUND_MAX_CONCURRENT_JOBS,
        gt=0,
        description="Live background jobs one owner may hold at once",
    )
    background_output_byte_cap: int = Field(
        default=DEFAULT_BACKGROUND_OUTPUT_BYTE_CAP,
        gt=0,
        description="Bytes of a background job's output captured at write time",
    )


__all__ = [
    "DEFAULT_BACKGROUND_MAX_CONCURRENT_JOBS",
    "DEFAULT_BACKGROUND_OUTPUT_BYTE_CAP",
    "DEFAULT_CODE_RUNNER_OUTPUT_TAIL_LIMIT",
    "DEFAULT_GIT_LOG_MAX_COUNT",
    "ToolCeilings",
]
