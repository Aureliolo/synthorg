"""Design namespace setting definitions.

Governs image generation for the design tools: a master feature flag and
the image model the ``image_generator`` tool routes through. Both are read
when the boot tool registry is assembled; a change triggers a
runtime-services rebuild via the reload subscriber, so the tool is
registered / withdrawn for the next task without a restart.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.DESIGN,
        key="image_generation_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Master switch for agent image generation. Off by default:"
            " enabling it registers the design ``image_generator`` tool, which"
            " routes through the connected image model (an outbound provider"
            " call), so an operator opts in knowingly. Also requires the"
            " ``design_tools`` config section to be enabled (via a template or"
            " config)."
        ),
        group="General",
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.DESIGN,
        key="image_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the ``image_generator`` tool generates images"
            " with, selected through the model picker (a `{provider, model_id}`"
            " reference). Must resolve to an image-capable model"
            " (``supports_image_generation``); unset until an operator selects"
            " one. Connecting an image-capable hosted provider makes its image"
            " models available here."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)
