# module-kind: tests
"""The measurement that decides whether the contract stage did anything.

It has to be trustworthy before the number it produces means anything, and the
two ways it could lie are opposite. Reading agreement too generously would
report the contract working when the corpus's defect is still there; reading it
too strictly would report the intended division of labour as divergence, since
two units are SUPPOSED to write different bodies for a module they share.

So the property is: same public surface, different bodies, is agreement.
"""

from pathlib import Path

import pytest

from evals.recursion_depth.divergence import (
    leaf_trees,
    measure,
    read_surface,
    render,
)

pytestmark = pytest.mark.unit


def _tree(root: Path, unit: str, files: dict[str, str]) -> Path:
    """Lay out one unit's project tree.

    Returns:
        The project directory.
    """
    project = root / unit / "projects" / "recursion-depth-suite"
    for name, source in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return project


class TestThePublicSurface:
    """What a sibling can reach for, and nothing else."""

    def test_a_public_function_is_surface(self) -> None:
        assert "tokenize" in read_surface("def tokenize(text): ...").names

    def test_an_underscore_name_is_not(self) -> None:
        """A sibling is not entitled to reach for it, so it cannot diverge."""
        assert read_surface("def _scan(text): ...").names == ()

    def test_a_method_is_reached_through_its_class_not_directly(self) -> None:
        """Only top-level names; a class agreeing by name still compiles."""
        surface = read_surface("class Lexer:\n    def tokenize(self): ...\n")

        assert surface.names == ("Lexer",)

    def test_a_module_constant_is_surface(self) -> None:
        assert "KEYWORDS" in read_surface("KEYWORDS = ('select',)").names

    def test_an_annotated_constant_is_surface(self) -> None:
        assert "LIMIT" in read_surface("LIMIT: int = 5").names

    def test_parameters_are_taken_in_order(self) -> None:
        surface = read_surface("def run(query, *, data, fmt): ...")

        assert surface.signatures["run"] == ("query", "data", "fmt")


class TestAgreementIsAboutTheSurfaceNotTheBytes:
    """The half that would make this measure useless if it were wrong."""

    def test_the_same_surface_with_different_bodies_agrees(
        self, tmp_path: Path
    ) -> None:
        """This IS the division of labour, not the defect."""
        trees = {
            "leaf-a": _tree(
                tmp_path, "leaf-a", {"lexer.py": "def tokenize(text):\n    return []\n"}
            ),
            "leaf-b": _tree(
                tmp_path,
                "leaf-b",
                {"lexer.py": "def tokenize(text):\n    return list(text)\n"},
            ),
        }

        assert measure(trees).diverged == 0

    def test_a_name_one_unit_omits_diverges(self, tmp_path: Path) -> None:
        """The case that makes a merge choose."""
        trees = {
            "leaf-a": _tree(
                tmp_path,
                "leaf-a",
                {"lexer.py": "def tokenize(t): ...\ndef untokenize(t): ...\n"},
            ),
            "leaf-b": _tree(tmp_path, "leaf-b", {"lexer.py": "def tokenize(t): ...\n"}),
        }

        result = measure(trees)

        assert result.diverged == 1
        assert result.modules[0].missing_names == ("untokenize",)

    def test_a_changed_parameter_list_diverges(self, tmp_path: Path) -> None:
        """The case that makes the chosen one wrong rather than absent."""
        trees = {
            "leaf-a": _tree(
                tmp_path, "leaf-a", {"lexer.py": "def tokenize(text): ...\n"}
            ),
            "leaf-b": _tree(
                tmp_path, "leaf-b", {"lexer.py": "def tokenize(text, strict): ...\n"}
            ),
        }

        result = measure(trees)

        assert result.diverged == 1
        assert result.modules[0].conflicting_signatures == ("tokenize",)


class TestOnlySharedModulesAreCounted:
    """Otherwise the many write-once files bury the finding."""

    def test_a_module_one_unit_owns_is_not_counted(self, tmp_path: Path) -> None:
        trees = {
            "leaf-a": _tree(tmp_path, "leaf-a", {"ingest.py": "def read(p): ...\n"}),
            "leaf-b": _tree(tmp_path, "leaf-b", {"lexer.py": "def tokenize(t): ...\n"}),
        }

        assert measure(trees).shared == 0

    def test_the_denominator_is_shared_modules_not_all_files(
        self, tmp_path: Path
    ) -> None:
        """A per-file rate reads near-perfect while every seam is broken."""
        trees = {
            "leaf-a": _tree(
                tmp_path,
                "leaf-a",
                {
                    "lexer.py": "def tokenize(t): ...\n",
                    "own_a.py": "def a(): ...\n",
                    "more_a.py": "def b(): ...\n",
                },
            ),
            "leaf-b": _tree(
                tmp_path,
                "leaf-b",
                {
                    "lexer.py": "def tokenize(t, strict): ...\n",
                    "own_b.py": "def c(): ...\n",
                },
            ),
        }

        result = measure(trees)

        assert result.shared == 1
        assert result.diverged == 1


class TestNothingIsSilentlyDropped:
    """A unit that wrote prose where a module belongs is a finding."""

    def test_an_unparseable_module_is_reported(self, tmp_path: Path) -> None:
        trees = {
            "leaf-a": _tree(tmp_path, "leaf-a", {"lexer.py": "def tokenize(t): ...\n"}),
            "leaf-b": _tree(tmp_path, "leaf-b", {"lexer.py": "this is not python(\n"}),
        }

        result = measure(trees)

        assert result.unreadable == ("leaf-b:lexer.py",)

    def test_it_does_not_read_as_agreement(self, tmp_path: Path) -> None:
        """Dropping it would report better agreement than the tree holds."""
        trees = {
            "leaf-a": _tree(tmp_path, "leaf-a", {"lexer.py": "def tokenize(t): ...\n"}),
            "leaf-b": _tree(tmp_path, "leaf-b", {"lexer.py": "this is not python(\n"}),
        }

        assert "unparseable" in "\n".join(render(measure(trees)))


class TestFindingTheTreesOnDisk:
    """Read off the layout, because a killed cell journals less than it wrote."""

    def test_every_leaf_tree_is_found(self, tmp_path: Path) -> None:
        cell = tmp_path / "d1-gated-r0"
        _tree(cell, "leaf-1", {"a.py": "x = 1\n"})
        _tree(cell, "leaf-2", {"a.py": "x = 1\n"})

        assert set(leaf_trees(tmp_path, "d1-gated-r0")) == {"leaf-1", "leaf-2"}

    def test_a_merge_tree_is_not_a_leaf(self, tmp_path: Path) -> None:
        """A merge assembles the leaves, so counting it compares work to itself."""
        cell = tmp_path / "d1-gated-r0"
        _tree(cell, "leaf-1", {"a.py": "x = 1\n"})
        _tree(cell, "merge-1", {"a.py": "x = 1\n"})

        assert set(leaf_trees(tmp_path, "d1-gated-r0")) == {"leaf-1"}

    def test_a_recording_that_kept_nothing_answers_empty(self, tmp_path: Path) -> None:
        assert leaf_trees(tmp_path, "never-ran") == {}


class TestTheHeadline:
    """One line, and it has to be the ratio the corpus is comparable against."""

    def test_it_names_both_halves(self, tmp_path: Path) -> None:
        trees = {
            "leaf-a": _tree(tmp_path, "leaf-a", {"lexer.py": "def tokenize(t): ...\n"}),
            "leaf-b": _tree(
                tmp_path, "leaf-b", {"lexer.py": "def tokenize(t, strict): ...\n"}
            ),
        }

        assert "1 of 1 shared modules" in measure(trees).headline()

    def test_no_shared_module_says_so_rather_than_claiming_agreement(
        self, tmp_path: Path
    ) -> None:
        """Zero of zero would read as a perfect score for an empty measure."""
        trees = {"leaf-a": _tree(tmp_path, "leaf-a", {"a.py": "x = 1\n"})}

        assert (
            "no module was written by more than one unit" in measure(trees).headline()
        )
