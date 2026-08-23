"""IDNA canonicalisation for hostnames crossing the SSRF guard.

``urlparse`` hands back whatever spelling the URL used, so an
internationalised host stays a U-label all the way through the allowlist
comparison and into the ``Host`` header, while the resolver quietly looks up
the A-label instead.  Canonicalising here means the guard compares, logs, and
resolves one spelling, and that a label claiming to be Punycode is actually
the canonical encoding of the name it claims to encode.
"""

from typing import Final

import idna

_A_LABEL_PREFIX: Final[str] = "xn--"

_UNNAMED_FAILURE: Final[str] = "invalid_hostname"


def needs_canonicalization(hostname: str) -> bool:
    """Report whether IDNA processing decides anything for *hostname*.

    A pure-ASCII host carrying no A-label is left alone.  UTS #46 applies
    STD3 rules, which reject characters that DNS and real deployments use
    freely: the underscore in an internal service name, and the colons of an
    IPv6 literal.  Running those through IDNA would refuse hosts the guard
    has always accepted.  What is left is a non-ASCII host, whose A-label the
    resolver will use in place of what the URL spelled, and a host already
    claiming to be an A-label, whose Punycode may not be canonical.

    Args:
        hostname: Host portion of a URL, already lowercased.

    Returns:
        ``True`` when *hostname* must go through IDNA.
    """
    if not hostname.isascii():
        return True
    return any(label.startswith(_A_LABEL_PREFIX) for label in hostname.split("."))


def canonical_hostname(hostname: str) -> str:
    """Return *hostname* in its canonical IDNA A-label form.

    Args:
        hostname: Host portion of a URL, already lowercased.

    Returns:
        The A-label spelling, or *hostname* unchanged when IDNA has no say
        over it.

    Raises:
        idna.IDNAError: When *hostname* is not a valid internationalised
            domain name.
    """
    if not needs_canonicalization(hostname):
        return hostname
    return idna.encode(hostname, uts46=True).decode("ascii")


def describe_idna_failure(exc: idna.IDNAError) -> str:
    """Render *exc* as a short refusal reason naming the rule that failed.

    The exception's ``code`` is a stable identifier for the rule, unlike its
    message, which the library documents as free to change between releases.
    ``position`` is included when the failure is attributable to one
    character, so an operator reading the log can find it without decoding
    the message.  Neither carries the offending text itself.

    Args:
        exc: The failure raised by :func:`canonical_hostname`.

    Returns:
        A short, non-sensitive description of the failed rule.
    """
    code = exc.code or _UNNAMED_FAILURE
    if exc.position is None:
        return code
    return f"{code} at position {exc.position}"
