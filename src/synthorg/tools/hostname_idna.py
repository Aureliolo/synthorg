# module-kind: code
"""IDNA canonicalisation for hostnames crossing the SSRF guard.

``urlparse`` hands back whatever spelling the URL used, so an
internationalised host stays a U-label through the allowlist comparison
while the resolver separately looks up the A-label.  Canonicalising here
means the guard compares, logs and resolves one spelling, and that a label
claiming to be Punycode is the canonical encoding of the name it claims to
encode.

Canonicalisation is decided and applied per LABEL, never over the joined
hostname.  IDNA validates a whole domain as a unit, so encoding the joined
string lets one label veto its siblings: ``my_service.xn--mnchen-3ya.de``
is a legitimate internal service under an internationalised domain, and
handing that whole string to :func:`idna.encode` rejects it for the
underscore in a label that needed no canonicalisation at all.
"""

from typing import Final

import idna

_A_LABEL_PREFIX: Final[str] = "xn--"

_UNNAMED_FAILURE: Final[str] = "invalid_hostname"


def _label_needs_canonicalisation(label: str) -> bool:
    """Report whether IDNA decides anything for a single *label*.

    A pure-ASCII label carrying no A-label prefix is left alone.  IDNA
    validates against the RFC 5892 codepoint classes, which reject
    characters DNS and real deployments use freely: the underscore in an
    internal service name, and the colons of an IPv6 literal.  Running
    those through IDNA would refuse hosts the guard has always accepted.
    What is left is a non-ASCII label, whose A-label the resolver will use
    in place of what the URL spelled, and a label already claiming to be an
    A-label, whose Punycode may not be canonical.

    The prefix test is case-insensitive because an uppercase ``XN--`` is
    the same claim as a lowercase one, and skipping it would let an
    unvalidated A-label through on nothing but its spelling.

    Args:
        label: One dot-separated component of a hostname.

    Returns:
        ``True`` when *label* must go through IDNA.
    """
    if not label.isascii():
        return True
    return label.lower().startswith(_A_LABEL_PREFIX)


def needs_canonicalisation(hostname: str) -> bool:
    """Report whether any label of *hostname* must go through IDNA.

    Args:
        hostname: Host portion of a URL.

    Returns:
        ``True`` when at least one label needs canonicalising.
    """
    return any(_label_needs_canonicalisation(label) for label in hostname.split("."))


def canonical_hostname(hostname: str) -> str:
    """Return *hostname* with every label in its canonical A-label form.

    Labels that need nothing are copied through byte for byte, so a
    hostname mixing an internal underscore label with an internationalised
    one keeps the first and canonicalises the second.

    Args:
        hostname: Host portion of a URL.

    Returns:
        The canonical spelling, or *hostname* unchanged when IDNA has no
        say over any of its labels.

    Raises:
        idna.IDNAError: When a label that needs canonicalising is not a
            valid internationalised label, or when an interior label is
            empty.
    """
    if not needs_canonicalisation(hostname):
        return hostname
    labels = hostname.split(".")
    last_index = len(labels) - 1
    canonical: list[str] = []
    for index, label in enumerate(labels):
        if not label:
            # A trailing dot spells an absolute FQDN and is legitimate;
            # an empty label anywhere else names nothing.
            if index == last_index:
                canonical.append(label)
                continue
            msg = "Empty label"
            raise idna.IDNAError(msg, code="empty_label")
        if _label_needs_canonicalisation(label):
            canonical.append(idna.encode(label, uts46=True).decode("ascii"))
        else:
            canonical.append(label)
    return ".".join(canonical)


def describe_idna_failure(exc: idna.IDNAError) -> str:
    """Render *exc* as a short refusal reason naming the rule that failed.

    The exception's ``code`` is a stable identifier for the rule, unlike
    its message, which the library documents as free to change between
    releases.  ``position`` is included when the failure is attributable to
    one character.  Neither carries the offending text, which lives on the
    ``text`` and ``codepoint`` attributes this deliberately never reads.

    Args:
        exc: The failure raised by :func:`canonical_hostname`.

    Returns:
        A short, non-sensitive description of the failed rule.
    """
    code = exc.code if exc.code is not None else _UNNAMED_FAILURE
    if exc.position is None:
        return code
    return f"{code} at position {exc.position}"
