"""Company fixtures shared across test tiers.

Lives here rather than in a tier's ``conftest.py`` so an integration test
can build the same org an equivalent unit test does without importing
from ``tests.unit``.
"""

from synthorg.core.company import Company, CompanyConfig
from synthorg.core.company_departments import Department, Team

#: Monthly budget for the shared test company, in the default currency.
_BUDGET_MONTHLY: float = 100.0


def make_company() -> Company:
    """Create a test company with eng + qa departments.

    Returns:
        A two-department company with a three-team hierarchy, enough to
        exercise lead, head and cross-department escalation paths.
    """
    return Company(
        name="Test Corp",
        departments=(
            Department(
                name="Engineering",
                head="cto",
                budget_percent=60.0,
                teams=(
                    Team(
                        name="backend",
                        lead="backend_lead",
                        members=("sr_dev", "jr_dev"),
                    ),
                    Team(
                        name="frontend",
                        lead="frontend_lead",
                        members=("ui_dev",),
                    ),
                ),
            ),
            Department(
                name="QA",
                head="qa_head",
                budget_percent=20.0,
                teams=(
                    Team(
                        name="testing",
                        lead="qa_lead",
                        members=("qa_eng",),
                    ),
                ),
            ),
        ),
        config=CompanyConfig(budget_monthly=_BUDGET_MONTHLY),
    )
