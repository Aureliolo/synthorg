"""Telemetry namespace setting definitions.

Anonymous Logfire telemetry is opt-in and off by default. The
write-only project token is **embedded** in the release wheel at
build time (``synthorg.telemetry.reporters._embedded_token``);
operators never configure or paste it. They flip
``telemetry.enabled`` (or ``SYNTHORG_TELEMETRY_ENABLED=1``) and
either get the embedded token (release wheel) or a single-shot
ERROR at startup explaining the build artifact is missing it.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TELEMETRY,
        key="enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Send anonymous usage telemetry to the SynthOrg-owned"
            " Logfire project. Token is embedded; operators only"
            " toggle this flag."
        ),
        group="General",
        level=SettingLevel.BASIC,
        env_var_override="SYNTHORG_TELEMETRY_ENABLED",
        yaml_path="telemetry.enabled",
    )
)
