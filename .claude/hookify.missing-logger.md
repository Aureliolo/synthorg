---
name: missing-logger
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    value: "src/ai_company/.*\\.py$"
  - field: file_path
    operator: not_contains
    value: "__init__"
  - field: file_path
    operator: not_contains
    value: "enums.py"
  - field: file_path
    operator: not_contains
    value: "errors.py"
  - field: file_path
    operator: not_contains
    value: "types.py"
  - field: file_path
    operator: not_contains
    value: "models.py"
  - field: file_path
    operator: not_contains
    value: "protocol.py"
  - field: new_text
    operator: regex_match
    value: "^(def |class )"
  - field: new_text
    operator: not_contains
    value: "get_logger"
action: warn
---

Missing logger: modules with functions/classes in `src/ai_company/` must have `from ai_company.observability import get_logger` and `logger = get_logger(__name__)`.
