---
name: function-length-reminder
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    value: "src/ai_company/.*\\.py$"
  - field: new_text
    operator: regex_match
    value: "^\\s*(?:async\\s+)?def "
action: warn
---

Reminder: functions must be <50 lines (CLAUDE.md convention). Verify new/modified functions comply.
