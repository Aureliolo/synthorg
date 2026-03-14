"""Hierarchy resolver for organizational structure."""

from types import MappingProxyType

from synthorg.communication.errors import HierarchyResolutionError
from synthorg.core.company import Company  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_HIERARCHY_BUILT,
    DELEGATION_HIERARCHY_CYCLE,
)

logger = get_logger(__name__)


class HierarchyResolver:
    """Resolves org hierarchy from a Company structure (read-only).

    Built from three sources, in priority order:

    1. Explicit ``ReportingLine.supervisor`` (most specific) — overrides
       team-derived relationships.
    2. ``Team.lead`` for team members
    3. ``Department.head`` for team leads without explicit reporting

    Detects cycles at construction time.

    Args:
        company: Frozen company structure to resolve hierarchy from.

    Raises:
        HierarchyResolutionError: If a cycle is detected.
    """

    __slots__ = ("_known_agents", "_reports_of", "_supervisor_of")

    def __init__(self, company: Company) -> None:
        supervisor_of: dict[str, str] = {}
        reports_of: dict[str, list[str]] = {}

        for dept in company.departments:
            for team in dept.teams:
                # Team lead -> department head (lowest priority)
                if team.lead != dept.head and team.lead not in supervisor_of:
                    supervisor_of[team.lead] = dept.head
                    reports_of.setdefault(dept.head, []).append(team.lead)

                # Team members -> team lead (medium priority)
                for member in team.members:
                    if member == team.lead:
                        continue
                    if member not in supervisor_of:
                        supervisor_of[member] = team.lead
                        reports_of.setdefault(team.lead, []).append(member)

            # Explicit reporting lines (highest priority — override)
            for line in dept.reporting_lines:
                old_sup = supervisor_of.get(line.subordinate)
                if old_sup == line.supervisor:
                    continue
                if old_sup is not None:
                    # Remove from old supervisor's reports
                    old_reports = reports_of.get(old_sup, [])
                    reports_of[old_sup] = [
                        r for r in old_reports if r != line.subordinate
                    ]
                supervisor_of[line.subordinate] = line.supervisor
                reports_of.setdefault(line.supervisor, []).append(
                    line.subordinate,
                )

        # Cycle detection
        self._detect_cycles(supervisor_of)

        # Freeze internal state with MappingProxyType
        self._supervisor_of: MappingProxyType[str, str] = MappingProxyType(
            supervisor_of,
        )
        self._reports_of: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
            {k: tuple(v) for k, v in reports_of.items()}
        )
        self._known_agents: frozenset[str] = frozenset(
            set(supervisor_of.keys())
            | set(supervisor_of.values())
            | set(reports_of.keys())
        )

        logger.debug(
            DELEGATION_HIERARCHY_BUILT,
            agents=len(supervisor_of),
            supervisors=len(reports_of),
        )

    @staticmethod
    def _detect_cycles(supervisor_of: dict[str, str]) -> None:
        """Detect cycles in the supervisor graph via single-pass chain walking.

        Uses a visited/in-stack approach for O(n) complexity — each
        agent is processed at most once.

        Args:
            supervisor_of: Mapping from agent to supervisor.

        Raises:
            HierarchyResolutionError: If a cycle is found.
        """
        visited: set[str] = set()

        for agent in supervisor_of:
            if agent in visited:
                continue
            in_stack: set[str] = set()
            current: str | None = agent
            while current is not None and current not in visited:
                if current in in_stack:
                    logger.warning(
                        DELEGATION_HIERARCHY_CYCLE,
                        agent=agent,
                        cycle_at=current,
                    )
                    msg = (
                        f"Cycle detected in hierarchy at "
                        f"{current!r} (starting from {agent!r})"
                    )
                    raise HierarchyResolutionError(
                        msg,
                        context={
                            "agent": agent,
                            "cycle_at": current,
                        },
                    )
                in_stack.add(current)
                current = supervisor_of.get(current)
            visited.update(in_stack)

    def get_supervisor(self, agent_name: str) -> str | None:
        """Get the direct supervisor of an agent.

        Args:
            agent_name: Agent name to look up.

        Returns:
            Supervisor name or None if the agent is at the top.
        """
        return self._supervisor_of.get(agent_name)

    def get_direct_reports(
        self,
        agent_name: str,
    ) -> tuple[str, ...]:
        """Get all direct reports of an agent.

        Args:
            agent_name: Supervisor agent name.

        Returns:
            Tuple of direct report agent names.
        """
        return self._reports_of.get(agent_name, ())

    def is_direct_report(
        self,
        supervisor: str,
        subordinate: str,
    ) -> bool:
        """Check if subordinate directly reports to supervisor.

        Args:
            supervisor: Supervisor agent name.
            subordinate: Potential subordinate agent name.

        Returns:
            True if subordinate is a direct report.
        """
        return subordinate in self.get_direct_reports(supervisor)

    def is_subordinate(
        self,
        supervisor: str,
        subordinate: str,
    ) -> bool:
        """Check if subordinate is anywhere below supervisor.

        Walks up the hierarchy from subordinate to root.

        Args:
            supervisor: Supervisor agent name.
            subordinate: Potential subordinate agent name.

        Returns:
            True if subordinate is below supervisor at any depth.
        """
        current = subordinate
        while current in self._supervisor_of:
            current = self._supervisor_of[current]
            if current == supervisor:
                return True
        return False

    def get_ancestors(self, agent_name: str) -> tuple[str, ...]:
        """Get all ancestors from agent up to root.

        Args:
            agent_name: Agent to start from.

        Returns:
            Tuple of ancestor names, bottom-up (immediate supervisor
            first, root last).
        """
        ancestors: list[str] = []
        current = agent_name
        while current in self._supervisor_of:
            current = self._supervisor_of[current]
            ancestors.append(current)
        return tuple(ancestors)

    def get_lowest_common_manager(
        self,
        agent_a: str,
        agent_b: str,
    ) -> str | None:
        """Find the lowest common manager of two agents.

        If one agent is an ancestor of the other, that agent is
        returned as the LCM.  When both arguments refer to the same
        agent, that agent is returned directly.

        Args:
            agent_a: First agent name.
            agent_b: Second agent name.

        Returns:
            Name of the lowest common manager, or None if no
            common manager exists.
        """
        if agent_a == agent_b:
            if agent_a not in self._known_agents:
                return None
            return agent_a
        ancestors_a = self.get_ancestors(agent_a)
        ancestors_b_set = set(self.get_ancestors(agent_b))
        # Check if agent_a is an ancestor of agent_b
        if agent_a in ancestors_b_set:
            return agent_a
        # Check if agent_b is an ancestor of agent_a
        if agent_b in set(ancestors_a):
            return agent_b
        # Walk agent_a's ancestors bottom-up; first hit in agent_b's
        # ancestor set is the LCM
        for ancestor in ancestors_a:
            if ancestor in ancestors_b_set:
                return ancestor
        return None

    def get_delegation_depth(
        self,
        from_agent: str,
        to_agent: str,
    ) -> int | None:
        """Get hierarchy levels between two agents.

        Args:
            from_agent: Higher-level agent (potential supervisor).
            to_agent: Lower-level agent (potential subordinate).

        Returns:
            Number of hierarchy levels between them, or None if
            to_agent is not below from_agent.
        """
        depth = 0
        current = to_agent
        while current in self._supervisor_of:
            current = self._supervisor_of[current]
            depth += 1
            if current == from_agent:
                return depth
        return None
