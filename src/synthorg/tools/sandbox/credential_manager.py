"""Sandbox credential manager -- prevent credentials from entering containers.

Audits environment variable overrides to strip credential-like keys
before they enter sandbox containers.  Provides a sanitization API
and a reporting variant that lists which keys were stripped.
"""

import re
from collections.abc import Mapping
from typing import ClassVar, Final

from synthorg.observability import get_logger
from synthorg.observability.events.sandbox import SANDBOX_CREDENTIAL_STRIPPED

logger = get_logger(__name__)

_CREDENTIAL_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)token"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)credential"),
    re.compile(r"(?i)private[_-]?key"),
    re.compile(r"(?i)access[_-]?key"),
    re.compile(r"(?i)signing[_-]?key"),
    re.compile(r"(?i)encryption[_-]?key"),
    re.compile(r"(?i)passphrase"),
    re.compile(r"(?i)connection[_-]?string"),
    re.compile(r"(?i)database[_-]?url"),
    re.compile(r"(?i)\bauth\b"),
    re.compile(r"(?i)\bdsn\b"),
)


class SandboxCredentialManager:
    """Prevent credentials from entering sandbox environments.

    Strips environment variables whose names match common credential
    patterns (API keys, secrets, tokens, passwords, credentials,
    private keys) from sandbox env overrides.

    The matching is case-insensitive and uses substring matching;
    any key containing a credential pattern is stripped.
    """

    CREDENTIAL_PATTERNS: ClassVar[tuple[re.Pattern[str], ...]] = _CREDENTIAL_PATTERNS

    def _is_credential_key(self, key: str) -> bool:
        """Check if an env var key matches a credential pattern.

        Args:
            key: Environment variable name.

        Returns:
            ``True`` if the key matches a credential pattern.
        """
        return any(p.search(key) for p in _CREDENTIAL_PATTERNS)

    def sanitize_env(
        self,
        env_overrides: Mapping[str, str],
    ) -> dict[str, str]:
        """Remove credential-like keys and return sanitized env.

        Args:
            env_overrides: Original environment variable mapping.

        Returns:
            New dict with credential-like keys removed.
        """
        result, stripped = self._do_sanitize(env_overrides)
        if stripped:
            logger.info(
                SANDBOX_CREDENTIAL_STRIPPED,
                stripped_count=len(stripped),
                stripped_keys=sorted(stripped),
            )
        return result

    def sanitize_env_with_report(
        self,
        env_overrides: Mapping[str, str],
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        """Remove credential-like keys and report which were stripped.

        Args:
            env_overrides: Original environment variable mapping.

        Returns:
            Tuple of (sanitized env dict, stripped key names).
        """
        result, stripped = self._do_sanitize(env_overrides)
        if stripped:
            logger.info(
                SANDBOX_CREDENTIAL_STRIPPED,
                stripped_count=len(stripped),
                stripped_keys=sorted(stripped),
            )
        return result, tuple(sorted(stripped))

    def _do_sanitize(
        self,
        env_overrides: Mapping[str, str],
    ) -> tuple[dict[str, str], set[str]]:
        """Core sanitization logic.

        Returns:
            Tuple of (sanitized dict, set of stripped key names).
        """
        result: dict[str, str] = {}
        stripped: set[str] = set()
        for key, value in env_overrides.items():
            if self._is_credential_key(key):
                stripped.add(key)
            else:
                result[key] = value
        return result, stripped
