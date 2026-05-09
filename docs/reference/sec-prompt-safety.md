# SEC-1: Prompt Safety, HTML Parsing, and Secret-Log Redaction

On-demand reference for the SEC-1 cluster. Short rules in `CLAUDE.md`:

- Wrap untrusted strings at LLM call sites via `wrap_untrusted()` from `synthorg.engine.prompt_safety`.
- Never call `lxml.html.fromstring` directly; use `HTMLParseGuard`.
- Never call any `logger` severity (`exception` / `warning` / `error` / `info` / `debug`) with `error=str(exc)` (or any wrapper that smuggles `str(exc)` through, including `[:200]`, `or fallback`, f-strings, and `**{"error": ...}` dict-unpack); use `logger.warning(EVENT, error_type=type(exc).__name__, error=safe_error_description(exc))` instead. The rule is global, not credential-bearing-paths-only: `logger.exception` adds traceback frame-locals, and `str(exc)` on `httpx.HTTPStatusError` / `psycopg.Error` / OAuth provider errors embeds URL or POSTed body content into the log record at every severity level.

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
- `TAG_MEMORY_ENTRY`

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
- `LLMConsolidationStrategy._build_user_prompt` and `._build_system_prompt` (`memory/consolidation/llm_strategy.py`): wraps each entry under `TAG_MEMORY_ENTRY`; trajectory-context entries reuse the same tag.
- `LlmCalibrationSampler._build_prompt` (`hr/performance/llm_calibration_sampler.py`): wraps the free-form `interaction_summary` under `TAG_TASK_DATA`; bounded numeric metrics are emitted as plain text.
- `SuccessMemoryProposer._build_user_message` and module `_SYSTEM_PROMPT` (`memory/procedural/success_proposer.py`): execution context is fenced under `TAG_TASK_DATA`.
- `SafetyClassifier._build_messages` (`security/safety_classifier.py`): the action `description` (only attacker-controllable field) is fenced under `TAG_TASK_DATA`; bounded label fields (tool name, action type, risk level) stay `html.escape`d. The system prompt is computed lazily via `_system_prompt()` to avoid a circular import through `synthorg.engine.__init__`.
- Meeting protocol prompt builders (peer-contribution wrapping):
    - `build_agenda_prompt` (`communication/meeting/_prompts.py`): wraps agenda title / context / items in `TAG_TASK_DATA`
    - `RoundRobinProtocol.run` and `RoundRobinProtocol._run_discussion_rounds` (`communication/meeting/round_robin.py`): both transcript-build paths wrap each turn's content via the shared `_format_transcript_entry` helper using `TAG_PEER_CONTRIBUTION`
    - `_build_conflict_check_prompt` / `_build_discussion_prompt` / `_build_synthesis_prompt` (`communication/meeting/structured_phases.py`)
    - `_build_synthesis_prompt` (`communication/meeting/position_papers.py`)
    - `_render_system_prompt` in `communication/meeting/agent_caller.py` appends the directive listing both `TAG_TASK_DATA` and `TAG_PEER_CONTRIBUTION` for every meeting LLM call

### Completion config pinning

LLM sites that previously invoked `provider.complete()` without an explicit `CompletionConfig` now pin `temperature` + `max_tokens` at construction (via `__init__` params) so prompt-fingerprint stability can be asserted in tests.

### Injection detector (tool results)

Tool-result interpolation additionally runs an advisory injection-pattern detector (`TOOL_INJECTION_PATTERN_DETECTED`) covering closing-tag look-alikes for every standard fence (`</task-data>`, `</task-fact>`, `</tool-result>`, `</tool-arguments>`, `</untrusted-artifact>`, `</code-diff>`, `</config-value>`, `</criteria-json>`, `</peer-contribution>`) plus common override phrases. The telemetry sample is scrubbed via `scrub_secret_tokens` before logging.

## HTML parsing: XXE protection

Never call `lxml.html.fromstring` directly on attacker-controlled input. Use `HTMLParseGuard` in `synthorg.tools.html_parse_guard`, which:

1. Pre-scans for DOCTYPE with SYSTEM/PUBLIC identifiers and any `<!ENTITY>` declaration (rejecting via `XXEDetectedError`, `is_retryable=False`).
2. Parses with a module-scope `lxml.html.HTMLParser(no_network=True, remove_blank_text=True, recover=True, huge_tree=False)`.

`sanitize()` catches `XXEDetectedError` explicitly so the pre-scan's `TOOL_HTML_PARSE_XXE_DETECTED` event is the single log entry per rejection (no duplicate `TOOL_HTML_PARSE_ERROR`). Generic parse failures log `error=safe_error_description(exc)` without `exc_info=True` so attacker-controlled payload bytes are not serialised via traceback frame locals.

## Secret-log redaction

NEVER use these patterns, anywhere in the codebase:

```python
logger.exception(EVENT, error=str(exc))
logger.warning(EVENT,   error=str(exc))
logger.error(EVENT,     error=str(exc))
logger.info(EVENT,      error=str(exc))
logger.debug(EVENT,     error=str(exc))
```

The rule is unconditional. The risk is most acute on credential-bearing paths (OAuth flows, secret backends, settings encryption, A2A client/gateway, API auth middleware, persistence repos), but the pattern is forbidden globally because:

- `logger.exception` attaches a traceback whose serialised frame-locals can leak `client_secret` / `refresh_token` / Fernet ciphertext sitting on the stack at any call site.
- `str(exc)` on `httpx.HTTPStatusError` / `psycopg.Error` / similar embeds URL or posted credential bodies into the message field. The embedded-URL/body risk is independent of severity: a `debug` / `info` / `warning` / `error` call still ends up shipping the credential to whatever sink the operator wires the logger to. The gate therefore covers all five severity methods.

A site that "doesn't handle credentials today" can be one refactor away from carrying a request body or connection string into its frame.

Use instead:

```python
from synthorg.observability import safe_error_description
logger.warning(EVENT, error_type=type(exc).__name__, error=safe_error_description(exc))
```

`exc_info=True` is forbidden by default (the structlog exc-info processor still serialises traceback frame-locals even when the `error=` field is redacted). For genuine framework-boundary handlers that operate downstream of a frame-local scrubber (e.g., a hardened crash sink), opt out per-line with `# lint-allow: exc-info -- <reason>` on the same physical line as the `exc_info=True,` keyword. The reason field is mandatory and non-empty.

Caller-facing detail is preserved via `raise ... from exc`.

### Belt-and-braces masking

The `scrub_event_fields` structlog processor masks every log record (covering escaped-quote JSON values, URL form values with stray `%` bytes, and `Authorization:` headers).

### Pre-commit gate

`scripts/check_logger_exception_str_exc.py` enforces two rules unconditionally (no global allowlist, no baseline) for every logger severity (`exception`, `warning`, `error`, `info`, `debug`) on bare `logger`, attribute-chain loggers (`self._logger`, `audit_logger`), or any Name whose id contains `logger`. The **`exc_info=True` rule** (rule 2 below) supports a required same-line per-call opt-out marker (`# lint-allow: exc-info -- <reason>` with a mandatory non-empty reason); this is a per-instance carve-out for genuine framework-boundary handlers, *not* a global allowlist or list-based baseline:

1. **Leak-shape rule** (`error=` value): the `error=` value subtree is walked via a custom `ast.walk` traversal that excludes `Call.args` and class-introspection chains (so `f"{type(exc).__name__}"`, `f"{exc.__class__.__name__}"`, `f"{safe_error_description(exc)}"` are not flagged) and that flags any of:
   - `str(<exc_like>)` calls where `<exc_like>` is `Name` / `Attribute` / `Subscript` (covers `str(exc)`, `str(self._inner)`, `str(exc.args[0])`).
   - `FormattedValue` interpolation with conversion `-1` / `!s` / `!r` / `!a` of any leaf whose Name id or Attribute terminal-attr matches `_EXCEPTION_LEAF_NAMES` (`exc, e, err, error, exception, cause, original, inner, _inner`).
   - One-level Name-binding indirection: `error_msg = str(exc); ...; error=error_msg` (or any RHS leak shape including `f"...{exc}..."`) -- the alias is collected per-function-scope and flagged when later passed as `error=`.
   - Wrapper combinations are walked: `str(exc)[:200]` (Subscript), `str(exc) or fallback` (BoolOp), `str(exc) if cond else fallback` (IfExp), `str(exc) + " ctx"` (BinOp), `f"failed: {str(exc)}"` (JoinedStr), `**{"error": str(exc)}` (Dict-unpack).

2. **`exc_info=True` rule**: any literal `exc_info=True` kwarg on a logger call is flagged, with a per-line `# lint-allow: exc-info -- <reason>` opt-out (mandatory non-empty reason). The marker must sit on the same physical line as the `exc_info=True,` keyword so reviewers and tooling can locate the opt-out without scanning the file.

The matcher is the source of truth; the gate's docstring (`scripts/check_logger_exception_str_exc.py`) describes the AST shapes covered and the rationale per-rule. The script's filename is preserved (rather than renamed) so the pre-commit hook ID `no-new-logger-exception-str-exc` stays stable in `.pre-commit-config.yaml` and CI job references.

### OTLP span redaction posture

The structlog secret-log redaction policy above covers the **structlog sink only**: log records that flow through `synthorg.observability.get_logger`. OpenTelemetry spans are a separate transport (OTLP exporter), so the structlog `exc_info=True` ban does not transitively cover spans. Instead, the per-transport rules are:

- **Span exception attributes**: never call `span.record_exception(exc)` in production code paths -- it serialises the full traceback (and frame locals) into the OTLP exporter, bypassing every redaction step the structlog sink applies. The middleware's exception handler in `src/synthorg/api/middleware.py` instead sets the OTel-semconv attributes directly:

  ```python
  span.set_attribute("exception.type", type(exc).__name__)
  span.set_attribute("exception.message", safe_error_description(exc))
  span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
  ```

  The message is scrubbed via `safe_error_description` so credentials embedded in exception strings (httpx response bodies, psycopg connection strings, OAuth tokens) cannot reach the OTLP exporter.

- **Auto-instrumentation opt-out**: when wrapping code in `tracer.start_as_current_span(...)`, pass `record_exception=False` and `set_status_on_exception=False` so the OTel SDK's default exception-on-context-exit behaviour does not undo the redaction by stamping `str(exc)` (unscrubbed) into the span before the `set_attribute` calls run.

- **Span events**: code that calls `span.add_event(name, attributes)` is responsible for applying `safe_error_description` (or equivalent scrubbing) to every attribute that may carry exception strings, request bodies, or other attacker-controllable content.

The pre-commit `check_logger_exception_str_exc.py` gate does not cover OTel spans (it AST-walks logger calls only). New OTel call sites must self-police; reviewers should reject any `span.record_exception` outside test fixtures.

## CodeQL barrier inventory

CodeQL is taught about SynthOrg's sanitiser helpers via custom query packs at `.github/codeql/queries/synthorg-*/`. Each pack defines a QL sanitiser subclass (in `Sanitisers.qll`) and ships a custom query that re-runs the standard flow analysis with the subclass in scope. The subclass extends the upstream abstract base (`CleartextLogging::Sanitizer`, `ServerSideRequestForgery::Sanitizer`); the upstream base uses American spelling because the upstream class is named that way, while our subclass names use the British "Sanitiser" form. The standard rule is excluded via `query-filters` in `codeql-config.yml`, and the custom query emits results under a `synthorg/<rule>` ID. The bindings are provably precise: `.github/workflows/codeql-pack-validate.yml` runs negative + positive fixtures and asserts the rule fires on the deliberate leak and does not fire on the sanitised path.

| Sanitiser | File | Rule | Runtime guarantee |
|---|---|---|---|
| `safe_error_description` | `src/synthorg/observability/redaction.py` | `synthorg/clear-text-logging-sensitive-data` | Strips OAuth tokens, JSON credential values, URI userinfo, `Authorization:` headers, and Fernet ciphertexts from exception messages; truncates to `MAX_SCRUBBED_LENGTH` |
| `scrub_secret_tokens` | `src/synthorg/observability/redaction.py` | `synthorg/clear-text-logging-sensitive-data` | Lower-level helper called by `safe_error_description`; pattern-replace credential shapes idempotently |
| `_validate_repo_prefix` | `scripts/check_image_signatures.py` | `synthorg/partial-ssrf` | Anchored regex matches the OCI repo grammar; `\A...\Z` rejects newlines and CR |
| `_validate_image_tag` | `scripts/check_image_signatures.py` | `synthorg/partial-ssrf` | Anchored regex matches the OCI image-name + tag grammar |

Adding a new sanitiser is a four-part change in one PR: (1) the helper itself, (2) a sanitiser subclass under `.github/codeql/queries/synthorg-<rule>/Sanitisers.qll`, (3) a fixture pair (negative + positive) under `.github/codeql/fixtures/`, and (4) its `expected.json` entry. The standalone `codeql-pack-validate.yml` workflow runs on every pack-related change and fails if either fixture diverges from its expectation.

### Why no Models-as-Data extension pack?

CodeQL's `barrierModel` extensible predicate (added April 2026) would let project sanitisers be declared as YAML rows instead of QL subclasses, but the per-language signature arity differs (Python and JS take 3 user-provided columns, Go takes 9). A single repo cannot host barrier rows for both Python/JS and Go simultaneously: the CodeQL CLI validates every YAML row's arity against the current language's signature regardless of `addsTo.pack`, so Go's 9-column rows fail the Python and JS analyses with `Expected 4, but was 9`. The custom-query approach above is signature-uniform per language and avoids the cross-language collision entirely.

### Rules covered without a barrier model

Two rules from the recurring-FP inventory are addressed by mechanisms other than a custom query:

| Rule | Resolution |
|---|---|
| `py/incomplete-url-substring-sanitization` | Suppressed by the `paths-ignore: ['tests/**']` clause in `.github/codeql/codeql-config.yml`. Every historical dismissal of this rule was on tuple-membership host checks (`host in allowed_hosts`) inside `tests/unit/tools/sandbox/*.py`; test files cannot ship a runtime vulnerability, so excluding them at the workflow level is more precise than modelling the membership check as a barrier. Production code that needs this idiom should use a dedicated typed validator (and earn its own custom-query subclass above). |
| `go/log-injection`, `go/path-injection`, `js/log-injection`, `py/path-injection` | Sanitisers for these queries (`config.SecurePath`, `safeStateDir`, `_resolve_root`, `sanitizeForLog`, `sanitizeWsString`, `sanitizeWsEnum`) are tracked for follow-up modelling once a per-language sanitiser-pack architecture is settled. CodeQL's built-in heuristics already cover most idiomatic cases; remaining false-positives are dismissed via the GitHub UI with rationale on a per-alert basis. |

### Cross-references

The "Key reference call sites" section above lists LLM call sites that wrap untrusted strings via `wrap_untrusted`. Sites that interpolate a sanitised value (e.g. `error=safe_error_description(exc)`) rely on the rows above to keep CodeQL's data-flow accurate. If a sanitiser is renamed or moved, update the matching inventory row in lock-step.
