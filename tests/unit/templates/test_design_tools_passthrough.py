"""Tests for the ``design_tools`` template passthrough."""

import pytest

from synthorg.templates.loader import load_template
from synthorg.templates.renderer import render_template
from synthorg.tools.design.config import DesignToolsConfig

pytestmark = pytest.mark.unit


def test_agency_enables_design_tools() -> None:
    config = render_template(load_template("agency"))
    assert isinstance(config.design_tools, DesignToolsConfig)


def test_growth_marketing_enables_design_tools() -> None:
    config = render_template(load_template("growth_marketing"))
    assert isinstance(config.design_tools, DesignToolsConfig)


def test_template_without_design_tools_leaves_it_disabled() -> None:
    config = render_template(load_template("dev_shop"))
    assert config.design_tools is None
