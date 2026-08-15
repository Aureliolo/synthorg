"""Unit tests for ``synthorg.tools.sandbox.deployment_identity``.

The identity and the containment check together decide which containers the
boot reconciliation pass is allowed to stop and remove, so the interesting
assertions here are the ones about paths that are nearly the same: a
different case, a sibling whose name extends ours, a relative segment. Each
is a way one deployment could come to claim another's live work.
"""

import os
from pathlib import Path

import pytest

from synthorg.tools.sandbox.deployment_identity import (
    deployment_id_for,
    normalised_path,
    path_is_within,
)

pytestmark = pytest.mark.unit

_ROOT = Path("/synthorg-identity/ours/workspaces")

_PLATFORM_IGNORES_CASE = os.path.normcase("A") == os.path.normcase("a")
"""Whether this platform's paths are case-insensitive.

Asserted against rather than skipped on: both answers are correct
behaviour, and which one is correct is a property of the platform.
"""


def test_identity_is_stable_for_the_same_root() -> None:
    """The same root yields the same label on every boot."""
    assert deployment_id_for(_ROOT) == deployment_id_for(_ROOT)


def test_identity_differs_between_roots() -> None:
    """Two deployments with different workspaces are told apart."""
    assert deployment_id_for(_ROOT) != deployment_id_for(Path("/synthorg-identity/x"))


def test_identity_does_not_leak_the_path() -> None:
    """The label carries a digest, not the operator's filesystem layout."""
    assert "synthorg-identity" not in deployment_id_for(_ROOT)


def test_identity_follows_the_platform_case_rule() -> None:
    """Case folding is the platform's decision, never a blanket ``casefold``.

    Windows resolves ``/work/A`` and ``/work/a`` to one directory, so a
    deployment restarted with differently cased configuration must still
    recognise its own containers. POSIX has two directories there, and
    folding them together hands one deployment's containers to the other:
    the reconciliation pass would stop and remove live work.
    """
    upper = deployment_id_for(Path("/synthorg-identity/A"))
    lower = deployment_id_for(Path("/synthorg-identity/a"))

    assert (upper == lower) is _PLATFORM_IGNORES_CASE


def test_containment_follows_the_platform_case_rule() -> None:
    """The same rule decides whether a mount lies inside our workspace."""
    inside = path_is_within(
        Path("/synthorg-identity/A/agent-1"), Path("/synthorg-identity/a")
    )

    assert inside is _PLATFORM_IGNORES_CASE


def test_containment_accepts_the_root_itself() -> None:
    """A mount of the root is inside it."""
    assert path_is_within(_ROOT, _ROOT)


def test_containment_accepts_a_descendant() -> None:
    """A workspace handed to an agent is inside the root."""
    assert path_is_within(_ROOT / "agent-1" / "project", _ROOT)


def test_containment_rejects_a_sibling_sharing_our_prefix() -> None:
    """A neighbour whose name merely extends ours is not inside it.

    The comparison is on path components rather than string prefixes for
    this case alone: ``/…/workspaces-other`` starts with ``/…/workspaces``
    and belongs to somebody else entirely.
    """
    assert not path_is_within(Path("/synthorg-identity/ours/workspaces-other"), _ROOT)


def test_containment_rejects_an_escape_through_a_parent_segment() -> None:
    """``..`` is resolved before the comparison, so it cannot walk out unseen."""
    assert not path_is_within(_ROOT / ".." / "elsewhere" / "agent-1", _ROOT)


def test_normalised_path_is_absolute() -> None:
    """Relative input is resolved, so two spellings of one directory agree."""
    assert normalised_path(_ROOT) == normalised_path(_ROOT / "agent-1" / "..")
