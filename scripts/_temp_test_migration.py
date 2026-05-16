"""One-off script: rewrite test files to use new persistence protocol surfaces.

Run once. Inspect git diff. Delete this script after.
"""

import re
from pathlib import Path

TESTS_DIR = Path("tests")

# Per-file targeted transforms.
TARGETED: dict[str, list[tuple[str, str]]] = {
    "tests/unit/settings/test_service.py": [
        # Convert tuple `("X", "Y")` after `mock_repo.get.return_value =`
        # to `_row("X", "Y")`. The ``_row`` helper defaults to
        # namespace="budget", key="total_monthly" which matches the
        # default registry. The two api_key tests overlap with this
        # default key but the service code keys off (namespace, key)
        # from its callsite, not the row, so leaving key=total_monthly
        # on those rows is harmless.
        (
            r"mock_repo\.get\.return_value\s*=\s*\(([^,)]+),\s*([^)]+)\)",
            r"mock_repo.get.return_value = _row(\1, \2)",
        ),
    ],
}


def apply(path: Path, patterns: list[tuple[str, str]]) -> bool:
    text = path.read_text(encoding="utf-8")
    new = text
    for pattern, replacement in patterns:
        new = re.sub(pattern, replacement, new)
    if new != text:
        path.write_text(new, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed: list[Path] = []
    for rel, patterns in TARGETED.items():
        p = Path(rel)
        if not p.exists():
            print(f"skip (not found): {rel}")
            continue
        if apply(p, patterns):
            changed.append(p)
    print(f"Rewrote {len(changed)} files")
    for p in changed:
        print(f"  {p.as_posix()}")


if __name__ == "__main__":
    main()
