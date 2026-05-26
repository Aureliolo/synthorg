"""Resolve a brownfield import source into a fetch-ready reference.

Owns the auth + SSRF concerns so the git backend's :meth:`seed` stays
auth-agnostic. A local path is validated as a readable directory; a
remote URL is scheme-checked and SSRF-validated, its host matched against
a configured forge connection (whose token is injected for HTTPS), and
DNS-pinned via ``http.curloptResolve`` to close the TOCTOU gap. Private
remotes with no matching connection fetch anonymously and fail at fetch
time (surfaced as an unavailable source).
"""

from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import quote, urlsplit, urlunsplit

from synthorg.core.types import NotBlankStr
from synthorg.engine.brownfield.errors import BrownfieldSourceUnavailableError
from synthorg.engine.workspace.git_backend.protocol import (
    ResolvedSource,
    SourceKind,
)
from synthorg.observability import get_logger
from synthorg.tools.git_url_validator import (
    DnsValidationOk,
    GitCloneNetworkPolicy,
    build_curl_resolve_value,
    is_allowed_clone_scheme,
    validate_clone_url_host,
)

if TYPE_CHECKING:
    from synthorg.integrations.connections.catalog import ConnectionCatalog

logger = get_logger(__name__)

_REMOTE_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https", "ssh", "git"})
_TOKEN_USER: Final[str] = "x-access-token"  # noqa: S105 -- username, not a secret
_DEFAULT_HTTPS_PORT: Final[int] = 443


class BrownfieldSourceResolver:
    """Resolves an import source reference into a :class:`ResolvedSource`."""

    def __init__(
        self,
        *,
        connection_catalog: ConnectionCatalog | None = None,
        network_policy: GitCloneNetworkPolicy | None = None,
    ) -> None:
        self._catalog = connection_catalog
        self._network_policy = (
            network_policy if network_policy is not None else GitCloneNetworkPolicy()
        )

    async def resolve(self, source_ref: NotBlankStr) -> ResolvedSource:
        """Resolve *source_ref* (local path or remote URL) for fetching.

        Returns:
            A :class:`ResolvedSource` carrying the fetch URL and
            source-kind discriminator (and any token / pinning args
            for remote HTTPS sources).

        Raises:
            BrownfieldSourceUnavailableError: The source is an unreadable
                local path or a disallowed / SSRF-blocked remote URL.
        """
        if self._is_remote(source_ref):
            return await self._resolve_remote(source_ref)
        return self._resolve_local(source_ref)

    @staticmethod
    def _is_remote(source_ref: str) -> bool:
        """Classify *source_ref* as a remote URL vs a local path.

        Args:
            source_ref: The operator-supplied source reference.

        Returns:
            ``True`` for a remote URL (known scheme or scp-like
            ``user@host:path``); ``False`` for a local / ``file://`` path.
        """
        if source_ref.startswith("file://"):
            return False
        scheme = urlsplit(source_ref).scheme
        if scheme in _REMOTE_SCHEMES:
            return True
        # scp-like ``user@host:path`` (ssh) has no urlsplit scheme.
        before_slash = source_ref.split("/", 1)[0]
        return "@" in before_slash and ":" in before_slash

    def _resolve_local(self, source_ref: str) -> ResolvedSource:
        """Resolve a local-path source into a fetch-ready reference.

        Args:
            source_ref: A local path, optionally ``file://``-prefixed.

        Returns:
            A :class:`ResolvedSource` with ``LOCAL_PATH`` kind.

        Raises:
            BrownfieldSourceUnavailableError: The path is not a readable
                directory.
        """
        raw = source_ref.removeprefix("file://")
        path = Path(raw)
        if not path.is_dir():
            msg = f"brownfield local source {source_ref!r} is not a readable directory"
            raise BrownfieldSourceUnavailableError(msg)
        return ResolvedSource(
            fetch_url=NotBlankStr(str(path)),
            source_kind=SourceKind.LOCAL_PATH,
        )

    async def _resolve_remote(self, source_ref: str) -> ResolvedSource:
        """Resolve a remote-URL source, applying auth and SSRF guards.

        Validates the scheme, rejects URL-embedded credentials, SSRF-checks
        and DNS-pins the host, and injects a matching forge token for HTTPS.

        Args:
            source_ref: The remote source URL.

        Returns:
            A :class:`ResolvedSource` with ``REMOTE`` kind, the (possibly
            token-injected) fetch URL, and DNS-pinning args.

        Raises:
            BrownfieldSourceUnavailableError: Disallowed scheme,
                URL-embedded credentials, or a blocked / unresolvable host.
        """
        if not is_allowed_clone_scheme(source_ref):
            msg = (
                f"brownfield remote source {source_ref!r} uses a disallowed "
                "scheme (only https:// and ssh:// are permitted)"
            )
            raise BrownfieldSourceUnavailableError(msg)
        split = urlsplit(source_ref)
        if split.password is not None or (
            split.scheme in {"http", "https"} and split.username is not None
        ):
            # A credential embedded in the URL would be logged and persisted
            # in the structure map's source_ref. Forge tokens must come from
            # the connection catalog (injected transiently into the fetch
            # URL), never from the operator-supplied source reference. The
            # bare ``git@host`` username of an ssh:// ref is not a credential,
            # so SSH usernames stay allowed.
            msg = (
                "brownfield remote source must not embed credentials in the "
                "URL; register a forge connection instead"
            )
            raise BrownfieldSourceUnavailableError(msg)
        validation = await validate_clone_url_host(source_ref, self._network_policy)
        if isinstance(validation, str):
            raise BrownfieldSourceUnavailableError(validation)
        fetch_url = await self._maybe_inject_token(source_ref, validation)
        return ResolvedSource(
            fetch_url=NotBlankStr(fetch_url),
            source_kind=SourceKind.REMOTE,
            pre_fetch_config_args=self._pin_args(validation),
        )

    @staticmethod
    def _pin_args(validation: DnsValidationOk) -> tuple[str, ...]:
        """Build ``git -c http.curloptResolve`` pinning args for HTTPS.

        Returns:
            ``("-c", "http.curloptResolve=...")`` when the validation
            is HTTPS with resolved IPs to pin; ``()`` otherwise.
        """
        if not validation.is_https or not validation.resolved_ips:
            return ()
        resolve_value = build_curl_resolve_value(
            validation.hostname,
            validation.port or _DEFAULT_HTTPS_PORT,
            validation.resolved_ips,
        )
        return ("-c", f"http.curloptResolve={resolve_value}")

    async def _maybe_inject_token(
        self, source_ref: str, validation: DnsValidationOk
    ) -> str:
        """Inject a forge token into HTTPS userinfo when the host matches.

        Returns:
            The original ``source_ref`` when no catalog / matching
            token is wired (or the URL is not HTTPS); otherwise a
            URL string with the token injected into userinfo.
        """
        if not validation.is_https or self._catalog is None:
            return source_ref
        token = await self._token_for_host(validation.hostname)
        if token is None:
            return source_ref
        split = urlsplit(source_ref)
        host = split.hostname or ""
        # ``urlsplit.hostname`` strips IPv6 brackets, so wrap them back
        # before composing netloc; otherwise the embedded ``:`` would be
        # misparsed as the host:port separator and break urlunsplit.
        host_for_netloc = f"[{host}]" if ":" in host else host
        netloc = f"{_TOKEN_USER}:{quote(token, safe='')}@{host_for_netloc}"
        if split.port is not None:
            netloc = f"{netloc}:{split.port}"
        return urlunsplit(
            (split.scheme, netloc, split.path, split.query, split.fragment)
        )

    async def _token_for_host(self, hostname: str) -> str | None:
        """Return the token of the first connection whose host matches."""
        if self._catalog is None:
            return None
        connections = await self._catalog.list_all()
        for connection in connections:
            if not connection.base_url:
                continue
            conn_host = urlsplit(str(connection.base_url)).hostname
            if conn_host is not None and conn_host.casefold() == hostname.casefold():
                credentials = await self._catalog.get_credentials(connection.name)
                token = credentials.get("token")
                return token or None
        return None


__all__ = ["BrownfieldSourceResolver"]
