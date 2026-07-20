"""Typed argument models for file-system tools.

One frozen Pydantic model per tool in the file-system family.  Each
model carries the static shape (required keys, types, lower-bound
constraints) the tool's ``execute`` consumes today.  Path validation
(workspace root, traversal blocks) and content-size guards stay in the
tool body because they depend on per-tool runtime state.

Tools wired to consume these models:

* :class:`~synthorg.tools.file_system.read_file.ReadFileTool`
  -> :class:`ReadFileArgs`
* :class:`~synthorg.tools.file_system.write_file.WriteFileTool`
  -> :class:`WriteFileArgs`
* :class:`~synthorg.tools.file_system.edit_file.EditFileTool`
  -> :class:`EditFileArgs`
* :class:`~synthorg.tools.file_system.delete_file.DeleteFileTool`
  -> :class:`DeleteFileArgs`
* :class:`~synthorg.tools.file_system.list_directory.ListDirectoryTool`
  -> :class:`ListDirectoryArgs`
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


class ReadFileArgs(BaseModel):
    """Args for ``read_file``.

    Both ``start_line`` and ``end_line`` are 1-based inclusive.  When
    both are set, ``start_line <= end_line`` is enforced at validation
    time so a malformed request is rejected at the boundary; the
    error reports the offending field for the LLM caller.
    """

    model_config = _ARGS_CONFIG

    path: NotBlankStr = Field(description="File path relative to workspace")
    start_line: int | None = Field(
        default=None,
        ge=1,
        description="First line to read (1-based inclusive)",
    )
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="Last line to read (1-based inclusive)",
    )

    @model_validator(mode="after")
    def _check_line_range(self) -> Self:
        """Reject ``start_line > end_line`` when both are supplied.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.start_line > self.end_line
        ):
            msg = (
                f"start_line ({self.start_line}) must be <= end_line ({self.end_line})"
            )
            raise ValueError(msg)
        return self


class WriteFileArgs(BaseModel):
    """Args for ``write_file``."""

    model_config = _ARGS_CONFIG

    path: NotBlankStr = Field(description="File path relative to workspace")
    content: str = Field(description="Content to write")
    create_directories: bool = Field(
        default=False,
        description="Create parent directories if missing",
    )


class EditHunk(BaseModel):
    """A single find-and-replace within a multi-hunk ``edit_file`` call.

    ``new_text`` may be the empty string (deletes the matched
    ``old_text``).  ``old_text`` requires at least one character so the
    edit has something to anchor on.  When ``replace_all`` is false the
    match must be unique in the working content at the point this hunk is
    applied, otherwise the whole edit is rejected.
    """

    model_config = _ARGS_CONFIG

    old_text: str = Field(min_length=1, description="Exact text to find")
    new_text: str = Field(
        description="Replacement text (empty string to delete the match)",
    )
    replace_all: bool = Field(
        default=False,
        description=(
            "Replace every occurrence. When false the match must be unique"
            " or the edit is rejected."
        ),
    )


class EditFileArgs(BaseModel):
    """Args for ``edit_file``.

    Two mutually exclusive modes:

    * **Single edit**: supply ``old_text`` (+ ``new_text``, optionally
      ``replace_all``). ``new_text`` may be the empty string (deletes the
      match). When ``replace_all`` is false the match must be unique.
    * **Batch edit**: supply ``edits`` (a non-empty list of hunks) applied
      in order, atomically (all-or-nothing) to one file. Each hunk sees the
      result of the preceding hunks.

    Exactly one mode must be used: supplying both ``old_text`` and
    ``edits``, or neither, is rejected at the boundary.
    """

    model_config = _ARGS_CONFIG

    path: NotBlankStr = Field(description="File path relative to workspace")
    old_text: str | None = Field(
        default=None,
        min_length=1,
        description="Exact text to find (single-edit mode)",
    )
    new_text: str | None = Field(
        default=None,
        description=(
            "Replacement text for single-edit mode (empty string to delete"
            " the match); required when ``old_text`` is set"
        ),
    )
    replace_all: bool = Field(
        default=False,
        description=(
            "Single-edit mode: replace every occurrence. When false the"
            " match must be unique or the edit is rejected."
        ),
    )
    edits: tuple[EditHunk, ...] = Field(
        default=(),
        description="Ordered hunks applied atomically (batch-edit mode)",
    )

    @model_validator(mode="after")
    def _validate_mode(self) -> Self:
        """Enforce single-vs-batch mutual exclusion and single-mode shape.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If both modes are supplied, neither is supplied, or
                single-edit mode omits ``new_text``.
        """
        single = self.old_text is not None
        batch = bool(self.edits)
        if single and batch:
            msg = "provide either old_text/new_text or edits, not both"
            raise ValueError(msg)
        if not single and not batch:
            msg = "provide old_text/new_text (single edit) or edits (batch)"
            raise ValueError(msg)
        if single and self.new_text is None:
            msg = "new_text is required when old_text is set"
            raise ValueError(msg)
        return self

    def normalized_hunks(self) -> tuple[EditHunk, ...]:
        """Return the edit hunks for either mode as one ordered tuple.

        Returns:
            The ``edits`` tuple in batch mode, or a single-element tuple
            built from ``old_text``/``new_text``/``replace_all`` in
            single-edit mode. The mode validator guarantees exactly one is
            populated, so ``new_text`` is non-``None`` in the single-edit
            branch.
        """
        if self.edits:
            return self.edits
        return (
            EditHunk(
                old_text=self.old_text or "",
                new_text=self.new_text or "",
                replace_all=self.replace_all,
            ),
        )


class DeleteFileArgs(BaseModel):
    """Args for ``delete_file``."""

    model_config = _ARGS_CONFIG

    path: NotBlankStr = Field(description="File path relative to workspace")


class ListDirectoryArgs(BaseModel):
    """Args for ``list_directory``.

    ``path`` defaults to the workspace root (``"."``).  Glob safety
    checks (``**`` requires ``recursive``, no absolute paths, no
    parent-directory traversal) stay inside the tool body because the
    error messages reference the workspace policy.
    """

    model_config = _ARGS_CONFIG

    path: NotBlankStr = Field(
        default=".",
        description='Directory path relative to workspace (default ".")',
    )
    pattern: NotBlankStr | None = Field(
        default=None,
        description='Glob filter (e.g. "*.py")',
    )
    recursive: bool = Field(
        default=False,
        description="Recursive listing",
    )


__all__ = [
    "DeleteFileArgs",
    "EditFileArgs",
    "EditHunk",
    "ListDirectoryArgs",
    "ReadFileArgs",
    "WriteFileArgs",
]
