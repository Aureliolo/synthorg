# module-kind: declarative
"""Type shapes for the in-sandbox browser executor (host-side typing only).

These TypedDicts describe the JSON payloads the standalone ``_executor``
script exchanges with the host. They live in a separate module so the
executor stays free of ``synthorg`` imports at sandbox runtime: the
executor references them only under ``TYPE_CHECKING``.
"""

from typing import Literal, NotRequired, TypedDict


class BrowserPayload(TypedDict):
    """Decoded ``BROWSER_TOOL_ARGS_JSON`` payload (host-validated shape)."""

    operation: str
    url: str
    wait_condition: NotRequired[Literal["load", "domcontentloaded", "networkidle"]]
    viewport_width: NotRequired[int]
    viewport_height: NotRequired[int]
    full_page: NotRequired[bool]
    navigation_timeout_seconds: NotRequired[float]
    launch_timeout_seconds: NotRequired[float]
    screenshot_path: NotRequired[str]
    axe_script_path: NotRequired[str]
    min_impact: NotRequired[str]
    axe_version: NotRequired[str]
    storage_type: NotRequired[Literal["local", "session"]]
    storage_key: NotRequired[str]
    storage_value: NotRequired[str]
    # Ceiling on the serialised DOM the ``content`` operation returns. The
    # page decides how big its own document is, and the result crosses a
    # process boundary as one JSON string, so the cap is applied in the
    # container before the envelope is built rather than by the host after
    # it has already been transported and parsed.
    content_max_characters: NotRequired[int]
    # Workspace-mounted paths that persist a browsing session across
    # separate tool calls: the Playwright storage_state (cookies +
    # localStorage) and the host-side virtual-authenticator keystore.
    storage_state_path: NotRequired[str]
    webauthn_state_path: NotRequired[str]
    webauthn_rp_id: NotRequired[str]
    webauthn_user_handle: NotRequired[str]
    webauthn_credential_id: NotRequired[str]


class Violation(TypedDict):
    """Normalised axe-core violation row (in-container JSON shape)."""

    rule_id: str
    impact: str
    description: str
    help_url: str | None
    affected_nodes: int


class StoragePayload(TypedDict):
    """WebStorage read/write result (in-container JSON shape)."""

    storage_type: Literal["local", "session"]
    items: dict[str, str]


class WebAuthnCredentialPayload(TypedDict):
    """A model-safe virtual credential returned to the host (no private key)."""

    id: str
    rp_id: str
    user_handle: str
    public_key: str


class WebAuthnKeystoreEntry(TypedDict):
    """A full credential tuple persisted host-side to re-seed the authenticator.

    Includes the private key. This shape is written only to the
    workspace-mounted keystore file, never to the result returned to the
    model-facing surface.
    """

    id: str
    rp_id: str
    user_handle: str
    private_key: str
    public_key: str


class WebAuthnPayload(TypedDict):
    """WebAuthn operation result (in-container JSON shape)."""

    credentials: list[WebAuthnCredentialPayload]
