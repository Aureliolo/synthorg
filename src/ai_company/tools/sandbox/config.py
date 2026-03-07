"""Subprocess sandbox configuration model."""

from pathlib import PurePath

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SubprocessSandboxConfig(BaseModel):
    """Configuration for the subprocess sandbox backend.

    Attributes:
        timeout_seconds: Default command timeout in seconds.
        workspace_only: When enabled, rejects commands whose working
            directory falls outside the workspace boundary.
        restricted_path: When enabled, filters PATH entries to retain
            only known safe system directories.
        env_allowlist: Environment variable names allowed to pass through.
            Supports ``LC_*`` as a glob for all locale variables.
        env_denylist_patterns: fnmatch patterns to strip even if in
            the allowlist (e.g. ``*KEY*`` catches ``API_KEY``).
            Includes secret-name heuristics and library injection vars.
        extra_safe_path_prefixes: Additional non-empty absolute PATH
            prefixes appended to platform defaults for the PATH filter.
    """

    model_config = ConfigDict(frozen=True)

    timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=600,
    )
    workspace_only: bool = True
    restricted_path: bool = True
    env_allowlist: tuple[str, ...] = (
        "HOME",
        "PATH",
        "USER",
        "LANG",
        "LC_*",
        "TERM",
        "TZ",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
    )
    env_denylist_patterns: tuple[str, ...] = (
        "*KEY*",
        "*SECRET*",
        "*TOKEN*",
        "*PASSWORD*",
        "*CREDENTIAL*",
        "*PRIVATE*",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONPATH",
        "NODE_PATH",
        "RUBYLIB",
        "PERL5LIB",
    )
    extra_safe_path_prefixes: tuple[str, ...] = ()
    """Additional safe PATH prefixes appended to platform defaults.

    Use this to allow tool-specific directories (e.g. a custom Git
    install location) through the PATH filter without modifying the
    built-in platform defaults.
    """

    @field_validator("extra_safe_path_prefixes")
    @classmethod
    def _validate_prefixes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for prefix in v:
            if not prefix or not PurePath(prefix).is_absolute():
                msg = (
                    "extra_safe_path_prefixes entries must be "
                    f"non-empty absolute paths, got: {prefix!r}"
                )
                raise ValueError(msg)
        return v
