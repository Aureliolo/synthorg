# module-kind: code
"""OTLP endpoint SSRF / transport-safety validation.

These security-sensitive checks live in a standalone module so they are
unit-testable in isolation. ``SinkConfig`` calls
:func:`validate_otlp_endpoint_safety` from its OTLP field validator.
"""


def is_private_ip(addr_str: str) -> bool:
    """Check whether an IP address string is private/loopback/link-local.

    Returns:
        ``True`` if *addr_str* parses as a private, loopback, or
        link-local IP; ``False`` for public IPs or unparseable strings.
    """
    import ipaddress  # noqa: PLC0415

    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local)


def validate_otlp_endpoint_safety(
    endpoint: str,
    hostname: str,
    *,
    has_headers: bool,
) -> None:
    """Reject private IPs (SSRF) and warn on unencrypted HTTP.

    Checks both IP literals and DNS-resolved addresses (best-effort).
    Localhost (127.0.0.1, ::1, ``localhost``) is always allowed as a
    standard local OTLP collector endpoint.

    Raises:
        ValueError: If the hostname is a non-localhost private/loopback
            IP literal, or resolves via DNS to a private/loopback
            address.
    """
    localhost_names = {"localhost", "127.0.0.1", "::1"}

    # Allow localhost/loopback -- standard for local collectors.
    if hostname in localhost_names:
        return

    # Direct IP literal check (non-localhost private IPs).
    if is_private_ip(hostname):
        msg = (
            f"otlp_endpoint must not target private/loopback IP addresses ({hostname})"
        )
        raise ValueError(msg)

    # DNS resolution check (best-effort). The private-IP literal case
    # already raised above, so *hostname* here is a name to resolve.
    import socket  # noqa: PLC0415

    try:
        addrs = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # DNS resolution failed -- skip the resolved-IP check (hostname may
        # be valid at runtime even if not resolvable at config-load time),
        # but fall through so the plaintext-HTTP warning below still fires.
        addrs = []
    for _family, _type, _proto, _canonname, sockaddr in addrs:
        resolved_ip = str(sockaddr[0])
        if is_private_ip(resolved_ip):
            msg = (
                f"otlp_endpoint hostname {hostname!r} resolves to "
                f"private/loopback address {resolved_ip}"
            )
            raise ValueError(msg)

    if endpoint.startswith("http://") and has_headers:
        import warnings  # noqa: PLC0415

        warnings.warn(
            "OTLP endpoint uses unencrypted HTTP with headers "
            "that may contain secrets; prefer https://",
            UserWarning,
            stacklevel=4,
        )
