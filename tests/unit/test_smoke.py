"""Smoke tests to verify project setup."""

import re

import pytest

SEMVER_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+"
    r"(-[a-zA-Z0-9]+(\.[a-zA-Z0-9]+)*)?"
    r"(\+[a-zA-Z0-9]+(\.[a-zA-Z0-9]+)*)?$"
)


@pytest.mark.unit
def test_package_importable() -> None:
    """Verify the synthorg package can be imported."""
    import synthorg

    assert hasattr(synthorg, "__version__")


@pytest.mark.unit
def test_version_format() -> None:
    """Verify version string matches MAJOR.MINOR.PATCH format."""
    from synthorg import __version__

    assert SEMVER_PATTERN.match(__version__), (
        f"Version {__version__!r} is not valid semver"
    )


@pytest.mark.unit
def test_markers_registered(pytestconfig: pytest.Config) -> None:
    """Verify custom markers are registered in pyproject.toml."""
    raw_markers: list[str] = pytestconfig.getini("markers")
    marker_names = {m.split(":")[0].strip() for m in raw_markers}
    expected = {"unit", "integration", "e2e", "slow"}
    missing = expected - marker_names
    assert expected.issubset(marker_names), f"Missing markers: {missing}"
