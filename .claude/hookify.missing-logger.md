---
name: missing-logger
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    pattern: "src/ai_company/.*\\.py$"
  - field: file_path
    operator: not_contains
    pattern: "__init__"
  - field: file_path
    operator: not_contains
    pattern: "enums.py"
  - field: file_path
    operator: not_contains
    pattern: "errors.py"
  - field: file_path
    operator: not_contains
    pattern: "types.py"
  - field: file_path
    operator: not_contains
    pattern: "models.py"
  - field: file_path
    operator: not_contains
    pattern: "protocol.py"
  - field: new_text
    operator: regex_match
    pattern: "^\\s*(?:async\\s+)?(?:def |class )"
  - field: file_content
    operator: not_contains
    pattern: "get_logger"
action: warn
---

Missing logger: modules with functions/classes in `src/ai_company/` must have `from ai_company.observability import get_logger` and `logger = get_logger(__name__)`.
