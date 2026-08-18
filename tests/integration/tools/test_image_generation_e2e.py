"""End-to-end image generation through the built tool registry.

Exercises the full wired path with no network and no vendor: the offline
scripted provider -> ProviderImageProvider adapter -> ImageGeneratorTool ->
filesystem design-asset store -> AssetManagerTool retrieval, assembled by
the production tool factory.
"""

import base64
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.tools.base import BaseTool
from synthorg.tools.design.config import DesignToolsConfig
from synthorg.tools.design.provider_image_provider import ProviderImageProvider
from synthorg.tools.factory import build_default_tools
from tests._shared.web_timeout import DEFAULT_TEST_WEB_WIRING

pytestmark = pytest.mark.integration


def _tool(tools: tuple[BaseTool, ...], name: str) -> BaseTool:
    return next(tool for tool in tools if tool.name == name)


async def test_agent_generates_image_asset_end_to_end(tmp_path: Path) -> None:
    asset_dir = tmp_path / "assets"
    tools = build_default_tools(
        workspace=tmp_path,
        web=DEFAULT_TEST_WEB_WIRING,
        design_config=DesignToolsConfig(asset_storage_path=str(asset_dir)),
        image_provider=ProviderImageProvider(
            provider=ScriptedDriver(), model="example-image-001"
        ),
    )

    image_generator = _tool(tools, "image_generator")
    asset_manager = _tool(tools, "asset_manager")

    generated = await image_generator.execute(
        arguments={"prompt": "a company logo", "width": 512, "height": 512}
    )
    assert not generated.is_error
    asset_id = generated.metadata["asset_id"]
    assert isinstance(asset_id, str)

    # A real, decodable PNG landed on disk under the asset-storage path.
    png_path = asset_dir / f"{asset_id}.png"
    assert png_path.is_file()
    image = Image.open(BytesIO(png_path.read_bytes()))
    assert image.format == "PNG"
    assert image.size == (512, 512)

    # The asset is durably queryable through the asset manager.
    listed = await asset_manager.execute(arguments={"action": "list"})
    assert asset_id in listed.content
    got = await asset_manager.execute(arguments={"action": "get", "asset_id": asset_id})
    assert not got.is_error
    assert "image/png" in got.content

    # Base64 metadata round-trips to the same bytes stored on disk.
    data = generated.metadata["data"]
    assert isinstance(data, str)
    assert base64.b64decode(data) == png_path.read_bytes()
