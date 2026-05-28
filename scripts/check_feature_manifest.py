#!/usr/bin/env python3
# module-kind: code
"""Feature-manifest gate.

Every directory under ``src/synthorg/`` whose ``state.py`` (or peer
``*_state.py``) declares a class subclassing
:class:`synthorg._core.features.BaseFeatureStateSlice` MUST ship a sibling
``feature.py`` carrying a module-level ``FEATURE: FeatureModule`` manifest.

The substrate's own definition of a "feature" (a directory with a typed
state slice) is the contract; this gate walks the tree, finds slice-bearing
directories via AST, and asserts each has a matching ``feature.py``. It
also enforces the per-file invariants the gate alone owns: the
``# module-kind: feature`` header on the first non-blank line, the 100-LOC
ceiling for the ``feature`` tier, and that the file actually exposes a
``FEATURE`` attribute that imports as a :class:`FeatureModule`.

Run from the repo root::

    uv run python scripts/check_feature_manifest.py
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _module_size_lib import count_loc

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_SLICE_BASE_NAME: Final[str] = "BaseFeatureStateSlice"
_FEATURE_FILE: Final[str] = "feature.py"
_FEATURE_TIER_HEADER: Final[str] = "# module-kind: feature"
_FEATURE_TIER_LOC_CAP: Final[int] = 100


def _declares_state_slice(state_py: Path) -> bool:
    """Return ``True`` when *state_py* declares a ``BaseFeatureStateSlice`` subclass.

    AST-only scan: matches `class X(BaseFeatureStateSlice)` and
    `class X(synthorg._core.features.BaseFeatureStateSlice)` shapes; does not
    follow aliasing (intentional: the substrate's name is THE name).
    """
    try:
        tree = ast.parse(state_py.read_text(encoding="utf-8"), filename=str(state_py))
    except OSError, SyntaxError, UnicodeDecodeError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            name = _base_name(base)
            if name == _SLICE_BASE_NAME:
                return True
    return False


def _base_name(node: ast.expr) -> str | None:
    """Return the bare class name from a base-class expression, if any."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def find_feature_directories(src_root: Path) -> list[Path]:
    """Return the directories under *src_root* that own a state slice.

    A "feature directory" is any directory containing a ``*.py`` file that
    declares a :class:`BaseFeatureStateSlice` subclass. The gate special-cases
    ``state.py`` and ``*_state.py`` (so the api-core feature, whose slice is
    in ``api/api_core_state.py`` rather than ``api/state.py``, is detected).

    Args:
        src_root: The repo's ``src/`` directory.

    Returns:
        Sorted list of repo-relative feature directories (absolute paths).
    """
    synthorg_root = src_root / "synthorg"
    if not synthorg_root.is_dir():
        return []
    matches: set[Path] = set()
    for path in sorted(synthorg_root.rglob("*.py")):
        if path.name != "state.py" and not path.name.endswith("_state.py"):
            continue
        if _declares_state_slice(path):
            matches.add(path.parent)
    return sorted(matches)


def _check_feature_py(feature_py: Path) -> list[str]:
    """Validate one ``feature.py`` against the per-file invariants."""
    findings: list[str] = []
    try:
        text = feature_py.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        findings.append(f"{feature_py}: cannot read ({exc})")
        return findings

    first_non_blank = _first_non_blank_non_shebang_line(text)
    if first_non_blank != _FEATURE_TIER_HEADER:
        findings.append(
            f"{feature_py}: missing '{_FEATURE_TIER_HEADER}' header on first "
            f"non-blank line (got {first_non_blank!r})"
        )

    loc = count_loc(feature_py)
    if loc > _FEATURE_TIER_LOC_CAP:
        findings.append(
            f"{feature_py}: LOC {loc} exceeds feature-tier cap {_FEATURE_TIER_LOC_CAP}"
        )

    try:
        tree = ast.parse(text, filename=str(feature_py))
    except SyntaxError as exc:
        findings.append(f"{feature_py}: syntax error ({exc})")
        return findings
    if not _has_module_level_feature_attr(tree):
        findings.append(
            f"{feature_py}: missing module-level FEATURE attribute "
            "(expected `FEATURE: FeatureModule = FeatureManifest(...)`)"
        )
    return findings


def _first_non_blank_non_shebang_line(text: str) -> str:
    """Return the first line that isn't blank or a shebang."""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#!"):
            continue
        return stripped
    return ""


def _has_module_level_feature_attr(tree: ast.AST) -> bool:
    """Return ``True`` when the module assigns a top-level ``FEATURE``."""
    if not isinstance(tree, ast.Module):
        return False
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            ann_target = node.target
            if isinstance(ann_target, ast.Name) and ann_target.id == "FEATURE":
                return True
        elif isinstance(node, ast.Assign):
            for plain_target in node.targets:
                if isinstance(plain_target, ast.Name) and plain_target.id == "FEATURE":
                    return True
    return False


def check(*, src_root: Path) -> list[str]:
    """Run the gate against *src_root*; return findings list (empty == pass)."""
    findings: list[str] = []
    for feature_dir in find_feature_directories(src_root):
        feature_py = feature_dir / _FEATURE_FILE
        if not feature_py.is_file():
            findings.append(
                f"{feature_dir}: missing {_FEATURE_FILE} "
                f"(slice-bearing directory needs a feature manifest)"
            )
            continue
        findings.extend(_check_feature_py(feature_py))
    return findings


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enforce per-feature manifests.")
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT_DEFAULT)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on clean tree, 1 on any violation."""
    args = _build_arg_parser().parse_args(argv)
    src_root: Path = args.repo_root.resolve() / "src"
    findings = check(src_root=src_root)
    if not findings:
        return 0
    print("Feature-manifest gate findings:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
