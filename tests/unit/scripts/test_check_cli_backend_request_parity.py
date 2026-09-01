"""Tests for the CLI/backend request-parity gate.

The gate's whole value is that neither side's own suite can see the
defect, so the cases below mutate ONE side at a time and assert the gate
notices. The anchor-loss cases matter as much as the drift ones: a gate
that cannot find the mint site must fail loudly, because a silent pass
there is indistinguishable from agreement.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]

_CLAIMS_REL = "src/synthorg/api/auth/claims.py"
_SERVICE_REL = "src/synthorg/api/auth/service.py"
_SYSTEM_USER_REL = "src/synthorg/api/auth/system_user.py"
_CONTROLLER_REL = "src/synthorg/api/controllers/backup.py"
_MINT_REL = "cli/cmd/backup.go"


class _GateModule(Protocol):
    """Subset of ``scripts/check_cli_backend_request_parity.py`` under test."""

    GateSourceError: type[Exception]

    @staticmethod
    def _check(repo_root: Path) -> list[str]: ...

    @staticmethod
    def main() -> int: ...


def _load_module() -> _GateModule:
    script_path = _REPO_ROOT / "scripts" / "check_cli_backend_request_parity.py"
    spec = importlib.util.spec_from_file_location(
        "check_cli_backend_request_parity",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()

# A claims model whose required set is the six standard claims: five plain
# annotations plus one carrying a Field() with no default, which Pydantic
# treats as required and a naive "has an assignment" read would not.
_CLAIMS = """\
class JwtClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    iss: NotBlankStr
    aud: NotBlankStr
    sub: NotBlankStr
    jti: NotBlankStr
    iat: int = Field(ge=0, description="Issued-at.")
    exp: int = Field(ge=0, description="Expiry.")
    username: NotBlankStr | None = None
    role: HumanRole | None = None
    must_change_password: bool | None = None
    pwd_sig: NotBlankStr | None = None
"""

_SERVICE = """\
def _decode_token_raw(token):
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        options={
            "require": ["exp", "iat", "sub", "jti", "iss", "aud"],
            "verify_aud": False,
        },
    )
"""

_SYSTEM_USER = """\
SYSTEM_USER_ID: Final[str] = "system"
SYSTEM_ISSUER: Final[str] = "synthorg-cli"
SYSTEM_AUDIENCE: Final[str] = "synthorg-backend"
USER_ISSUER: Final[str] = "synthorg-api"
"""

_CONTROLLER = """\
class BackupController(Controller):
    path = "/admin/backups"

    @post()
    async def create_backup(
        self,
        idempotency_key: Annotated[
            NotBlankStr,
            HeaderParameter(name="Idempotency-Key", required=True, min_length=1),
        ],
        trace_id: Annotated[
            str | None,
            HeaderParameter(name="X-Trace-Id", required=False),
        ] = None,
    ) -> None:
        return None
"""

_PAYLOAD_OK = (
    '`{"sub":"system","iss":"synthorg-cli","aud":"synthorg-backend",`+\n'
    '\t\t\t`"jti":%q,"iat":%d,"exp":%d}`,\n'
)

_HEADERS_OK = ("Content-Type", "Idempotency-Key", "Authorization")


def _go_source(
    payload_body: str = _PAYLOAD_OK,
    headers: tuple[str, ...] = _HEADERS_OK,
) -> str:
    """Build a synthetic ``backup.go`` around *payload_body* and *headers*.

    The JOSE header literal is included verbatim because its ``alg`` /
    ``typ`` keys are the false positive the payload-only scan exists to
    avoid.

    Returns:
        Go source for the mint function and the request builder.
    """
    sets = "".join(f'\treq.Header.Set("{name}", value)\n' for name in headers)
    return (
        "package cmd\n\n"
        "func buildLocalJWT(secret string) (string, error) {\n"
        "\theader := base64.RawURLEncoding.EncodeToString("
        '[]byte(`{"alg":"HS256","typ":"JWT"}`))\n'
        "\tpayload := base64.RawURLEncoding.EncodeToString(\n"
        "\t\tfmt.Appendf(\n"
        "\t\t\tnil,\n"
        f"\t\t\t{payload_body}"
        "\t\t\trand.Text(), now, exp,\n"
        "\t\t),\n"
        "\t)\n"
        "\treturn header + payload, nil\n"
        "}\n\n"
        "func buildBackupRequest(method string) (*http.Request, error) {\n"
        f"{sets}"
        "\treturn req, nil\n"
        "}\n"
    )


def _make_tree(
    tmp_path: Path,
    *,
    claims: str = _CLAIMS,
    service: str = _SERVICE,
    system_user: str = _SYSTEM_USER,
    controller: str = _CONTROLLER,
    go: str | None = None,
) -> Path:
    """Materialise a synthetic repository root under *tmp_path*.

    Returns:
        The synthetic repository root.
    """
    files = {
        _CLAIMS_REL: claims,
        _SERVICE_REL: service,
        _SYSTEM_USER_REL: system_user,
        _CONTROLLER_REL: controller,
        _MINT_REL: _go_source() if go is None else go,
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_real_tree_is_clean() -> None:
    assert _MODULE._check(_REPO_ROOT) == []


def test_matching_claim_sets_pass(tmp_path: Path) -> None:
    assert _MODULE._check(_make_tree(tmp_path)) == []


def test_missing_required_claim_flagged(tmp_path: Path) -> None:
    payload = (
        '`{"sub":"system","iss":"synthorg-cli","aud":"synthorg-backend",`+\n'
        '\t\t\t`"iat":%d,"exp":%d}`,\n'
    )
    violations = _MODULE._check(_make_tree(tmp_path, go=_go_source(payload)))
    assert len(violations) == 1
    assert "does not mint jti" in violations[0]
    assert _MINT_REL in violations[0]


def test_surplus_claim_flagged(tmp_path: Path) -> None:
    payload = (
        '`{"sub":"system","iss":"synthorg-cli","aud":"synthorg-backend",`+\n'
        '\t\t\t`"jti":%q,"role":"ceo","iat":%d,"exp":%d}`,\n'
    )
    violations = _MODULE._check(_make_tree(tmp_path, go=_go_source(payload)))
    assert len(violations) == 1
    assert "mints role" in violations[0]
    assert "extra=forbid" in violations[0]


def test_jose_header_keys_are_not_read_as_claims(tmp_path: Path) -> None:
    # `alg` and `typ` live in a raw string inside the same function; reading
    # every raw string rather than the payload statement would report both.
    assert _MODULE._check(_make_tree(tmp_path)) == []


def test_field_without_default_counts_as_required(tmp_path: Path) -> None:
    payload = (
        '`{"sub":"system","iss":"synthorg-cli","aud":"synthorg-backend",`+\n'
        '\t\t\t`"jti":%q,"exp":%d}`,\n'
    )
    violations = _MODULE._check(_make_tree(tmp_path, go=_go_source(payload)))
    assert any("does not mint iat" in v for v in violations)


def test_optional_field_is_not_required(tmp_path: Path) -> None:
    # pwd_sig defaults to None, so omitting it must not be a violation.
    assert all("pwd_sig" not in v for v in _MODULE._check(_make_tree(tmp_path)))


def test_pinned_issuer_drift_flagged(tmp_path: Path) -> None:
    payload = (
        '`{"sub":"system","iss":"synthorg-api","aud":"synthorg-backend",`+\n'
        '\t\t\t`"jti":%q,"iat":%d,"exp":%d}`,\n'
    )
    violations = _MODULE._check(_make_tree(tmp_path, go=_go_source(payload)))
    assert len(violations) == 1
    assert "SYSTEM_ISSUER" in violations[0]
    assert "synthorg-api" in violations[0]


def test_missing_required_header_flagged(tmp_path: Path) -> None:
    go = _go_source(headers=("Content-Type", "Authorization"))
    violations = _MODULE._check(_make_tree(tmp_path, go=go))
    assert len(violations) == 1
    assert "never sets Idempotency-Key" in violations[0]
    assert "required=True" in violations[0]


def test_optional_header_is_not_required(tmp_path: Path) -> None:
    # X-Trace-Id is declared required=False, so not sending it is fine.
    assert all("X-Trace-Id" not in v for v in _MODULE._check(_make_tree(tmp_path)))


def test_controller_without_header_params_is_a_config_error(tmp_path: Path) -> None:
    controller = "class BackupController(Controller):\n    path = '/admin/backups'\n"
    with pytest.raises(_MODULE.GateSourceError, match="HeaderParameter"):
        _MODULE._check(_make_tree(tmp_path, controller=controller))


def test_request_builder_setting_no_header_is_a_config_error(tmp_path: Path) -> None:
    go = _go_source(headers=())
    with pytest.raises(_MODULE.GateSourceError, match="sets no header"):
        _MODULE._check(_make_tree(tmp_path, go=go))


def test_require_list_beyond_the_model_flagged(tmp_path: Path) -> None:
    service = _SERVICE.replace('"aud"]', '"aud", "nbf"]')
    violations = _MODULE._check(_make_tree(tmp_path, service=service))
    assert any("does not declare" in v and "nbf" in v for v in violations)


@pytest.mark.parametrize(
    ("go_source", "expected"),
    [
        ("package cmd\n\nfunc other() {}\n", "CLI request site is gone"),
        (
            (
                "package cmd\n\nfunc buildLocalJWT(secret string) (string, error) {\n"
                "\tclaims := map[string]any{}\n"
                "\treturn encode(claims), nil\n"
                "}\n"
            ),
            "cannot be located",
        ),
    ],
    ids=["mint_function_absent", "payload_local_absent"],
)
def test_unreadable_mint_site_is_a_config_error(
    tmp_path: Path,
    go_source: str,
    expected: str,
) -> None:
    with pytest.raises(_MODULE.GateSourceError, match=expected):
        _MODULE._check(_make_tree(tmp_path, go=go_source))


def test_doc_comment_quoting_the_signature_is_not_read_as_the_body(
    tmp_path: Path,
) -> None:
    # These functions carry doc comments describing their own contract, so
    # an unanchored header search could read a comment as the definition.
    go = _go_source().replace(
        "func buildLocalJWT(",
        "// See func buildLocalJWT(secret) for the claim set.\nfunc buildLocalJWT(",
        1,
    )
    assert _MODULE._check(_make_tree(tmp_path, go=go)) == []


def test_paren_inside_a_quoted_string_does_not_break_balancing(
    tmp_path: Path,
) -> None:
    payload = (
        '`{"sub":"system","iss":"synthorg-cli","aud":"synthorg-backend",`+\n'
        '\t\t\t`"jti":%q,"iat":%d,"exp":%d}`,\n'
        '\t\t\tlabelFor("mint (system)"),\n'
    )
    assert _MODULE._check(_make_tree(tmp_path, go=_go_source(payload))) == []


def test_model_with_no_annotated_fields_is_a_config_error(tmp_path: Path) -> None:
    claims = "class JwtClaims(BaseModel):\n    model_config = ConfigDict(frozen=True)\n"
    with pytest.raises(_MODULE.GateSourceError, match="no annotated claim fields"):
        _MODULE._check(_make_tree(tmp_path, claims=claims))


def test_unbalanced_payload_parentheses_is_a_config_error(tmp_path: Path) -> None:
    go = (
        "package cmd\n\n"
        "func buildLocalJWT(secret string) (string, error) {\n"
        "\tpayload := encode(\n"
        '\t\t`{"sub":"system"}`,\n'
        "\treturn payload, nil\n"
        "}\n"
    )
    with pytest.raises(_MODULE.GateSourceError, match="unbalanced parentheses"):
        _MODULE._check(_make_tree(tmp_path, go=go))


def test_payload_template_without_claim_keys_is_a_config_error(tmp_path: Path) -> None:
    go = _go_source(payload_body="`{}`,\n")
    with pytest.raises(_MODULE.GateSourceError, match="no claim keys"):
        _MODULE._check(_make_tree(tmp_path, go=go))


def test_non_literal_required_header_name_is_a_config_error(tmp_path: Path) -> None:
    # Hoisting the twice-repeated literal into a constant is the refactor
    # the No Hardcoded Values rule invites; it must fail loudly, never
    # drop the header from the enforced set.
    controller = _CONTROLLER.replace(
        'HeaderParameter(name="Idempotency-Key", required=True, min_length=1)',
        "HeaderParameter(name=_IDEMPOTENCY_HEADER, required=True, min_length=1)",
    )
    with pytest.raises(_MODULE.GateSourceError, match="non-literal name="):
        _MODULE._check(_make_tree(tmp_path, controller=controller))


def test_non_literal_required_flag_is_a_config_error(tmp_path: Path) -> None:
    controller = _CONTROLLER.replace(
        'HeaderParameter(name="Idempotency-Key", required=True, min_length=1)',
        'HeaderParameter(name="Idempotency-Key", required=_IS_REQUIRED, min_length=1)',
    )
    with pytest.raises(_MODULE.GateSourceError, match="non-literal required="):
        _MODULE._check(_make_tree(tmp_path, controller=controller))


def test_non_literal_require_element_is_a_config_error(tmp_path: Path) -> None:
    service = _SERVICE.replace('"require": [', '"require": [*_SHARED_CLAIMS, ')
    with pytest.raises(_MODULE.GateSourceError, match="non-literal"):
        _MODULE._check(_make_tree(tmp_path, service=service))


def test_spread_decode_options_is_a_config_error(tmp_path: Path) -> None:
    # A decode reached through **kwargs carries its require list somewhere
    # this scan cannot open, which reads exactly like a decode that
    # require-lists nothing.
    service = _SERVICE.replace("options={", "**_DECODE_OPTIONS,\n        options={")
    with pytest.raises(_MODULE.GateSourceError, match="spreads its keyword"):
        _MODULE._check(_make_tree(tmp_path, service=service))


def test_unpacked_options_key_is_a_config_error(tmp_path: Path) -> None:
    service = _SERVICE.replace(
        "options={\n", "options={\n            **_BASE_OPTIONS,\n"
    )
    with pytest.raises(_MODULE.GateSourceError, match="unpacks another mapping"):
        _MODULE._check(_make_tree(tmp_path, service=service))


def test_spread_on_an_unrelated_call_is_not_a_config_error(tmp_path: Path) -> None:
    # The fail-closed rule is scoped to the decode callee: every other call
    # in the module is none of this gate's business, and raising on one
    # would make the gate fail on code it never reads.
    unrelated = "def _unrelated():\n    return helper(**kwargs)\n\n\n"
    service = unrelated + _SERVICE
    assert _MODULE._check(_make_tree(tmp_path, service=service)) == []


def test_ellipsis_field_default_counts_as_required(tmp_path: Path) -> None:
    # Field(..., description=...) is Pydantic's "required, no default"
    # idiom, so the one positional argument that means NO default is the
    # one an arity check reads as supplying one.
    claims = _CLAIMS.replace(
        'iat: int = Field(ge=0, description="Issued-at.")',
        'iat: int = Field(..., description="Issued-at.")',
    )
    payload = (
        '`{"sub":"system","iss":"synthorg-cli","aud":"synthorg-backend",`+\n'
        '\t\t\t`"jti":%q,"exp":%d}`,\n'
    )
    violations = _MODULE._check(
        _make_tree(tmp_path, claims=claims, go=_go_source(payload))
    )
    assert any("does not mint iat" in v for v in violations)


def test_classvar_and_private_attrs_are_not_claims(tmp_path: Path) -> None:
    claims = _CLAIMS.replace(
        "    iss: NotBlankStr\n",
        "    _version: ClassVar[int] = 2\n"
        "    SCHEMA: ClassVar[str] = 'jwt'\n"
        "    iss: NotBlankStr\n",
    )
    assert _MODULE._check(_make_tree(tmp_path, claims=claims)) == []


def test_non_field_default_call_does_not_decide_requiredness(tmp_path: Path) -> None:
    # A default built by some other callable must not have Field's arity
    # rules applied to it.
    claims = _CLAIMS.replace(
        "    pwd_sig: NotBlankStr | None = None\n",
        "    pwd_sig: NotBlankStr | None = default_sig()\n",
    )
    assert _MODULE._check(_make_tree(tmp_path, claims=claims)) == []


def test_payload_without_raw_string_is_a_config_error(tmp_path: Path) -> None:
    go = (
        "package cmd\n\n"
        "func buildLocalJWT(secret string) (string, error) {\n"
        "\tpayload := encodeClaims(claimSet)\n"
        "\treturn payload, nil\n"
        "}\n"
    )
    with pytest.raises(_MODULE.GateSourceError, match="raw-string template"):
        _MODULE._check(_make_tree(tmp_path, go=go))


def test_missing_model_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(_MODULE.GateSourceError, match="no class JwtClaims"):
        _MODULE._check(_make_tree(tmp_path, claims="class Other:\n    x: int\n"))


def test_missing_require_list_is_a_config_error(tmp_path: Path) -> None:
    service = "def _decode_token_raw(token):\n    return jwt.decode(token, secret)\n"
    with pytest.raises(_MODULE.GateSourceError, match="require"):
        _MODULE._check(_make_tree(tmp_path, service=service))


def test_missing_pinned_constant_is_a_config_error(tmp_path: Path) -> None:
    system_user = _SYSTEM_USER.replace(
        'SYSTEM_ISSUER: Final[str] = "synthorg-cli"\n', ""
    )
    with pytest.raises(_MODULE.GateSourceError, match="SYSTEM_ISSUER"):
        _MODULE._check(_make_tree(tmp_path, system_user=system_user))


def test_main_exits_two_on_a_config_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _make_tree(tmp_path, go="package cmd\n")
    monkeypatch.setattr(sys, "argv", ["gate", "--repo-root", str(root)])
    assert _MODULE.main() == 2


def test_main_exits_one_on_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = (
        '`{"sub":"system","iss":"synthorg-cli","aud":"synthorg-backend",`+\n'
        '\t\t\t`"iat":%d,"exp":%d}`,\n'
    )
    root = _make_tree(tmp_path, go=_go_source(payload))
    monkeypatch.setattr(sys, "argv", ["gate", "--repo-root", str(root)])
    assert _MODULE.main() == 1


def test_main_exits_zero_when_the_sets_agree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["gate", "--repo-root", str(_make_tree(tmp_path))])
    assert _MODULE.main() == 0
