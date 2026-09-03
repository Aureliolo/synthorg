"""Tests for the credential detector security rule."""

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.models import SecurityContext, SecurityVerdictType
from synthorg.security.rules.credential_detector import CredentialDetector


def _ctx(
    arguments: dict[str, object] | None = None,
    *,
    action_type: str = "code:read",
) -> SecurityContext:
    """Build a SecurityContext with sensible defaults."""
    return SecurityContext(
        tool_name="test-tool",
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
        arguments=arguments or {},
    )


# ── Detection of known bad patterns ──────────────────────────────────


@pytest.mark.unit
class TestCredentialDetectorDetectsSecrets:
    """Credential detector catches known credential patterns."""

    @pytest.mark.parametrize(
        ("label", "value"),
        [
            ("AWS access key", "config AKIAIOSFODNN7EXAMPLE stored"),
            (
                "AWS secret key",
                "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            ),
            (
                "AWS secret key (colon separator)",
                "secret_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            ),
            (
                "Generic API key",
                "api_key=xk_test_1234567890abcdef1234567890abcdef",
            ),
            (
                "Generic auth token",
                "auth_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            ),
            (
                "SSH private key",
                "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB...",
            ),
            (
                "SSH OPENSSH key",
                "-----BEGIN OPENSSH PRIVATE KEY-----",
            ),
            (
                "SSH EC key",
                "-----BEGIN EC PRIVATE KEY-----",
            ),
            (
                "Bearer token",
                "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxxxx",
            ),
            (
                "GitHub PAT",
                "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
            ),
            (
                "Generic secret assignment",
                "SECRET=my-super-secret-value-here",
            ),
            (
                "Generic TOKEN assignment",
                "TOKEN: a1b2c3d4e5f6g7h8",
            ),
            (
                "Generic PASSWORD assignment",
                "PASSWORD=longpassword123",
            ),
            (
                "Quoted token literal in code",
                'token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"',
            ),
            (
                "Lowercase YAML password with digits",
                "password: hunter2hunter2",
            ),
            (
                "Quoted passphrase constant",
                'SECRET = "correct-horse-battery-staple"',
            ),
        ],
        ids=lambda x: x if isinstance(x, str) and len(x) < 30 else None,
    )
    def test_detects_credential_pattern(
        self,
        label: str,
        value: str,
    ) -> None:
        """Each known credential pattern triggers a DENY verdict."""
        detector = CredentialDetector()
        ctx = _ctx({"content": value})
        verdict = detector.evaluate(ctx)

        assert verdict is not None, f"Expected detection of: {label}"
        assert verdict.verdict == SecurityVerdictType.DENY
        assert verdict.risk_level == ApprovalRiskLevel.CRITICAL
        assert "credential_detector" in verdict.matched_rules

    def test_detects_credential_in_nested_dict(self) -> None:
        """Credentials inside nested dicts are detected."""
        detector = CredentialDetector()
        ctx = _ctx(
            {
                "outer": {
                    "inner": "-----BEGIN PRIVATE KEY-----\nMIIE...",
                },
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_detects_credential_in_list(self) -> None:
        """Credentials inside list values are detected."""
        detector = CredentialDetector()
        ctx = _ctx(
            {
                "files": [
                    "safe content",
                    "api_key=xk_test_ABCDEFGHIJKLMNOP1234",
                ],
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_detects_credential_in_list_of_dicts(self) -> None:
        """Credentials in dicts nested inside lists are detected."""
        detector = CredentialDetector()
        ctx = _ctx(
            {
                "entries": [
                    {"value": "SECRET=do_not_leak_this"},
                ],
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_multiple_findings_deduped_and_sorted(self) -> None:
        """Multiple credential types produce sorted, unique findings."""
        detector = CredentialDetector()
        ctx = _ctx(
            {
                "a": "AKIAIOSFODNN7EXAMPLE is a key",
                "b": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9012345",
                "c": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9012345",
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        # Reason should contain both pattern names.
        assert "AWS access key" in verdict.reason
        assert "Bearer token" in verdict.reason


# ── Clean input (no detection) ───────────────────────────────────────


@pytest.mark.unit
class TestCredentialDetectorPassThrough:
    """Clean inputs return None (no verdict)."""

    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"code": "print('hello world')"},
            {"path": "/usr/local/bin/python"},
            {"content": "Just a regular document with no secrets"},
            {"key": "short"},
            {"nested": {"safe": "value"}},
            {"items": ["one", "two", "three"]},
        ],
        ids=[
            "empty",
            "normal_code",
            "normal_path",
            "normal_text",
            "short_value",
            "nested_safe",
            "list_safe",
        ],
    )
    def test_returns_none_for_clean_input(
        self,
        arguments: dict[str, object],
    ) -> None:
        """Clean arguments produce no verdict."""
        detector = CredentialDetector()
        ctx = _ctx(arguments)
        assert detector.evaluate(ctx) is None


@pytest.mark.unit
class TestCodeThatNamesATokenIsNotACredential:
    """Ordinary source code assigns to variables called ``token`` all day.

    A live run had four of eight agents refused on ``write_file`` for a
    parser whose tokeniser did ``token = self._peek()``; two of them spent
    their whole budget bisecting the refusal and delivered nothing. A secret
    VALUE is a literal or a run of secret-shaped characters, never a call, an
    index or an attribute chain, and the rule reads the value's shape rather
    than the variable's name.
    """

    @pytest.mark.parametrize(
        "code",
        [
            "token = self._peek()",
            "token: Token = self.tokens[self.pos]",
            "password = getpass.getpass()",
            "credential = build_credential(name)",
            'TOKEN = re.compile(r"[A-Za-z_]+")',
            "secret = None",
            'SECRET_HEADER = "X-Secret-Header"',
            "token = next_token",
            (
                "token = self._peek()\n"
                "if token.kind == 'SELECT':\n"
                "    return self._select()\n"
            ),
            "MAX_TOKEN_LENGTH = 100000000",
            "SECRET_ROTATION_DAYS = 90000000",
            "TOKEN_PATH = self.config.token_file_path",
            "token_hash: HMAC-SHA256 hash of the opaque token.",
            "token = settings.tokens_2024.primary",
            "token = settings.tokens_2024.abcdefghijklmnop",
            "TOKEN = config.tokens_2024.SECONDARY_FALLBACK_TOKEN",
            "token = client.auth.refreshTokenV2Handler",
            "token = codec.base64UrlEncoder.encodedOutput",
            "token = handlers.oauth2TokenV2Handler",
            "token = settings.longAttributeV123Handler",
            "token = codec.utf8ToBase64Encoder.output",
            "token = settings.accessTokenV1alpha",
            "token = registry.sha256sumOfManifestV1beta2",
            "password = 12345678",
            "password_length = 12",
            'expected_token = "identifier"',
        ],
    )
    def test_code_shaped_assignments_pass(self, code: str) -> None:
        detector = CredentialDetector()

        assert detector.evaluate(_ctx({"content": code})) is None

    @pytest.mark.parametrize(
        "code",
        [
            'password = "hunter2hunter2"',
            'password = "correcthorse"',
            "db_password = correcthorsebatterystaple",
            "TOKEN=abcdefghijklmnopqrstuvwx",
            "token = 1234abcd",
            'SECRET_KEY: "abcdefghijklmnopq"',
            'token = "abc123defghi"',
            (
                "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0."
                "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            ),
            "credential: 7f9a8b7c6d5e4f3a2b1c",
            "token = hvs.CAESIJ8Xq2LkE9vB0mYtQ3Rf-Hn7cWp1sZa4Dg6",
            (
                "token = eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJhbGljZSJ9."
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ),
        ],
        ids=[
            "quoted_password",
            "short_word_password",
            "bare_lowercase_no_digit",
            "bare_uppercase_no_digit",
            "bare_digit_bearing_eight",
            "quoted_sixteen_no_digit",
            "quoted_digit_bearing",
            "bare_jwt",
            "hex_credential",
            "prefixed_dotted_secret",
            "digit_free_jwt",
        ],
    )
    def test_secret_shaped_assignments_are_refused(self, code: str) -> None:
        detector = CredentialDetector()

        assert detector.evaluate(_ctx({"content": code})) is not None

    @pytest.mark.parametrize(
        ("code", "refused"),
        [
            ('token = "abcdefg"', False),
            ('token = "abcdefg1"', True),
            ('token = "abcdefghijklmno"', False),
            ('token = "abcdefghijklmnop"', True),
            ("token = abcdefg1", True),
            ("token = abcdefghijklmno", False),
            ("token = abcdefghijklmnop", True),
        ],
        ids=[
            "seven_with_digit",
            "eight_with_digit",
            "fifteen_no_digit",
            "sixteen_no_digit",
            "bare_eight_with_digit",
            "bare_fifteen_no_digit",
            "bare_sixteen_no_digit",
        ],
    )
    def test_the_length_boundaries(self, code: str, refused: bool) -> None:
        detector = CredentialDetector()

        assert (detector.evaluate(_ctx({"content": code})) is not None) is refused


# ── Name property ────────────────────────────────────────────────────


@pytest.mark.unit
class TestCredentialDetectorName:
    """Verify the rule name property."""

    def test_name_is_credential_detector(self) -> None:
        detector = CredentialDetector()
        assert detector.name == "credential_detector"
