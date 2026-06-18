"""Tests for the Ed25519 audit-chain signer."""

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synthorg.observability.audit_chain.signer import (
    Ed25519AuditChainSigner,
    build_ed25519_signer,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit


def _write_key(path: Path) -> Ed25519PrivateKey:
    """Write a fresh Ed25519 PEM key to *path* and return it."""
    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return key


async def test_sign_then_verify_roundtrip() -> None:
    signer = Ed25519AuditChainSigner(Ed25519PrivateKey.generate())
    payload = await signer.sign(b"audit-event")
    assert payload.algorithm == "ed25519"
    assert await signer.verify(b"audit-event", payload.signature) is True


async def test_verify_rejects_tampered_data() -> None:
    signer = Ed25519AuditChainSigner(Ed25519PrivateKey.generate())
    payload = await signer.sign(b"audit-event")
    assert await signer.verify(b"tampered", payload.signature) is False


async def test_signed_at_uses_injected_clock() -> None:
    clock = FakeClock()
    signer = Ed25519AuditChainSigner(Ed25519PrivateKey.generate(), clock=clock)
    payload = await signer.sign(b"data")
    assert payload.signed_at == clock.now()


async def test_signer_id_is_stable_for_same_key(tmp_path: Path) -> None:
    key_path = tmp_path / "audit.pem"
    _write_key(key_path)
    first = build_ed25519_signer(key_path)
    second = build_ed25519_signer(key_path)
    assert first.signer_id == second.signer_id


async def test_ephemeral_key_when_path_missing(tmp_path: Path) -> None:
    signer = build_ed25519_signer(tmp_path / "absent.pem")
    payload = await signer.sign(b"x")
    assert await signer.verify(b"x", payload.signature) is True


async def test_loaded_key_verifies_signature_from_same_key(tmp_path: Path) -> None:
    key_path = tmp_path / "audit.pem"
    key = _write_key(key_path)
    signer = build_ed25519_signer(key_path)
    payload = await signer.sign(b"event")
    # The loaded signer's public key matches the on-disk key.
    key.public_key().verify(payload.signature, b"event")
