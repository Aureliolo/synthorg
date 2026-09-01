"""Gate: the CLI's admin requests satisfy what the backend enforces.

``synthorg backup``, ``synthorg restore`` and the backup ``synthorg wipe``
offers all reach ``/api/v1/admin/backups``, and the Go CLI builds those
requests entirely by hand (``cli/cmd/backup.go``). Every requirement the
backend places on them is therefore written twice, in two languages, and
nothing held the halves together. Two independent breakages shipped at once,
each on its own sufficient to refuse every call:

* ``jti`` was added to the backend's PyJWT ``require`` list for session
  revocation and was never minted by the CLI, so the token 401'd.
* Both POST operations declare ``Idempotency-Key`` required, and the CLI
  sent no such header, so the request 400'd before any handler ran.

Neither suite could see either one: each asserted its OWN side's shape. This
gate re-asks the question neither asks, does the request the CLI actually
builds satisfy the contract the backend actually enforces, and derives both
halves rather than listing them, because a list is one edit away from
disagreeing with the thing it claims to mirror.

Four checks:

1. Every required claim is minted. A missing one is a
   ``MissingRequiredClaimError`` inside the decode, surfaced as a 401 that
   looks exactly like a wrong secret.
2. Nothing extra is minted. ``JwtClaims`` forbids unknown keys, and its
   token-class validator rejects a system token carrying any of the four
   user-only fields, so a surplus claim is the same 401 from the other
   direction.
3. The three pinned claims (``sub`` / ``iss`` / ``aud``) carry exactly the
   values the backend's own constants name. A drifted issuer is rejected by
   ``_enforce_jwt_token_binding`` after the decode succeeds, which is a
   different code path with the identical symptom.
4. Every header the controller declares ``required=True`` is set on the
   request the CLI builds. The check is per-header rather than per-operation
   on purpose: the CLI has ONE request builder for the whole surface, so
   "some operation requires this and the builder never sets it" is the
   decidable question, and scoping a header to the method that needs it is
   the builder's own business.

Required claims are the union of what ``JwtClaims`` declares without a
default and what every ``jwt.decode`` in the auth service require-lists,
because either one alone fails the token.

There is deliberately no baseline and no per-line opt-out: a request the
backend refuses is never something to preserve, and the only honest
"exception" is a second request builder, which would need its own derivation
rather than a marker silencing this one.

Usage:
    uv run python scripts/check_cli_backend_request_parity.py

Exit codes:
    0 -- the CLI's requests satisfy the enforced contract.
    1 -- the two sides disagree.
    2 -- configuration error: an anchor could not be found, so the scan
         cannot be trusted (a renamed model, mint function, payload
         variable, or request builder lands here rather than passing
         silently).
"""

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
        read_source,
    )
else:
    from scripts._gate_source import (
        GateSourceError,
        read_and_parse,
        read_source,
    )

_CLAIMS_REL: Final[str] = "src/synthorg/api/auth/claims.py"
_SERVICE_REL: Final[str] = "src/synthorg/api/auth/service.py"
_SYSTEM_USER_REL: Final[str] = "src/synthorg/api/auth/system_user.py"
_MINT_REL: Final[str] = "cli/cmd/backup.go"

_CONTROLLER_REL: Final[str] = "src/synthorg/api/controllers/backup.py"

_MODEL: Final[str] = "JwtClaims"
_MINT_FUNC: Final[str] = "buildLocalJWT"
_REQUEST_FUNC: Final[str] = "buildBackupRequest"
_HEADER_PARAM: Final[str] = "HeaderParameter"
# The Go local holding the encoded claim set. Anchoring on it is what keeps
# the scan honest: a rename fails the gate rather than leaving it reading an
# unrelated literal.
_PAYLOAD_LOCAL: Final[str] = "payload"
_REQUIRE_KEY: Final[str] = "require"
_DECODE_CALLEE: Final[str] = "jwt.decode"

# Each claim whose value is fixed, mapped to the backend constant that owns
# it. The CLI hard-codes all three, so a drift here is invisible to the
# claim-name checks above.
_PINNED_CLAIMS: Final[dict[str, str]] = {
    "sub": "SYSTEM_USER_ID",
    "iss": "SYSTEM_ISSUER",
    "aud": "SYSTEM_AUDIENCE",
}

# A JSON object key in the Go payload template. The template carries printf
# verbs in value position, so it is scanned rather than parsed as JSON.
_KEY_RE: Final[re.Pattern[str]] = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"([^"]*)"'
)
# Go raw strings are backtick-delimited and cannot themselves contain one.
_RAW_STRING_RE: Final[re.Pattern[str]] = re.compile(r"`([^`]*)`")
# A header the Go request builder sets, in either quoting style.
_HEADER_SET_RE: Final[re.Pattern[str]] = re.compile(
    r"\.Header\.Set\(\s*[\"`]([^\"`]+)[\"`]"
)

_FIELD_DEFAULT_KWARGS: Final[frozenset[str]] = frozenset({"default", "default_factory"})
_FIELD_CALLABLE: Final[str] = "Field"


def _is_field_call(call: ast.Call) -> bool:
    """Whether a call is Pydantic's ``Field(...)``.

    Requiredness is read off ``Field``'s own argument shape, so applying
    that reading to some other callable answers a different question with
    this one's vocabulary.

    Returns:
        ``True`` when the callee is named ``Field``.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr == _FIELD_CALLABLE
    return isinstance(func, ast.Name) and func.id == _FIELD_CALLABLE


def _field_call_has_default(call: ast.Call) -> bool:
    """Whether a ``Field(...)`` call supplies a default.

    ``iat: int = Field(ge=0)`` is a REQUIRED field despite carrying a
    value, so an assignment being present cannot decide the question. The
    positional form inverts it once more: ``Field(..., description=...)``
    passes Ellipsis, which is Pydantic's way of spelling "required" while
    still attaching keywords, so the one positional argument that means
    NO default is the one an arity check reads as having one.

    Returns:
        ``True`` when the call names a default positionally or by keyword.
    """
    if call.args:
        first = call.args[0]
        supplies_default = not (
            isinstance(first, ast.Constant) and first.value is Ellipsis
        )
        if supplies_default:
            return True
    return any(kw.arg in _FIELD_DEFAULT_KWARGS for kw in call.keywords)


def _annotation_is_classvar(annotation: ast.expr) -> bool:
    """Whether an annotation is ``ClassVar[...]``.

    Pydantic excludes a ``ClassVar`` from the model's field set, so
    counting one as a claim invents a requirement no token can satisfy.

    Returns:
        ``True`` when the annotation subscripts ``ClassVar``.
    """
    target = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    if isinstance(target, ast.Attribute):
        return target.attr == "ClassVar"
    return isinstance(target, ast.Name) and target.id == "ClassVar"


def _model_fields(tree: ast.Module, rel: str) -> tuple[frozenset[str], frozenset[str]]:
    """Derive the model's declared and required claim names.

    Returns:
        ``(declared, required)`` claim-name sets.

    Raises:
        GateSourceError: If the model or its annotated fields are absent.
    """
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == _MODEL):
            continue
        declared: set[str] = set()
        required: set[str] = set()
        for stmt in node.body:
            if not (
                isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            ):
                continue
            name = stmt.target.id
            # Neither a private attribute nor a ClassVar is a Pydantic
            # field, so neither is a claim the wire carries.
            if name.startswith("_") or _annotation_is_classvar(stmt.annotation):
                continue
            declared.add(name)
            value = stmt.value
            if value is None or (
                isinstance(value, ast.Call)
                and _is_field_call(value)
                and not _field_call_has_default(value)
            ):
                required.add(name)
        if not declared:
            msg = f"{rel}: {_MODEL} declares no annotated claim fields"
            raise GateSourceError(msg)
        return frozenset(declared), frozenset(required)
    msg = f"{rel}: no class {_MODEL}; the claim contract anchor is gone"
    raise GateSourceError(msg)


def _is_decode_call(node: ast.Call) -> bool:
    """Report whether node is the decode entry point this gate reads.

    Scoping matters both ways: an unreadable ``options`` is exit 2, so a
    ``**`` spread on some unrelated call must not raise, and a decode
    reached under another name must not pass unread. A different import
    style therefore fails closed at the caller, which finds no require
    list at all.

    Returns:
        True when the callee is spelled exactly ``jwt.decode``.
    """
    module, _, attribute = _DECODE_CALLEE.partition(".")
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == module
    )


def _decode_required(tree: ast.Module, rel: str) -> frozenset[str]:
    """Collect every claim name PyJWT is told to require.

    Every element is read, not just the ones this scan happens to
    understand: having located a require list is a claim about the SHAPE
    of the anchor, and treating it as a claim about having understood the
    contents is how a gate certifies a contract it never read.

    Returns:
        The union of every ``options={"require": [...]}`` list in the module.

    Raises:
        GateSourceError: If no decode call carries a require list, or a
            located list holds an element this scan cannot resolve.
    """
    required: set[str] = set()
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_decode_call(node):
            continue
        for keyword in node.keywords:
            if keyword.arg is None:
                msg = (
                    f"{rel}: a {_DECODE_CALLEE} call spreads its keyword arguments, "
                    f"so its {_REQUIRE_KEY!r} option cannot be read"
                )
                raise GateSourceError(msg)
            if keyword.arg != "options" or not isinstance(keyword.value, ast.Dict):
                continue
            for key, value in zip(
                keyword.value.keys, keyword.value.values, strict=True
            ):
                if key is None:
                    msg = (
                        f"{rel}: an options dict unpacks another mapping, so the "
                        "enforced claim set cannot be read"
                    )
                    raise GateSourceError(msg)
                if not (isinstance(key, ast.Constant) and key.value == _REQUIRE_KEY):
                    continue
                if not isinstance(value, ast.List):
                    msg = (
                        f"{rel}: a {_REQUIRE_KEY!r} option is not a list literal, "
                        "so the enforced claim set cannot be read"
                    )
                    raise GateSourceError(msg)
                found = True
                for element in value.elts:
                    if not (
                        isinstance(element, ast.Constant)
                        and isinstance(element.value, str)
                    ):
                        msg = (
                            f"{rel}: a {_REQUIRE_KEY!r} list holds a non-literal "
                            "element, so the enforced claim set is incomplete"
                        )
                        raise GateSourceError(msg)
                    required.add(element.value)
    if not found:
        msg = (
            f"{rel}: no jwt.decode options carry a {_REQUIRE_KEY!r} list; "
            "the enforced claim set cannot be derived"
        )
        raise GateSourceError(msg)
    return frozenset(required)


def _pinned_values(tree: ast.Module, rel: str) -> dict[str, str]:
    """Read the backend constants naming each pinned claim's value.

    Returns:
        A mapping of claim name to its only admissible value.

    Raises:
        GateSourceError: If a constant is missing or is not a string.
    """
    by_constant = {constant: claim for claim, constant in _PINNED_CLAIMS.items()}
    values: dict[str, str] = {}
    # Module level only. A whole-tree walk would let a same-named
    # annotated local in any function silently become the expected value
    # for the three claims this gate exists to pin.
    for node in tree.body:
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        claim = by_constant.get(node.target.id)
        if claim is None:
            continue
        if not (
            isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        ):
            msg = f"{rel}: {node.target.id} is not a string constant"
            raise GateSourceError(msg)
        values[claim] = node.value.value
    missing = sorted(set(_PINNED_CLAIMS) - set(values))
    if missing:
        named = ", ".join(_PINNED_CLAIMS[claim] for claim in missing)
        msg = f"{rel}: missing pinned-claim constants: {named}"
        raise GateSourceError(msg)
    return values


def _function_body(source: str, rel: str, func: str) -> str:
    """Slice the Go source down to *func*'s body.

    The header match is anchored at a line start, because these functions
    carry doc comments that quote their own cross-language contract: an
    unanchored search would happily read a comment as the definition and
    then scan whatever followed it.

    Returns:
        The text between the function header and its closing brace.

    Raises:
        GateSourceError: If the function cannot be located.
    """
    header = re.compile(rf"(?m)^func {re.escape(func)}\(")
    match = header.search(source)
    if match is None:
        msg = f"{rel}: no func {func}(...; the CLI request site is gone"
        raise GateSourceError(msg)
    start = match.start()
    end = source.find("\n}\n", start)
    if end < 0:
        msg = f"{rel}: {func} has no closing brace at column zero"
        raise GateSourceError(msg)
    return source[start:end]


def _payload_statement(body: str, rel: str) -> str:
    """Slice the body down to the statement building the claim payload.

    Taking every raw string in the function would sweep in the JOSE header
    literal, whose ``alg`` and ``typ`` keys are not claims.

    Both Go string forms are skipped while balancing, not just the raw
    one: a parenthesis inside an ordinary quoted literal counts as
    punctuation to a scanner that cannot see it is inside a string, and
    the resulting span is silently the wrong one rather than an error.

    Returns:
        The text of the payload assignment, brackets balanced.

    Raises:
        GateSourceError: If the payload local or its statement is absent.
    """
    start = body.find(f"{_PAYLOAD_LOCAL} :=")
    if start < 0:
        msg = (
            f"{rel}: {_MINT_FUNC} has no {_PAYLOAD_LOCAL!r} assignment; "
            "the claim template cannot be located"
        )
        raise GateSourceError(msg)
    depth = 0
    opened = False
    index = start
    while index < len(body):
        char = body[index]
        if char == "`":
            closing = body.find("`", index + 1)
            index = len(body) if closing < 0 else closing + 1
            continue
        if char == '"':
            index = _skip_quoted(body, index)
            continue
        if char == "(":
            depth += 1
            opened = True
        elif char == ")":
            depth -= 1
            if opened and depth == 0:
                return body[start : index + 1]
        index += 1
    msg = f"{rel}: {_PAYLOAD_LOCAL} assignment has unbalanced parentheses"
    raise GateSourceError(msg)


def _skip_quoted(body: str, opening: int) -> int:
    r"""Return the index just past the Go quoted string opening at *opening*.

    A backslash escapes the next character, so ``"\""`` closes on the
    second quote rather than the first.

    Returns:
        The index of the first character after the closing quote.
    """
    index = opening + 1
    while index < len(body):
        char = body[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        index += 1
    return len(body)


def _minted_claims(source: str) -> tuple[frozenset[str], dict[str, str]]:
    """Derive the claim names and pinned literals the CLI mints.

    Args:
        source: The text of ``_MINT_REL``, read once by the caller.

    Returns:
        ``(claim_names, literal_values)`` read from the payload template.

    Raises:
        GateSourceError: If the template is absent or carries no keys.
    """
    statement = _payload_statement(
        _function_body(source, _MINT_REL, _MINT_FUNC), _MINT_REL
    )
    template = "".join(_RAW_STRING_RE.findall(statement))
    if not template:
        msg = (
            f"{_MINT_REL}: {_MINT_FUNC} builds its payload without a raw-string "
            "template, so the minted claim set cannot be read"
        )
        raise GateSourceError(msg)
    names = frozenset(_KEY_RE.findall(template))
    if not names:
        msg = f"{_MINT_REL}: the payload template declares no claim keys"
        raise GateSourceError(msg)
    return names, dict(_LITERAL_RE.findall(template))


def _required_headers(tree: ast.Module, rel: str) -> frozenset[str]:
    """Collect every header name the controller declares required.

    Having found a ``HeaderParameter`` call is a claim about the shape of
    the anchor, never about having understood what is inside it. A
    ``required=`` or ``name=`` this scan cannot resolve is therefore an
    error rather than an omission: the controller names its two required
    headers with a repeated literal today, and the very refusal of
    hardcoded values that would hoist them into a constant is what would
    otherwise drop them from the enforced set while the gate still passed.

    Returns:
        The names of every ``HeaderParameter(..., required=True)``.

    Raises:
        GateSourceError: If the controller declares no header parameters,
            or a declaration carries a non-literal ``required=`` / ``name=``.
    """
    names: set[str] = set()
    declared = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else ""
        )
        if callee != _HEADER_PARAM:
            continue
        declared = True
        by_keyword = {kw.arg: kw.value for kw in node.keywords}
        required = by_keyword.get("required")
        if required is not None and not isinstance(required, ast.Constant):
            msg = (
                f"{rel}: a {_HEADER_PARAM} carries a non-literal required=, "
                "so whether it is required cannot be read"
            )
            raise GateSourceError(msg)
        if not (isinstance(required, ast.Constant) and required.value is True):
            continue
        name = by_keyword.get("name")
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
            msg = (
                f"{rel}: a required {_HEADER_PARAM} carries a non-literal name=, "
                "so the header it demands cannot be read"
            )
            raise GateSourceError(msg)
        names.add(name.value)
    if not declared:
        msg = (
            f"{rel}: no {_HEADER_PARAM} declarations; the required-header "
            "set cannot be derived"
        )
        raise GateSourceError(msg)
    return frozenset(names)


def _cli_headers(source: str) -> frozenset[str]:
    """Collect every header the CLI's request builder sets.

    Args:
        source: The text of ``_MINT_REL``, read once by the caller.

    Returns:
        The header names passed to ``Header.Set`` in the builder.

    Raises:
        GateSourceError: If the builder is absent or sets no header.
    """
    body = _function_body(source, _MINT_REL, _REQUEST_FUNC)
    names = frozenset(_HEADER_SET_RE.findall(body))
    if not names:
        msg = (
            f"{_MINT_REL}: {_REQUEST_FUNC} sets no header; the sent header "
            "set cannot be read"
        )
        raise GateSourceError(msg)
    return names


def _header_violations(sent: frozenset[str], required: frozenset[str]) -> list[str]:
    """Check the CLI sets every header the controller demands.

    Returns:
        A list of violation messages (empty when every header is sent).
    """
    missing = sorted(required - sent)
    if not missing:
        return []
    return [
        (
            f"{_MINT_REL}: {_REQUEST_FUNC} never sets {', '.join(missing)}, "
            f"which {_CONTROLLER_REL} declares required=True. Litestar refuses "
            "the request with a 400 before any handler runs, so the command "
            "fails whatever the token says"
        )
    ]


def _claim_set_violations(
    minted: frozenset[str],
    declared: frozenset[str],
    required: frozenset[str],
) -> list[str]:
    """Compare the minted claim names against the enforced contract.

    Returns:
        A list of violation messages (empty when the sets agree).
    """
    violations: list[str] = []
    undeclared = sorted(required - declared)
    if undeclared:
        violations.append(
            f"{_SERVICE_REL}: jwt.decode requires {', '.join(undeclared)}, which "
            f"{_MODEL} ({_CLAIMS_REL}) does not declare, so no token can satisfy "
            "both the require list and extra=forbid"
        )
    missing = sorted(required - minted)
    if missing:
        violations.append(
            f"{_MINT_REL}: {_MINT_FUNC} does not mint {', '.join(missing)}; the "
            f"backend require-lists {'it' if len(missing) == 1 else 'them'}, so "
            "every CLI admin call is refused with a 401 indistinguishable from a "
            f"wrong secret. Add the claim to the {_PAYLOAD_LOCAL} template"
        )
    surplus = sorted(minted - required)
    if surplus:
        violations.append(
            f"{_MINT_REL}: {_MINT_FUNC} mints {', '.join(surplus)}, which the "
            f"backend does not require. {_MODEL} sets extra=forbid and rejects a "
            "system token carrying any user-only field, so a surplus claim is "
            f"also a 401. Remove it from the {_PAYLOAD_LOCAL} template"
        )
    return violations


def _pinned_violations(
    literals: dict[str, str],
    expected: dict[str, str],
) -> list[str]:
    """Compare each hard-coded claim value against its backend constant.

    Returns:
        A list of violation messages (empty when every value agrees).
    """
    violations: list[str] = []
    for claim, want in sorted(expected.items()):
        got = literals.get(claim)
        if got is None:
            violations.append(
                f"{_MINT_REL}: {_MINT_FUNC} no longer mints a literal {claim!r}; "
                f"it must equal {_PINNED_CLAIMS[claim]} ({want!r}) from "
                f"{_SYSTEM_USER_REL}"
            )
        elif got != want:
            violations.append(
                f"{_MINT_REL}: {_MINT_FUNC} mints {claim}={got!r} but "
                f"{_SYSTEM_USER_REL} pins {_PINNED_CLAIMS[claim]}={want!r}; the "
                "middleware rejects the mismatch after the decode succeeds"
            )
    return violations


def _check(repo_root: Path) -> list[str]:
    """Hold the CLI's admin requests to the backend's enforced contract.

    Returns:
        A list of violation messages (empty when the two sides agree).

    Raises:
        GateSourceError: If either side's anchors cannot be read.
    """
    _claims_source, claims_tree = read_and_parse(repo_root / _CLAIMS_REL)
    _service_source, service_tree = read_and_parse(repo_root / _SERVICE_REL)
    _system_source, system_tree = read_and_parse(repo_root / _SYSTEM_USER_REL)
    _controller_source, controller_tree = read_and_parse(repo_root / _CONTROLLER_REL)

    declared, model_required = _model_fields(claims_tree, _CLAIMS_REL)
    required = model_required | _decode_required(service_tree, _SERVICE_REL)
    expected = _pinned_values(system_tree, _SYSTEM_USER_REL)
    headers = _required_headers(controller_tree, _CONTROLLER_REL)

    # Both Go-side reads anchor in the same file, so it is read once here
    # rather than by each helper.
    mint_source = read_source(repo_root / _MINT_REL)
    minted, literals = _minted_claims(mint_source)
    return (
        _claim_set_violations(minted, declared, required)
        + _pinned_violations(literals, expected)
        + _header_violations(_cli_headers(mint_source), headers)
    )


def main() -> int:
    """Run the CLI/backend request-parity gate.

    Returns:
        The process exit code (0 clean, 1 violations, 2 config error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        violations = _check(args.repo_root)
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if violations:
        print("CLI/backend request-parity check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
