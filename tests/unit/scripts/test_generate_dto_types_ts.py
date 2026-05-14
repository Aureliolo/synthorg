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
