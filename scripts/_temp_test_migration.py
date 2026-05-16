"""One-off script: rewrite test files to use new persistence protocol surfaces."""

import re
from pathlib import Path


def rename_connections_list_all(text: str) -> str:
    return re.sub(
        r"\bconnections(?:_repo)?\.list_all\b",
        lambda m: m.group(0).replace("list_all", "list_items"),
        text,
    )


TARGETS = {
    "tests/unit/api/test_webhook_receipt_cleanup_loop.py": rename_connections_list_all,
    "tests/unit/api/test_kill_switches.py": rename_connections_list_all,
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
