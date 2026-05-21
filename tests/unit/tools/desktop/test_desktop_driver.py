"""Unit tests for the pluggable desktop driver subsystem."""

import pytest
from pydantic import ValidationError

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.tools.desktop.driver import (
    DesktopDriverConfig,
    DesktopDriverKind,
    VncDesktopDriver,
    XvfbDesktopDriver,
    build_desktop_driver,
)

pytestmark = pytest.mark.unit


class TestDriverFactory:
    def test_default_is_xvfb(self) -> None:
        driver = build_desktop_driver(DesktopDriverConfig())
        assert isinstance(driver, XvfbDesktopDriver)
        assert driver.kind is DesktopDriverKind.XVFB

    def test_vnc_dispatch(self) -> None:
        config = DesktopDriverConfig(kind=DesktopDriverKind.VNC, vnc_port=5910)
        driver = build_desktop_driver(config)
        assert isinstance(driver, VncDesktopDriver)
        assert driver.kind is DesktopDriverKind.VNC

    def test_unknown_kind_raises(self) -> None:
        # The registry rejects an unregistered discriminator at lookup.
        from synthorg.tools.desktop.driver.factory import _REGISTRY

        with pytest.raises(StrategyFactoryNotFoundError):
            _REGISTRY.build("wayland")


class TestSessionConfig:
    def test_xvfb_session_disables_vnc(self) -> None:
        driver = build_desktop_driver(
            DesktopDriverConfig(screen_width=1024, screen_height=768),
        )
        session = driver.session_config()
        assert session.enable_vnc is False
        assert (session.screen_width, session.screen_height) == (1024, 768)

    def test_vnc_session_enables_vnc_with_port(self) -> None:
        driver = build_desktop_driver(
            DesktopDriverConfig(kind=DesktopDriverKind.VNC, vnc_port=5915),
        )
        session = driver.session_config()
        assert session.enable_vnc is True
        assert session.vnc_port == 5915

    def test_session_config_is_frozen(self) -> None:
        session = build_desktop_driver(DesktopDriverConfig()).session_config()
        with pytest.raises(ValidationError):
            session.display = ":1"
