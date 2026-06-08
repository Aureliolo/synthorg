"""Environment- and PATH-filtering mixin for the subprocess sandbox.

Holds the allowlist/denylist matching, PATH hardening (with safe-prefix
boundary checks and a hardcoded fallback), and declaration-env screening that
``SubprocessSandbox`` composes via inheritance. Kept as a mixin (rather than
free functions) so the existing ``sandbox._build_filtered_env()`` call sites,
the ``SubprocessSandbox._is_safe_path_entry(...)`` class reference, and the
``patch.object(SubprocessSandbox, ...)`` test seams keep resolving via the MRO.
"""

import fnmatch
import os
from collections.abc import Mapping
from pathlib import Path

from synthorg.observability import get_logger
from synthorg.observability.events.sandbox import (
    SANDBOX_ENV_FILTERED,
    SANDBOX_PATH_FALLBACK,
)
from synthorg.tools.sandbox._subprocess_proc import _get_platform_default_dirs
from synthorg.tools.sandbox.config import SubprocessSandboxConfig
from synthorg.tools.sandbox.errors import SandboxError

logger = get_logger(__name__)

_PATH_SEP = ";" if os.name == "nt" else ":"


class _EnvFilterMixin:
    """Environment-variable and PATH filtering for ``SubprocessSandbox``.

    Expects the host class to provide ``_config``; every method here is
    stateless apart from that one collaborator.
    """

    _config: SubprocessSandboxConfig

    def _matches_allowlist(self, name: str) -> bool:
        """Check if an env var name matches any entry in the allowlist.

        Uses case-insensitive matching on Windows where env var names
        are case-insensitive.

        Returns:
            ``True`` if *name* matches any allowlist entry, else ``False``.
        """
        check_name = name.upper() if os.name == "nt" else name
        for pattern in self._config.env_allowlist:
            check_pattern = pattern.upper() if os.name == "nt" else pattern
            if fnmatch.fnmatch(check_name, check_pattern):
                return True
        return False

    def _matches_denylist(self, name: str) -> bool:
        """Check if an env var name matches any denylist pattern.

        Both name and patterns are uppercased for case-insensitive
        matching -- denylist patterns must catch secrets regardless of
        casing.

        Returns:
            ``True`` if *name* matches any denylist pattern, else ``False``.
        """
        upper = name.upper()
        return any(
            fnmatch.fnmatch(upper, pat.upper())
            for pat in self._config.env_denylist_patterns
        )

    def _filter_path(self, path_value: str) -> str:
        """Filter PATH entries, keeping only safe system directories.

        Uses directory-boundary checking to prevent prefix spoofing
        (e.g. ``/usr/bin-malicious`` is rejected even though it starts
        with ``/usr/bin``).  Entries are normalized before comparison.

        When no entries survive filtering, falls back to known safe
        directories that actually exist on the system.

        Returns:
            Result of type ``str``.

        Raises:
            SandboxError: If the related operation fails.
        """
        safe_prefixes = self._get_safe_path_prefixes()
        entries = path_value.split(_PATH_SEP)
        filtered = [e for e in entries if self._is_safe_path_entry(e, safe_prefixes)]
        if filtered:
            return _PATH_SEP.join(filtered)
        logger.warning(
            SANDBOX_PATH_FALLBACK,
            reason="no PATH entries matched safe prefixes; using safe defaults",
            original_entry_count=len(entries),
        )
        # Fallback uses fully hardcoded directories -- no os.environ reads,
        # no user-provided extra_safe_path_prefixes -- so that the
        # Path.is_dir() filesystem probe receives only compile-time
        # constants (CodeQL py/path-injection).
        fallback_dirs = self._get_hardcoded_fallback_dirs()
        safe_dirs = [p for p in fallback_dirs if Path(p).is_dir()]
        if not safe_dirs:
            logger.error(
                SANDBOX_PATH_FALLBACK,
                reason="no safe PATH directories exist on system",
            )
            msg = (
                "No safe PATH directories found on system; "
                "cannot create safe sandbox environment"
            )
            raise SandboxError(msg)
        return _PATH_SEP.join(safe_dirs)

    @staticmethod
    def _is_safe_path_entry(
        entry: str,
        safe_prefixes: tuple[str, ...],
    ) -> bool:
        """Check if a PATH entry falls within a safe prefix directory.

        Rejects null-byte entries, then uses directory-boundary
        matching to prevent prefix spoofing (e.g. ``/usr/bin-malicious``
        does not match ``/usr/bin``).

        Returns:
            ``True`` when the predicate holds, ``False`` otherwise.
        """
        if "\x00" in entry:
            return False
        entry_norm = os.path.normcase(os.path.normpath(entry))
        for prefix in safe_prefixes:
            prefix_norm = os.path.normcase(os.path.normpath(prefix))
            if entry_norm == prefix_norm or entry_norm.startswith(
                prefix_norm + os.sep,
            ):
                return True
        return False

    @staticmethod
    def _get_hardcoded_fallback_dirs() -> tuple[str, ...]:
        """Return fully hardcoded safe PATH directories for fallback.

        Unlike ``_get_platform_default_dirs``, this reads **no**
        environment variables -- every value is a compile-time constant.
        Used only in the fallback branch of ``_filter_path`` where
        ``Path.is_dir()`` probes the filesystem, so that no
        ``os.environ`` data reaches a filesystem call
        (CodeQL ``py/path-injection``).

        Returns:
            Tuple of ``str``.
        """
        if os.name == "nt":
            return (
                r"C:\WINDOWS",
                r"C:\WINDOWS\system32",
                r"C:\Program Files\Git",
                r"C:\Program Files (x86)\Git",
            )
        return ("/usr/bin", "/usr/local/bin", "/bin", "/usr/sbin", "/sbin")

    def _get_safe_path_prefixes(self) -> tuple[str, ...]:
        """Return safe PATH prefixes for the current platform.

        Combines built-in platform defaults with any extra prefixes
        from ``SubprocessSandboxConfig.extra_safe_path_prefixes``.

        Returns:
            Tuple of ``str``.
        """
        return _get_platform_default_dirs() + self._config.extra_safe_path_prefixes

    def _screen_declaration_env(
        self,
        env_additions: Mapping[str, str],
    ) -> dict[str, str]:
        """Drop denylisted keys from declaration-sourced env additions.

        The per-project environment declaration is committed code, but
        unlike the trusted internal overrides (git hardening vars) it is
        screened through the secret denylist so a declared
        secret-pattern variable cannot bypass the filter the inherited
        host environment is subject to. Dropped keys are logged; PATH and
        other allowed toolchain vars pass through.

        Returns:
            Mapping from ``str`` to ``str``.
        """
        screened: dict[str, str] = {}
        dropped: list[str] = []
        for name, value in env_additions.items():
            if self._matches_denylist(name):
                dropped.append(name)
            else:
                screened[name] = value
        if dropped:
            logger.warning(
                SANDBOX_ENV_FILTERED,
                source="declaration",
                dropped_count=len(dropped),
                dropped_keys=sorted(dropped),
            )
        return screened

    def _build_filtered_env(
        self,
        env_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Build a filtered environment for the subprocess.

        Starts with an empty dict, copies allowed vars from the current
        process environment, strips denylist matches, optionally filters
        PATH, then applies overrides.

        Note: ``env_overrides`` bypass the denylist by design -- they
        are trusted internal overrides (e.g. git hardening vars).
        Callers must not pass untrusted user-controlled data as
        overrides.

        Args:
            env_overrides: Trusted internal vars applied on top.

        Returns:
            The filtered environment mapping.
        """
        env: dict[str, str] = {}
        filtered_count = 0

        for name, value in os.environ.items():
            allowed = self._matches_allowlist(name)
            denied = self._matches_denylist(name)
            if allowed and not denied:
                env[name] = value
            else:
                filtered_count += 1

        # Case-insensitive key check on Windows where env var names
        # are case-insensitive (e.g. "Path" vs "PATH").
        if self._config.restricted_path and any(k.upper() == "PATH" for k in env):
            path_keys = [k for k in env if k.upper() == "PATH"]
            path_val = next(
                (env[k] for k in reversed(path_keys)),
                "",
            )
            for k in path_keys:
                del env[k]
            env["PATH"] = self._filter_path(path_val)

        if env_overrides:
            env.update(env_overrides)
            # Re-filter PATH if overrides injected one -- prevents
            # bypassing the restricted-path guard via env_overrides.
            # Case-insensitive key check on Windows where env var
            # names are case-insensitive (e.g. "Path" vs "PATH").
            if self._config.restricted_path and any(
                k.upper() == "PATH" for k in env_overrides
            ):
                # Consolidate to a canonical PATH key.
                path_keys = [k for k in env if k.upper() == "PATH"]
                path_val = next(
                    (env[k] for k in reversed(path_keys)),
                    "",
                )
                for k in path_keys:
                    del env[k]
                env["PATH"] = self._filter_path(path_val)

        logger.debug(
            SANDBOX_ENV_FILTERED,
            filtered_count=filtered_count,
            kept_count=len(env),
        )
        return env
