"""Client namespace setting definitions.

Covers tunables for the runtime client surface (HumanClient,
hybrid client, simulated client).  See
``src/synthorg/client/human_client.py`` for the consumer of the
human-response timeout.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CLIENT,
        key="human_response_timeout_seconds",
        type=SettingType.FLOAT,
        default="60.0",
        description=(
            "Maximum wait for a human-in-the-loop response before the client"
            " gives up on the request."
        ),
        group="Human Client",
        level=SettingLevel.ADVANCED,
        min_value=10.0,
        max_value=3600.0,
        yaml_path="client.human_response_timeout_seconds",
    )
)
