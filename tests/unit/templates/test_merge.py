"""Tests for merge narrowing guards on malformed rendered config.

The merge pipeline operates on rendered-YAML config dicts.  When a
field that must be a list of mappings is malformed, the merge raises a
domain ``TemplateInheritanceError`` rather than crashing with a raw
``AttributeError`` deep inside the key computation.
"""

import pytest

from synthorg.templates._inheritance import deduplicate_merged_agent_names
from synthorg.templates.errors import TemplateInheritanceError
from synthorg.templates.merge import merge_template_configs


@pytest.mark.unit
class TestMergeNarrowingGuards:
    def test_non_list_agents_raises(self) -> None:
        parent: dict[str, object] = {"agents": "not-a-list"}
        with pytest.raises(TemplateInheritanceError, match="must be a list"):
            merge_template_configs(parent, {})

    def test_non_mapping_agent_entry_raises(self) -> None:
        parent: dict[str, object] = {"agents": [123]}
        with pytest.raises(TemplateInheritanceError, match="must be mappings"):
            merge_template_configs(parent, {})

    def test_non_list_departments_raises(self) -> None:
        parent: dict[str, object] = {"departments": "not-a-list"}
        with pytest.raises(TemplateInheritanceError, match="must be a list"):
            merge_template_configs(parent, {})

    def test_non_mapping_department_entry_raises(self) -> None:
        child: dict[str, object] = {"departments": [123]}
        with pytest.raises(TemplateInheritanceError, match="must be mappings"):
            merge_template_configs({}, child)


@pytest.mark.unit
class TestDeduplicateNarrowingGuards:
    def test_non_mapping_agent_entry_raises(self) -> None:
        merged: dict[str, object] = {"agents": [123]}
        with pytest.raises(TemplateInheritanceError, match="must be mappings"):
            deduplicate_merged_agent_names(merged)

    def test_non_list_agents_is_noop(self) -> None:
        merged: dict[str, object] = {"agents": "not-a-list"}
        assert deduplicate_merged_agent_names(merged) == merged
