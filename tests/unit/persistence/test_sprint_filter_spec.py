"""Unit tests for ``SprintFilterSpec``'s scope semantics.

A sprint belongs either to a project or to the org as a whole, and the two
are different scopes. ``project`` unset means "no project predicate", which
matches every scope, so the org-wide scope has to be asked for by name.
Conflating them is how a question about the org-wide sprint comes to be
answered about some arbitrary project's.

Pure model validation, so no backend: the SQL side of the same distinction
is covered by the dual-backend conformance suite.
"""

import pytest
from pydantic import ValidationError as PydanticValidationError

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import SprintStatus
from synthorg.persistence.sprint_protocol import SprintFilterSpec

pytestmark = pytest.mark.unit


class TestScope:
    def test_empty_spec_names_no_scope(self) -> None:
        spec = SprintFilterSpec()
        assert spec.project is None
        assert spec.org_wide_only is False

    def test_a_project_and_org_wide_together_are_refused(self) -> None:
        # Two contradictory scopes in one spec: refused rather than one
        # silently winning.
        with pytest.raises(PydanticValidationError, match="org_wide_only"):
            SprintFilterSpec(project=NotBlankStr("named"), org_wide_only=True)

    def test_org_wide_only_stands_alone(self) -> None:
        spec = SprintFilterSpec(org_wide_only=True)
        assert spec.org_wide_only is True
        assert spec.project is None

    def test_org_wide_only_combines_with_a_status(self) -> None:
        spec = SprintFilterSpec(org_wide_only=True, status=SprintStatus.ACTIVE)
        assert spec.status is SprintStatus.ACTIVE

    def test_spec_is_frozen(self) -> None:
        spec = SprintFilterSpec()
        with pytest.raises(PydanticValidationError):
            spec.org_wide_only = True  # type: ignore[misc]

    def test_unknown_field_is_refused(self) -> None:
        with pytest.raises(PydanticValidationError):
            SprintFilterSpec(scope="org")  # type: ignore[call-arg]
