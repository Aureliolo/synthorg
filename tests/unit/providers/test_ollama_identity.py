"""Tests for the Ollama model-id family/generation parser."""

import pytest

from synthorg.providers.ollama_identity import parse_ollama_identity

pytestmark = pytest.mark.unit


class TestParseOllamaIdentity:
    def test_base_family_and_generation(self) -> None:
        assert parse_ollama_identity("glm-5.2") == ("glm", 5.2)
        assert parse_ollama_identity("gemma4:31b") == ("gemma", 4.0)
        assert parse_ollama_identity("minimax-m2.5") == ("minimax", 2.5)

    def test_tier_suffixes_share_one_family(self) -> None:
        # -pro / -flash are the same lineage at a different tier: one family so
        # the recommender's scorer ranks within it.
        assert parse_ollama_identity("deepseek-v4-pro") == ("deepseek", 4.0)
        assert parse_ollama_identity("deepseek-v4-flash") == ("deepseek", 4.0)

    def test_coder_folds_into_its_own_family(self) -> None:
        assert parse_ollama_identity("qwen3-coder:480b") == ("qwen-coder", 3.0)
        assert parse_ollama_identity("qwen3-coder-next") == ("qwen-coder", 3.0)
        # ... and stays distinct from the general line.
        assert parse_ollama_identity("qwen3.5:397b") == ("qwen", 3.5)

    def test_embedding_folds_into_its_own_family(self) -> None:
        assert parse_ollama_identity("qwen3-embedding:8b") == ("qwen-embedding", 3.0)
        assert parse_ollama_identity("qwen3.6:27b-q4_K_M") == ("qwen", 3.6)

    def test_code_variant_splits_from_general(self) -> None:
        general_family, _ = parse_ollama_identity("kimi-k2.5")
        code_family, _ = parse_ollama_identity("kimi-k2.7-code")
        assert general_family == "kimi"
        assert code_family == "kimi-code"

    def test_no_leading_alpha_yields_no_family(self) -> None:
        # A digit-led id has no derivable family; the single-letter guard also
        # rejects a one-char stem.
        assert parse_ollama_identity("12345")[0] is None
        assert parse_ollama_identity("x1")[0] is None
