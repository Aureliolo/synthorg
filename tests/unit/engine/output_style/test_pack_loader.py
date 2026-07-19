"""Unit tests for output-style rule-pack loading and merging."""

from pathlib import Path

import pytest

from synthorg.engine.output_style import pack_loader
from synthorg.engine.output_style.errors import (
    OutputStylePackNotFoundError,
    OutputStylePackValidationError,
)
from synthorg.engine.output_style.models import (
    EnforcementMode,
    ExemptionScopeKind,
    OutputStyleConfig,
    SanctionedExemption,
)
from synthorg.engine.output_style.pack_loader import (
    list_builtin_packs,
    load_pack,
    merge_exemptions,
)

_EM_DASH = chr(0x2014)


class TestBuiltinPack:
    @pytest.mark.unit
    def test_default_pack_loads(self) -> None:
        pack = load_pack("default")
        assert pack.name == "default"
        assert pack.house_style
        assert pack.rules

    @pytest.mark.unit
    def test_emdash_rule_expands_codepoints_and_entities(self) -> None:
        pack = load_pack("default")
        rule = next(r for r in pack.rules if r.id == "emdash_literal")
        assert rule.mode is EnforcementMode.REJECT_REWORK
        assert _EM_DASH in rule.patterns
        assert (chr(38) + "mdash;") in rule.patterns
        assert (chr(38) + "#8212;") in rule.patterns
        assert (chr(38) + "#x2014;") in rule.patterns

    @pytest.mark.unit
    def test_fuzzy_tells_ship_in_shadow(self) -> None:
        pack = load_pack("default")
        for rule in pack.rules:
            if rule.id != "emdash_literal":
                assert rule.mode is EnforcementMode.SHADOW

    @pytest.mark.unit
    def test_list_builtin_packs(self) -> None:
        assert "default" in list_builtin_packs()

    @pytest.mark.unit
    def test_unknown_pack_raises(self) -> None:
        with pytest.raises(OutputStylePackNotFoundError):
            load_pack("does-not-exist")

    @pytest.mark.unit
    def test_invalid_pack_name_raises(self) -> None:
        with pytest.raises(OutputStylePackNotFoundError):
            load_pack("Bad Name!")


class TestUserPack:
    @pytest.mark.unit
    def test_user_pack_overrides_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pack_loader, "_USER_PACKS_DIR", tmp_path)
        (tmp_path / "default.yaml").write_text(
            'name: "default"\nversion: "9.9.9"\n'
            "rules:\n"
            '  - id: "x"\n    type: "literal_ban"\n    patterns: ["zzz"]\n'
            '    message: "no zzz"\n',
            encoding="utf-8",
        )
        pack = load_pack("default")
        assert pack.version == "9.9.9"

    @pytest.mark.unit
    def test_user_pack_invalid_falls_back_to_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pack_loader, "_USER_PACKS_DIR", tmp_path)
        (tmp_path / "default.yaml").write_text("not: [a mapping", encoding="utf-8")
        pack = load_pack("default")
        assert pack.version == "1.0.0"

    @pytest.mark.unit
    def test_user_only_pack_invalid_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pack_loader, "_USER_PACKS_DIR", tmp_path)
        (tmp_path / "custom.yaml").write_text(
            'name: "custom"\nversion: "1.0.0"\n'
            "rules:\n"
            '  - id: "dup"\n    type: "literal_ban"\n    patterns: ["a"]\n'
            '    message: "m"\n'
            '  - id: "dup"\n    type: "literal_ban"\n    patterns: ["b"]\n'
            '    message: "m"\n',
            encoding="utf-8",
        )
        with pytest.raises(OutputStylePackValidationError):
            load_pack("custom")

    @pytest.mark.unit
    def test_codepoints_reject_non_integer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pack_loader, "_USER_PACKS_DIR", tmp_path)
        (tmp_path / "custom.yaml").write_text(
            'name: "custom"\nversion: "1.0.0"\n'
            "rules:\n"
            '  - id: "cp"\n    type: "literal_ban"\n    codepoints: ["nope"]\n'
            '    message: "m"\n',
            encoding="utf-8",
        )
        with pytest.raises(OutputStylePackValidationError):
            load_pack("custom")


class TestMergeExemptions:
    @pytest.mark.unit
    def test_pack_then_operator_order(self) -> None:
        pack = load_pack("default")
        operator = SanctionedExemption(
            rule_id="emdash_literal",
            scope_kind=ExemptionScopeKind.PROJECT,
            match="proj-*",
            reason="filter product",
        )
        merged = merge_exemptions(pack, OutputStyleConfig(exemptions=(operator,)))
        assert merged[len(pack.exemptions) :] == (operator,)
