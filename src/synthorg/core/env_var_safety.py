"""Safety screening for environment-variable names in credential injection.

A ``credential_env_map`` directs a connection secret's value into a named
environment variable of a spawned MCP server, so the map's *values* are the
env-var names the credential lands under. An untrusted (catalog-authored or
YAML) entry could therefore target a process- or loader-control variable --
``LD_PRELOAD`` injects a shared library, ``NODE_OPTIONS`` injects Node flags,
``PATH`` hijacks binary resolution -- turning a benign-looking secret into
arbitrary code execution or a denial of service inside the sandbox. This screens
those names before they can reach the child environment.
"""

import re
from typing import Final

# A POSIX-ish environment variable name: a letter or underscore followed by
# letters, digits, or underscores. Rejects names carrying shell metacharacters,
# whitespace, ``=``, or an empty string.
_ENV_VAR_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Loader / interpreter / shell control variables whose *value* steers process
# execution rather than being consumed as application data. A credential must
# never be injected under one of these names. Secret-named vars (``*_TOKEN`` /
# ``*_API_KEY`` / ...) are the whole point of credential injection and are
# intentionally NOT screened here. Matched case-insensitively so a look-alike
# (``ld_preload``) is rejected too.
DANGEROUS_ENV_VAR_NAMES: Final[frozenset[str]] = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "NODE_OPTIONS",
        "NODE_PATH",
        "PATH",
        "RUBYLIB",
        "PERL5LIB",
        "BASH_ENV",
        "ENV",
        "IFS",
        "PROMPT_COMMAND",
    }
)


def validate_credential_env_var_name(name: str) -> None:
    """Reject an unsafe target env-var name for credential injection.

    Args:
        name: The environment-variable name a credential would be injected
            under.

    Raises:
        ValueError: If ``name`` is not a valid identifier, or is a known
            process/loader-control variable.
    """
    if not _ENV_VAR_NAME_RE.fullmatch(name):
        msg = f"invalid environment variable name: {name!r}"
        raise ValueError(msg)
    if name.upper() in DANGEROUS_ENV_VAR_NAMES:
        msg = (
            f"environment variable {name!r} controls process/loader behaviour "
            f"and must not receive an injected credential"
        )
        raise ValueError(msg)
