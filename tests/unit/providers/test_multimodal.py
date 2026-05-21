"""Unit tests for multimodal (image) message support.

Covers the additive ``ImagePart`` model, ``ChatMessage.image_parts``
role validation, the litellm multimodal content emission in
``_message_to_dict``, and cassette redaction elision of image bytes.
"""

import pytest
from pydantic import ValidationError

from synthorg.providers.cassette.redaction import (
    IMAGE_DATA_PLACEHOLDER,
    NullRedactor,
    PatternRedactor,
)
from synthorg.providers.drivers.mappers import messages_to_dicts
from synthorg.providers.enums import ImageDetail, ImageMediaType, MessageRole
from synthorg.providers.models import ChatMessage, ImagePart

pytestmark = pytest.mark.unit

# A tiny valid base64 payload (the literal string "PNGDATA").
_B64 = "UE5HREFUQQ=="


# ── ImagePart ─────────────────────────────────────────────────────


class TestImagePart:
    def test_defaults_detail_auto(self) -> None:
        part = ImagePart(media_type=ImageMediaType.PNG, base64_data=_B64)
        assert part.detail is ImageDetail.AUTO

    def test_data_uri_computed(self) -> None:
        part = ImagePart(media_type=ImageMediaType.PNG, base64_data=_B64)
        assert part.data_uri == f"data:image/png;base64,{_B64}"

    def test_frozen(self) -> None:
        part = ImagePart(media_type=ImageMediaType.JPEG, base64_data=_B64)
        with pytest.raises(ValidationError):
            part.base64_data = "other"  # type: ignore[misc]

    def test_rejects_blank_data(self) -> None:
        with pytest.raises(ValidationError):
            ImagePart(media_type=ImageMediaType.PNG, base64_data="   ")

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ImagePart(
                media_type=ImageMediaType.PNG,
                base64_data=_B64,
                bogus=1,  # type: ignore[call-arg]
            )


# ── ChatMessage.image_parts validation ───────────────────────────


class TestChatMessageImageParts:
    def _img(self) -> ImagePart:
        return ImagePart(media_type=ImageMediaType.PNG, base64_data=_B64)

    def test_user_message_with_image_and_text(self) -> None:
        msg = ChatMessage(
            role=MessageRole.USER,
            content="Does this match the brief?",
            image_parts=(self._img(),),
        )
        assert len(msg.image_parts) == 1

    def test_user_message_image_only_no_text(self) -> None:
        msg = ChatMessage(role=MessageRole.USER, image_parts=(self._img(),))
        assert msg.content is None
        assert len(msg.image_parts) == 1

    def test_default_is_empty_tuple(self) -> None:
        msg = ChatMessage(role=MessageRole.USER, content="hi")
        assert msg.image_parts == ()

    def test_system_rejects_image_parts(self) -> None:
        with pytest.raises(ValidationError, match="image_parts"):
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="sys",
                image_parts=(self._img(),),
            )

    def test_assistant_rejects_image_parts(self) -> None:
        with pytest.raises(ValidationError, match="image_parts"):
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="hi",
                image_parts=(self._img(),),
            )

    def test_tool_rejects_image_parts(self) -> None:
        from synthorg.providers.models import ToolResult

        with pytest.raises(ValidationError, match="image_parts"):
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=ToolResult(tool_call_id="c1", content="ok"),
                image_parts=(self._img(),),
            )


# ── mapper multimodal emission ────────────────────────────────────


class TestMultimodalMapping:
    def _img(self) -> ImagePart:
        return ImagePart(media_type=ImageMediaType.PNG, base64_data=_B64)

    def test_text_only_path_unchanged(self) -> None:
        msg = ChatMessage(role=MessageRole.USER, content="Hello!")
        assert messages_to_dicts([msg]) == [{"role": "user", "content": "Hello!"}]

    def test_image_with_text_emits_content_list(self) -> None:
        msg = ChatMessage(
            role=MessageRole.USER,
            content="Look:",
            image_parts=(self._img(),),
        )
        result = messages_to_dicts([msg])[0]
        assert result["role"] == "user"
        content = result["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "Look:"}
        assert content[1] == {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{_B64}",
                "detail": "auto",
            },
        }

    def test_image_only_omits_text_part(self) -> None:
        msg = ChatMessage(role=MessageRole.USER, image_parts=(self._img(),))
        content = messages_to_dicts([msg])[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "image_url"

    def test_multiple_images_preserve_order(self) -> None:
        a = ImagePart(media_type=ImageMediaType.PNG, base64_data="QUFB")
        b = ImagePart(media_type=ImageMediaType.JPEG, base64_data="QkJC")
        msg = ChatMessage(role=MessageRole.USER, content="x", image_parts=(a, b))
        content = messages_to_dicts([msg])[0]["content"]
        assert isinstance(content, list)
        assert content[1]["image_url"]["url"] == "data:image/png;base64,QUFB"
        assert content[2]["image_url"]["url"] == "data:image/jpeg;base64,QkJC"


# ── cassette redaction of image bytes ─────────────────────────────


class TestImageRedaction:
    def test_pattern_redactor_elides_image_bytes(self) -> None:
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": "hi",
                    "image_parts": [
                        {
                            "media_type": "image/png",
                            "base64_data": _B64,
                            "detail": "auto",
                            "data_uri": f"data:image/png;base64,{_B64}",
                        },
                    ],
                },
            ],
        }
        redacted = PatternRedactor().redact(payload)
        assert isinstance(redacted, dict)
        part = redacted["messages"][0]["image_parts"][0]
        assert part["base64_data"] == IMAGE_DATA_PLACEHOLDER
        assert part["data_uri"] == IMAGE_DATA_PLACEHOLDER
        assert part["media_type"] == "image/png"

    def test_null_redactor_preserves_image_bytes(self) -> None:
        payload = {"base64_data": _B64}
        assert NullRedactor().redact(payload) == {"base64_data": _B64}


# ── cassette replay keying covers image_parts ─────────────────────


class TestImageReplayKey:
    def _msg(self, data: str) -> ChatMessage:
        return ChatMessage(
            role=MessageRole.USER,
            content="look",
            image_parts=(ImagePart(media_type=ImageMediaType.PNG, base64_data=data),),
        )

    def test_identical_images_key_identically(self) -> None:
        from synthorg.providers.cassette.keying import CassetteMethod, request_hash

        a = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="example-provider",
            model="example-medium-001",
            messages=(self._msg(_B64),),
        )
        b = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="example-provider",
            model="example-medium-001",
            messages=(self._msg(_B64),),
        )
        assert a == b

    def test_different_images_key_differently(self) -> None:
        from synthorg.providers.cassette.keying import CassetteMethod, request_hash

        a = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="example-provider",
            model="example-medium-001",
            messages=(self._msg("QUFB"),),
        )
        b = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="example-provider",
            model="example-medium-001",
            messages=(self._msg("QkJC"),),
        )
        assert a != b
