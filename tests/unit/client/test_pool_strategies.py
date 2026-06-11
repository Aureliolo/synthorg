"""Unit tests for ClientPool and pool selection strategies."""

import pytest

from synthorg.client.models import (
    ClientFeedback,
    ClientProfile,
    GenerationContext,
    PoolConstraints,
    ReviewContext,
    TaskRequirement,
)
from synthorg.client.pool import (
    ClientPool,
    DomainMatchedStrategy,
    RoundRobinStrategy,
    WeightedRandomStrategy,
)


class _StubClient:
    """Minimal ClientInterface stub for pool tests."""

    def __init__(self, profile: ClientProfile) -> None:
        self.profile = profile

    async def submit_requirement(
        self,
        context: GenerationContext,
    ) -> TaskRequirement | None:
        return None

    async def review_deliverable(
        self,
        context: ReviewContext,
    ) -> ClientFeedback:
        from synthorg.client.models import ClientFeedback

        return ClientFeedback(
            task_id="stub",
            client_id=self.profile.client_id,
            accepted=True,
        )


def _profile(
    client_id: str = "c-1",
    *,
    strictness: float = 0.5,
    domains: tuple[str, ...] = (),
) -> ClientProfile:
    return ClientProfile(
        client_id=client_id,
        name=f"Client {client_id}",
        persona="test",
        expertise_domains=domains,
        strictness_level=strictness,
    )


def _client(
    client_id: str = "c-1",
    *,
    strictness: float = 0.5,
    domains: tuple[str, ...] = (),
) -> _StubClient:
    return _StubClient(
        _profile(client_id, strictness=strictness, domains=domains),
    )


@pytest.mark.unit
class TestClientPool:
    async def test_add_and_list(self) -> None:
        pool = ClientPool()
        profile = _profile("a")
        await pool.add(profile=profile, client=_StubClient(profile))
        profiles = await pool.list_profiles()
        assert len(profiles) == 1
        assert profiles[0].client_id == "a"

    async def test_deactivate_excludes_from_list(self) -> None:
        pool = ClientPool()
        p = _profile("d")
        await pool.add(profile=p, client=_StubClient(p))
        await pool.deactivate("d")
        assert len(await pool.list_profiles()) == 0
        assert len(await pool.list_profiles(include_inactive=True)) == 1

    async def test_reactivate_includes_again(self) -> None:
        pool = ClientPool()
        p = _profile("r")
        await pool.add(profile=p, client=_StubClient(p))
        await pool.deactivate("r")
        await pool.reactivate("r")
        assert len(await pool.list_profiles()) == 1

    async def test_has_profile(self) -> None:
        pool = ClientPool()
        p = _profile("h")
        await pool.add(profile=p, client=_StubClient(p))
        assert await pool.has_profile("h") is True
        assert await pool.has_profile("missing") is False

    async def test_re_add_reactivates(self) -> None:
        pool = ClientPool()
        p = _profile("re")
        c = _StubClient(p)
        await pool.add(profile=p, client=c)
        await pool.deactivate("re")
        await pool.add(profile=p, client=c)
        assert await pool.is_active("re") is True

    async def test_remove_cleans_up(self) -> None:
        pool = ClientPool()
        p = _profile("rm")
        await pool.add(profile=p, client=_StubClient(p))
        removed = await pool.remove("rm")
        assert removed.client_id == "rm"
        assert await pool.has_profile("rm") is False

    async def test_size_counts_active_only(self) -> None:
        pool = ClientPool()
        for cid in ("a", "b", "c"):
            p = _profile(cid)
            await pool.add(profile=p, client=_StubClient(p))
        await pool.deactivate("b")
        assert await pool.size() == 2


@pytest.mark.unit
class TestRoundRobinStrategy:
    async def test_cycles_through_clients(self) -> None:
        strategy = RoundRobinStrategy()
        clients = tuple(_client(f"c{i}") for i in range(3))
        constraints = PoolConstraints(max_clients=1)
        ids = []
        for _ in range(6):
            selected = await strategy.select_clients(clients, constraints)
            cid = selected[0].profile.client_id  # type: ignore[attr-defined]
            ids.append(cid)
        assert ids == ["c0", "c1", "c2", "c0", "c1", "c2"]

    async def test_empty_pool_returns_empty(self) -> None:
        strategy = RoundRobinStrategy()
        result = await strategy.select_clients(
            (),
            PoolConstraints(max_clients=1),
        )
        assert result == ()


@pytest.mark.unit
class TestWeightedRandomStrategy:
    async def test_seeded_deterministic(self) -> None:
        clients = tuple(_client(f"c{i}", strictness=0.1 * (i + 1)) for i in range(5))
        constraints = PoolConstraints(max_clients=2)
        first = await WeightedRandomStrategy(seed=42).select_clients(
            clients,
            constraints,
        )
        second = await WeightedRandomStrategy(seed=42).select_clients(
            clients,
            constraints,
        )
        assert len(first) == 2
        ids_a = [c.profile.client_id for c in first]  # type: ignore[attr-defined]
        ids_b = [c.profile.client_id for c in second]  # type: ignore[attr-defined]
        assert ids_a == ids_b

    async def test_empty_pool_returns_empty(self) -> None:
        strategy = WeightedRandomStrategy(seed=0)
        result = await strategy.select_clients(
            (),
            PoolConstraints(max_clients=1),
        )
        assert result == ()


@pytest.mark.unit
class TestDomainMatchedStrategy:
    async def test_filters_by_domain(self) -> None:
        strategy = DomainMatchedStrategy()
        c1 = _client("c1", domains=("backend", "security"))
        c2 = _client("c2", domains=("frontend",))
        constraints = PoolConstraints(
            required_domains=("backend",),
            max_clients=5,
        )
        result = await strategy.select_clients((c1, c2), constraints)
        assert len(result) == 1
        assert result[0].profile.client_id == "c1"  # type: ignore[attr-defined]

    async def test_no_required_domains_returns_all(self) -> None:
        strategy = DomainMatchedStrategy()
        clients = tuple(_client(f"c{i}") for i in range(3))
        constraints = PoolConstraints(max_clients=5)
        result = await strategy.select_clients(clients, constraints)
        assert len(result) == 3
