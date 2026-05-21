"""Live clone/push/fetch for the external-remote git backend.

Exercises the real git subprocess path of ``ExternalRemoteGitBackend``
against a local HTTPS ``git http-backend`` server (a stand-in for a
GitHub/GitLab/Gitea/Forgejo personal repo), plus lazy forge-repo
provisioning on first push. The token-refresh leg is covered at the
manager level by ``tests/unit/integrations/oauth/test_token_manager``;
here the backend re-resolves the token from the catalog each call, so a
refreshed credential propagates without extra wiring.

TLS trust without touching production code: the backend deliberately
strips ``GIT_*`` env vars (``_git_subprocess``), so the test points
``HOME`` at a temp dir whose ``.gitconfig`` disables SSL verification.
``HOME`` is not a ``GIT_*`` var, so it survives into the subprocess.

POSIX + git + cryptography only; skipped otherwise (CI runs it on
Linux). Heavy by design; marked integration.
"""

import http.server
import os
import ssl
import subprocess
import threading
from pathlib import Path
from typing import Final, cast

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend import ExternalRemoteGitBackend
from synthorg.engine.workspace.git_backend.forge_api import ForgeApiClient, ForgeRepo
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from tests._shared import FakeClock, mock_of

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

_OWNER: Final[str] = "acme"


def _git_available() -> bool:
    try:
        subprocess.run(
            ["git", "--version"],  # noqa: S607
            check=True,
            capture_output=True,
        )
    except Exception:
        return False
    return True


skip_unsupported = pytest.mark.skipif(
    os.name == "nt" or not _git_available(),
    reason="requires POSIX + git (git http-backend); CI runs this on Linux",
)
pytest.importorskip("cryptography")


def _self_signed_cert(directory: Path) -> tuple[Path, Path]:
    """Generate a localhost self-signed cert; return (cert, key) paths."""
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _make_handler(server_root: Path) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a handler that proxies smart-HTTP requests to git http-backend."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        # HTTP/1.1 + an explicit Content-Length below give git's TLS
        # client a length-delimited response so it never relies on
        # connection-close framing. Close-delimited framing over Python's
        # ssl socket surfaces as "GnuTLS recv error (-110): the TLS
        # connection was non-properly terminated" because the socket
        # closes without a TLS close_notify.
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: object) -> None:
            pass

        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            path, _, query = self.path.partition("?")
            env = {
                **os.environ,
                "GIT_PROJECT_ROOT": str(server_root),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            }
            proc = subprocess.run(
                ["git", "http-backend"],  # noqa: S607
                input=body,
                capture_output=True,
                env=env,
                check=False,
            )
            header_blob, _, payload = proc.stdout.partition(b"\r\n\r\n")
            status = 200
            out_headers: list[tuple[str, str]] = []
            for line in header_blob.decode("latin-1").splitlines():
                key, _, value = line.partition(":")
                value = value.strip()
                if key.lower() == "status":
                    status = int(value.split()[0])
                elif key.lower() in (
                    "content-length",
                    "transfer-encoding",
                    "connection",
                ):
                    # Drop git http-backend's own framing; we set our own
                    # Content-Length so the response is length-delimited.
                    continue
                else:
                    out_headers.append((key, value))
            self.send_response(status)
            for key, value in out_headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._serve()

        def do_POST(self) -> None:
            self._serve()

    return _Handler


def _init_bare_empty(repo: Path) -> None:
    """Create an empty bare repo (what a forge's create endpoint yields)."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603
        ["git", "init", "--bare", "--initial-branch=main", str(repo)],  # noqa: S607
        check=True,
        capture_output=True,
    )


def _init_bare_with_commit(repo: Path) -> None:
    """Create a bare repo on the server with one commit on ``main``."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603
        ["git", "init", "--bare", "--initial-branch=main", str(repo)],  # noqa: S607
        check=True,
        capture_output=True,
    )
    seed = repo.parent / f"{repo.stem}-seed"
    subprocess.run(  # noqa: S603
        ["git", "clone", str(repo), str(seed)],  # noqa: S607
        check=True,
        capture_output=True,
    )
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid"}
    env["GIT_COMMITTER_NAME"] = "t"
    env["GIT_COMMITTER_EMAIL"] = "t@t.invalid"
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    for args in (
        ["git", "-C", str(seed), "add", "."],
        ["git", "-C", str(seed), "commit", "-m", "seed"],
        ["git", "-C", str(seed), "push", "origin", "main"],
    ):
        subprocess.run(args, check=True, capture_output=True, env=env)  # noqa: S603


@pytest.fixture
def _git_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at a temp .gitconfig that trusts the self-signed cert."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[http]\n\tsslVerify = false\n[user]\n"
        "\tname = SynthOrg\n\temail = bot@synthorg.local\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    return home


def _backend(port: int) -> tuple[ExternalRemoteGitBackend, ConnectionCatalog]:
    catalog = mock_of[ConnectionCatalog]()
    catalog.get.return_value = Connection(
        name=NotBlankStr("forge"),
        connection_type=ConnectionType.GITHUB,
        auth_method=AuthMethod.API_KEY,
        base_url=NotBlankStr(f"https://localhost:{port}/{_OWNER}"),
    )
    catalog.get_credentials.return_value = {"token": "x"}
    backend = ExternalRemoteGitBackend(
        connection_name="forge",
        connection_catalog=catalog,
        cmd_timeout=60.0,
        clock=FakeClock(),
    )
    return backend, catalog


@skip_unsupported
@pytest.mark.usefixtures("_git_home")
class TestExternalRemoteLive:
    async def test_clone_push_fetch_round_trip(
        self,
        tmp_path: Path,
    ) -> None:
        server_root = tmp_path / "srv"
        _init_bare_with_commit(server_root / _OWNER / "proj-1.git")
        cert, key = _self_signed_cert(tmp_path)
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        # Pin the floor to TLS 1.2 explicitly: ``create_default_context``
        # already does this at runtime, but the static analyser models it
        # as still permitting TLS 1.0/1.1, so make the constraint explicit.
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
        # Bind to port 0 and read back the assigned port so there is no
        # TOCTOU window between picking a free port and binding it.
        httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _make_handler(server_root)
        )
        port = int(httpd.server_address[1])
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            ws = tmp_path / "ws"
            provision = await _backend(port)[0].provision(
                project_id=NotBlankStr("proj-1"),
                workspace_path=ws,
                default_branch=NotBlankStr("main"),
            )
            assert provision.newly_created
            assert (ws / "README.md").exists()

            # Commit a change and push it back over HTTPS.
            (ws / "feature.txt").write_text("work\n", encoding="utf-8")
            for args in (
                ["git", "-C", str(ws), "checkout", "-b", "feature"],
                ["git", "-C", str(ws), "add", "."],
                ["git", "-C", str(ws), "commit", "-m", "feature"],
            ):
                subprocess.run(  # noqa: ASYNC221, S603 -- sync git in test setup
                    args,
                    check=True,
                    capture_output=True,
                )
            push = await _backend(port)[0].push(
                project_id=NotBlankStr("proj-1"),
                repo_root=ws,
                branch=NotBlankStr("feature"),
                base_branch=NotBlankStr("main"),
            )
            assert str(push.head_sha)

            fetch = await _backend(port)[0].fetch(
                project_id=NotBlankStr("proj-1"),
                repo_root=ws,
                branch=NotBlankStr("main"),
            )
            assert fetch.updated_refs == (NotBlankStr("main"),)
        finally:
            httpd.shutdown()
            thread.join(timeout=5)

    async def test_lazy_create_on_missing_remote(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        server_root = tmp_path / "srv"
        server_root.mkdir()
        cert, key = _self_signed_cert(tmp_path)
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        # Pin the floor to TLS 1.2 explicitly: ``create_default_context``
        # already does this at runtime, but the static analyser models it
        # as still permitting TLS 1.0/1.1, so make the constraint explicit.
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
        # Bind to port 0 and read back the assigned port so there is no
        # TOCTOU window between picking a free port and binding it.
        httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _make_handler(server_root)
        )
        port = int(httpd.server_address[1])
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        # Stub the forge REST client so create_repo provisions the bare
        # repo server-side (what a real forge's create endpoint does).
        def _fake_create(**_kwargs: object) -> ForgeApiClient:
            client = mock_of[ForgeApiClient]()

            async def _create(
                *,
                owner: NotBlankStr,
                repo: NotBlankStr,
                **_kw: object,
            ) -> ForgeRepo:
                # A real forge create endpoint yields an *empty* repo; the
                # first push then populates it. Seeding a commit here would
                # make the local default branch a non-fast-forward.
                bare = server_root / str(owner) / f"{repo}.git"
                _init_bare_empty(bare)
                return ForgeRepo(
                    full_name=NotBlankStr(f"{owner}/{repo}"),
                    default_branch=NotBlankStr("main"),
                    private=True,
                    clone_url=NotBlankStr(
                        f"https://localhost:{port}/{owner}/{repo}.git"
                    ),
                )

            client.repo_exists.return_value = False
            client.create_repo.side_effect = _create
            return cast("ForgeApiClient", client)

        monkeypatch.setattr(
            "synthorg.engine.workspace.git_backend.external_remote.build_forge_api_client",
            _fake_create,
        )
        try:
            ws = tmp_path / "ws"
            # Remote does not exist: provision inits a local repo.
            await _backend(port)[0].provision(
                project_id=NotBlankStr("proj-new"),
                workspace_path=ws,
                default_branch=NotBlankStr("main"),
            )
            # First push lazily creates the remote then succeeds.
            push = await _backend(port)[0].push(
                project_id=NotBlankStr("proj-new"),
                repo_root=ws,
                branch=NotBlankStr("main"),
                base_branch=NotBlankStr("main"),
            )
            assert str(push.head_sha)
            assert (server_root / _OWNER / "proj-new.git").is_dir()
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
