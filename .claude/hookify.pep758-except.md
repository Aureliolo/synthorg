---
name: pep758-except
enabled: true
event: file
pattern: except\s*\(
action: warn
---

PEP 758 violation: use `except A, B:` without parentheses, not `except (A, B):`.

Note: When using `as exc`, parentheses are still required: `except (A, B) as exc:`.
