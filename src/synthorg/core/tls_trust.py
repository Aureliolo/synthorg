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

import ssl
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
#: Bumped on every install so a client that cached a connection built under
#: an older snapshot can tell. Without it a long-lived client keeps the TLS
#: configuration it was constructed with, and a verify-off -> verify-on write
#: would leave exactly the traffic that most needs verifying still skipping
#: it. Comparing a value beats comparing the model: two equal-valued
#: snapshots are genuinely interchangeable, and an int is cheap to hold.
_revision: int = 0


def set_tls_trust(trust: TlsTrust) -> None:
    """Install *trust* as what every later outbound call uses.

    Args:
        trust: The resolved configuration.
    """
    global _current, _revision  # noqa: PLW0603 -- process-wide snapshot, see module docstring
    _current = trust
    _revision += 1


def current_tls_trust() -> TlsTrust:
    """Return the installed trust configuration.

    Returns:
        The current snapshot, defaulting to system trust with verification
        on when nothing has been installed.
    """
    return _current


def trust_revision() -> int:
    """Return the generation of the installed trust configuration.

    A client that caches a connection records this alongside it and rebuilds
    when it no longer matches, which is what makes the settings live for
    already-constructed clients rather than only for the next one.

    Returns:
        A counter incremented on every :func:`set_tls_trust`.
    """
    return _revision


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


def httpx_verify() -> ssl.SSLContext | bool:
    """Render the trust configuration as httpx's ``verify`` argument.

    A context rather than a path, for two reasons that point the same way.
    Passing ``verify="<path>"`` REPLACES the trust store with that one file,
    which contradicts the additive policy this module documents: an operator
    naming their internal CA would silently stop trusting every public root.
    Loading the bundle into a default context adds it instead. It also
    avoids the string form httpx deprecated.

    Returns:
        ``False`` when verification is off, else a context trusting the
        system roots plus any configured bundle.
    """
    trust = _current
    if not trust.verify:
        return False
    context = ssl.create_default_context()
    if trust.ca_bundle:
        context.load_verify_locations(cafile=trust.ca_bundle)
    return context


__all__ = [
    "GIT_CA_BUNDLE_KEY",
    "GIT_VERIFY_KEY",
    "TlsTrust",
    "current_tls_trust",
    "git_tls_config",
    "httpx_verify",
    "set_tls_trust",
    "trust_revision",
]
