"""Unit tests for the governed publish argument models.

The argument models are the boundary where an agent-supplied ``target`` /
``reference`` / ``dest_tag`` / ``source_digest`` is validated against the OCI
grammar and the URL-segment guard before it can become a request path segment,
and where each method's required source is enforced.
"""

import pytest
from pydantic import ValidationError

from synthorg.tools.publish._args import PublishInspectArgs, PublishPushArgs

pytestmark = pytest.mark.unit

_TARGET = "prod-images"
_DIGEST = "sha256:" + "a" * 64


class TestPublishInspectArgs:
    def test_list_tags_is_a_read(self) -> None:
        args = PublishInspectArgs(action="list_tags", target=_TARGET)
        assert args.is_write is False

    def test_get_manifest_requires_a_reference(self) -> None:
        with pytest.raises(ValidationError):
            PublishInspectArgs(action="get_manifest", target=_TARGET)

    def test_get_manifest_rejects_a_malformed_reference(self) -> None:
        with pytest.raises(ValidationError):
            PublishInspectArgs(
                action="get_manifest", target=_TARGET, reference="has space"
            )

    def test_get_manifest_accepts_a_tag_or_digest(self) -> None:
        assert (
            PublishInspectArgs(
                action="get_manifest", target=_TARGET, reference="v1"
            ).reference
            == "v1"
        )
        assert (
            PublishInspectArgs(
                action="get_manifest", target=_TARGET, reference=_DIGEST
            ).reference
            == _DIGEST
        )

    def test_unsafe_target_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            PublishInspectArgs(action="list_tags", target="bad/segment")

    @pytest.mark.parametrize("limit", [0, 201], ids=["below-min", "above-max"])
    def test_limit_bounds_are_enforced(self, limit: int) -> None:
        with pytest.raises(ValidationError):
            PublishInspectArgs(action="list_tags", target=_TARGET, limit=limit)


class TestPublishPushArgs:
    def test_push_is_a_write(self) -> None:
        args = PublishPushArgs(
            action="push", target=_TARGET, dest_tag="latest", source_digest=_DIGEST
        )
        assert args.is_write is True

    def test_digest_promote_requires_a_source_digest(self) -> None:
        with pytest.raises(ValidationError):
            PublishPushArgs(
                action="push",
                target=_TARGET,
                dest_tag="latest",
                method="digest_promote",
            )

    def test_workspace_push_requires_an_image_path(self) -> None:
        with pytest.raises(ValidationError):
            PublishPushArgs(
                action="push",
                target=_TARGET,
                dest_tag="latest",
                method="workspace_push",
            )

    def test_neither_source_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            PublishPushArgs(action="push", target=_TARGET, dest_tag="latest")

    def test_malformed_dest_tag_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            PublishPushArgs(
                action="push", target=_TARGET, dest_tag="bad tag", source_digest=_DIGEST
            )

    def test_malformed_source_digest_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            PublishPushArgs(
                action="push",
                target=_TARGET,
                dest_tag="latest",
                source_digest="sha256:short",
            )

    def test_unsafe_target_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            PublishPushArgs(
                action="push",
                target="bad/segment",
                dest_tag="latest",
                source_digest=_DIGEST,
            )

    def test_valid_workspace_push_is_accepted(self) -> None:
        args = PublishPushArgs(
            action="push",
            target=_TARGET,
            dest_tag="latest",
            method="workspace_push",
            source_image_path="image",
        )
        assert args.source_image_path == "image"
