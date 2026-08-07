# module-kind: code
"""Outbound TLS trust, shared by every client the product dials out with.

Two transports reach the same hosts and neither reads the other's
configuration: git subprocesses (the workspace backends, the docs engine,
the agent git tools) and the httpx clients (forge, chat, deploy, health,
A2A). The hardening in :mod:`synthorg.core.git_env` cuts the host's own git
configuration out of the first, which is what stops an operator's
``insteadOf`` rewrite redirecting a clone, and it necessarily takes the
host's TLS trust with it. Nothing replaced that, so a forge behind an
internal CA became unreachable from the workspace path while remaining
reachable from a shell on the same machine.

So trust is configured here instead, once, and both transports read it:

* ``security.tls_ca_bundle`` adds a CA bundle to what is already trusted.
  Additional, not replacing: a private CA is normally one issuer alongside
  the public roots, and an operator who names theirs should not silently
  stop trusting everything else.
* ``security.tls_verify`` turns verification off outright. It exists
  because self-signed hosts are real and an operator will otherwise reach
  for something worse, but it is a security-weakening write and routes
  through the confirm+reason+actor guardrail like the other posture
  toggles.

The value is a process-wide snapshot rather than a per-call resolver read
because ``_sanitised_env`` builds a child environment synchronously, deep
inside a subprocess call with no resolver in reach. The settings
subscriber replaces the snapshot on every write, so it is as live as a
resolver read at the only granularity that matters here: the next command.
"""

from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict

#: git spells "trust this bundle as well" as a path and "do not verify" as a
#: boolean, both under ``http.*``. Held as constants so the subscriber, the
#: gate and the tests name the same keys.
GIT_CA_BUNDLE_KEY: Final[str] = "http.sslCAInfo"
GIT_VERIFY_KEY: Final[str] = "http.sslVerify"


class TlsTrust(BaseModel):
    """What every outbound client should trust.

    Attributes:
        ca_bundle: Path to an additional CA bundle, or blank for the
            system trust store alone.
        verify: Whether certificates are verified at all. ``False`` is the
            deliberate insecure escape hatch.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    ca_bundle: str = ""
    verify: bool = True


_DEFAULT: Final[TlsTrust] = TlsTrust()

_current: TlsTrust = _DEFAULT


def set_tls_trust(trust: TlsTrust) -> None:
    """Install *trust* as what every later outbound call uses.

    Args:
        trust: The resolved configuration.
    """
    global _current  # noqa: PLW0603 -- process-wide snapshot, see module docstring
    _current = trust


def current_tls_trust() -> TlsTrust:
    """Return the installed trust configuration.

    Returns:
        The current snapshot, defaulting to system trust with verification
        on when nothing has been installed.
    """
    return _current


def git_tls_config() -> MappingProxyType[str, str]:
    """Render the trust configuration as git config for one invocation.

    Empty when nothing is configured, so an ordinary deployment adds no
    keys at all and git falls through to its own defaults.

    Returns:
        The ``http.*`` keys to merge into a git invocation's config.
    """
    rendered: dict[str, str] = {}
    trust = _current
    if trust.ca_bundle:
        rendered[GIT_CA_BUNDLE_KEY] = trust.ca_bundle
    if not trust.verify:
        rendered[GIT_VERIFY_KEY] = "false"
    return MappingProxyType(rendered)


def httpx_verify() -> str | bool:
    """Render the trust configuration as httpx's ``verify`` argument.

    Returns:
        ``False`` when verification is off, the bundle path when one is
        configured, else ``True`` for the system trust store.
    """
    trust = _current
    if not trust.verify:
        return False
    return trust.ca_bundle or True


__all__ = [
    "GIT_CA_BUNDLE_KEY",
    "GIT_VERIFY_KEY",
    "TlsTrust",
    "current_tls_trust",
    "git_tls_config",
    "httpx_verify",
    "set_tls_trust",
]
