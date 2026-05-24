# mypy: disable-error-code="explicit-any,explicit-override"
"""Unit tests for client simulation protocol definitions."""

from typing import Any

import pytest

from synthorg.client.protocols import (
    ClientInterface,
    ClientPoolStrategy,
    EntryPointStrategy,
    FeedbackStrategy,
    ReportStrategy,
    RequirementGenerator,
)

pytestmark = pytest.mark.unit


class TestProtocolsAreRuntimeCheckable:
    """Verify all protocols are runtime-checkable."""

    def test_client_interface_is_runtime_checkable(self) -> None:
        assert hasattr(ClientInterface, "__protocol_attrs__") or hasattr(
            ClientInterface, "_is_runtime_protocol"
        )

    def test_requirement_generator_is_runtime_checkable(self) -> None:
        assert hasattr(RequirementGenerator, "__protocol_attrs__") or hasattr(
            RequirementGenerator, "_is_runtime_protocol"
        )

    def test_feedback_strategy_is_runtime_checkable(self) -> None:
        assert hasattr(FeedbackStrategy, "__protocol_attrs__") or hasattr(
            FeedbackStrategy, "_is_runtime_protocol"
        )

    def test_report_strategy_is_runtime_checkable(self) -> None:
        assert hasattr(ReportStrategy, "__protocol_attrs__") or hasattr(
            ReportStrategy, "_is_runtime_protocol"
        )

    def test_client_pool_strategy_is_runtime_checkable(self) -> None:
        assert hasattr(ClientPoolStrategy, "__protocol_attrs__") or hasattr(
            ClientPoolStrategy, "_is_runtime_protocol"
        )

    def test_entry_point_strategy_is_runtime_checkable(self) -> None:
        assert hasattr(EntryPointStrategy, "__protocol_attrs__") or hasattr(
            EntryPointStrategy, "_is_runtime_protocol"
        )


class TestProtocolStructuralConformance:
    """Verify that mock implementations satisfy protocol checks."""

    def test_client_interface_conformance(self) -> None:
        class MockClient:
            async def submit_requirement(self, context: Any) -> Any:
                return None

            async def review_deliverable(self, context: Any) -> Any:
                return None

        assert isinstance(MockClient(), ClientInterface)

    def test_requirement_generator_conformance(self) -> None:
        class MockGenerator:
            async def generate(self, context: Any) -> Any:
                return ()

        assert isinstance(MockGenerator(), RequirementGenerator)

    def test_feedback_strategy_conformance(self) -> None:
        class MockFeedback:
            async def evaluate(self, context: Any) -> Any:
                return None

        assert isinstance(MockFeedback(), FeedbackStrategy)

    def test_report_strategy_conformance(self) -> None:
        class MockReport:
            async def generate_report(self, metrics: Any) -> Any:
                return {}

        assert isinstance(MockReport(), ReportStrategy)

    def test_client_pool_strategy_conformance(self) -> None:
        class MockPoolStrategy:
            async def select_clients(
                self,
                pool: Any,
                constraints: Any,
            ) -> Any:
                return ()

        assert isinstance(MockPoolStrategy(), ClientPoolStrategy)

    def test_entry_point_strategy_conformance(self) -> None:
        class MockEntryPoint:
            async def route(self, request: Any) -> Any:
                return request

        assert isinstance(MockEntryPoint(), EntryPointStrategy)

    def test_non_conforming_rejected(self) -> None:
        class NotAClient:
            def unrelated_method(self) -> None:
                pass

        assert not isinstance(NotAClient(), ClientInterface)
