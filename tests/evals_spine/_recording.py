# module-kind: tests
"""What a recording-host test boots against.

Shared by every harness on the recording spine, so it sits beside the spine's
conftest rather than inside one harness's suite: two copies of a company config
would let the gateway resolve one thing and a binding test assert another.
"""

from evals.harness.host import RecordingGatewayHost, RecordingHostConfig
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr

#: The capability every binding test binds to, present in the company config below.
RECORDING_PROVIDER = "test-provider"
RECORDING_MODEL = "example-expert-001"

#: Image references the fixture host is started with. Deliberately unlike the
#: registered defaults, so a test asserting one of them cannot pass against a
#: value that arrived from the code default or from an import-time singleton.
#: Unresolvable on purpose: nothing in this suite launches a container.
RECORDING_SANDBOX_IMAGE = "example.invalid/sandbox:under-test"
RECORDING_SIDECAR_IMAGE = "example.invalid/sidecar:under-test"


def recording_company_config() -> RootConfig:
    """Build the recording company config the host boots against.

    The driver is the deterministic scripted one, so a full round trip through
    the gateway contacts no provider and costs nothing. What a harness's own
    legs dial is the gateway itself, which they reach over HTTP regardless.

    Returns:
        The recording company config.
    """
    return RootConfig(
        company_name="Recording Host",
        providers={
            RECORDING_PROVIDER: ProviderConfig(
                driver=NotBlankStr("scripted"),
                connection_name=NotBlankStr("conn-scripted"),
                models=(ProviderModelConfig(id=NotBlankStr(RECORDING_MODEL)),),
            )
        },
    )


__all__ = [
    "RECORDING_MODEL",
    "RECORDING_PROVIDER",
    "RECORDING_SANDBOX_IMAGE",
    "RECORDING_SIDECAR_IMAGE",
    "RecordingGatewayHost",
    "RecordingHostConfig",
    "recording_company_config",
]
