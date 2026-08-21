# module-kind: tests
"""Everything knowable before the sweep boots is settled before it boots."""

from pathlib import Path

import pytest

from evals.errors import HarnessProviderMissingError
from evals.recursion_depth.manifest import load_manifest
from evals.recursion_depth.preflight import run_preflight
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_MANIFEST = (
    Path(__file__).resolve().parents[3] / "evals" / "recursion_depth" / "manifest.yaml"
)


class TestProviderCoverage:
    """A pair naming a provider nothing carries cannot record anything."""

    async def test_an_empty_providers_block_is_refused_by_name(self) -> None:
        # The easiest mistake there is: omitting --company-config, whose
        # default baseline carries no providers block at all. Left to the
        # sweep, it fails once per cell as a decomposition error.
        with pytest.raises(HarnessProviderMissingError, match="example-provider"):
            await run_preflight(
                manifest=load_manifest(_MANIFEST),
                company_config=RootConfig(company_name=NotBlankStr("Empty")),
            )

    async def test_the_message_names_the_flag_that_fixes_it(self) -> None:
        with pytest.raises(HarnessProviderMissingError, match="--company-config"):
            await run_preflight(
                manifest=load_manifest(_MANIFEST),
                company_config=RootConfig(company_name=NotBlankStr("Empty")),
            )

    async def test_it_runs_before_anything_is_built(self) -> None:
        # No Docker call, no host, no gateway: the coverage check needs none of
        # them, so a config mistake is reported on a machine with no daemon at
        # all rather than being masked by a daemon failure.
        with pytest.raises(HarnessProviderMissingError):
            await run_preflight(
                manifest=load_manifest(_MANIFEST),
                company_config=RootConfig(company_name=NotBlankStr("Empty")),
            )
