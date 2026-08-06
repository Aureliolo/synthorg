"""A per-feature model an operator names takes effect without a restart.

Every Chief-of-Staff feature model is blank by default and baked into its
component at construction, so the setting has to reach the reconciler twice
over: to bring an unwired feature up when a model is first named, and to
replace a running one when the model changes or is cleared. These drive the
shipped declarations and the shipped wiring through a real pass, because the
defect they cover was a declaration that looked complete and wired nothing.
"""

from collections.abc import Mapping

import pytest

from synthorg.api.state import AppState
from synthorg.api.subsystems.registry import SUBSYSTEMS
from synthorg.api.subsystems.runtime import reconcile_subsystems
from synthorg.api.subsystems.spec import SubsystemSpec
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.meta.state import MetaStateSlice
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings import definitions as _definitions  # noqa: F401
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.models import SettingValue
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_MODEL = serialize_model_ref(
    ModelRef(provider="example-provider", model_id="example-medium-001")
)
_OTHER_MODEL = serialize_model_ref(
    ModelRef(provider="example-provider", model_id="example-small-001")
)


class _Values:
    """The settings a pass reads, rewritable between passes.

    The reconciler snapshots a subsystem's declared settings through the real
    resolver to spot an operator edit, so moving a value here is what a write
    looks like from the reconciler's side. Keys the test never sets fall back
    to the registered default, so the rest of the pass behaves as it would on
    a fresh install rather than reading every setting as blank.
    """

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def get(self, namespace: str, key: str) -> SettingValue:
        """Resolve one setting from the mutable map.

        Returns:
            The current value, or the registered default for an unset key.
        """
        definition = get_registry().get(namespace, key)
        default = "" if definition is None else str(definition.default or "")
        return SettingValue(
            namespace=SettingNamespace(namespace),
            key=NotBlankStr(key),
            value=self.values.get(f"{namespace}.{key}", default),
            source=SettingSource.DATABASE,
        )


def _spec(name: str) -> SubsystemSpec:
    """Return the shipped spec called *name*.

    Returns:
        The declaration, so a rename fails here rather than silently passing.
    """
    for spec in SUBSYSTEMS:
        if spec.name == name:
            return spec
    msg = f"no subsystem declared as {name!r}"
    raise AssertionError(msg)


def _binds_a_cos_model(spec: SubsystemSpec) -> bool:
    """Report whether *spec* declares a Chief-of-Staff per-feature model.

    Returns:
        ``True`` when any declared setting is a ``chief_of_staff.*_model``.
    """
    return any(
        key.startswith("chief_of_staff.") and key.endswith("_model")
        for key in spec.settings
    )


def _app_state(values: dict[str, str]) -> tuple[AppState, _Values]:
    """Build state with a provider registry and a rewritable settings source.

    Returns:
        The state and the source, so a test can move a value between passes.
    """
    source = _Values(values)
    config = RootConfig(company_name="test")
    resolver = ConfigResolver(
        settings_service=mock_of[SettingsServiceProtocol](get=source.get),
        config=config,
    )
    registry = mock_of[ProviderRegistry](
        get=lambda _name: mock_of[CompletionProvider]()
    )
    app_state = make_app_state(config=config, provider_registry=registry)
    app_state.wire(SettingsStateSlice, config_resolver=resolver)
    return app_state, source


async def _pass(app_state: AppState, config_overrides: Mapping[str, str]) -> None:
    """Run one reconcile pass over a config carrying *config_overrides*.

    The self-improvement config is cached on the meta slice and invalidated by
    its own settings subscriber, so a test that moves a setting has to move the
    cache with it: seeding the field directly is that invalidation, without
    standing up a settings service to re-parse through.
    """
    from synthorg.meta.config import SelfImprovementConfig

    config = SelfImprovementConfig().model_copy(
        update={
            "chief_of_staff": SelfImprovementConfig().chief_of_staff.model_copy(
                update=dict(config_overrides)
            )
        }
    )
    app_state.wire(MetaStateSlice, self_improvement_config=config)
    await reconcile_subsystems(app_state, trigger="test")


class TestTurnIntentClassifierComesUpOnAWrite:
    """The acceptance case from the dogfood: name the model, get a classifier."""

    async def test_naming_the_model_wires_the_classifier_with_no_restart(self) -> None:
        app_state, resolver = _app_state({"chief_of_staff.turn_intent_model": ""})

        await _pass(app_state, {"turn_intent_model": ""})
        assert app_state.slice(MetaStateSlice).turn_intent_classifier is None

        resolver.values["chief_of_staff.turn_intent_model"] = _MODEL
        await _pass(app_state, {"turn_intent_model": _MODEL})

        assert app_state.slice(MetaStateSlice).turn_intent_classifier is not None

    async def test_changing_the_model_replaces_the_classifier(self) -> None:
        # Without a rebuild the first instance keeps classifying on the pair it
        # was built with, so the second write reads as saved and changes nothing.
        app_state, resolver = _app_state({"chief_of_staff.turn_intent_model": _MODEL})
        await _pass(app_state, {"turn_intent_model": _MODEL})
        first = app_state.slice(MetaStateSlice).turn_intent_classifier
        assert first is not None

        resolver.values["chief_of_staff.turn_intent_model"] = _OTHER_MODEL
        await _pass(app_state, {"turn_intent_model": _OTHER_MODEL})

        second = app_state.slice(MetaStateSlice).turn_intent_classifier
        assert second is not None
        assert second is not first

    async def test_clearing_the_model_takes_the_classifier_down(self) -> None:
        # The direction a teardown-less declaration cannot express: switched on
        # without a restart but never off again.
        app_state, resolver = _app_state({"chief_of_staff.turn_intent_model": _MODEL})
        await _pass(app_state, {"turn_intent_model": _MODEL})
        assert app_state.slice(MetaStateSlice).turn_intent_classifier is not None

        resolver.values["chief_of_staff.turn_intent_model"] = ""
        await _pass(app_state, {"turn_intent_model": ""})

        assert app_state.slice(MetaStateSlice).turn_intent_classifier is None


class TestMultiVoiceRouterComesUpOnAWrite:
    """The classifier's sibling, which had the identical declaration gap."""

    async def test_naming_the_model_wires_the_router_with_no_restart(self) -> None:
        app_state, resolver = _app_state({"chief_of_staff.multi_voice_model": ""})

        await _pass(app_state, {"multi_voice_model": ""})
        assert app_state.slice(MetaStateSlice).multi_voice_router is None

        resolver.values["chief_of_staff.multi_voice_model"] = _MODEL
        await _pass(app_state, {"multi_voice_model": _MODEL})

        assert app_state.slice(MetaStateSlice).multi_voice_router is not None

    async def test_clearing_the_model_takes_the_router_down(self) -> None:
        app_state, resolver = _app_state({"chief_of_staff.multi_voice_model": _MODEL})
        await _pass(app_state, {"multi_voice_model": _MODEL})
        assert app_state.slice(MetaStateSlice).multi_voice_router is not None

        resolver.values["chief_of_staff.multi_voice_model"] = ""
        await _pass(app_state, {"multi_voice_model": ""})

        assert app_state.slice(MetaStateSlice).multi_voice_router is None


class TestChiefOfStaffChatFollowsItsModel:
    """The same shape on the chat surface, found by the audit rather than the run."""

    async def test_changing_the_model_replaces_the_chat_backend(self) -> None:
        app_state, resolver = _app_state({"chief_of_staff.chat_model": _MODEL})
        await _pass(app_state, {"chat_model": _MODEL})
        first = app_state.slice(MetaStateSlice).chief_of_staff_chat
        assert first is not None

        resolver.values["chief_of_staff.chat_model"] = _OTHER_MODEL
        await _pass(app_state, {"chat_model": _OTHER_MODEL})

        second = app_state.slice(MetaStateSlice).chief_of_staff_chat
        assert second is not None
        assert second is not first

    async def test_clearing_the_model_takes_the_chat_backend_down(self) -> None:
        app_state, resolver = _app_state({"chief_of_staff.chat_model": _MODEL})
        await _pass(app_state, {"chat_model": _MODEL})
        assert app_state.slice(MetaStateSlice).chief_of_staff_chat is not None

        resolver.values["chief_of_staff.chat_model"] = ""
        await _pass(app_state, {"chat_model": ""})

        assert app_state.slice(MetaStateSlice).chief_of_staff_chat is None


class TestEveryPerFeatureModelCanBeReplaced:
    """Derived from the declarations, so a new feature model joins the rule.

    Scoped to the Chief-of-Staff feature models because those components
    resolve one ``(provider, model)`` pair and hold the client: the pair they
    were built with is the pair they keep. A feature that instead takes the
    registry and re-reads its own key per call (the charter interviewer) is
    already live and needs no rebuild, which is why the family and not every
    ``*_model`` setting is the population here.
    """

    @pytest.mark.parametrize(
        "spec",
        [spec for spec in SUBSYSTEMS if _binds_a_cos_model(spec)],
        ids=lambda spec: spec.name,
    )
    def test_a_declared_model_setting_comes_with_a_rebuild(
        self, spec: SubsystemSpec
    ) -> None:
        # Declaring the key alone only buys the unwired -> wired transition. A
        # component that bakes the model in at construction also has to be
        # replaceable, or the operator's second write is the one that silently
        # does nothing.
        assert spec.rebuild_on_change, (
            f"{spec.name} declares a model setting with no rebuild_on_change; "
            "changing the model would leave the built instance serving"
        )
        assert spec.deactivate is not None

    def test_the_classifier_and_router_are_not_wired_inside_the_proposer(self) -> None:
        # The declaration that failed: wiring them from the proposer's
        # activation, which the reconciler never re-runs once it is up.
        proposer = _spec("chief_of_staff_proposer")

        assert "chief_of_staff.turn_intent_model" not in proposer.settings
        assert "chief_of_staff.multi_voice_model" not in proposer.settings
        assert _spec("turn_intent_classifier").settings == (
            "chief_of_staff.turn_intent_model",
        )
        assert _spec("multi_voice_router").settings == (
            "chief_of_staff.multi_voice_model",
        )
