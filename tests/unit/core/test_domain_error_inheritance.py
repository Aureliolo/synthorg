"""Every registered domain-error base inherits from ``DomainError``.

Several error hierarchies (budget, engine, providers, tools,
communication, ontology, integrations, memory) declared the RFC 9457
ClassVars (``status_code``, ``error_code``, ``error_category``,
``retryable``, ``default_message``) but inherited from bare
``Exception``.  Without ``DomainError`` on the MRO,
``DomainError.__init_subclass__`` never ran on any subclass -- a
typo'd ``error_code`` whose first digit no longer matches the
declared ``error_category`` would slip through to runtime.

Re-basing each of those classes onto ``DomainError`` enforces the
prefix-vs-category contract at class-definition time.  This test
pins the inheritance so a future revert (back to ``Exception``)
fails the suite immediately.

The test also imports each module so the ``__init_subclass__``
validator runs once for every subclass in the hierarchy -- if a
typed mismatch slips into a future subclass, the import side-effect
raises ``TypeError`` before the assertions ever run.
"""

import pytest

from synthorg.budget.errors import (
    BudgetExhaustedError,
    DailyLimitExceededError,
    MixedCurrencyAggregationError,
    ProjectBudgetExhaustedError,
    QuotaExhaustedError,
    RiskBudgetExhaustedError,
)
from synthorg.communication.errors import CommunicationError
from synthorg.core.domain_errors import DomainError
from synthorg.engine.errors import (
    EngineError,
    ProjectNotFoundError,
    SubworkflowNotFoundError,
    TaskNotFoundError,
    TaskVersionConflictError,
    WorkflowExecutionNotFoundError,
)
from synthorg.integrations.errors import IntegrationError
from synthorg.memory.errors import MemoryError as DomainMemoryError
from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError
from synthorg.memory.org.errors import OrgMemoryError
from synthorg.memory.service import (
    CheckpointNotFoundError,
    CheckpointRollbackCorruptError,
    CheckpointRollbackUnavailableError,
    FineTuneRunNotFoundError,
    FineTuneRunNotResumableError,
)
from synthorg.ontology.errors import OntologyError
from synthorg.providers.errors import ProviderError
from synthorg.tools.errors import ToolError

pytestmark = pytest.mark.unit

# Every domain-error hierarchy base class. Each MUST inherit from
# ``DomainError`` so the prefix-vs-category validator runs on its
# subclasses at class-definition time.
DOMAIN_ERROR_BASES: tuple[type[BaseException], ...] = (
    BudgetExhaustedError,
    MixedCurrencyAggregationError,
    EngineError,
    ProviderError,
    ToolError,
    CommunicationError,
    OntologyError,
    IntegrationError,
    DomainMemoryError,
    OrgMemoryError,
    MemoryBackendUnsupportedError,
    CheckpointNotFoundError,
    CheckpointRollbackUnavailableError,
    CheckpointRollbackCorruptError,
    FineTuneRunNotFoundError,
    FineTuneRunNotResumableError,
)

# Representative subclasses across the hierarchies under test.  Each
# carries an overridden ``error_code`` / ``error_category`` pair, so
# ``__init_subclass__`` validation must accept all of them.
DOMAIN_ERROR_SUBCLASSES: tuple[type[BaseException], ...] = (
    DailyLimitExceededError,
    RiskBudgetExhaustedError,
    ProjectBudgetExhaustedError,
    QuotaExhaustedError,
    ProjectNotFoundError,
    TaskNotFoundError,
    TaskVersionConflictError,
    SubworkflowNotFoundError,
    WorkflowExecutionNotFoundError,
)


@pytest.mark.parametrize("cls", DOMAIN_ERROR_BASES, ids=lambda c: c.__name__)
def test_base_inherits_from_domain_error(cls: type[BaseException]) -> None:
    """Each base class is a ``DomainError`` so __init_subclass__ runs."""
    assert issubclass(cls, DomainError), (
        f"{cls.__name__} must inherit from DomainError so the "
        f"error_code-prefix-vs-error_category validator runs on its "
        f"subclasses."
    )


@pytest.mark.parametrize(
    "cls", DOMAIN_ERROR_BASES + DOMAIN_ERROR_SUBCLASSES, ids=lambda c: c.__name__
)
def test_class_attrs_are_consistent(cls: type[BaseException]) -> None:
    """Class declares the RFC 9457 ClassVars and they pass the validator.

    The validator runs at class-definition time (import side-effect of
    this module).  Re-asserting the prefix here makes the contract
    visible to readers and pins the invariant in the test report.
    """
    assert hasattr(cls, "error_code"), f"{cls.__name__}.error_code missing"
    assert hasattr(cls, "error_category"), f"{cls.__name__}.error_category missing"
    prefix = cls.error_code.value // 1000
    # Prefix 0 (e.g. ``ErrorCode`` value 0) is reserved for code-not-set
    # cases; every error class must declare a non-zero code.
    assert prefix > 0, f"{cls.__name__}.error_code prefix is 0"
