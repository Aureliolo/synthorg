# module-kind: tests
"""The entry point: plan mode spends nothing, and staging narrows honestly."""

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from scripts import record_recursion_depth as record_module
from scripts.record_recursion_depth import (
    PairOverride,
    _parse_args,
    _reclaim_workspaces,
    _recording_slug,
    check_declared_families,
    describe_plan,
    main,
    narrow,
)

from evals.errors import (
    HarnessImageUnresolvedError,
    HarnessProviderMissingError,
    RecursionDepthJudgeNotIndependentError,
)
from evals.harness.binding import HarnessBinder
from evals.harness.host import RecordingGatewayHost
from evals.recursion_depth.manifest import Independence, load_manifest
from evals.recursion_depth.runner import planned_cells
from evals.recursion_depth.tree import SpecBrief, load_spec_brief
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.completion_enums import REASONING_UNSET, ReasoningEffort
from synthorg.core.types import NotBlankStr
from synthorg.providers.enums import AuthType
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_MANIFEST = (
    Path(__file__).resolve().parents[3] / "evals" / "recursion_depth" / "manifest.yaml"
)


def _spec() -> SpecBrief:
    """Load the committed specification.

    Returns:
        The brief.
    """
    manifest = load_manifest(_MANIFEST)
    return load_spec_brief(Path(manifest.spec_dir))


def _config(*, executor_family: str | None, reviewer_family: str | None) -> RootConfig:
    """Build a company config aliasing the manifest's two placeholder pairs.

    Args:
        executor_family: Family the config claims for the executor's model.
        reviewer_family: Family the config claims for the reviewer's model.

    Returns:
        The config.
    """

    return _config_declaring(
        executor_family=executor_family,
        reviewer_family=reviewer_family,
        connection_family=None,
    )


def _config_declaring(
    *,
    executor_family: str | None,
    reviewer_family: str | None,
    connection_family: str | None,
) -> RootConfig:
    """Build a config declaring families at the model and connection levels.

    Args:
        executor_family: Family the config claims for the executor's model.
        reviewer_family: Family the config claims for the reviewer's model.
        connection_family: Family the CONNECTION declares, which every model
            that declares none of its own inherits.

    Returns:
        The config.
    """

    def _model(alias: str, family: str | None) -> ProviderModelConfig:
        return ProviderModelConfig(
            id=NotBlankStr(f"real-{alias}"),
            alias=NotBlankStr(alias),
            metadata=ModelMetadata(family=family),
        )

    return RootConfig(
        company_name=NotBlankStr("Recursion Depth Sweep"),
        providers={
            "example-provider": ProviderConfig(
                auth_type=AuthType.CUSTOM_HEADER,
                custom_header_name=NotBlankStr("Authorization"),
                custom_header_value=NotBlankStr("Bearer test-key"),
                family=connection_family,
                models=(
                    _model("example-capable-001", executor_family),
                    _model("example-expert-001", reviewer_family),
                ),
            )
        },
    )


class TestDeclaredFamiliesMatchWhatAnswers:
    """The manifest claims decorrelation; the config picks who actually runs."""

    def test_a_config_putting_both_pairs_in_one_family_is_refused(self) -> None:
        # The loophole the manifest alone cannot close: every check there
        # passes on the declared strings, and the models that answer are both
        # from one organisation.
        with pytest.raises(
            RecursionDepthJudgeNotIndependentError, match="nobody achieved"
        ):
            check_declared_families(
                load_manifest(_MANIFEST),
                _config(
                    executor_family="bound-family-a",
                    reviewer_family="bound-family-a",
                ),
            )

    def test_bound_families_that_differ_satisfy_the_placeholder_claim(self) -> None:
        # The bound names never match the manifest's own placeholders and are
        # not required to. What is checked is that the two pairs differ, which
        # is the whole content of a cross_family claim.
        check_declared_families(
            load_manifest(_MANIFEST),
            _config(
                executor_family="bound-family-a",
                reviewer_family="bound-family-b",
            ),
        )

    def test_a_config_declaring_no_family_leaves_the_manifest_the_only_claim(
        self,
    ) -> None:
        # Not an error: the config saying nothing is not the config disagreeing.
        check_declared_families(
            load_manifest(_MANIFEST),
            _config(executor_family=None, reviewer_family=None),
        )

    def test_a_family_inherited_from_the_connection_is_still_a_family(self) -> None:
        """Reading only the model half turns a collision into two silences.

        A config that declares the family once on the connection and lets both
        models inherit it puts both pairs in ONE organisation. Resolved from
        the model alone that reads as two undeclared families, which the check
        waves through by saying nothing rather than by differing, and the run
        records a correlated judge as independent.
        """
        with pytest.raises(
            RecursionDepthJudgeNotIndependentError, match="nobody achieved"
        ):
            check_declared_families(
                load_manifest(_MANIFEST),
                _config_declaring(
                    executor_family=None,
                    reviewer_family=None,
                    connection_family="bound-family-a",
                ),
            )

    def test_a_models_own_family_still_wins_over_its_connections(self) -> None:
        """The connection is the fallback, never an override."""
        check_declared_families(
            load_manifest(_MANIFEST),
            _config_declaring(
                executor_family="bound-family-a",
                reviewer_family="bound-family-b",
                connection_family="bound-family-a",
            ),
        )


class TestPlanMode:
    """The default path boots nothing and states the bill."""

    def test_it_exits_clean_without_recording(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # No --record, so no gateway, no port, no container and no spend.
        assert main([]) == 0
        assert "Recursion-depth recording plan" in capsys.readouterr().out

    def test_it_names_the_scenario_and_the_ceiling(self) -> None:
        # A depth sweep's session count is a product of branching factors the
        # manifest cannot predict, so the figure is what a FULL tree costs at
        # the declared branching rather than a bound in either direction, and
        # the ceiling is what actually bounds the spend. Presenting it as a
        # floor invites an operator to read a number the run can exceed.
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        assert "full tree" in plan
        assert "at least" not in plan
        assert str(manifest.max_sessions) in plan

    def test_it_names_the_figure_that_decides_whether_a_cell_starts(self) -> None:
        """Both halves of the cost model, because they answer different things.

        The full-tree projection is what a ceiling is sized against; the
        declared per-cell cost is what the sweep refuses a cell on. An operator
        reading only the first cannot tell whether the deepest cell will be
        entered at all, which is the one that carries the whole matrix.
        """
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        assert "expected" in plan
        for depth in manifest.depths:
            assert f"cap {depth}: {manifest.expected_sessions(depth):,}/cell" in plan

    def test_the_projection_is_derived_from_the_tree_each_cap_admits(self) -> None:
        """The whole tree is counted, not summarised as a sentence about it.

        A figure that scales only with the number of runs is the one an
        operator sizes ``max_sessions`` from and then loses a paid run to: the
        tree, not the matrix, is where a depth sweep's sessions come from.
        """
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        projected = sum(
            manifest.projected_sessions(cell.depth_cap)
            for cell in planned_cells(manifest)
        )
        per_run = len(planned_cells(manifest)) * (1 + manifest.merge_attempts * 2)

        assert projected > per_run * 10
        assert f"{projected:,}" in plan

    def test_the_projection_prints_the_assumption_it_rests_on(self) -> None:
        """A model whose input is hidden reads as a measurement."""
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        assert f"{manifest.projected_branching} subtasks per planning session" in plan

    def test_the_per_cell_cost_grows_with_the_cap(self) -> None:
        """The deep end is where a ceiling is chosen wrong and money is lost."""
        manifest = load_manifest(_MANIFEST)

        per_cell = [manifest.projected_sessions(cap) for cap in (1, 2, 3)]

        assert per_cell == sorted(per_cell)
        assert per_cell[0] < per_cell[2]

    def test_the_model_reproduces_the_shape_a_real_tree_had(self) -> None:
        """Checked against the measured run the projection models.

        A cap-3 tree held 85 leaves across 25 nodes that planned and cost about
        158 sessions. The declared branching is rounded DOWN from the ~4.4 that
        implies, so the projection stays a floor: it must not read HIGHER than
        what was actually measured, or an operator would size a ceiling off a
        number no run has ever reached.
        """
        manifest = load_manifest(_MANIFEST)

        projected = manifest.projected_sessions(3)

        assert projected < 158
        assert projected > 100

    def test_it_states_the_equal_attempt_budget(self) -> None:
        # Repair only in the gated arm would let it win by spending more, so
        # the operator reading the plan is told the budget is shared. Stated
        # even for a single-arm matrix, because the figure is what makes the
        # first run's arm comparison readable beside this one.
        plan = describe_plan(load_manifest(_MANIFEST), _spec())

        assert "the SAME in every arm" in plan

    def test_it_states_a_token_bound_the_money_bound_cannot_give(self) -> None:
        # A flat-rate connection attributes 0.0 to every call, so the money
        # ceiling never fires there and the money worst case reads 0.00
        # however long the sweep runs. Tokens are counted on every provider,
        # so the plan states a bound that holds without the reader first
        # knowing how they are billed.
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        widest = record_module._widest_token_ceiling(manifest)
        assert f"{manifest.max_sessions * widest:,}" in plan
        assert "flat-rate" in plan

    def test_the_bound_uses_the_widest_role_not_the_leafs_flat_one(self) -> None:
        # A merge and a review scale with fan-in up to their own declared
        # caps, which the shipped matrix sets above the leaf's flat budget. A
        # bound stated in the leaf's terms alone would understate what a
        # sweep dominated by wide merges can actually spend.
        manifest = load_manifest(_MANIFEST)

        widest = record_module._widest_token_ceiling(manifest)

        assert widest == max(
            manifest.unit_token_ceiling,
            manifest.merge_token_cap,
            manifest.review_token_cap,
        )
        assert widest > manifest.unit_token_ceiling

    def test_the_shipped_manifest_needs_no_independence_caveat(self) -> None:
        plan = describe_plan(load_manifest(_MANIFEST), _spec())

        assert "CAVEAT" not in plan

    def test_the_shipped_ceiling_covers_the_shipped_matrix(self) -> None:
        """The one property an operator must not have to work out themselves.

        The ceiling is chosen against the full-tree projection, so the two
        agreeing is a design decision rather than a coincidence, and it stops
        being true silently: raising a repetition count or adding a cap moves
        the projection and nothing moves the ceiling.
        """
        plan = describe_plan(load_manifest(_MANIFEST), _spec())

        assert "SHORTFALL" not in plan

    def test_a_matrix_the_ceiling_cannot_pay_for_says_so(self) -> None:
        """The comparison is done for the reader, on the one screen it matters.

        The projection and the ceiling are related for the reader rather than
        left on adjacent lines, because this is where the spend decision is
        taken: a run was launched at a ceiling four times too small from
        exactly that reading, and it bought a whole planned tree, six built
        units and no
        measurement at all.
        """
        starved = narrow(load_manifest(_MANIFEST), None, 200)

        plan = describe_plan(starved, _spec())

        assert "SHORTFALL" in plan
        assert f"{starved.max_sessions:,}" in plan
        # And which of the caps the ceiling actually reaches, because "narrow
        # --depths" is only actionable once the operator knows how far.
        assert "caps 1, 2 fit" in plan
        assert "stop inside cap 3" in plan

    def test_the_stopping_cap_is_one_the_sweep_actually_runs(self) -> None:
        """`--depths` may be non-contiguous, and the note names a SWEPT cap.

        Adding one to the deepest affordable cap reads correctly only while
        the caps happen to be consecutive. Told to stop inside a cap the run
        never planned, an operator narrows against a number that means
        nothing.
        """
        gapped = narrow(load_manifest(_MANIFEST), "1,2,4", 200)

        plan = describe_plan(gapped, _spec())

        assert "caps 1, 2 fit" in plan
        assert "stop inside cap 4" in plan
        assert "cap 3" not in plan

    def test_a_ceiling_that_covers_the_matrix_stays_quiet(self) -> None:
        # The note is a warning, not a running commentary: printed always, it
        # would be the line an operator stops reading.
        covered = narrow(load_manifest(_MANIFEST), "1,2", 100_000)

        assert "SHORTFALL" not in describe_plan(covered, _spec())

    def test_a_ceiling_below_even_the_shallowest_cap_says_that(self) -> None:
        # The prefix is empty, and reporting "caps  fit" would read as though
        # something did.
        starved = narrow(load_manifest(_MANIFEST), None, 1)

        plan = describe_plan(starved, _spec())

        assert "not even the shallowest cap fits" in plan

    def test_a_weakened_judge_puts_its_caveat_on_the_plan(self) -> None:
        # The operator is told before spending, not after reading the chart.
        shipped = load_manifest(_MANIFEST)
        weakened = shipped.model_copy(
            update={
                "reviewer": shipped.reviewer.model_copy(
                    update={"family": shipped.executor.family}
                ),
                "independence": Independence.SAME_FAMILY,
            }
        )

        plan = describe_plan(weakened, _spec())

        assert "CAVEAT" in plan
        assert "share a model family" in plan


class TestSamplingIsStatedBeforeAnythingIsSpent:
    """The treatment reaches the plan, and an override reaches the manifest.

    The plan is the screen where the spend decision is taken, so a dial that
    is an input to the result has to be legible there: a recording that
    cannot say what it sampled at cannot say what it measured.
    """

    def test_the_plan_states_what_each_pair_will_sample_at(self) -> None:
        plan = describe_plan(load_manifest(_MANIFEST), _spec())

        # Both the label and a value it carries, so a renamed label fails here
        # rather than silently leaving the operator a plan with no treatment
        # on it, and so does a label that survives while its row empties.
        assert "exec declared : temperature 0.7" in plan
        assert "revw declared : temperature 0.6" in plan

    def test_a_dial_a_manifest_leaves_open_reads_as_unset(self) -> None:
        """An unstated dial reads as unset rather than vanishing.

        Omitting it would tell the operator the pair pins nothing there, when
        the value resolves downstream to whatever the vendor supplies.

        Built by unsetting the dial rather than read off the committed
        manifest, which now states it: the property under test is how the plan
        RENDERS an open dial, and tying that to a value the matrix pins for a
        measured reason makes a deliberate change look like a regression.
        """
        manifest = load_manifest(_MANIFEST)
        opened = manifest.model_copy(
            update={
                "executor": manifest.executor.model_copy(
                    update={"reasoning_effort": None}
                )
            }
        )

        assert "reasoning_effort unset" in describe_plan(opened, _spec())

    def test_the_committed_matrix_leaves_no_reasoning_dial_open(self) -> None:
        """Unset is not "no treatment" for either pair this matrix binds.

        Both families default an absent ``reasoning_effort`` to their most
        expensive tier, so an unstated dial is an expensive choice nobody
        recorded. Measured on the executor's own endpoint at an 8192-token
        cap: unset spent the whole cap on reasoning and returned no content at
        all, against 1,556 tokens at ``low`` and 3,345 at ``high``.
        """
        manifest = load_manifest(_MANIFEST)

        assert manifest.executor.reasoning_effort is not None
        assert manifest.reviewer.reasoning_effort is not None

    def test_an_override_reaches_the_plan_not_just_the_run(self) -> None:
        # A value applied downstream of the plan prints the manifest's own
        # figure beside the flag meant to change it, which is the one moment
        # the number is being relied on.
        probed = narrow(
            load_manifest(_MANIFEST),
            None,
            None,
            None,
            executor=PairOverride(temperature=1.0, top_p=0.95),
        )

        assert probed.executor.temperature == pytest.approx(1.0)
        assert probed.executor.top_p == pytest.approx(0.95)
        assert "temperature 1.0, top_p 0.95" in describe_plan(probed, _spec())

    def test_an_override_leaves_the_reviewer_alone(self) -> None:
        # The two pairs run on different dials, so a probe of one must not
        # silently move the other.
        shipped = load_manifest(_MANIFEST)
        probed = narrow(
            shipped,
            None,
            None,
            None,
            executor=PairOverride(temperature=1.0, top_p=0.95),
        )

        assert probed.reviewer == shipped.reviewer

    def test_naming_one_dial_alone_is_refused(self) -> None:
        """Half a vendor recommendation is worse than none, and this is paid.

        Applying a temperature without its matching nucleus threshold samples
        a distribution neither the manifest nor the vendor describes, so the
        probe would measure something nobody chose.
        """
        shipped = load_manifest(_MANIFEST)

        with pytest.raises(ValueError, match="probed together"):
            narrow(shipped, None, None, None, executor=PairOverride(temperature=1.0))

        with pytest.raises(ValueError, match="probed together"):
            narrow(shipped, None, None, None, executor=PairOverride(top_p=0.95))

    def test_naming_no_dial_changes_nothing(self) -> None:
        shipped = load_manifest(_MANIFEST)

        assert narrow(shipped, None, None, None) == shipped

    def test_the_top_tier_is_reachable_only_by_omitting_the_parameter(self) -> None:
        """The arm reproducing the corpus asks for the field to be ABSENT.

        This executor's family dials low / high / max, the vocabulary the
        product emits is minimal / low / medium / high, and the two overlap on
        two values. So the tier the corpus actually ran at cannot be named, and
        the only way back to it is the one the corpus took without deciding to:
        send no ``reasoning_effort`` at all.
        """
        unset = narrow(
            load_manifest(_MANIFEST),
            None,
            None,
            None,
            executor=PairOverride(reasoning_effort=REASONING_UNSET),
        )

        assert unset.executor.reasoning_effort is None
        assert "reasoning_effort unset" in describe_plan(unset, _spec())

    def test_asking_for_no_override_is_not_asking_for_no_reasoning(self) -> None:
        # The two arrive one character apart on a command line and mean
        # opposite things: an unnamed flag keeps the pinned tier, and `none`
        # asks for the most expensive one there is.
        shipped = load_manifest(_MANIFEST)

        assert narrow(shipped, None, None, None).executor == shipped.executor

    def test_units_can_be_bound_below_the_pair_that_plans_and_assembles(
        self,
    ) -> None:
        """The sandwich: deep to plan and assemble, shallow to build.

        The only published harness ablation with numbers behind it reports
        reasoning deeply everywhere and reasoning moderately everywhere as the
        two arms that LOSE, so a matrix that can only move one global tier
        cannot express the arm that won.
        """
        sandwiched = narrow(
            load_manifest(_MANIFEST),
            None,
            None,
            None,
            leaf_reasoning_effort="low",
        )

        assert sandwiched.leaf_reasoning_effort is ReasoningEffort.LOW
        # The outer phases must NOT move with it, or the arm is the losing
        # uniform one wearing the winning arm's name.
        assert (
            sandwiched.executor.reasoning_effort
            == load_manifest(_MANIFEST).executor.reasoning_effort
        )

    def test_asking_units_for_no_override_leaves_them_on_the_pair(self) -> None:
        # `none` is the third state and means "build at whatever the pair
        # carries", which is what every recording before the flag existed did.
        #
        # Started from a manifest that PINS a depth, because the committed one
        # leaves it unset: asserting `None` against that manifest holds whether
        # or not the sentinel branch runs at all, so the clearing is what has
        # to be observed rather than the absence.
        pinned = narrow(
            load_manifest(_MANIFEST), None, None, None, leaf_reasoning_effort="low"
        )
        assert pinned.leaf_reasoning_effort is ReasoningEffort.LOW

        bound = narrow(pinned, None, None, None, leaf_reasoning_effort=REASONING_UNSET)

        assert bound.leaf_reasoning_effort is None

    def test_the_flag_refuses_a_tier_this_vocabulary_cannot_spell(self) -> None:
        """A value the manifest would reject is rejected before the boot.

        Left to the manifest, a mistyped tier costs a full registry build per
        queued cell before anything refuses it, which is how a queue of six
        variants reported four failures with no cell attempted.
        """
        with pytest.raises(SystemExit):
            _parse_args(["--executor-reasoning-effort", "max"])

        named = _parse_args(["--executor-reasoning-effort", REASONING_UNSET])
        assert named.executor_reasoning_effort == REASONING_UNSET

    def test_an_out_of_range_override_is_refused(self) -> None:
        # Re-validated rather than copied: the value came off a command line.
        # BOTH dials are passed so the pairing guard admits the call and the
        # range check is what refuses it; naming one alone would be refused a
        # step earlier, and the test would pass while proving nothing about
        # the bound it claims to cover.
        with pytest.raises(ValidationError):
            narrow(
                load_manifest(_MANIFEST),
                None,
                None,
                None,
                executor=PairOverride(temperature=1.0, top_p=1.5),
            )


class TestPreflightGuardsTheBoot:
    """Nothing is stood up until everything knowable has been settled."""

    async def test_a_failing_preflight_stops_before_the_host_is_built(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The ordering IS the finding: a probe after the boot buys nothing.

        Every condition preflight checks is a property of the configuration or
        the machine, and none of it becomes truer once a scratch database, a
        gateway and a container are standing. Asserted on the collaborator
        never being reached, because the two calls type-check in either order
        and only the sequence decides whether an operator waits.
        """
        built: list[object] = []

        refusal = "no such provider"
        built_early = "the host was built before preflight passed"

        async def _refuse(**kwargs: object) -> None:
            del kwargs
            raise HarnessProviderMissingError(refusal)

        def _host(config: object) -> object:
            built.append(config)
            raise AssertionError(built_early)

        monkeypatch.setattr(record_module, "run_preflight", _refuse)
        monkeypatch.setattr(record_module, "RecordingGatewayHost", _host)

        with pytest.raises(HarnessProviderMissingError):
            await record_module._record(
                record_module._parse_args(["--record", "--work-root", str(tmp_path)]),
                manifest=load_manifest(_MANIFEST),
                spec=_spec(),
                company_config=_config(
                    executor_family="bound-family-a",
                    reviewer_family="bound-family-b",
                ),
            )

        assert not built

    async def test_an_unresolvable_image_stops_before_the_sweep_spends(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The second ordering, and the one that costs money to get wrong.

        This check cannot run before the host, because unless the operator
        names an image the reference comes from the booted instance's own
        settings resolver. What it must still beat is the FIRST SESSION:
        planning runs entirely through the gateway, so a cell that cannot be
        graded buys a whole plan before anything opens a container. Asserted
        on the sweep never being reached, since a refusal after it would type
        -check identically and read identically in the report.
        """
        swept: list[object] = []
        missing = "no image under that reference"
        swept_early = "the sweep ran before the image was resolved"

        async def _refuse(_references: object) -> None:
            raise HarnessImageUnresolvedError(missing)

        async def _sweep(*args: object, **kwargs: object) -> object:
            del args, kwargs
            swept.append(True)
            raise AssertionError(swept_early)

        # The provider probe sits earlier in the same preflight and would
        # otherwise dial a real endpoint. Its own ordering is pinned by the
        # sibling test above; what is under test here is the check AFTER it.
        async def _probe(**kwargs: object) -> None:
            del kwargs

        monkeypatch.setattr(record_module, "run_preflight", _probe)
        monkeypatch.setattr(record_module, "check_images_resolve", _refuse)
        monkeypatch.setattr(record_module, "run_sweep", _sweep)
        # `_record` ENTERS the host before it asks about the image, so leaving
        # these real made this ordering assertion connect and migrate a
        # scratch database, seed the project and serve the gateway on its way
        # to the one call it is about. The same collaborators `_recorded`
        # stubs, for the same reason.
        _stub_the_host(monkeypatch)

        with pytest.raises(HarnessImageUnresolvedError):
            await record_module._record(
                record_module._parse_args(["--record", "--work-root", str(tmp_path)]),
                manifest=load_manifest(_MANIFEST),
                spec=_spec(),
                company_config=_config(
                    executor_family="bound-family-a",
                    reviewer_family="bound-family-b",
                ),
            )

        assert not swept


class TestThePlanNamesTheTreatment:
    """An operator deciding to spend can see which arm they are buying.

    Every sampling dial already prints here, on the reasoning that inputs to
    the result belong on the screen where the decision is taken. The two
    settings that decide what the LOOP does were the ones missing, so two arms
    of the same experiment printed identically up to the moment of spending.
    """

    def test_the_contract_stage_is_named(self) -> None:
        manifest = load_manifest(_MANIFEST)

        plan = record_module.describe_plan(manifest, _spec())

        assert "contract stage" in plan

    def test_an_inherited_leaf_depth_does_not_read_as_unset(self) -> None:
        # "unset" elsewhere on this screen means the manifest pins nothing and
        # the model is asked with the field absent. Leaves inheriting the
        # executor's depth is a different claim, and printing it in the other
        # one's vocabulary would misreport what the run is about to do.
        manifest = record_module.narrow(
            load_manifest(_MANIFEST), None, None, None, leaf_reasoning_effort=None
        )

        plan = record_module.describe_plan(manifest, _spec())

        assert "the executor's own" in plan

    def test_a_declared_leaf_depth_is_named(self) -> None:
        manifest = record_module.narrow(
            load_manifest(_MANIFEST), None, None, None, leaf_reasoning_effort="low"
        )

        plan = record_module.describe_plan(manifest, _spec())

        assert "low" in plan


class TestStaging:
    """A large bill is paid in instalments, and never for a cap nobody asked."""

    def test_depths_narrows_to_the_named_caps(self) -> None:
        narrowed = narrow(load_manifest(_MANIFEST), "1,2")

        assert narrowed.depths == (1, 2)

    def test_no_depths_keeps_the_manifest(self) -> None:
        manifest = load_manifest(_MANIFEST)

        assert narrow(manifest, None).depths == manifest.depths

    def test_max_sessions_reaches_the_plan_the_operator_reads(self) -> None:
        # The ceiling is what turns "at least 182 sessions" into a decision, so
        # applying the override only to the run would print the manifest's own
        # figure beside the flag that was meant to lower it.
        narrowed = narrow(load_manifest(_MANIFEST), None, 30)

        plan = describe_plan(narrowed, _spec())

        shipped = load_manifest(_MANIFEST)

        assert narrowed.max_sessions == 30
        assert f"{shipped.max_sessions} sessions" not in plan
        assert "30 sessions" in plan

    def test_max_sessions_survives_a_depth_narrowing(self) -> None:
        narrowed = narrow(load_manifest(_MANIFEST), "1,2", 30)

        assert narrowed.depths == (1, 2)
        assert narrowed.max_sessions == 30

    def test_a_cap_the_manifest_does_not_carry_is_refused(self) -> None:
        # Silently recording nothing for it would leave a gap in the curve that
        # reads as a measured zero.
        with pytest.raises(ValueError, match="does not carry"):
            narrow(load_manifest(_MANIFEST), "1,4,9")

    def test_narrowing_leaves_the_unswept_caps_priced(self) -> None:
        """A stage keeps the costs of the caps it is not running.

        `narrow` rewrites `depths` and touches no mapping beside it, so the
        expected costs for caps 3 and 4 outlive a stage that records 1 and 2.
        A validator refusing that would refuse every staged run, which is how
        a matrix this size is paid for at all.
        """
        narrowed = narrow(load_manifest(_MANIFEST), "1,2")

        assert narrowed.depths == (1, 2)
        assert narrowed.expected_sessions(4) >= 1


class TestTradingRepetitionsForASchedule:
    """The deep end is where the bill is, so it is where the lever belongs.

    A cap costs its branching to the power of its depth, so one repetition
    fewer at the deepest cap buys back more time than any other single change.
    The committed counts are the experimental design (samples concentrated
    where the aggregation transition is expected), so an operator trading one
    of them for a schedule overrides it per run rather than editing that design
    into something the next reader inherits as if it were intended.
    """

    def test_only_the_named_cap_changes(self) -> None:
        shipped = load_manifest(_MANIFEST)

        narrowed = narrow(shipped, None, None, "4:1")

        assert narrowed.repetitions[4] == 1
        for cap in (1, 2, 3):
            assert narrowed.repetitions[cap] == shipped.repetitions[cap]

    def test_it_reaches_the_plan_the_operator_reads(self) -> None:
        # Same reason --max-sessions is folded into the manifest: a count
        # applied downstream of the plan prints the manifest's own figure
        # beside the flag meant to lower it.
        narrowed = narrow(load_manifest(_MANIFEST), "3,4", None, "4:1")

        plan = describe_plan(narrowed, _spec())

        assert "cap 4: 1" in plan

    def test_it_composes_with_the_other_two_levers(self) -> None:
        narrowed = narrow(load_manifest(_MANIFEST), "1,2,3,4", 6000, "4:1")

        assert narrowed.depths == (1, 2, 3, 4)
        assert narrowed.max_sessions == 6000
        assert narrowed.repetitions[4] == 1

    def test_it_lowers_the_planned_cell_count(self) -> None:
        shipped = load_manifest(_MANIFEST)

        narrowed = narrow(shipped, "1,2,3,4", None, "4:1")

        assert len(planned_cells(narrowed)) < len(
            planned_cells(narrow(shipped, "1,2,3,4"))
        )

    def test_a_cap_the_matrix_does_not_sweep_is_refused(self) -> None:
        # The manifest validator only checks that every SWEPT depth has a
        # count, so an extra key validates cleanly and does nothing: '41:1' is
        # a typo for '4:1' that plans the full three repetitions and reports
        # nothing wrong, which is discovered a day into a paid run.
        with pytest.raises(ValueError, match="does not sweep"):
            narrow(load_manifest(_MANIFEST), None, None, "41:1")

    def test_zero_repetitions_is_refused(self) -> None:
        # Recording none of a cap is what --depths is for, and expressing it
        # here would leave the cap in the swept list with nothing under it.
        with pytest.raises(ValueError, match="leave the cap out of --depths"):
            narrow(load_manifest(_MANIFEST), None, None, "4:0")

    @pytest.mark.parametrize("raw", ["4", "four:1", "4:one", ""])
    def test_malformed_input_is_refused_rather_than_ignored(self, raw: str) -> None:
        # Ignored, every one of these would silently run the full matrix.
        with pytest.raises(ValueError, match="--repetitions"):
            narrow(load_manifest(_MANIFEST), None, None, raw)


def _record_args(tmp_path: Path) -> argparse.Namespace:
    """Build the argument bundle ``_record`` reads.

    Returns:
        The namespace.
    """
    return argparse.Namespace(
        out_dir=tmp_path / "out",
        work_root=tmp_path / "work",
        keep_workspaces=False,
        manifest=_MANIFEST,
        resume=False,
        max_sessions=None,
    )


def _stub_the_host(
    monkeypatch: pytest.MonkeyPatch, *, release: Exception | None = None
) -> None:
    """Stand in for the gateway host and everything its context build needs.

    ``_record`` ENTERS the host before it reaches most of what a test of its
    ordering is about, and the real one connects and migrates a scratch
    database, seeds the project and serves a gateway. One owner rather than a
    set repeated per test, because a second copy is one collaborator away from
    the older of them quietly booting for real again.

    Args:
        monkeypatch: Patching seam.
        release: Raised when the containers are released, or ``None`` for a
            release that succeeds.
    """
    host = mock_of[RecordingGatewayHost](
        # The addresses the start log states, which is everything the
        # lifecycle under test reads off a host.
        container_gateway_url="http://gateway.invalid/v1",
        port=0,
    )
    host.__aenter__.return_value = host
    host.__aexit__.return_value = False
    binder = mock_of[HarnessBinder]()
    binder.release_all_sandboxes.side_effect = release

    monkeypatch.setattr(record_module, "RecordingGatewayHost", lambda _c: host)
    monkeypatch.setattr(record_module, "HarnessBinder", lambda **_k: binder)
    monkeypatch.setattr(record_module, "_host_config", lambda *a, **k: None)
    monkeypatch.setattr(record_module, "_build_context", _built_context)
    monkeypatch.setattr(record_module, "capture_provenance", lambda **_k: None)


def _recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sweep: Exception | None,
    release: Exception | None = None,
) -> Path:
    """Stub everything ``_record`` reaches for, and seed the tree it builds.

    Every collaborator between the preflight and the report is a gateway, a
    container or a provider call, so each is replaced with the smallest thing
    that lets the lifecycle run. What is left unstubbed is the part under
    test: which scratch root is chosen, and whether it survives.

    Args:
        tmp_path: The test's directory.
        monkeypatch: Patching seam.
        sweep: Raised by the sweep, or ``None`` for one that measured a cell.
        release: Raised when the containers are released, or ``None`` for a
            release that succeeds.

    Returns:
        The scratch root the recorder will build under, already populated so
        its removal is observable.
    """
    root = tmp_path / "work" / f"run-{_recording_slug(tmp_path / 'out')}"
    (root / "unit").mkdir(parents=True)

    _stub_the_host(monkeypatch, release=release)

    async def _no_preflight(**_kwargs: object) -> None:
        return None

    async def _swept(*_args: object, **_kwargs: object) -> object:
        if sweep is not None:
            raise sweep
        return SimpleNamespace(measured_cells=("one",))

    async def _images_resolve(_references: object) -> None:
        return None

    monkeypatch.setattr(record_module, "run_preflight", _no_preflight)
    # The image check talks to the daemon, and what is under test here is which
    # scratch root survives a failure. Its own behaviour is pinned in
    # `test_image_preflight.py`.
    monkeypatch.setattr(record_module, "check_images_resolve", _images_resolve)
    monkeypatch.setattr(record_module, "run_sweep", _swept)
    monkeypatch.setattr(record_module, "write_report", lambda *_a: (tmp_path / "r",))
    return root


async def _built_context(*_args: object, **_kwargs: object) -> None:
    """Stand in for the context build, which needs a live gateway.

    Returns:
        Nothing the lifecycle under test reads.
    """
    return


class TestTheScratchRootAResumeContinuesWith:
    """A journal buys nothing if the trees it indexes move every run."""

    def test_the_same_output_directory_names_the_same_root(
        self, tmp_path: Path
    ) -> None:
        # A resume rebuilds each unit's tree path from the run root, so a root
        # it cannot predict leaves every cell finding nothing and paying again
        # for what the last attempt already built.
        assert _recording_slug(tmp_path / "out") == _recording_slug(tmp_path / "out")

    def test_two_output_directories_name_different_roots(self, tmp_path: Path) -> None:
        # Each unit's provisioning removes and re-copies a whole tree, which is
        # race-free only within one process, so two recordings running at once
        # must never share a root.
        assert _recording_slug(tmp_path / "one") != _recording_slug(tmp_path / "two")

    def test_the_root_is_one_path_segment(self, tmp_path: Path) -> None:
        # An output directory is an absolute path carrying separators, and on
        # this platform a drive letter; embedding it would build a tree nobody
        # asked for.
        slug = _recording_slug(tmp_path / "out")

        assert "/" not in slug
        assert "\\" not in slug

    async def test_an_unfinished_run_keeps_its_trees(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Reclaiming on the way out of a failure destroys exactly what the
        # next --resume continues with.
        root = tmp_path / "run-abc"
        (root / "unit").mkdir(parents=True)

        await _reclaim_workspaces(root, keep=True)

        assert root.is_dir()
        assert "--resume" in capsys.readouterr().out

    async def test_a_finished_run_reclaims_them(self, tmp_path: Path) -> None:
        root = tmp_path / "run-abc"
        (root / "unit").mkdir(parents=True)

        await _reclaim_workspaces(root, keep=False)

        assert not root.exists()


class TestWhatTheRecorderDoesWithTheTreesItBuilt:
    """Which trees survive is decided by the recorder, not by its helper.

    The two cases above pin what ``_reclaim_workspaces`` does when told; these
    pin what it is told, which is the half a resume actually depends on. A
    ``completed`` flag set one statement too early reads correctly in both
    branches of the helper and still deletes what the next attempt needed.
    """

    async def test_a_sweep_that_raised_leaves_its_trees_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _recorded(tmp_path, monkeypatch, sweep=OSError("the gateway died"))

        with pytest.raises(OSError, match="the gateway died"):
            await record_module._record(
                _record_args(tmp_path),
                manifest=load_manifest(_MANIFEST),
                spec=_spec(),
                company_config=_config(
                    executor_family="bound-family-a", reviewer_family="bound-family-b"
                ),
            )

        assert root.is_dir()

    async def test_a_release_failure_still_reclaims_a_finished_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Releasing the containers and reclaiming the trees are two
        # obligations, not a sequence: run as one, the first one raising
        # silently drops the second and the disk grows for ever.
        root = _recorded(
            tmp_path, monkeypatch, sweep=None, release=OSError("the daemon went away")
        )

        with pytest.raises(OSError, match="the daemon went away"):
            await record_module._record(
                _record_args(tmp_path),
                manifest=load_manifest(_MANIFEST),
                spec=_spec(),
                company_config=_config(
                    executor_family="bound-family-a", reviewer_family="bound-family-b"
                ),
            )

        assert not root.exists()

    async def test_a_sweep_that_wrote_its_report_reclaims_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _recorded(tmp_path, monkeypatch, sweep=None)

        assert (
            await record_module._record(
                _record_args(tmp_path),
                manifest=load_manifest(_MANIFEST),
                spec=_spec(),
                company_config=_config(
                    executor_family="bound-family-a", reviewer_family="bound-family-b"
                ),
            )
            == 0
        )

        assert not root.exists()
