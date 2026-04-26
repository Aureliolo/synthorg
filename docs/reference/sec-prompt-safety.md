# SEC-1: Prompt Safety, HTML Parsing, and Secret-Log Redaction

On-demand reference for the SEC-1 cluster. Short rules in `CLAUDE.md`:

- Wrap untrusted strings at LLM call sites via `wrap_untrusted()` from `synthorg.engine.prompt_safety`.
- Never call `lxml.html.fromstring` directly; use `HTMLParseGuard`.
- Never `logger.exception(EVENT, error=str(exc))` on credential-bearing paths; use `logger.warning(..., error=safe_error_description(exc))`.

## Untrusted-content fences at LLM call sites

Any attacker-controllable string interpolated into an LLM prompt MUST be wrapped via `wrap_untrusted(tag, content)` from `synthorg.engine.prompt_safety`, and the enclosing system prompt MUST append `untrusted_content_directive(tags)` so the model is explicitly told those fences contain untrusted data.

### Attacker-controllable surfaces

Task title / description, acceptance criteria, artifact payloads, tool results, tool-invocation arguments, code diffs, multi-tenant strategy config, proposal / alert / query fields, rule metadata, triage requirements, generator context, peer-agent contributions in meeting protocols.

### Standard tags

- `TAG_TASK_DATA`
- `TAG_TASK_FACT`
- `TAG_UNTRUSTED_ARTIFACT`
- `TAG_TOOL_RESULT`
- `TAG_TOOL_ARGUMENTS`
- `TAG_CODE_DIFF`
- `TAG_CONFIG_VALUE`
- `TAG_CRITERIA_JSON`
- `TAG_PEER_CONTRIBUTION`

### Fence breakout protection

`wrap_untrusted` escapes literal `</tag>` in content (case-insensitively, including whitespace-terminated variants like `</tag >` or `</tag\t>`).

### Key reference call sites

This list is non-exhaustive; treat it as a navigational starting point for new SEC-1 audits. Any LLM call site that interpolates an attacker-controllable string is in scope, whether or not it appears here.

- `format_task_instruction`
- `TaskLedgerMiddleware`
- `LLMRubricGrader._prepare_payload_text`
- `_wrap_tool_result`
- `build_review_message` (semantic_llm_prompt)
- `build_strategic_prompt_sections`
- `_encode_decomposer_payload`
- `build_task_message` / `build_system_message` (`engine/decomposition/llm_prompt.py`)
- `separate_analyzer._build_user_message` (evolution proposer)
- `LlmSecurityEvaluator._build_messages` (tool-invocation arguments via `TAG_TOOL_ARGUMENTS`)
- `ChiefOfStaffChat.explain_proposal` / `.explain_alert` / `.ask` (three surfaces under `meta/chief_of_staff/chat.py` plus directive-append in `prompts.py` templates)
- `CodeModificationStrategy._build_user_prompt` (rule metadata + signal context)
- `_BaseSemanticDetector._prompt` (four subclasses in `engine/classification/semantic_detectors.py`)
- `LLMGenerator._build_prompt` (`client/generators/llm.py`)
- `AgentIntake._build_prompt` (`engine/intake/strategies/agent_intake.py`)
- Meeting protocol prompt builders (peer-contribution wrapping):
    - `build_agenda_prompt` (`communication/meeting/_prompts.py`) -- wraps agenda title / context / items in `TAG_TASK_DATA`
    - `RoundRobinProtocol` transcript builders -- wrap each turn's content in `TAG_PEER_CONTRIBUTION`
    - `StructuredPhasesProtocol._build_conflict_check_prompt` / `_build_discussion_prompt` / `_build_synthesis_prompt`
    - `PositionPapersProtocol._build_synthesis_prompt`
    - `_render_system_prompt` in `communication/meeting/agent_caller.py` appends the directive listing both `TAG_TASK_DATA` and `TAG_PEER_CONTRIBUTION` for every meeting LLM call

### Completion config pinning

LLM sites that previously invoked `provider.complete()` without an explicit `CompletionConfig` now pin `temperature` + `max_tokens` at construction (via `__init__` params) so prompt-fingerprint stability can be asserted in tests.

### Injection detector (tool results)

Tool-result interpolation additionally runs an advisory injection-pattern detector (`TOOL_INJECTION_PATTERN_DETECTED`) covering closing-tag look-alikes for every standard fence (`</task-data>`, `</task-fact>`, `</tool-result>`, `</tool-arguments>`, `</untrusted-artifact>`, `</code-diff>`, `</config-value>`, `</criteria-json>`, `</peer-contribution>`) plus common override phrases. The telemetry sample is scrubbed via `scrub_secret_tokens` before logging.

## HTML parsing: XXE protection

Never call `lxml.html.fromstring` directly on attacker-controlled input. Use `HTMLParseGuard` in `synthorg.tools.html_parse_guard`, which:

1. Pre-scans for DOCTYPE with SYSTEM/PUBLIC identifiers and any `<!ENTITY>` declaration (rejecting via `XXEDetectedError`, `is_retryable=False`).
2. Parses with a module-scope `lxml.html.HTMLParser(no_network=True, remove_blank_text=True, recover=True, huge_tree=False)`.

`sanitize()` catches `XXEDetectedError` explicitly so the pre-scan's `TOOL_HTML_PARSE_XXE_DETECTED` event is the single log entry per rejection (no duplicate `TOOL_HTML_PARSE_ERROR`). Generic parse failures log `error=safe_error_description(exc)` without `exc_info=True` so attacker-controlled payload bytes are not serialized via traceback frame locals.

## Secret-log redaction

On credential-bearing paths (OAuth flows, secret backends, settings encryption, A2A client/gateway, API auth middleware, persistence repos), NEVER use:

```python
logger.exception(EVENT, error=str(exc))
```

That attaches a traceback whose serialized frame-locals can leak `client_secret` / `refresh_token` / Fernet ciphertext, and `str(exc)` on httpx errors often embeds the POST body.

Use instead:

```python
from synthorg.observability import safe_error_description
logger.warning(EVENT, error_type=type(exc).__name__, error=safe_error_description(exc))
```

Caller-facing detail is preserved via `raise ... from exc`.

### Belt-and-braces masking

The `scrub_event_fields` structlog processor masks every log record (covering escaped-quote JSON values, URL form values with stray `%` bytes, and `Authorization:` headers).

### Pre-commit gate

`scripts/check_logger_exception_str_exc.py` blocks new `logger.exception(..., error=str(exc))` sites above the `scripts/_logger_exception_baseline.json` baseline. The gate matches bare `logger`, attribute-chain loggers (`self._logger`, `audit_logger`, etc.) and `str(...)` of `Name` / `Attribute` / `Subscript` expressions so swapping sites within a file is caught by location diff, not by a count that could tie.
