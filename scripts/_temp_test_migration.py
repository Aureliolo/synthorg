"""One-off script: rewrite test files to use new persistence protocol surfaces.

Run once. Inspect git diff. Delete this script after.
"""

import re
from pathlib import Path

TESTS_DIR = Path("tests")

# Per-file targeted reverts where earlier mechanical script over-renamed.
TARGETED_REVERTS: dict[str, list[tuple[str, str]]] = {
    # TaskRepository moved list_tasks/count_tasks -> query/count
    # under ADR-0001. The engine now calls .query()/.count() but
    # the test still spies on the old names.
    "tests/unit/engine/test_task_engine_mutations.py": [
        (r"persistence\.tasks\.list_tasks", "persistence.tasks.query"),
        (r"persistence\.tasks\.count_tasks", "persistence.tasks.count"),
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
    for rel, patterns in TARGETED_REVERTS.items():
        p = Path(rel)
        if not p.exists():
            print(f"skip (not found): {rel}")
            continue
        if apply(p, patterns):
            changed.append(p)
    print(f"Reverted {len(changed)} files")
    for p in changed:
        print(f"  {p.as_posix()}")


if __name__ == "__main__":
    main()
