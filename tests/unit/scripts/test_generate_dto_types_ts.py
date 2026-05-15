"""Tests for ``scripts/generate_dto_types_ts.py``.

The generator's two render functions (``render_dtos`` and
``render_enum_values``) are pure: given a parsed OpenAPI schema
dict they return a deterministic string. Those are exercised
against ``tests/unit/scripts/fixtures/dto_codegen_fixture_schema.py``
without booting the real Litestar app or shelling out to
``openapi-typescript``.

End-to-end behaviour (the full pipeline) is verified by the gate
itself in CI; this file focuses on the pure-Python surface.
"""

import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _import_script() -> ModuleType:
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "generate_dto_types_ts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "generate_dto_types_ts",
        script,
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _import_script()
from tests.unit.scripts.fixtures.dto_codegen_fixture_schema import (  # noqa: E402
    FIXTURE_SCHEMA,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def fresh_schema() -> dict[str, Any]:
    """Return a fresh deepcopy of the fixture schema.

    Wrapping the deepcopy in a fixture (rather than a module helper)
    makes the safe access pattern the default: a test that omits the
    fixture parameter has no path to FIXTURE_SCHEMA at all, so a
    direct-mutation regression cannot slip in.
    """
    import copy

    return copy.deepcopy(FIXTURE_SCHEMA)


class TestPrettyEnvelopeName:
    """``_pretty_envelope_name`` renames Litestar's monomorphised generics."""

    def test_api_response_inner_becomes_envelope(self) -> None:
        assert (
            gen._pretty_envelope_name("ApiResponse_AgentConfig_")
            == "AgentConfigEnvelope"
        )

    def test_paginated_response_inner_becomes_page(self) -> None:
        assert (
            gen._pretty_envelope_name("PaginatedResponse_AgentConfig_")
            == "AgentConfigPage"
        )

    def test_none_type_envelope_becomes_void_envelope(self) -> None:
        assert gen._pretty_envelope_name("ApiResponse_NoneType_") == "VoidEnvelope"

    def test_clean_name_returns_none(self) -> None:
        assert gen._pretty_envelope_name("AgentConfig") is None

    def test_unknown_wrapper_returns_none(self) -> None:
        assert gen._pretty_envelope_name("Unknown_AgentConfig_") is None


class TestToScreamingSnake:
    """PascalCase to SCREAMING_SNAKE_CASE."""

    def test_simple_pascal_case(self) -> None:
        assert gen._to_screaming_snake("TaskStatus") == "TASK_STATUS"

    def test_multi_word(self) -> None:
        assert gen._to_screaming_snake("ApprovalRiskLevel") == "APPROVAL_RISK_LEVEL"

    def test_initialism_prefix(self) -> None:
        assert gen._to_screaming_snake("HTTPStatus") == "HTTP_STATUS"

    def test_single_word(self) -> None:
        assert gen._to_screaming_snake("Priority") == "PRIORITY"


class TestHermeticEnv:
    """``_hermetic_env`` sets defaults inside the block and restores on exit."""

    _KEYS = (
        "SYNTHORG_DB_PATH",
        "SYNTHORG_DATABASE_URL",
        "SYNTHORG_PAGINATION_CURSOR_SECRET",
    )

    def test_restores_absent_keys_after_block(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for key in self._KEYS:
            monkeypatch.delenv(key, raising=False)
        with gen._hermetic_env():
            assert os.environ["SYNTHORG_DB_PATH"] == ":memory:"
            assert os.environ["SYNTHORG_PAGINATION_CURSOR_SECRET"]
        for key in self._KEYS:
            assert key not in os.environ

    def test_preserves_operator_pinned_db_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        operator_db = str(tmp_path / "operator.db")
        monkeypatch.setenv("SYNTHORG_DB_PATH", operator_db)
        monkeypatch.setenv("SYNTHORG_DATABASE_URL", "postgresql://operator")
        with gen._hermetic_env():
            # Operator-pinned values stay; the helper does not stomp them.
            assert os.environ["SYNTHORG_DB_PATH"] == operator_db
            assert os.environ["SYNTHORG_DATABASE_URL"] == "postgresql://operator"
        assert os.environ["SYNTHORG_DB_PATH"] == operator_db
        assert os.environ["SYNTHORG_DATABASE_URL"] == "postgresql://operator"

    def test_restores_on_error_inside_block(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for key in self._KEYS:
            monkeypatch.delenv(key, raising=False)

        def _raise_inside_hermetic() -> None:
            with gen._hermetic_env():
                assert os.environ["SYNTHORG_DB_PATH"] == ":memory:"
                msg = "boom"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="boom"):
            _raise_inside_hermetic()
        for key in self._KEYS:
            assert key not in os.environ


class TestPromoteResponseDefaultsToRequired:
    """``_promote_response_defaults_to_required`` promotes properties
    on response-side schemas only.

    A schema is request-only iff it is the target of at least one
    ``requestBody.$ref`` AND not the target of any response ``$ref``.
    Every other schema gets its defaulted properties promoted so the
    dashboard reads them as always present.
    """

    def test_response_only_schema_default_property_promoted(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        gen._promote_response_defaults_to_required(fresh_schema)
        defn = fresh_schema["components"]["schemas"]["FixtureResponseWithDefault"]
        assert "optional_with_default" in defn["required"]

    def test_request_only_schema_default_property_left_optional(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        gen._promote_response_defaults_to_required(fresh_schema)
        defn = fresh_schema["components"]["schemas"]["FixtureRequestWithDefault"]
        assert "optional_with_default" not in defn.get("required", [])

    def test_both_sided_schema_promoted(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """A schema referenced by BOTH a requestBody and a response is
        treated as response-side (per #1906 wording)."""
        gen._promote_response_defaults_to_required(fresh_schema)
        defn = fresh_schema["components"]["schemas"]["FixtureBothSided"]
        assert "optional_with_default" in defn["required"]

    def test_no_default_property_still_promoted_on_response_side(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """Every property on a response-side schema is promoted, not
        just defaulted ones. Pydantic's serialiser emits every field,
        but the JSON schema drops ``default`` for ``$ref``-typed and
        ``Optional[X] = None`` fields. The wire reality is "always
        emitted", so the walker promotes every property."""
        gen._promote_response_defaults_to_required(fresh_schema)
        defn = fresh_schema["components"]["schemas"]["FixtureResponseWithDefault"]
        assert "optional_no_default" in defn["required"]

    def test_request_only_no_default_property_left_optional(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """The promoter must never tighten request-only schemas; both
        defaulted and non-defaulted properties stay optional."""
        gen._promote_response_defaults_to_required(fresh_schema)
        defn = fresh_schema["components"]["schemas"]["FixtureRequestWithDefault"]
        existing = set(defn.get("required", []))
        assert "optional_no_default" not in existing
        assert "optional_with_default" not in existing

    def test_existing_required_entries_preserved(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """Pre-existing ``required[]`` members are kept across the
        promotion (set semantics)."""
        gen._promote_response_defaults_to_required(fresh_schema)
        defn = fresh_schema["components"]["schemas"]["FixtureResponseWithDefault"]
        assert "required_field" in defn["required"]

    def test_idempotent(self, fresh_schema: dict[str, Any]) -> None:
        gen._promote_response_defaults_to_required(fresh_schema)
        first = sorted(
            fresh_schema["components"]["schemas"]["FixtureResponseWithDefault"][
                "required"
            ],
        )
        gen._promote_response_defaults_to_required(fresh_schema)
        second = sorted(
            fresh_schema["components"]["schemas"]["FixtureResponseWithDefault"][
                "required"
            ],
        )
        assert first == second

    def test_string_enum_schema_untouched(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """String enums have no ``properties``; nothing to promote."""
        before = dict(fresh_schema["components"]["schemas"]["FixtureEnum"])
        gen._promote_response_defaults_to_required(fresh_schema)
        after = fresh_schema["components"]["schemas"]["FixtureEnum"]
        assert after == before

    def test_envelope_schema_with_default_data_promoted(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """``ApiResponse_FixtureResponse_`` has ``data`` and ``error``
        defaulted to ``None``; the wire emits both. Promote them."""
        gen._promote_response_defaults_to_required(fresh_schema)
        defn = fresh_schema["components"]["schemas"]["ApiResponse_FixtureResponse_"]
        assert set(defn["required"]) >= {"data", "error"}

    def test_required_list_is_sorted(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """The promoter writes ``required`` back sorted so re-runs are
        byte-stable for the drift gate."""
        gen._promote_response_defaults_to_required(fresh_schema)
        defn = fresh_schema["components"]["schemas"]["FixtureResponseWithDefault"]
        assert defn["required"] == sorted(defn["required"])

    def test_returns_mutated_schema(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """Mirror ``_normalise_enum_descriptions``: the function returns
        the schema dict for chaining."""
        returned = gen._promote_response_defaults_to_required(fresh_schema)
        assert returned is fresh_schema


class TestRenderDtos:
    """``render_dtos`` walks ``components.schemas`` and emits aliases."""

    def test_emits_named_alias_for_clean_schema(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        output = gen.render_dtos(fresh_schema)
        assert (
            "export type FixtureRequest = components['schemas']['FixtureRequest']"
            in output
        )
        assert (
            "export type FixtureResponse = components['schemas']['FixtureResponse']"
            in output
        )

    def test_emits_envelope_alias_for_monomorphised_api_response(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        output = gen.render_dtos(fresh_schema)
        assert (
            "export type FixtureResponseEnvelope = "
            "components['schemas']['ApiResponse_FixtureResponse_']" in output
        )

    def test_emits_page_alias_for_monomorphised_paginated_response(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        output = gen.render_dtos(fresh_schema)
        assert (
            "export type FixtureResponsePage = "
            "components['schemas']['PaginatedResponse_FixtureResponse_']" in output
        )

    def test_emits_void_envelope_for_none_type(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        output = gen.render_dtos(fresh_schema)
        assert (
            "export type VoidEnvelope = "
            "components['schemas']['ApiResponse_NoneType_']" in output
        )

    def test_skips_non_pascal_inline_schema(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        output = gen.render_dtos(fresh_schema)
        # The fixture includes a deliberately lower-cased inline name.
        assert "inline_anon_schema" not in output

    def test_skips_string_enum_schemas(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """Enums are owned by ``enum-values.gen.ts``; no duplicate alias here."""
        output = gen.render_dtos(fresh_schema)
        assert "export type FixtureEnum =" not in output

    def test_imports_components_from_openapi_gen(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        output = gen.render_dtos(fresh_schema)
        assert "import type { components } from './openapi.gen'" in output

    def test_header_present(self, fresh_schema: dict[str, Any]) -> None:
        output = gen.render_dtos(fresh_schema)
        assert "AUTO-GENERATED: do not edit by hand." in output
        assert "scripts/generate_dto_types_ts.py" in output

    def test_output_is_sorted_deterministically(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """Two runs must produce identical bytes."""
        first = gen.render_dtos(fresh_schema)
        second = gen.render_dtos(fresh_schema)
        assert first == second

    def test_empty_components_yields_header_only(self) -> None:
        empty: dict[str, Any] = {"components": {"schemas": {}}}
        output = gen.render_dtos(empty)
        assert output.startswith("// AUTO-GENERATED")
        assert "import type { components }" in output
        # No `export type ...` lines.
        assert "export type" not in output


class TestRenderEnumValues:
    """``render_enum_values`` emits runtime tuples for string-enum schemas."""

    def test_emits_screaming_snake_tuple(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        output = gen.render_enum_values(fresh_schema)
        assert (
            "export const FIXTURE_ENUM_VALUES = [\n"
            "    'alpha',\n"
            "    'beta',\n"
            "    'gamma',\n"
            "] as const" in output
        )

    def test_emits_derived_string_union_type(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        output = gen.render_enum_values(fresh_schema)
        assert (
            "export type FixtureEnum = (typeof FIXTURE_ENUM_VALUES)[number]" in output
        )

    def test_skips_inline_lowercase_enum(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """An inline schema lacking a PascalCase name is ignored."""
        output = gen.render_enum_values(fresh_schema)
        assert "INLINE_ANON_SCHEMA_VALUES" not in output
        assert "inline_anon_schema" not in output

    def test_skips_non_enum_object_schemas(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """Object types must not produce ``*_VALUES`` blocks."""
        output = gen.render_enum_values(fresh_schema)
        assert "FIXTURE_REQUEST_VALUES" not in output
        assert "FIXTURE_RESPONSE_VALUES" not in output

    def test_output_is_deterministic(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        first = gen.render_enum_values(fresh_schema)
        second = gen.render_enum_values(fresh_schema)
        assert first == second

    def test_header_present(self, fresh_schema: dict[str, Any]) -> None:
        output = gen.render_enum_values(fresh_schema)
        assert "AUTO-GENERATED: do not edit by hand." in output

    def test_escapes_special_characters_in_enum_member(
        self,
        fresh_schema: dict[str, Any],
    ) -> None:
        """Members with quote / backslash / newline escape into valid TS.

        No real Pydantic ``StrEnum`` carries these characters today, but
        the escape is the only thing standing between a future awkward
        member value and a syntactically broken ``enum-values.gen.ts``.
        """
        fresh_schema["components"]["schemas"]["AwkwardEnum"] = {
            "type": "string",
            "enum": ["it's", "back\\slash", "with\nnewline", "tab\tinside"],
        }
        output = gen.render_enum_values(fresh_schema)
        assert "    'it\\'s',\n" in output
        assert "    'back\\\\slash',\n" in output
        assert "    'with\\nnewline',\n" in output
        assert "    'tab\\tinside',\n" in output


class TestCheckMode:
    """The ``--check`` path compares each output against the committed file."""

    def test_check_passes_when_outputs_match(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        openapi_ts = "open\n"
        dtos_ts = "dtos\n"
        enum_values_ts = "enums\n"
        openapi_path = tmp_path / "openapi.gen.ts"
        dtos_path = tmp_path / "dtos.gen.ts"
        enum_path = tmp_path / "enum-values.gen.ts"
        for path, content in (
            (openapi_path, openapi_ts),
            (dtos_path, dtos_ts),
            (enum_path, enum_values_ts),
        ):
            # Match the production ``_write`` semantics: LF newlines on every
            # platform. ``write_text`` without ``newline="\n"`` would emit CRLF
            # on Windows, which the byte-comparison gate (correctly) flags as
            # drift.
            path.write_bytes(content.encode("utf-8"))
        targets = (
            (openapi_path, openapi_ts),
            (dtos_path, dtos_ts),
            (enum_path, enum_values_ts),
        )
        monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
        assert gen._check(targets) == 0

    def test_check_fails_when_output_drifts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        stale = tmp_path / "dtos.gen.ts"
        stale.write_bytes(b"stale\n")
        targets = ((stale, "fresh\n"),)
        monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
        assert gen._check(targets) == 1
        err = capsys.readouterr().err
        assert "out of sync" in err
        assert "dtos.gen.ts" in err

    def test_check_fails_when_file_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        missing = tmp_path / "dtos.gen.ts"
        targets = ((missing, "fresh\n"),)
        monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
        assert gen._check(targets) == 1
        err = capsys.readouterr().err
        assert "missing generated file" in err

    def test_check_fails_on_crlf_when_expected_is_lf(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``_write`` always emits LF; on-disk CRLF is real drift.

        Universal-newline ``read_text`` would silently normalise CRLF to
        LF and let a CRLF-encoded file pass the gate, masking a real
        platform-divergent output. Byte comparison (this gate) catches it.
        """
        crlf_path = tmp_path / "dtos.gen.ts"
        crlf_path.write_bytes(b"line one\r\nline two\r\n")
        targets = ((crlf_path, "line one\nline two\n"),)
        monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
        assert gen._check(targets) == 1
        err = capsys.readouterr().err
        assert "out of sync" in err


class TestRunOpenapiTypescript:
    """``run_openapi_typescript`` handles missing tooling and child failure."""

    def test_raises_file_not_found_when_npx_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(gen.shutil, "which", lambda _name: None)
        schema_path = tmp_path / "openapi.json"
        schema_path.write_text("{}", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="npx"):
            gen.run_openapi_typescript(schema_path)

    def test_raises_child_process_error_when_npx_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(gen.shutil, "which", lambda _name: "/usr/bin/npx")

        def _fake_run(
            *_args: object,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="boom",
            )

        monkeypatch.setattr(gen.subprocess, "run", _fake_run)
        schema_path = tmp_path / "openapi.json"
        schema_path.write_text("{}", encoding="utf-8")
        with pytest.raises(ChildProcessError, match="boom"):
            gen.run_openapi_typescript(schema_path)
