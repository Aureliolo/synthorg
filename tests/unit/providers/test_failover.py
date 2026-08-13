"""The operator's declared alternates: parsed, keyed, and never guessed."""

import json

import pytest

from synthorg.providers.failover import (
    EMPTY_ROUTES,
    RETRYABLE_ON_ALTERNATE,
    FailoverRoutes,
    parse_failover_routes,
    parse_route_key,
    route_key,
)
from synthorg.providers.health import ProviderOutcomeClass
from synthorg.settings.model_ref import ModelRef

pytestmark = pytest.mark.unit

_DECLARED = ModelRef(provider="example-provider", model_id="example-expert-001")
_ALTERNATE = ModelRef(provider="test-provider", model_id="example-capable-001")


def _stored(**routes: dict[str, str]) -> str:
    return json.dumps(routes)


class TestRouteKey:
    def test_key_round_trips_through_its_parser(self) -> None:
        # The writer and the reader must agree character for character, or a
        # declared route resolves for nobody.
        assert parse_route_key(route_key(_DECLARED)) == _DECLARED

    def test_a_model_id_carrying_a_slash_survives(self) -> None:
        # A model id routinely carries one (`vendor/model-y` is one model); a
        # connection name never does, so the split takes the first separator.
        ref = ModelRef(provider="example-provider", model_id="vendor/model-y")
        assert parse_route_key(route_key(ref)) == ref

    def test_a_key_naming_no_pair_parses_unbound(self) -> None:
        assert not parse_route_key("example-provider").is_bound


class TestResolution:
    def test_declared_pair_resolves_to_its_alternate(self) -> None:
        routes = parse_failover_routes(
            _stored(**{route_key(_DECLARED): _ALTERNATE.model_dump()})
        )
        assert routes.alternate_for(_DECLARED) == _ALTERNATE

    def test_an_undeclared_pair_has_no_alternate(self) -> None:
        # No nearest match, no provider scan, no default: a pair with no entry
        # reads exactly like the feature being off.
        routes = parse_failover_routes(
            _stored(**{route_key(_DECLARED): _ALTERNATE.model_dump()})
        )
        other = ModelRef(provider="example-provider", model_id="example-basic-001")
        assert routes.alternate_for(other) is None

    def test_a_different_connection_on_the_same_model_is_undeclared(self) -> None:
        # The same model id through two connections is two different calls, so
        # a route declared for one must not resolve for the other.
        routes = parse_failover_routes(
            _stored(**{route_key(_DECLARED): _ALTERNATE.model_dump()})
        )
        elsewhere = ModelRef(provider="other-provider", model_id=_DECLARED.model_id)
        assert routes.alternate_for(elsewhere) is None

    def test_a_route_to_itself_is_not_a_fallback(self) -> None:
        # Serving it would report a failover that changed nothing.
        routes = parse_failover_routes(
            _stored(**{route_key(_DECLARED): _DECLARED.model_dump()})
        )
        assert routes.alternate_for(_DECLARED) is None

    def test_an_unbound_declared_pair_resolves_nothing(self) -> None:
        routes = parse_failover_routes(
            _stored(**{route_key(_DECLARED): _ALTERNATE.model_dump()})
        )
        assert routes.alternate_for(ModelRef(model_id="example-expert-001")) is None


class TestParsing:
    def test_unset_yields_no_routes(self) -> None:
        assert len(parse_failover_routes(None)) == 0
        assert len(parse_failover_routes("   ")) == 0

    def test_malformed_json_yields_no_routes(self) -> None:
        # A half-read table would fail over some pairs and not others with
        # nothing saying which; no failover is the safe reading of "we could
        # not tell what you declared".
        assert parse_failover_routes("{not json") is EMPTY_ROUTES

    def test_a_non_object_yields_no_routes(self) -> None:
        assert parse_failover_routes("[1, 2]") is EMPTY_ROUTES

    def test_a_provider_less_alternate_is_dropped(self) -> None:
        # An alternate with no connection names no dispatch target, so it is
        # refused at parse rather than resolved into one later.
        routes = parse_failover_routes(
            _stored(**{route_key(_DECLARED): {"model_id": "example-capable-001"}})
        )
        assert routes.alternate_for(_DECLARED) is None

    def test_one_bad_entry_does_not_take_the_good_ones(self) -> None:
        other = ModelRef(provider="example-provider", model_id="example-basic-001")
        routes = parse_failover_routes(
            json.dumps(
                {
                    route_key(_DECLARED): _ALTERNATE.model_dump(),
                    route_key(other): {"model_id": "no-connection"},
                }
            )
        )
        assert routes.alternate_for(_DECLARED) == _ALTERNATE
        assert routes.alternate_for(other) is None


class TestDeclaredPairs:
    def test_reports_every_resolvable_route(self) -> None:
        other = ModelRef(provider="example-provider", model_id="example-basic-001")
        routes = parse_failover_routes(
            json.dumps(
                {
                    route_key(_DECLARED): _ALTERNATE.model_dump(),
                    route_key(other): _ALTERNATE.model_dump(),
                }
            )
        )
        assert routes.declared_pairs() == (
            (other, _ALTERNATE),
            (_DECLARED, _ALTERNATE),
        )

    def test_a_self_route_is_absent_from_the_report(self) -> None:
        # What is displayed and what resolves must be the same set, or an
        # operator reads a route as active that never fires.
        routes = parse_failover_routes(
            _stored(**{route_key(_DECLARED): _DECLARED.model_dump()})
        )
        assert routes.declared_pairs() == ()

    def test_empty_map_reports_nothing(self) -> None:
        assert FailoverRoutes({}).declared_pairs() == ()


class TestRetryableSet:
    @pytest.mark.parametrize(
        "outcome",
        [
            ProviderOutcomeClass.INTERNAL,
            ProviderOutcomeClass.OVERLOADED,
            ProviderOutcomeClass.RATE_LIMIT,
            ProviderOutcomeClass.PAYMENT_REQUIRED,
            ProviderOutcomeClass.TIMEOUT,
            ProviderOutcomeClass.CONNECTION,
        ],
    )
    def test_a_failing_connection_is_worth_retrying_elsewhere(
        self, outcome: ProviderOutcomeClass
    ) -> None:
        assert outcome in RETRYABLE_ON_ALTERNATE

    @pytest.mark.parametrize(
        "outcome",
        [
            ProviderOutcomeClass.INVALID_REQUEST,
            ProviderOutcomeClass.AUTH,
            ProviderOutcomeClass.CONTENT_FILTER,
            ProviderOutcomeClass.NOT_FOUND,
            ProviderOutcomeClass.SUCCESS,
        ],
    )
    def test_a_failure_about_the_request_is_not(
        self, outcome: ProviderOutcomeClass
    ) -> None:
        # The alternate would fail identically, so the retry costs the caller a
        # second round-trip on top of the first and buys nothing.
        assert outcome not in RETRYABLE_ON_ALTERNATE
