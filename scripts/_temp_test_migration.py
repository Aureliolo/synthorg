"""One-off script: rewrite test files to use new persistence protocol surfaces."""

import re
from pathlib import Path

# repo.set.<method> -> repo.save.<method>
SET_TO_SAVE = (
    r"\brepo\.set\.(assert_|await_|call_args|return_value)",
    r"repo.save.\1",
)


def fix_repo_set_assertions(text: str) -> str:
    return re.sub(*SET_TO_SAVE, text)


TARGETS = {
    "tests/unit/settings/test_new_registry_entries.py": fix_repo_set_assertions,
    "tests/unit/settings/test_readonly_init_settings.py": fix_repo_set_assertions,
}


def main() -> None:
    for rel, fn in TARGETS.items():
        p = Path(rel)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        new = fn(text)
        if new != text:
            p.write_text(new, encoding="utf-8")
            print(f"  rewrote {rel}")


if __name__ == "__main__":
    main()
