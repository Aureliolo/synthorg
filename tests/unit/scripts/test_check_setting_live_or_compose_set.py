"""Tests for the compose-set-or-live gate.

The gate answers one question per writable setting: can an operator's write
reach anything that is running? These tests pin each answer it is allowed to
give, every seam it accepts as evidence, the construction path it refuses to
accept, and the two ways it must fail closed rather than pass a partial scan.
"""

import textwrap
from pathlib import Path

import pytest
from scripts._setting_reachability_definitions import load_definitions
from scripts.check_setting_live_or_compose_set import (
    Violation,
    _load_baseline,
    main,
    run_with_baseline,
    scan_repo,
    write_baseline,
)

from tests.unit.scripts._setting_live_or_compose_set_helpers import (
    REPO_ROOT,
    definitions_module,
    make_repo,
    registration,
    registry_module,
    runtime_builder_module,
    subscriber_module,
)

pytestmark = pytest.mark.unit

_CONSUMER = "engine/consumer.py"
_READ = 'async def read(r):\n    return await r.get_bool("engine", "knob")\n'
_BUILD = 'async def build(r):\n    return await r.get_bool("engine", "knob")\n'
_WIRING = "api/lifecycle_helpers/thing_wiring.py"
_WIRE = (
    "async def wire_thing(state):\n"
    '    return await state.r.get_bool("engine", "knob")\n'
)
_DEFINITION_FILE = "src/synthorg/settings/definitions/engine.py"
_RUNTIME_BUILDER = "workers/runtime_builder.py"


def _repo(tmp_path: Path, **kwargs: object) -> Path:
    """Build a fake repo declaring one writable ``engine.knob`` setting."""
    kwargs.setdefault(
        "definitions", {"engine.py": definitions_module(registration("knob"))}
    )
    return make_repo(tmp_path, **kwargs)  # type: ignore[arg-type]


def _lines(*lines: str) -> str:
    """Join *lines* into a module body."""
    return "\n".join(lines) + "\n"


def _keys(violations: list[Violation]) -> list[str]:
    """Baseline keys of *violations*, sorted."""
    return sorted(v.baseline_key() for v in violations)


class TestReachabilitySeams:
    """Each sanctioned live seam on its own satisfies the gate."""

    @pytest.mark.parametrize(
        "body",
        [
            _READ,
            _lines(
                "async def read(r):",
                '    return await gate(state, "engine", "knob", x=1)',
            ),
            _lines(
                "async def read(r):",
                '    return await flag(namespace="engine", key="knob", fallback=True)',
            ),
            _lines(
                "async def read(r):",
                "    return await r._resolve_bridge_fields(",
                '        "engine", (("knob", "bool"),)',
                "    )",
            ),
            _lines(
                "_FIELDS = (",
                "    MirrorField(",
                '        field="knob",',
                "        namespace=SettingNamespace.ENGINE,",
                '        key="knob",',
                "    ),",
                ")",
            ),
            _lines(
                "async def read(svc):",
                '    return await svc.get_namespace("engine")',
            ),
            _lines('_DECLARED = ("engine.knob",)'),
            _lines(
                "_NS = SettingNamespace.ENGINE",
                '_KEY = "knob"',
                "",
                "",
                "async def read(r):",
                "    return await r.get_bool(_NS, _KEY)",
            ),
            _lines(
                "_KEYS = (",
                '    ("knob", 1),',
                ")",
                "",
                "",
                "async def read(r):",
                "    for key, _default in _KEYS:",
                '        await r.get_int("engine", key)',
            ),
            _lines(
                "async def _read(r, key):",
                '    return await r.get_bool("engine", key)',
                "",
                "",
                "async def read(r):",
                '    return await _read(r, "knob")',
            ),
            _lines(
                "async def _read(*, r, key):",
                '    return await r.get_bool("engine", key)',
                "",
                "",
                "async def read(r):",
                '    return await _read(r=r, key="knob")',
            ),
            _lines(
                "class Reader:",
                "    async def _resolve(self, key, default):",
                '        return await self._r.get_bool("engine", key)',
                "",
                "    async def read(self):",
                '        return await self._resolve("knob", False)',
            ),
            _lines(
                "async def _read_ns(svc, namespace):",
                "    return await svc.get_namespace(namespace)",
                "",
                "",
                "async def read(svc):",
                '    return await _read_ns(svc, "engine")',
            ),
            _lines(
                "async def read(svc):",
                '    return await svc.get_all("engine")',
            ),
        ],
        ids=[
            "positional-literals",
            "adjacent-pair-mid-call",
            "namespace-key-keywords",
            "bridge-fields",
            "mirror-field",
            "namespace-bulk-read",
            "dotted-literal",
            "module-level-constants",
            "loop-over-a-key-collection",
            "key-forwarded-through-a-helper",
            "key-forwarded-by-keyword",
            "key-forwarded-through-a-method",
            "namespace-forwarded-through-a-helper",
            "get-all-bulk-read",
        ],
    )
    def test_seam_satisfies_the_gate(self, tmp_path: Path, body: str) -> None:
        root = _repo(tmp_path, sources={_CONSUMER: body})
        assert scan_repo(root) == []

    def test_enabled_by_alone_satisfies_the_gate(self, tmp_path: Path) -> None:
        # The reconciler evaluates enabled_by on every pass, before and
        # independently of any rebuild, so a write flips the subsystem.
        root = _repo(
            tmp_path,
            registry=registry_module(enabled_by="engine.knob", rebuild_on_change=False),
        )
        assert scan_repo(root) == []

    def test_subscriber_watched_pair_satisfies_the_gate(self, tmp_path: Path) -> None:
        root = _repo(
            tmp_path,
            subscribers={"knob_subscriber.py": subscriber_module(("engine", "knob"))},
        )
        assert scan_repo(root) == []

    def test_subsystem_settings_declaration_satisfies_the_gate(
        self, tmp_path: Path
    ) -> None:
        root = _repo(tmp_path, registry=registry_module(settings=("engine.knob",)))
        assert scan_repo(root) == []

    def test_dashboard_reference_satisfies_the_gate(self, tmp_path: Path) -> None:
        # The dashboard persists nothing and re-fetches through GET /settings,
        # so a key it names is live without any Python consumer.
        root = _repo(
            tmp_path,
            web={
                "stores/prefs.ts": (
                    'await updateSetting("engine", "knob", { value });\n'
                )
            },
        )
        assert scan_repo(root) == []


class TestUnreachable:
    """A writable setting nothing can reach is the violation."""

    def test_no_evidence_anywhere_fails(self, tmp_path: Path) -> None:
        body = definitions_module(registration("knob"))
        root = make_repo(tmp_path, definitions={"engine.py": body})
        violations = scan_repo(root)
        assert _keys(violations) == ["engine.knob:unreachable"]
        assert violations[0].source_file == _DEFINITION_FILE
        # The exact line, not merely a non-sentinel one: an off-by-one or a
        # report against the enclosing register( call would still be non-zero.
        expected = next(
            number
            for number, line in enumerate(body.splitlines(), start=1)
            if "SettingDefinition(" in line
        )
        assert violations[0].source_line == expected

    def test_the_failure_message_names_the_definition_site(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _repo(tmp_path)
        assert main(["--repo-root", str(root)]) == 1
        out = capsys.readouterr()
        assert f"{_DEFINITION_FILE}:" in out.out
        assert "engine.knob" in out.out

    def test_a_different_key_in_the_same_namespace_is_not_evidence(
        self, tmp_path: Path
    ) -> None:
        root = _repo(
            tmp_path,
            sources={
                _CONSUMER: _lines(
                    "async def read(r):",
                    '    return await r.get_bool("engine", "other_knob")',
                )
            },
        )
        assert _keys(scan_repo(root)) == ["engine.knob:unreachable"]

    def test_a_compose_set_setting_is_out_of_scope(self, tmp_path: Path) -> None:
        # The sibling gate owns compose_set; this one must never flag it, or a
        # bind address would need a live consumer it cannot have.
        root = make_repo(
            tmp_path,
            definitions={
                "api.py": definitions_module(
                    registration(
                        "server_host", namespace_member="API", compose_set=True
                    )
                )
            },
        )
        assert scan_repo(root) == []

    def test_a_dashboard_test_file_is_not_evidence(self, tmp_path: Path) -> None:
        # A key named only by a dashboard test has no live consumer: the test
        # proves the store parses it, not that anything reads it.
        root = _repo(
            tmp_path,
            web={"__tests__/prefs.test.ts": 'const K = "knob";\n'},
        )
        assert _keys(scan_repo(root)) == ["engine.knob:unreachable"]

    @pytest.mark.parametrize(
        "path",
        ["__tests__/prefs.test.ts", "stores/prefs.test.ts"],
        ids=["tests-directory", "colocated-with-source"],
    )
    def test_a_colocated_dashboard_test_is_not_evidence(
        self, tmp_path: Path, path: str
    ) -> None:
        root = _repo(
            tmp_path, web={path: 'updateSetting("engine", "knob", { value });\n'}
        )
        assert _keys(scan_repo(root)) == ["engine.knob:unreachable"]

    def test_a_bare_key_without_its_namespace_is_not_evidence(
        self, tmp_path: Path
    ) -> None:
        # Eight settings are keyed "enabled". Matching the key alone would let
        # one unrelated token certify every one of them.
        root = _repo(tmp_path, web={"stores/prefs.ts": 'const label = "knob";\n'})
        assert _keys(scan_repo(root)) == ["engine.knob:unreachable"]

    def test_a_generated_api_type_is_not_evidence(self, tmp_path: Path) -> None:
        # Generated types spell every schema name whether anything reads it.
        root = _repo(
            tmp_path,
            web={
                "api/types/openapi.gen.ts": ('type N = "engine";\ntype K = "knob";\n')
            },
        )
        assert _keys(scan_repo(root)) == ["engine.knob:unreachable"]

    def test_a_settings_declaration_without_a_rebuild_is_not_liveness(
        self, tmp_path: Path
    ) -> None:
        # Without rebuild_on_change the reconciler short-circuits on an already
        # active subsystem, so the write is watched but replaces nothing.
        root = _repo(
            tmp_path,
            registry=registry_module(
                settings=("engine.knob",), rebuild_on_change=False
            ),
            sources={_WIRING: _WIRE},
        )
        assert _keys(scan_repo(root)) == ["engine.knob:construction-only"]

    def test_a_cross_row_pairing_of_one_loop_is_not_evidence(
        self, tmp_path: Path
    ) -> None:
        # Unpacking binds a namespace and a key one row at a time; pairing them
        # across rows would credit a read the loop never performs.
        root = _repo(
            tmp_path,
            sources={
                _CONSUMER: _lines(
                    '_ROUTES = {"memory": "knob", "engine": "other"}',
                    "",
                    "",
                    "async def read(r):",
                    "    for ns, key in _ROUTES.items():",
                    "        await r.get_bool(ns, key)",
                )
            },
        )
        assert _keys(scan_repo(root)) == ["engine.knob:unreachable"]

    def test_a_name_bound_to_two_collections_resolves_to_neither(
        self, tmp_path: Path
    ) -> None:
        # Walk order, not source order, would decide which binding won.
        root = _repo(
            tmp_path,
            sources={
                _CONSUMER: _lines(
                    '_KEYS = ("other",)',
                    "",
                    "",
                    "async def shadow(r):",
                    '    _KEYS = ("knob",)',
                    "    for key in _KEYS:",
                    '        await r.get_bool("engine", key)',
                )
            },
        )
        assert _keys(scan_repo(root)) == ["engine.knob:unreachable"]


class TestConstructionPath:
    """A read that only runs while the runtime is built is not liveness."""

    @pytest.mark.parametrize(
        "module",
        ["_openhands_wiring", "_engine_assembly", "_mcp_bridge_wiring"],
    )
    def test_a_runtime_build_read_is_construction_only(
        self, tmp_path: Path, module: str
    ) -> None:
        # The construction path is whatever build_runtime_services imports, so
        # a module reachable from it is assembly code however it is named.
        root = _repo(
            tmp_path,
            sources={
                f"workers/{module}.py": _BUILD,
                _RUNTIME_BUILDER: runtime_builder_module(f"synthorg.workers.{module}"),
            },
        )
        assert _keys(scan_repo(root)) == ["engine.knob:construction-only"]

    def test_a_module_the_builder_reaches_indirectly_is_construction_only(
        self, tmp_path: Path
    ) -> None:
        # The closure is transitive: _openhands_wiring is reached through
        # _engine_assembly, never imported by the builder directly.
        root = _repo(
            tmp_path,
            sources={
                "workers/_openhands_wiring.py": _BUILD,
                "workers/_engine_assembly.py": (
                    "from synthorg.workers._openhands_wiring import wire_it\n"
                ),
                _RUNTIME_BUILDER: runtime_builder_module(
                    "synthorg.workers._engine_assembly"
                ),
            },
        )
        assert _keys(scan_repo(root)) == ["engine.knob:construction-only"]

    def test_a_worker_module_the_builder_never_reaches_stays_live(
        self, tmp_path: Path
    ) -> None:
        # Living under workers/ is not what makes a read construction-only;
        # being reachable from the builder is.
        root = _repo(
            tmp_path,
            sources={
                "workers/execution_service.py": _BUILD,
                _RUNTIME_BUILDER: runtime_builder_module(),
            },
        )
        assert scan_repo(root) == []

    def test_a_read_inside_an_activation_target_is_construction_only(
        self, tmp_path: Path
    ) -> None:
        root = _repo(
            tmp_path,
            registry=registry_module(),
            sources={_WIRING: _WIRE},
        )
        assert _keys(scan_repo(root)) == ["engine.knob:construction-only"]

    def test_a_read_in_a_closure_of_an_activation_is_construction_only(
        self, tmp_path: Path
    ) -> None:
        # Burying the read one scope deeper does not make it run any more often
        # than the activation that owns the closure.
        root = _repo(
            tmp_path,
            registry=registry_module(),
            sources={
                _WIRING: _lines(
                    "async def wire_thing(state):",
                    "    async def inner():",
                    '        return await state.r.get_bool("engine", "knob")',
                    "",
                    "    return inner",
                )
            },
        )
        assert _keys(scan_repo(root)) == ["engine.knob:construction-only"]

    def test_declaring_the_key_on_the_subsystem_makes_it_live(
        self, tmp_path: Path
    ) -> None:
        # A declared key puts the subsystem in the watched set, so a write
        # triggers the reconcile pass that re-runs activation.
        root = _repo(
            tmp_path,
            registry=registry_module(settings=("engine.knob",)),
            sources={_WIRING: _WIRE},
        )
        assert scan_repo(root) == []

    def test_a_sibling_function_in_a_wiring_module_stays_live(
        self, tmp_path: Path
    ) -> None:
        # Only the activation target itself is the construction path; the
        # per-request helpers that share the module are not.
        root = _repo(
            tmp_path,
            registry=registry_module(),
            sources={
                _WIRING: _lines(
                    "async def wire_thing(state):",
                    "    return None",
                    "",
                    "",
                    "async def read_per_request(r):",
                    '    return await r.get_bool("engine", "knob")',
                )
            },
        )
        assert scan_repo(root) == []

    def test_a_live_read_elsewhere_outweighs_a_construction_read(
        self, tmp_path: Path
    ) -> None:
        root = _repo(
            tmp_path,
            sources={
                "workers/_engine_assembly.py": _BUILD,
                # Without this the assembly module is an ordinary consumer, so
                # the assertion would pass on two live reads and keep passing
                # even if the precedence it names stopped working.
                _RUNTIME_BUILDER: runtime_builder_module(
                    "synthorg.workers._engine_assembly"
                ),
                _CONSUMER: _READ,
            },
        )
        assert scan_repo(root) == []


class TestDefinitionScanning:
    """Every registration is scanned, or the gate refuses to report."""

    def test_a_namespace_alias_is_resolved(self, tmp_path: Path) -> None:
        root = make_repo(
            tmp_path,
            definitions={
                "engine.py": definitions_module(
                    registration("knob", namespace_expr="_NS"),
                    preamble="_NS = SettingNamespace.ENGINE\n",
                )
            },
        )
        assert _keys(scan_repo(root)) == ["engine.knob:unreachable"]

    def test_a_registration_helper_is_resolved(self, tmp_path: Path) -> None:
        # self_improvement.py registers six flags through a `_flag(key, ...)`
        # helper. A gate that skips a parameterised key never checks them.
        root = make_repo(
            tmp_path,
            definitions={
                "self_improvement.py": textwrap.dedent("""
                    from synthorg.settings.enums import SettingNamespace, SettingType
                    from synthorg.settings.models import SettingDefinition
                    from synthorg.settings.registry import get_registry

                    _r = get_registry()
                    _NS = SettingNamespace.ENGINE


                    def _flag(key: str, description: str) -> None:
                        _r.register(
                            SettingDefinition(
                                namespace=_NS,
                                key=key,
                                type=SettingType.BOOLEAN,
                                default="false",
                                description=description,
                                group="General",
                            )
                        )


                    _flag("helper_knob", "...")
                """).lstrip("\n")
            },
        )
        assert _keys(scan_repo(root)) == ["engine.helper_knob:unreachable"]

    def test_an_unresolvable_registration_fails_closed(self, tmp_path: Path) -> None:
        # A key the gate cannot pin is a setting it never checks; passing on
        # an incomplete scan is worse than no gate at all.
        root = make_repo(
            tmp_path,
            definitions={
                "engine.py": textwrap.dedent("""
                    from synthorg.settings.enums import SettingNamespace, SettingType
                    from synthorg.settings.models import SettingDefinition
                    from synthorg.settings.registry import get_registry

                    _r = get_registry()


                    def _flag(key: str) -> None:
                        _r.register(
                            SettingDefinition(
                                namespace=SettingNamespace.ENGINE,
                                key=key,
                                type=SettingType.BOOLEAN,
                                default="false",
                                description="...",
                                group="General",
                            )
                        )


                    for _name in ("a", "b"):
                        _flag(_name)
                """).lstrip("\n")
            },
        )
        assert main(["--repo-root", str(root)]) == 2

    def test_an_unparseable_definitions_file_fails_closed(self, tmp_path: Path) -> None:
        root = make_repo(tmp_path, definitions={"engine.py": "def broken(:\n"})
        assert main(["--repo-root", str(root)]) == 2

    def test_an_unparseable_consumer_fails_closed(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, sources={_CONSUMER: "def broken(:\n"})
        assert main(["--repo-root", str(root)]) == 2

    def test_a_missing_definitions_tree_fails_closed(self, tmp_path: Path) -> None:
        # Zero settings would otherwise pass vacuously, which is the one
        # failure mode that makes the gate worse than absent.
        assert main(["--repo-root", str(tmp_path)]) == 2

    def test_an_inaccessible_repo_root_fails_closed(self, tmp_path: Path) -> None:
        assert main(["--repo-root", str(tmp_path / "absent")]) == 2

    def test_two_helpers_sharing_a_parameter_name_both_resolve(
        self, tmp_path: Path
    ) -> None:
        # Keyed by parameter alone, the second helper's call sites answered for
        # the first, and the first helper's settings left the inventory
        # silently: registered, never checked, never baselined.
        root = make_repo(
            tmp_path,
            definitions={
                "engine.py": textwrap.dedent("""
                    from synthorg.settings.enums import SettingNamespace, SettingType
                    from synthorg.settings.models import SettingDefinition
                    from synthorg.settings.registry import get_registry

                    _r = get_registry()


                    def _flag(key: str, description: str) -> None:
                        _r.register(
                            SettingDefinition(
                                namespace=SettingNamespace.ENGINE,
                                key=key,
                                type=SettingType.BOOLEAN,
                                default="false",
                                description=description,
                                group="General",
                            )
                        )


                    def _number(key: str, description: str) -> None:
                        _r.register(
                            SettingDefinition(
                                namespace=SettingNamespace.ENGINE,
                                key=key,
                                type=SettingType.INTEGER,
                                default="1",
                                description=description,
                                group="General",
                            )
                        )


                    _flag("first_knob", "...")
                    _number("second_knob", "...")
                """).lstrip("\n")
            },
        )
        assert _keys(scan_repo(root)) == [
            "engine.first_knob:unreachable",
            "engine.second_knob:unreachable",
        ]

    def test_an_aliased_definition_import_is_scanned(self, tmp_path: Path) -> None:
        # An unrecognised call shape drops the setting from the inventory
        # without raising anywhere, so the alias has to resolve.
        root = make_repo(
            tmp_path,
            definitions={
                "engine.py": textwrap.dedent("""
                    from synthorg.settings.enums import SettingNamespace, SettingType
                    from synthorg.settings.models import SettingDefinition as SD
                    from synthorg.settings.registry import get_registry

                    _r = get_registry()

                    _r.register(
                        SD(
                            namespace=SettingNamespace.ENGINE,
                            key="knob",
                            type=SettingType.BOOLEAN,
                            default="false",
                            description="...",
                            group="General",
                        )
                    )
                """).lstrip("\n")
            },
        )
        assert _keys(scan_repo(root)) == ["engine.knob:unreachable"]

    def test_an_unresolvable_namespace_fails_closed(self, tmp_path: Path) -> None:
        root = make_repo(
            tmp_path,
            definitions={
                "engine.py": definitions_module(
                    registration("knob", namespace_expr="_unbound")
                )
            },
        )
        assert main(["--repo-root", str(root)]) == 2

    def test_a_non_literal_compose_set_fails_closed(self, tmp_path: Path) -> None:
        # Defaulting it to writable would report a spurious violation with no
        # diagnostic pointing at the real cause.
        root = make_repo(
            tmp_path,
            definitions={
                "engine.py": definitions_module(
                    registration("knob").replace(
                        '        group="General",',
                        '        group="General",\n        compose_set=_FLAG,',
                    ),
                    preamble="_FLAG = True\n",
                )
            },
        )
        assert main(["--repo-root", str(root)]) == 2

    def test_a_registry_declaring_no_subsystem_fails_closed(
        self, tmp_path: Path
    ) -> None:
        # Resolving no activation makes every activation-scoped read look live,
        # which hides violations rather than inventing them.
        root = _repo(
            tmp_path,
            registry=_lines(
                "from synthorg.api.subsystems.spec import SubsystemSpec",
                "",
                "SUBSYSTEMS: tuple[SubsystemSpec, ...] = ()",
            ),
        )
        assert main(["--repo-root", str(root)]) == 2

    def test_a_missing_runtime_builder_fails_closed(self, tmp_path: Path) -> None:
        # The construction path is derived from this module's imports, so if it
        # is renamed the closure empties and every assembly read looks live:
        # the gate would then pass exactly the settings it exists to catch.
        root = make_repo(
            tmp_path,
            definitions={"engine.py": definitions_module(registration("knob"))},
            sources={"workers/_engine_assembly.py": _BUILD},
            omit_runtime_builder=True,
        )
        assert main(["--repo-root", str(root)]) == 2

    def test_an_unfollowable_activation_fails_closed(self, tmp_path: Path) -> None:
        root = _repo(
            tmp_path,
            registry=_lines(
                "from functools import partial",
                "",
                "from synthorg.api.subsystems.spec import CapabilityId, SubsystemSpec",
                "",
                "",
                "async def _activate_thing(app_state, flavour):",
                "    return None",
                "",
                "",
                "SUBSYSTEMS: tuple[SubsystemSpec, ...] = (",
                "    SubsystemSpec(",
                '        name="thing",',
                "        provides=CapabilityId.THING,",
                '        activate=partial(_activate_thing, flavour="x"),',
                "    ),",
                ")",
            ),
        )
        assert main(["--repo-root", str(root)]) == 2


class TestBaseline:
    """The baseline absorbs history and nothing else."""

    def test_a_baselined_violation_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        baseline = tmp_path / "baseline.txt"
        baseline.write_text("engine.knob:unreachable\n", encoding="utf-8")
        new, stale = run_with_baseline(root, baseline_path=baseline)
        assert new == []
        assert stale == []

    def test_only_the_new_violation_is_reported(self, tmp_path: Path) -> None:
        root = make_repo(
            tmp_path,
            definitions={
                "engine.py": definitions_module(
                    registration("knob"), registration("fresh_knob")
                )
            },
        )
        baseline = tmp_path / "baseline.txt"
        baseline.write_text("engine.knob:unreachable\n", encoding="utf-8")
        new, stale = run_with_baseline(root, baseline_path=baseline)
        assert _keys(new) == ["engine.fresh_knob:unreachable"]
        assert stale == []

    def test_a_changed_kind_is_a_new_violation(self, tmp_path: Path) -> None:
        # A setting whose only evidence moves onto the construction path is a
        # different verdict, so it needs its own approval rather than riding
        # the row it already had.
        root = _repo(
            tmp_path,
            sources={
                "workers/_engine_assembly.py": _BUILD,
                _RUNTIME_BUILDER: runtime_builder_module(
                    "synthorg.workers._engine_assembly"
                ),
            },
        )
        baseline = tmp_path / "baseline.txt"
        baseline.write_text("engine.knob:unreachable\n", encoding="utf-8")
        new, stale = run_with_baseline(root, baseline_path=baseline)
        assert _keys(new) == ["engine.knob:construction-only"]
        assert stale == ["engine.knob:unreachable"]

    def test_a_stale_entry_warns_but_passes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _repo(tmp_path, sources={_CONSUMER: _READ})
        baseline = tmp_path / "baseline.txt"
        baseline.write_text("engine.knob:unreachable\n", encoding="utf-8")
        exit_code = main(["--repo-root", str(root), "--baseline", str(baseline)])
        assert exit_code == 0
        assert "stale baseline entries" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "entry",
        [
            "engine.knob\n",
            "engine.knob:\n",
            ":unreachable\n",
            "engine.knob:unreachable:extra\n",
            "engineknob:unreachable\n",
            "engine.knob:invented-kind\n",
        ],
        ids=[
            "no-kind",
            "empty-kind",
            "empty-key",
            "extra-field",
            "undotted-key",
            "unknown-kind",
        ],
    )
    def test_a_malformed_baseline_entry_raises(
        self, tmp_path: Path, entry: str
    ) -> None:
        baseline = tmp_path / "baseline.txt"
        baseline.write_text(entry, encoding="utf-8")
        with pytest.raises(ValueError, match="malformed baseline entry"):
            _load_baseline(baseline)

    def test_comments_and_blank_lines_are_ignored(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.txt"
        baseline.write_text("# a header\n\nengine.knob:unreachable\n", encoding="utf-8")
        assert _load_baseline(baseline) == {"engine.knob:unreachable"}

    def test_a_missing_baseline_reads_as_empty(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        new, stale = run_with_baseline(root, baseline_path=tmp_path / "absent.txt")
        assert _keys(new) == ["engine.knob:unreachable"]
        assert stale == []

    def test_update_baseline_writes_a_sorted_idempotent_file(
        self, tmp_path: Path
    ) -> None:
        root = make_repo(
            tmp_path,
            definitions={
                "engine.py": definitions_module(
                    registration("zeta"), registration("alpha")
                )
            },
        )
        baseline = tmp_path / "baseline.txt"
        write_baseline(scan_repo(root), baseline)
        first = baseline.read_text(encoding="utf-8")
        entries = [
            line for line in first.splitlines() if line and not line.startswith("#")
        ]
        assert entries == ["engine.alpha:unreachable", "engine.zeta:unreachable"]
        write_baseline(scan_repo(root), baseline)
        assert baseline.read_text(encoding="utf-8") == first

    def test_update_baseline_through_the_cli_exits_clean(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        baseline = tmp_path / "baseline.txt"
        exit_code = main(
            [
                "--repo-root",
                str(root),
                "--baseline",
                str(baseline),
                "--update-baseline",
            ]
        )
        assert exit_code == 0
        assert "engine.knob:unreachable" in baseline.read_text(encoding="utf-8")


def test_the_real_repo_resolves_every_registration() -> None:
    """Nothing in the shipped tree defeats the inventory scan.

    The gate's whole-tree verdict is not asserted here: reaching it means
    parsing every module under ``src/synthorg/``, which costs more than the
    rest of this suite combined and buys nothing, because the gate runs over
    the real tree on every push and in CI through ``consolidated-python-gates``.

    What is worth pinning here is the property that run depends on. The scan
    raises rather than skipping a registration it cannot resolve, so a
    definitions module written in a shape it cannot read turns the whole gate
    into a pass over a partial inventory. This reads only
    ``settings/definitions/``, and fails the moment that happens.
    """
    definitions = load_definitions(REPO_ROOT)
    pairs = {record.pair for record in definitions}
    # Named settings rather than a non-emptiness check, which would pass with
    # three of five hundred resolved if an AST shape quietly stopped matching.
    assert ("engine", "loop_auto_select_enabled") in pairs
    assert ("engine", "default_loop_type") in pairs
    assert ("engine", "loop_complexity_overrides") in pairs
    # Registered through a `_flag(key, ...)` helper, so this is the shape a
    # parameter-blind scan drops.
    assert ("self_improvement", "enabled") in pairs
    # Declared compose_set, so the inventory has to carry both categories.
    assert any(record.compose_set for record in definitions)
    assert all(record.namespace and record.key for record in definitions)
