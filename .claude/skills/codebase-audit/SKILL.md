---
description: "Full codebase audit: launches 155 specialized agents to find issues across Python/React/Go/docs/website, writes findings to _audit/latest/findings/, then triages with user"
argument-hint: "<scope: full | src/ | web/ | cli/ | docs/> [--report-only]"
allowed-tools: ["Agent", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "AskUserQuestion", "WebFetch", "mcp__github__issue_write", "mcp__github__issue_read", "mcp__github__list_issues", "mcp__github__search_issues"]
---

# /codebase-audit: Full Codebase Audit

Launch 155 specialized agents to audit the entire codebase (or a targeted scope), write findings to `_audit/latest/findings/`, build an index, REWORK report, JSON export, and DIFF (vs. previous run), then triage with the user.

## Key Principles

1. **File-based output**: agents write to `_audit/latest/findings/`, not in-session. Scales to 50+ agents.
2. **One concern per agent**: each agent searches for exactly ONE type of issue.
3. **Architecture context in every prompt**: no blind agents. All get the Architecture Brief.
4. **Severity-tagged findings**: critical/high/medium/low/info with file:line references.
5. **Triage together**: user reviews INDEX.md before any issues are created.
6. **Rerunnable**: creates a new run directory under `_audit/runs/<timestamp>/` and repoints the `_audit/latest` symlink. Older runs are preserved; never delete `_audit/runs/*`.

---

## Phase 0: Parse Arguments & Setup

### Parse scope

| Argument | Directories | Agents |
|----------|-------------|-------------|
| `full` (default) | All | All 155 agents |
| `src/` | `src/synthorg/`, `tests/`, `web/src/types/`, `docs/design/` | 01-06, 09-15, 16-34, 39-42, 48-51, 55, 58-80, 87-100, 102-108, 110-123, 124-130, 132-135, 136-150, 153, 154-155 |
| `web/` | `web/src/`, `src/synthorg/api/controllers/` | 07-08, 13, 17, 35-38, 45-47, 52-54, 57-59, 97, 100-101, 107-109, 111-112, 120-121, 123, 126, 131, 137-138, 141-145, 147, 149-150, 154-155 |
| `cli/` | `cli/` | 17, 18, 43-44, 56, 67, 78, 89, 107-108, 115-119, 122-123, 130, 134, 142, 154-155 |
| `docs/` | `docs/`, `site/`, `src/synthorg/` | 17, 20, 42, 48-51, 73-86, 103-104, 107-108, 123 |

Flags:
- `--report-only`: skip issue creation, findings files only

### Setup output directory

Use the run-history layout (see "Phase 0 setup: run-history layout" below for the full description):

```bash
RUN_DIR="_audit/runs/$(date +%Y-%m-%d-%H%M%S)" && mkdir -p "$RUN_DIR/findings" && if test -d _audit/latest && ! test -L _audit/latest; then rm -rf _audit/latest; else rm -f _audit/latest; fi && ln -sfn "runs/$(basename "$RUN_DIR")" _audit/latest && echo "RUN_DIR=$RUN_DIR"
```

Notes for the setup command:
- The `if test -d _audit/latest && ! test -L _audit/latest` branch detects the case where `_audit/latest` is a real directory (Junction on Windows from a prior run, or an accidental `mkdir _audit/latest`); plain `rm -f` would refuse and break the relink chain. The conditional handles both cases inline.
- `$RUN_DIR` is quoted inside `basename "$RUN_DIR"` even though the timestamp format never contains spaces -- defensive habit, costs nothing.
- **DO NOT use `2>/dev/null`, `&>/dev/null`, or any `>` / `>>` redirect in this Bash call**. The project's PreToolUse Bash hook (`scripts/check_bash_no_write.sh`) blocks all redirects unconditionally. Chain with `&&` instead and let stderr surface.
- **DO NOT use `cd`** -- the working directory is already the project root. Use absolute or workspace-relative paths.

Never delete `_audit/runs/*`. The `_audit/latest` symlink always points at the most recent run; older runs accumulate. On Windows, the OpenCode adapter first attempts `New-Item -ItemType SymbolicLink` (requires Developer Mode or admin); on failure it falls back to `New-Item -ItemType Junction`, which needs no special privileges and still resolves as a directory so downstream writes to `_audit/latest/findings/<file>` succeed.

Verify `_audit/` is in `.gitignore` via `grep -Eq '^/?_audit(/|$)' .gitignore`. The pattern allows `_audit`, `_audit/`, `/_audit`, and `/_audit/`, so plain-anchor false negatives don't trigger duplicate "add it" actions. If grep exits non-zero, add the entry.

---

## Phase 1: Build Architecture Brief

Read these files to build context injected into EVERY agent prompt:

1. `src/synthorg/observability/__init__.py` + `_logger.py`: logging stack
2. `src/synthorg/observability/events/`: list all event constant modules
3. `src/synthorg/api/auto_wire.py`: service wiring
4. `src/synthorg/api/app.py`: route registration
5. `web/src/router/routes.ts`: frontend routing
6. `web/src/stores/`: list all stores
7. `docs/DESIGN_SPEC.md`: spec index
8. Existing open issues: `gh issue list --state open --limit 200 --json number,title,labels,author`. **Filter the result before injecting into agent prompts**: drop any issue where `author.login == "app/renovate"` (the bot's `app/<name>` form), or with title containing "Dependency Dashboard", "Renovate", or "renovate-bot". The `--json` field list MUST include `author` -- without it the filter has nothing to match on and Renovate noise leaks into every agent prompt. Per memory rule `feedback_open_issues_exclude_renovate.md`, those are bot-managed dependency churn, not framework work, and they pollute every agent's "do not duplicate" guard.

Produce an **Architecture Brief** (~400 words) covering:
- Logging: `get_logger(__name__)`, structlog, event constants in `observability/events/`, structured kwargs
- Wiring: `auto_wire.py` phases, controller registration, factory pattern
- Frontend: router structure, Zustand stores, API layer
- Conventions: immutability, frozen Pydantic, `NotBlankStr`, vendor-agnostic naming
- Error hierarchy: custom exceptions inherit from project base, RFC 9457 responses
- Providers: all LLM calls through `BaseCompletionProvider` (auto-retry, rate limit)
- Pluggable subsystems: Protocol + concrete implementations + factory + config discriminator
- Database: SQLite + Postgres dual-backend, yoyo migrations in `persistence/*/revisions/`
- Async: `asyncio.TaskGroup` preferred, never bare `create_task`
- Testing: markers, xdist, async auto mode, Hypothesis profiles

**Python syntax note (PEP 758, Python 3.14) -- LOAD-BEARING -- DO NOT FLAG**: `except A, B:` without parentheses is *valid and preferred* when NOT binding the exception to a name. Parentheses are only required when binding (`except (A, B) as exc:`). The codebase deliberately uses the unparenthesized form per `CLAUDE.md` and ruff configuration.

This is the SINGLE most common false positive across audit runs. Past runs flagged 50+ findings of this kind. Every Python audit agent MUST treat the following as VALID Python 3.14 syntax and NOT flag it as syntax error, style issue, Python 2 syntax, missing parentheses, or convention violation:

```python
except A, B:           # VALID (no binding)
except A, B, C:        # VALID (no binding)
except (A, B) as exc:  # ALSO VALID (binding requires parens)
```

INVALID (flag if you see this):
```python
except A, B as exc:    # INVALID -- binding without parens
```

If you find yourself about to write a finding like "PEP 2 syntax", "missing parentheses around exception types", "should be `except (A, B):`" -- STOP. That finding is a guaranteed false positive on this codebase. Move on.

**Em-dash ban**: never emit em-dash characters in finding output, descriptions, or proposals. Use `--` instead. Pre-commit blocks em-dashes via `no-em-dashes` hook; findings that contain them are inadmissible.

**Vendor-agnostic naming**: never reference real vendor names (Anthropic, OpenAI, Claude, GPT) in finding text or proposed code changes outside `.claude/` skill bodies. Use `example-provider`, `example-large-001`, etc.

This brief is a string variable reused in all agent prompts below.

---

## Phase 2: Launch Agents

### Finding File Format

Every agent MUST write its output file using this exact format:

```markdown
# [Agent Title]

**Scope**: [directories/files searched]
**Files scanned**: [approximate count]
**Findings**: [count]

## Findings

### [critical|high|medium|low|info] path/to/file.py:LINE

Description of the issue.

**Suggestion**: How to fix it.

---
```

Severity definitions:
- **critical**: Security hole, data loss risk, silent corruption
- **high**: Logic error, broken feature, missing safety check
- **medium**: Dead code, missing wiring, inconsistency, hardcoded value that should be configurable
- **low**: Code quality, convention violation, missing docs
- **info**: TODO/deferred work, improvement opportunity

If zero findings, still create the file with `**Findings**: 0` and a brief note on what was checked.

### Agent Prompt Template

Every agent gets this structure (fill in the blanks per agent):

```text
You are auditing the SynthOrg codebase for ONE specific concern: {FOCUS}.

## Architecture Context
{ARCHITECTURE_BRIEF}

## Existing Open Issues (do NOT duplicate these)
{ISSUE_LIST}

## Your Task
{DETAILED_INSTRUCTIONS}

## Output
Write ALL findings to: _audit/latest/findings/{FILENAME}

Use this exact format for each finding:
### [severity] path/to/file:LINE
Description.
**Suggestion**: Fix.
---

Rules:
- Be thorough -- check EVERY relevant file in scope
- Only report REAL issues with file:line references
- If zero issues, still create the file and note what you checked
- Do NOT fix anything -- audit only
- Do NOT use Bash to write files -- use the Write tool
- **DO NOT write helper / analysis Python scripts to disk anywhere** (no `*.py` in
  the project root, in `scripts/`, in `c:\tmp\`, in `/tmp`, anywhere on the
  filesystem outside `_audit/latest/findings/`). Past runs leaked 14+ scratch
  scripts (`find_missing_logging.py`, `parse_audit.py`, `audit_pydantic_models*.py`,
  `validate_config_examples.py`, `audit_diff.py`, etc.) that triggered Pyright
  diagnostics in the main thread and required user cleanup. Use Grep/Glob/Read
  inline -- if you can't accomplish the audit with those, narrow your scope.
  The Write tool exists ONLY to write your finding file. The audit Bash tool is
  for read-only inspection (`git`, `gh`, `find`, `wc`); never for `python -c`,
  `cat >`, `tee`, redirects, or heredocs.

## Five FP-prevention rules (LOAD-BEARING)

These rules are mandatory for every audit agent. The 2026-05-03 run produced
12 distinct FP categories that traced back to one of these five gaps. Apply
all five before emitting a finding.

### R-A: Verify the proposed fix isn't already in place

Before writing a finding, verify the proposed fix is not ALREADY in place:
- For "missing X" findings: grep for X in the surrounding 50 lines, in the
  file's `__init__` / setup function, and in the test-setup module if
  applicable. If X exists, do NOT flag.
- For "no implementation" findings: grep for concrete subclasses overriding
  the method. Inspect MRO. If a subclass implements it, do NOT flag.
- For "settings-not-wired" findings: trace the setting through the consuming
  object's STARTUP wiring, not just import-graph references. A setting
  consumed by code that never runs at startup (because the parent service is
  never instantiated in `lifecycle_helpers.py` / `app.py`) is dead-on-arrival
  AND structurally hidden -- flag with `severity: high, kind: dead-on-arrival`
  rather than as a generic dead setting. The 2026-05-03 run had 11/13
  settings flagged as dead that were actually consumed by living machinery
  whose owning service was simply never started at boot. The audit's
  import-graph trace missed this entirely.

### R-B: Read existing helper docstrings for design carve-outs

Before recommending consolidation into an existing helper (e.g.
`GeneralRetryHandler`, `core.utils`, `core.normalization`), READ the helper's
module docstring and class docstring. If the docstring contains opt-out
language (`carve out`, `do not use for X`, `intentionally excluded`,
`deliberate non-goal`, `out of scope`), DO NOT flag the inline call site as
needing centralization. Document the carve-out as a known exception, not a
finding. The 2026-05-03 run had agent 138 flag 5 inline retry loops as
"should use GeneralRetryHandler" -- but the handler's module docstring
explicitly carved out 4 of the 5 patterns (semantic self-correction loops,
contention loops, sync logging-handler thread). Reading the docstring would
have caught this.

### R-C: Scope estimate alongside cited cases

When the audit covers a project-wide convention (e.g. "all exceptions must
inherit DomainError", "no hardcoded values", "every model must be frozen"),
the agent MUST emit a scope estimate alongside the cited cases:
- Sample 3-5 random files from candidate domains.
- Estimate total population (e.g. "12 cited; sample suggests ~80-120 across
  19 error modules").
- Distinguish "cited cases" from "tip of the iceberg" so the user can decide
  between surgical fix and convention rollout.

The 2026-05-03 run had agent 34 cite 12 plain-Exception classes; the actual
project-wide population was ~80-120 across 19 modules. The user's surgical
vs convention-rollout decision depends on knowing the gap.

### R-D: Semantic equivalence before flagging duplicates

When flagging duplicate / redundant code:
- Diff the loop body / inner predicate / inner expression -- not just the
  outer iteration shape. Two functions with identical structure but different
  inner logic are NOT duplicates.
- Distinguish *intentional* multi-instance patterns (different domains using
  the same shape, multiple bootstrap surfaces by design) from *accidental*
  duplication (same code copy-pasted by accident). Look for in-code comments,
  design-doc references, stable naming differences (`ConfigA` vs `ConfigB` =
  intentional vs `_helper` vs `_helper_v2` = accidental).

The 2026-05-03 run had agent 110 flag a pairwise-compare cluster as duplicate
when one used field-equality and the other used text-similarity -- identical
iteration shape, divergent inner predicate. Consolidating would have lost
domain semantics. Agent 146 flagged 8 multi-surface settings as "soup" but 4
of them were intentional bootstrap patterns (env-only init-time secrets,
operator-facing legacy env-var overrides).

### R-E: Caller-context distribution for API duplication findings

For findings of the shape "API has multiple variants" (mixed sync/async,
dual surfaces, multiple methods for one operation):
- Enumerate ALL callers in `src/synthorg/` AND `tests/`.
- Classify each caller by context (async route / sync helper / test fixture
  / CLI command).
- Emit the distribution in the finding (e.g. "100% of production callers are
  async; only test fixtures use sync").
- Severity calibration: only "high" if the duplication causes real ambiguity
  at production call sites; "low/info" if usage is segregated by call-site
  type. Mock-drift findings should sample actual mock-method usage in the
  test file and downgrade severity if the missing methods aren't called by
  any test.

The 2026-05-03 run had agent flag AuthService's mixed sync/async API as
high-severity duplication; the actual distribution was 100% production-async
+ test-fixture-sync, which made the choice obvious (drop sync, keep async).
Without the distribution, the finding looked like a hard choice.
```

### Streaming Pool Execution

Maintain a **rolling pool of 10 active agents** at all times. Do not wait for whole batches; as soon as one agent completes, immediately launch the next one in agent-id order to refill the slot. Initial fill: send agents 01-10 in a single message (10 parallel `run_in_background: true` calls). Then for each completion notification, launch the next pending agent. Continue until all 155 agents have completed.

This pipelines I/O and end-to-end runtime: a slow Wave 1 agent never blocks Wave 2-31 from starting, and the model spends notification cycles dispatching new work instead of idling. Skill agents are independent (each writes its own file), so order-of-completion does not matter -- only that the pool stays saturated until the queue drains.

**Pool size rationale**: 10 active agents matches what one main-loop cycle can usefully dispatch and track without notification fatigue. Going wider (20+) increases context spent on notification handling; going narrower (5) under-utilizes the agent runtime.

**Order**: agents 01 → 155 in numeric order. Skipping forward when a later agent is "more interesting" wastes the streaming property -- the pool drains itself naturally.

**Progress reporting**: every 10 completions, report "N/{AGENTS_LAUNCHED} done" to the user where `{AGENTS_LAUNCHED}` is the total for the current scope (155 for `full`, fewer for scoped runs). Do not report per-completion -- that is too chatty.

The 18-batch grouping (A-R) below is retained ONLY as a reference for which agent IDs map to which wave, not as a scheduling boundary:

| Group | Agents |
|-------|----------|
| A | 01-10 |
| B | 11-20 |
| C | 21-30 |
| D | 31-40 |
| E | 41-50 |
| F | 51-60 |
| G | 61-70 |
| H | 71-80 |
| I | 81-90 |
| J | 91-100 |
| K | 101-110 |
| L | 111-120 |
| M | 121-123 |
| N | 124-130 (Wave 26) |
| O | 131-140 (Wave 27 + first half of Wave 28) |
| P | 141-150 (second half of Wave 28) |
| Q | 151-153 (Waves 29 + 30) |
| R | 154-155 (Wave 31) |

---

## Agent Roster

### Wave 1: Observability & Logging (5 agents)

**Agent 01: missing-logger** (sonnet -- requires AST inspection, haiku produced 96-97% FP rate)
File: `_audit/latest/findings/01-missing-logger.md`

```text
Search every .py file in src/synthorg/ for modules that ACTUALLY DO BUSINESS
LOGIC but lack `logger = get_logger(__name__)`.

CRITICAL: past haiku runs of this agent produced 352 findings with 96-97% FP
rate by sweeping every file lacking a logger. Sonnet must be SKEPTICAL and
only flag files where the *absence* of a logger is a real defect.

Skip ALL of the following (they legitimately don't need loggers -- never flag):
- __init__.py files that only re-export (no def/class with bodies)
- Files containing ONLY: type aliases, Literal/TypedDict/NamedTuple definitions,
  enum class bodies (Enum/IntEnum/StrEnum), Pydantic model class bodies WITHOUT
  methods other than @field_validator/@model_validator/@computed_field/@property,
  Protocol class bodies (methods with `...` body or `pass`-only), abstract base
  classes with only abstract methods, dataclass field declarations, Final[...]
  module-level constants
- Files under observability/ (they ARE the logging system)
- Files under settings/definitions/ (pure setting registry data)
- Files named *_protocol.py / *_protocols.py with only Protocol definitions
- Files named *_types.py / types.py / *_enums.py / enums.py / *_constants.py
  / constants.py / *_models.py / models.py / dto*.py when they contain only
  data class declarations
- Files where every public function is a Pydantic validator, computed_field,
  or property accessor (no orchestration, no I/O, no try/except)

A FILE NEEDS A LOGGER ONLY IF it has at least one of:
- A service method that performs I/O (DB call, HTTP call, file read/write)
- A handler / controller method that processes a request
- An orchestrator method that coordinates multiple subsystems
- A function with try/except that handles real failure modes
- A function that mutates persistent state

Pydantic field validators and computed fields are NOT business logic for this
purpose. A file that is "almost all Pydantic models with two short helper
functions" is NOT a finding unless those helpers do real I/O.

VERIFICATION REQUIREMENT: Before flagging a file, confirm at least one of the
above triggers exists in the file body. If you cannot point to a specific
function with I/O / orchestration / state mutation, do NOT flag the file.

Severity: low.
```

**Agent 02: missing-event-constants** (haiku)
File: `_audit/latest/findings/02-missing-event-constants.md`

```text
The project requires all logger calls to use event constants from
src/synthorg/observability/events/ modules (e.g. API_REQUEST_STARTED from
events.api, TOOL_INVOKE_START from events.tool).

Search ALL logger.info/warning/error/debug/critical calls in src/synthorg/
(excluding observability/ itself). Flag calls where the first argument is:
- A string literal ("something happened")
- An f-string (f"processing {x}")
- A %-format string ("processing %s")

These should instead use a constant from observability/events/.

First, list all event constant modules in observability/events/ to understand
what's available. Then check every logger call.

Severity: low.

```

**Agent 03: missing-error-logging** (sonnet)
File: `_audit/latest/findings/03-missing-error-logging.md`

```text
Project convention: "All error paths must log at WARNING or ERROR with context
before raising."

Search src/synthorg/ for `raise` statements that are NOT preceded by a
logger.warning() or logger.error() call anywhere in the same function before
the raise. If a function has multiple raises, each must have its own preceding
log call or be inside an except block that already logged.

Exceptions to skip:
- `raise` inside `__init__` for validation errors (Pydantic handles these)
- Re-raising with bare `raise` in except blocks (the original error was
  presumably already logged)
- `raise NotImplementedError` in abstract/protocol methods
- `raise StopIteration` / `raise StopAsyncIteration`

Severity: medium for service/engine code, low for model validation.

```

**Agent 04: missing-state-transition-log** (sonnet)
File: `_audit/latest/findings/04-missing-state-transition-log.md`

```text
Project convention: "All state transitions must log at INFO."

Focus on these domains where state machines matter:
- engine/ (agent state transitions: idle, running, paused, completed, failed)
- hr/ (hiring, onboarding, evaluation, promotion, offboarding)
- core/task.py + core/task_transitions.py (task status changes)
- engine/workflow/ (workflow execution state changes)
- workers/ (worker claim/dispatch state)
- security/autonomy/ (autonomy level changes)
- api/ (request lifecycle, startup/shutdown phases)
- notifications/ (delivery state changes)
- persistence/ (connection state, transaction state)
- backup/ (backup job state)
If you find state machines in other modules, include them too.

For each domain, find where status/state fields are modified and check if
there's an INFO-level log call nearby. Missing transitions are severity=medium.

```

**Agent 05: observability-completeness** (sonnet)
File: `_audit/latest/findings/05-observability-completeness.md`

```text
Check whether key operations have full observability coverage:

1. Prometheus metrics (src/synthorg/observability/prometheus_collector.py):
   - Are all API endpoints instrumented? (request count, latency, error rate)
   - Are LLM provider calls tracked? (token usage, cost, latency per provider)
   - Are task/workflow state transitions counted?

2. OTLP traces (observability/otlp_handler.py):
   - Are spans created for LLM calls, tool invocations, task execution?

3. Audit chain (observability/audit_chain/):
   - Are security-sensitive operations (auth, permission changes, config
     changes) captured in the audit trail?

For each gap, describe what operation is missing coverage and suggest which
metric/trace/event to add. Severity: medium for missing metrics on key paths,
low for nice-to-have.

```

### Wave 2: Wiring & Integration (8 agents)

**Agent 06: unwired-api-controllers** (sonnet)
File: `_audit/latest/findings/06-unwired-api-controllers.md`

```text
Check api/controllers/ for controller classes not registered in auto_wire.py
or app.py. Also check for route handler methods that exist but are not mapped
to any HTTP route. Do NOT check frontend-to-backend connectivity (Agent 13
owns that). Severity: high (unreachable code).

```

**Agent 07: unwired-web-stores** (sonnet)
File: `_audit/latest/findings/07-unwired-web-stores.md`

```text
Check every Zustand store file in web/src/stores/. For each store, grep the
entire web/src/ directory for imports of that store. If a store is imported by
zero pages or components, it's dead. Severity: medium.

```

**Agent 08: unwired-web-pages** (sonnet)
File: `_audit/latest/findings/08-unwired-web-pages.md`

```text
Find all .tsx files in web/src/pages/ that are NOT imported by any other file
(not by routes.ts, not by another page as a nested layout). Pages with no
route AND no parent import are unreachable. Severity: medium.

```

**Agent 09: unwired-settings** (sonnet)
File: `_audit/latest/findings/09-unwired-settings.md`

```text
Check settings/definitions/ for setting definitions. For each setting key, verify
it is consumed somewhere. Settings defined but never read are dead config.

CRITICAL FALSE-POSITIVE GUARD: prior runs of this agent had ~38% FP rate (39 of
107 findings overturned in validation, 2026-05-03 run) because the prompt only
checked bridge config methods. Settings are LEGITIMATELY consumed via several
patterns -- DO NOT flag a setting as unwired unless you have ruled out ALL of:

1. **Bridge config methods**: ConfigResolver methods like get_api_bridge_config(),
   get_communication_bridge_config(), get_tools_bridge_config(), etc. that
   compose multiple settings into one config payload.
2. **Composed-config methods**: ConfigResolver.get_<area>_config() methods that
   return a Pydantic config model assembled from individual settings (e.g.
   get_coordination_config(), get_budget_config(), get_api_config()). All
   settings inside such a composed config ARE wired.
3. **Direct scalar accessors**: ConfigResolver.get_int(...), get_float(...),
   get_str(...), get_bool(...), get_secret(...) called with the namespace.key
   string. Grep for the literal "<namespace>.<key>" string across src/synthorg/.
4. **Pydantic config-model embedding**: when a setting is a field on a Pydantic
   config dataclass (e.g. ApiConfig.api_prefix, AuthConfig.jwt_expiry_minutes,
   RateLimitConfig.*, BudgetConfig.*), it IS consumed via the normal config
   chain -- not unwired.
5. **Subscriber pattern**: settings/subscribers/ registrations that listen for
   changes to a key.
6. **Bootstrap-only / read-only-post-init**: settings registered with
   read_only_post_init=True or marked bootstrap-only are intentionally not in
   bridge configs (they're registered for /settings discoverability only). DO
   NOT flag these.

VERIFICATION REQUIREMENT: before flagging a setting as unwired, you MUST run at
least three searches to rule out all six patterns above:
  - Grep for the literal "<namespace>.<key>" string across src/synthorg/
  - Grep for the field name in src/synthorg/config/ (Pydantic config models)
  - Grep for the namespace in ConfigResolver methods (any get_<area>_config /
    get_<area>_bridge_config method)

If any of those searches find a consumer, the setting is NOT unwired -- skip it.

A setting is "dead" only if NONE of the six consumption patterns apply.

Severity: medium.
```

**Agent 10: unwired-tools** (sonnet)
File: `_audit/latest/findings/10-unwired-tools.md`

```text
Check tool classes in tools/ subdirectories. For each tool class that extends
BaseTool, verify it is registered in tools/factory.py or tools/registry.py.
Unregistered tools are dead code. Severity: medium.

```

**Agent 11: unwired-protocols** (sonnet)
File: `_audit/latest/findings/11-unwired-protocols.md`

```text
Find all Protocol classes in src/synthorg/. For each, find concrete
implementations (classes that implement the protocol). Then check if those
implementations are registered in their factory. Report:
- Protocols with zero implementations (severity: medium)
- Implementations not registered in any factory (severity: medium)

```

**Agent 12: unwired-notifications** (sonnet)
File: `_audit/latest/findings/12-unwired-notifications.md`

```text
Check notifications/adapters/ for adapter classes. Verify each is registered
in notifications/factory.py. Also check if notification events are dispatched
from business logic. Report adapters with no factory registration and
notification types never dispatched. Severity: medium.

```

**Agent 13: frontend-backend-mismatch** (sonnet)
File: `_audit/latest/findings/13-frontend-backend-mismatch.md`

```text
Cross-reference web/src/api/ and web/src/services/ API calls with backend
api/controllers/ endpoints. Report:
- Frontend calls targeting endpoints that don't exist in backend (high)
- Backend endpoints with zero frontend consumers (low -- may be API-only)

CRITICAL FALSE-POSITIVE GUARD: a previous run produced a 44-finding "high"
list that was largely false-positives because the audit relied on regex
endpoint extraction. Litestar registers many routes through patterns that a
naive regex misses:
- Conditionally-registered controllers (gated on `app_state.has_*`)
- Controllers in BASE_CONTROLLERS / INTEGRATION_CONTROLLERS / OPTIONAL_CONTROLLERS
  tuples (different files than the @get/@post decorators)
- WebSocket handlers (@websocket_listener, @websocket)
- Wrapper / proxy controllers that delegate to multiple sub-handlers
- Path-parameter syntax: backend uses `{var:type}`, frontend uses `${var}`
- Routes prefixed via Router(path=...) wrappers
- Auth / OAuth flows split across multiple controller files

VERIFICATION REQUIREMENT: before flagging a "frontend calls X but backend
has no X" as severity high, you MUST search the entire backend tree (not
just controllers/) using a different strategy than the one that found the
miss. Use Grep with pattern `path="X"` AND pattern matching on the route
constant. If you cannot rule out the route exists after a second search,
downgrade to low severity and add note "regex matcher caveat -- needs
manual verification".

The audit's value is identifying drift; volume is not the goal. A 5-finding
list of HIGH-CONFIDENCE confirmed mismatches is far more useful than 44
findings with 80% FP rate.
```

### Wave 3: Dead Code & Unused (3 agents)

**Agent 14: unused-python-exports** (sonnet)
File: `_audit/latest/findings/14-unused-python-exports.md`

```text
Find public functions and classes in src/synthorg/ that are not imported by
any other module, not re-exported in __init__.py, and not referenced in tests/.
Exclude: __init__ methods, property descriptors, __repr__/__str__, metaclass
methods, enum members, Pydantic field definitions. Severity: medium.

```

**Agent 15: unused-dto-fields** (sonnet)
File: `_audit/latest/findings/15-unused-dto-fields.md`

```text
Compare DTO classes in api/dto*.py with frontend TypeScript types in
web/src/types/. Flag fields present in backend DTOs but absent from frontend
types (or vice versa). This suggests unused serialization. Severity: low.
Runs for `full`, `src/`, or `web/` scope (requires both backend and frontend).

```

**Agent 16: orphan-test-helpers** (haiku)
File: `_audit/latest/findings/16-orphan-test-helpers.md`

```text
Check all conftest.py files in tests/ for fixtures and helper functions.
Grep tests/ for usage of each. Unused fixtures/helpers are dead test code.
Severity: low.

```

### Wave 4: TODOs & Deferred (4 agents)

**Agent 17: todo-comments** (haiku)
File: `_audit/latest/findings/17-todo-comments.md`

```text
Grep source files in the provided scope for TODO, FIXME, HACK,
XXX, TEMPORARY, WORKAROUND comments. List each with file:line and the full
comment text. Severity: info.

```

**Agent 18: not-implemented** (haiku)
File: `_audit/latest/findings/18-not-implemented.md`

```text
Grep src/synthorg/ and cli/ for: NotImplementedError, pass-only function
bodies (def/async def with only pass), and Ellipsis (...) as sole function
body. Severity: info for abstract methods, medium for concrete stubs.

```

**Agent 19: placeholder-stubs** (sonnet)
File: `_audit/latest/findings/19-placeholder-stubs.md`

```text
Find functions that return hardcoded dummy values, empty lists/dicts, or have
"placeholder", "stub", "temporary", "mock" in their comments/docstrings.
These suggest incomplete implementations. Severity: medium.

```

**Agent 20: deferred-features** (sonnet)
File: `_audit/latest/findings/20-deferred-features.md`

```text
Read docs/design/ pages and cross-reference with src/synthorg/. Find features
described in the design spec that have no corresponding implementation.
Focus on major features (not minor details). Severity: info.

```

### Wave 5: Safety & Security (6 agents)

**Agent 21: silent-exception-swallow** (sonnet)
File: `_audit/latest/findings/21-silent-exception-swallow.md`

```text
Find except blocks that swallow exceptions silently: bare `except:`,
`except Exception: pass`, `except Exception as e:` with only DEBUG logging
(should be WARNING+), catching too broadly. Skip intentional patterns like
graceful shutdown cleanup. Severity: high for business logic, medium for cleanup.

```

**Agent 22: input-validation-gaps** (sonnet)
File: `_audit/latest/findings/22-input-validation-gaps.md`

```text
Check API controller methods for user input that bypasses validation. Look for
path/query parameters used directly without Pydantic validation, raw request
body access, and missing type coercion. Severity: high.

```

**Agent 23: sql-injection-risk** (sonnet)
File: `_audit/latest/findings/23-sql-injection-risk.md`

```text
Search persistence/ and any file using SQL for string concatenation or f-strings
in SQL queries instead of parameterized queries. Severity: critical.

```

**Agent 24: missing-auth-checks** (sonnet)
File: `_audit/latest/findings/24-missing-auth-checks.md`

```text
Check API controllers for endpoints missing auth guards. Compare with auth
middleware/dependency injection. Public endpoints should be explicitly marked.
Severity: high for data-mutating endpoints, medium for read-only.

```

**Agent 25: unsafe-deserialization** (sonnet)
File: `_audit/latest/findings/25-unsafe-deserialization.md`

```text
Flag ANY use of yaml.load (even with Loader parameter -- verify SafeLoader),
pickle.loads, eval(), exec(). Flag compile() ONLY if the argument contains a
variable or function parameter (not a string literal). Severity: critical.

```

**Agent 26: missing-rate-limiting** (sonnet)
File: `_audit/latest/findings/26-missing-rate-limiting.md`

```text
Check public-facing API endpoints for rate limiting. Check expensive operations
(LLM calls, bulk DB writes, file uploads) for throttling. Severity: medium.

```

### Wave 6: Configuration & Hardcoding (4 agents)

**Agent 27: hardcoded-urls-ports** (haiku)
File: `_audit/latest/findings/27-hardcoded-urls-ports.md`

```text
Grep files in the provided scope for hardcoded URLs, ports, hostnames, IP
addresses that should come from config or env vars. Skip test files and
documentation. Severity: medium.

```

**Agent 28: hardcoded-timeouts-limits** (sonnet)
File: `_audit/latest/findings/28-hardcoded-timeouts-limits.md`

```text
Grep src/synthorg/ for hardcoded timeout values (seconds/ms), retry counts,
batch sizes, max limits that should be configurable. Look for bare numeric
literals in asyncio.wait_for, sleep, timeout parameters. Severity: low.
```

**Agent 29: hardcoded-magic-numbers** (sonnet)
File: `_audit/latest/findings/29-hardcoded-magic-numbers.md`

```text
Find magic numbers in business logic: bare numeric literals (not 0, 1, -1)
without named constants. Focus on src/synthorg/ business logic, skip tests
and config defaults. Severity: low.

```

**Agent 30: missing-settings-bridge** (sonnet)
File: `_audit/latest/findings/30-missing-settings-bridge.md`

```text
Cross-reference hardcoded values in src/synthorg/ with settings definitions in
settings/definitions/. Find values that SHOULD be configurable but are hardcoded,
and settings defined but never consumed by any code. Severity: medium.

```

### Wave 7: Code Quality & Conventions (4 agents)

**Agent 31: model-convention-violations** (sonnet)
File: `_audit/latest/findings/31-model-convention-violations.md`

```text
Check Pydantic models in src/synthorg/ for convention violations:
- Missing frozen=True in ConfigDict (config/identity models must be frozen)
- Missing allow_inf_nan=False in ConfigDict
- Identifier/name fields not using NotBlankStr type
- Mutable runtime fields mixed with config fields in one model
Severity: medium.

```

**Agent 32: missing-immutability** (sonnet)
File: `_audit/latest/findings/32-missing-immutability.md`

```text
Find violations of immutability conventions:
- dict/list exposed without MappingProxyType wrapping (non-Pydantic)
- Mutable default arguments (def f(x=[]))
- In-place mutations of frozen model instances
- Missing copy.deepcopy at system boundaries
Severity: medium.

```

**Agent 33: async-antipatterns** (sonnet)
File: `_audit/latest/findings/33-async-antipatterns.md`

```text
Find async antipatterns in src/synthorg/:
- Bare asyncio.create_task without TaskGroup
- Missing await on coroutine calls
- Blocking I/O (open(), requests.) in async functions
- Fire-and-forget tasks with no error handling
- sync sleep() in async context
Severity: high for missing await, medium for others.

VERIFICATION REQUIREMENT for "fire-and-forget tasks with no error handling":
before flagging a `task = asyncio.create_task(coro)` as an issue, you MUST
read the next 10 lines of source AND grep the surrounding function for any of:
- `task.add_done_callback(...)` -- task has explicit error-logging callback
- `BackgroundTaskRegistry` / `_track_task(task)` / similar pattern that
  registers the task for tracked exception handling
- The task body itself wraps everything in try/except with logging
- The task is awaited later in the same scope

If ANY of these are present, the task is NOT fire-and-forget and is NOT a
finding. Past runs flagged 2 of 3 lifecycle_builder.py tasks as antipatterns
when the next-line registers a done_callback with logging -- false positive.

A real fire-and-forget finding looks like:
  task = asyncio.create_task(some_io())
  # ... task is never referenced again, no callback, no try/except in body

Where the task can fail silently with no observable trace.
```

**Agent 34: error-handling-consistency** (sonnet)
File: `_audit/latest/findings/34-error-handling-consistency.md`

```text
Check error handling conventions:
- Custom exceptions not inheriting from project error hierarchy
- API error responses not using RFC 9457 structured format
- Missing error mapping in controllers (raw exceptions leaking to clients)
- Inconsistent error response shapes across endpoints
Severity: medium.

```

### Wave 8: Frontend Quality (4 agents)

**Agent 35: missing-accessibility** (sonnet)
File: `_audit/latest/findings/35-missing-accessibility.md`

```text
Check web/src/components/ and web/src/pages/ for accessibility issues:
missing aria-label on interactive elements, missing role attributes, missing
keyboard navigation (onKeyDown handlers), missing focus management after
navigation, images without alt text. Severity: medium.

```

**Agent 36: missing-loading-states** (sonnet)
File: `_audit/latest/findings/36-missing-loading-states.md`

```text
Check pages/components that fetch data (useQuery, useEffect with fetch) for
missing loading skeletons/spinners, missing empty states when data is [],
and missing error boundaries around async content. Severity: medium.

```

**Agent 37: long-duplicated-error-strings** (sonnet)
File: `_audit/latest/findings/37-long-duplicated-error-strings.md`

```text
Find long error / banner messages (>=80 characters) that appear
verbatim in three or more TSX files. Those are deduplication
candidates for a small shared `errors.ts` module. SynthOrg ships
in International / British English with no translation framework
(see `docs/design/internationalization.md`); do NOT flag short
button labels, placeholders, headings, or empty-state copy as
"i18n readiness" -- centralization for i18n's sake is out of
scope. Severity: info.

```

**Agent 38: missing-error-handling-fe** (sonnet)
File: `_audit/latest/findings/38-missing-error-handling-fe.md`

```text
Check web/src/ for API calls without error handling: missing .catch() or
try/catch, missing toast/notification on failure, optimistic updates without
rollback on error, console.error without user feedback. Severity: medium.

```

### Wave 9: Logic & Architecture (4 agents)

**Agent 39: race-conditions** (sonnet)
File: `_audit/latest/findings/39-race-conditions.md`

```text
Find potential race conditions: shared mutable state without locks/mutexes,
TOCTOU patterns (check-then-act without atomicity), concurrent dict/list
modification, DB read-modify-write without transactions. Severity: high.

```

**Agent 40: resource-leaks** (sonnet)
File: `_audit/latest/findings/40-resource-leaks.md`

```text
Find unclosed resources: HTTP clients/sessions not using async with,
file handles not in with blocks, DB connections not properly returned to pool,
aiohttp sessions created but not closed. Severity: high.

```

**Agent 41: circular-dependencies** (sonnet)
File: `_audit/latest/findings/41-circular-dependencies.md`

```text
Find import cycles between src/synthorg/ packages. Check for circular
references that could cause runtime ImportError or require deferred imports.
Also check for TYPE_CHECKING guards that hide real circular deps. Severity: medium.

```

**Agent 42: design-spec-drift** (opus)
File: `_audit/latest/findings/42-design-spec-drift.md`

```text
Compare implementation in src/synthorg/ with docs/design/ specs. Find
behaviors, models, or flows that diverge from what the spec describes without
documented rationale. Focus on major architectural decisions. Severity: medium.

```

### Wave 10: Go CLI (2 agents)

**Agent 43: go-hardcoded-values** (haiku)
File: `_audit/latest/findings/43-go-hardcoded-values.md`

```text
Grep cli/ for hardcoded Docker image tags, ports, paths, timeouts that should
be configurable via flags or config. Severity: low.

```

**Agent 44: go-cli-wiring** (sonnet)
File: `_audit/latest/findings/44-go-cli-wiring.md`

```text
Check cobra command registration in cli/cmd/. Find commands registered but
non-functional, flags defined but never read, subcommands with no RunE.
Severity: medium.

```

### Wave 11: Dashboard Completeness (3 agents)

**Agent 45: dashboard-api-coverage** (sonnet)
File: `_audit/latest/findings/45-dashboard-api-coverage.md`

```text
Cross-reference every backend API endpoint (from all controllers in
api/controllers/) with the web dashboard. For each endpoint, check whether
the dashboard exposes the functionality to the user in SOME way -- a page,
a button, a settings panel, a dialog, etc. Report endpoints that exist in
the backend but have no corresponding UI surface. Severity: medium.

```

**Agent 46: dashboard-settings-completeness** (sonnet)
File: `_audit/latest/findings/46-dashboard-settings-completeness.md`

```text
Cross-reference every setting definition in src/synthorg/settings/definitions/ with the
Settings page in the dashboard (web/src/pages/settings/). For each setting,
check whether it is editable/visible in the UI. Also check ConfigDict fields
in src/synthorg/config/ that are user-facing but have no settings UI.
Report settings that exist but are not exposed. Severity: medium.

```

**Agent 47: dashboard-ux-improvements** (sonnet)
File: `_audit/latest/findings/47-dashboard-ux-improvements.md`

```text
Review the web dashboard pages for UX improvement opportunities. Look for:
- Pages with no sorting/filtering when data lists can grow large
- Missing pagination on list views
- Missing search/filter on pages with many items
- Missing breadcrumb navigation on detail pages
- Missing confirmation dialogs on destructive actions
- Missing keyboard shortcuts for common actions
- Missing bulk selection/actions on list views
- Inconsistent page layouts (some pages have sidebars, some don't)
- Missing contextual help or tooltips on complex features
- Missing progress indicators on long-running operations
Focus on the most impactful improvements. Severity: medium for missing
core UX patterns, low for polish.
```

### Wave 12: Documentation Quality (4 agents)

**Agent 48: docs-accuracy** (sonnet)
File: `_audit/latest/findings/48-docs-accuracy.md`

```text
Check docs/ pages for factual accuracy. Read key documentation pages
(architecture, tech-stack, decisions, design pages) and verify claims
against actual code. Look for:
- Outdated technology versions or library references
- Features described as "implemented" that are actually stubs
- Code examples that don't match actual API signatures
- Removed features still documented
- Incorrect file paths or module references
Severity: medium for misleading docs, low for minor inaccuracies.

```

**Agent 49: docs-completeness** (sonnet)
File: `_audit/latest/findings/49-docs-completeness.md`

```text
Check for documentation gaps. Look for:
- Major features with no documentation page
- API endpoints with no usage examples in docs
- Configuration options with no documentation
- Architecture decisions made but not recorded in decisions.md
- Missing "getting started" or onboarding content
- Design pages referenced in DESIGN_SPEC.md that don't exist
Cross-reference docs/design/ index with actual pages.
Severity: medium for missing feature docs, low for missing examples.

```

**Agent 50: readme-website-accuracy** (sonnet)
File: `_audit/latest/findings/50-readme-website-accuracy.md`

```text
Check README.md and any public-facing content (landing page content in
docs/, comparison page, etc.) for:
- Outdated version numbers or release dates
- Feature claims that don't match current implementation status
- Broken badges or status indicators
- Competitor comparisons with outdated information
- Missing or incorrect installation instructions
- Outdated screenshots or diagrams
Severity: medium for public-facing inaccuracies.

```

**Agent 51: docs-diagram-quality** (sonnet)
File: `_audit/latest/findings/51-docs-diagram-quality.md`

```text
Check all D2 and Mermaid diagrams in docs/ for:
- Diagrams that reference modules/classes that no longer exist
- Diagrams that are missing recently added major components
- Inconsistent naming between diagram labels and actual code names
- Diagrams using ASCII/Unicode box-drawing (forbidden by convention)
- Missing diagrams for complex subsystems that would benefit from visuals
Cross-reference diagram content with actual source structure.
Severity: low.

```

### Wave 13: UX & Content Quality (8 agents)

**Agent 52: ux-consistency** (sonnet)
File: `_audit/latest/findings/52-ux-consistency.md`

```text
Check dashboard pages for visual and interaction consistency:
- Pages using different card layouts for similar data
- Inconsistent button placement (some pages put actions top-right,
  others bottom)
- Inconsistent status indicator styles across pages
- Pages with different table/list component choices for similar data
- Inconsistent empty state messaging tone/style
- Inconsistent date/time display formats across pages
Severity: medium for jarring inconsistencies, low for minor.

```

**Agent 53: ux-responsiveness** (sonnet)
File: `_audit/latest/findings/53-ux-responsiveness.md`

```text
Check dashboard for responsive design issues. The dashboard shows a
MobileUnsupportedOverlay below the 768px breakpoint, but check for:
- Content overflow or horizontal scrolling on narrow screens (768-1024px)
- Tables that don't adapt (no horizontal scroll wrapper)
- Fixed-width layouts that don't flex
- Charts/graphs that don't resize properly
- Modals/drawers that overflow on smaller screens
Severity: medium.

```

**Agent 54: ux-performance-patterns** (sonnet)
File: `_audit/latest/findings/54-ux-performance-patterns.md`

```text
Check dashboard for performance antipatterns:
- Large component re-renders (components that subscribe to entire store
  instead of selectors)
- Missing React.memo on list item components
- Missing useMemo/useCallback on expensive computations passed as props
- Unnecessary re-fetching (polling without stale-time checks)
- Large bundle imports that should be lazy-loaded
- Images without width/height (causing layout shift)
Severity: medium.

```

**Agent 55: api-docs-openapi** (sonnet)
File: `_audit/latest/findings/55-api-docs-openapi.md`

```text
Check the OpenAPI/Scalar API documentation for completeness:
- Endpoints missing descriptions or summaries
- Request/response schemas missing field descriptions
- Missing example values in schemas
- Endpoints with undocumented error responses
- Missing authentication requirements in endpoint docs
Check by reading controller decorators and Pydantic model docstrings.
Severity: low.

```

**Agent 56: cli-docs-help** (haiku)
File: `_audit/latest/findings/56-cli-docs-help.md`

```text
Check Go CLI commands for documentation completeness:
- Commands missing Long descriptions
- Flags missing usage text
- Missing examples in command help
- Inconsistent flag naming conventions
- Commands without --help output verification
Check cli/cmd/*.go for cobra.Command fields. Severity: low.

```

**Agent 57: storybook-coverage** (sonnet)
File: `_audit/latest/findings/57-storybook-coverage.md`

```text
Check web/src/components/ui/ for Storybook story coverage. Every shared
component should have a .stories.tsx file. For existing stories, check:
- Missing story variants (default, loading, error, empty states)
- Stories that don't cover all major props/variants
- Components in ui/ without any story file
Severity: low for missing stories, medium for shared components with
zero coverage.

```

**Agent 58: error-messages-ux** (sonnet)
File: `_audit/latest/findings/58-error-messages-ux.md`

```text
Check error messages shown to users (toast notifications, error states,
API error responses, form validation messages) for quality:
- Generic "Something went wrong" without actionable guidance
- Technical jargon exposed to end users (stack traces, error codes
  without explanation)
- Missing retry suggestions on transient errors
- Inconsistent error message tone across the app
- Error messages that don't tell the user what to do next
Check both frontend toast calls and backend API error messages.
Severity: medium for unhelpful errors, low for tone inconsistencies.

```

**Agent 59: onboarding-flow** (sonnet)
File: `_audit/latest/findings/59-onboarding-flow.md`

```text
Check the setup wizard and first-run experience:
- Setup steps that can fail silently
- Missing validation on setup inputs
- Unclear or missing help text during setup
- Missing progress indicators during setup operations
- Post-setup state that leaves features half-configured
- Missing guidance on what to do after setup completes
Check web/src/pages/setup/ and src/synthorg/api/controllers/setup.py.
Severity: medium.

```

### Wave 14: Abstraction Boundaries & Backend Parity (13 agents)

**Agent 60: dual-backend-protocol-parity** (sonnet)
File: `_audit/latest/findings/60-dual-backend-protocol-parity.md`

```text
Every repository Protocol in src/synthorg/persistence/*_protocol.py must have
concrete implementations in BOTH src/synthorg/persistence/sqlite/ AND
src/synthorg/persistence/postgres/. For each Protocol, verify both impls exist
and implement every method with matching signatures.

Flag:
- Protocols with SQLite-only or Postgres-only impls
- Methods present on one backend but not the other
- Signature drift (parameter names, types, return types diverging)

Severity: high for missing impl, medium for signature drift.

```

**Agent 61: migration-parity** (sonnet)
File: `_audit/latest/findings/61-migration-parity.md`

```text
Migrations in src/synthorg/persistence/sqlite/revisions/ and
src/synthorg/persistence/postgres/revisions/ must stay in semantic parity.

For each recent migration, verify the same schema change exists in the other
backend. Compare schema.sql files in both backends for table/column drift.

Flag:
- Migrations added to one backend only
- Tables/columns in one schema but not the other
- Column type mismatches (TEXT vs VARCHAR, INTEGER vs BIGINT) implying drift

Severity: high.

```

**Agent 62: dual-backend-test-parity** (sonnet)
File: `_audit/latest/findings/62-dual-backend-test-parity.md`

```text
Every repository implementation in src/synthorg/persistence/sqlite/ and
src/synthorg/persistence/postgres/ must have test coverage on BOTH backends
(parametrized fixtures or mirrored test files).

Flag:
- Repo impls with tests on only one backend
- Integration suites that don't run against both backends
- Conftest fixtures defaulting to one backend with no parametrized counterpart

Severity: medium.

```

**Agent 63: persistence-boundary-deep** (sonnet)
File: `_audit/latest/findings/63-persistence-boundary-deep.md`

```text
scripts/check_persistence_boundary.py (PreToolUse hook) catches import-level
leaks. This agent does deeper analysis.

Outside src/synthorg/persistence/ and the allowlisted exceptions in the hook's
_ALLOWLIST, find:
- Raw SQL DDL/DML in multi-line strings (CREATE TABLE, INSERT INTO, UPDATE,
  DELETE, ALTER, DROP)
- f-string or template-rendered SQL
- Dynamic query builders
- ORM session or transaction boundary management

Severity: high.

```

**Agent 64: provider-boundary-leaks** (sonnet)
File: `_audit/latest/findings/64-provider-boundary-leaks.md`

```text
All LLM calls must go through BaseCompletionProvider in src/synthorg/providers/.

Outside src/synthorg/providers/, find:
- Direct litellm.completion / litellm.acompletion calls
- Raw openai / anthropic / mistralai / google.generativeai SDK imports or instantiation
- HTTP calls to /v1/chat/completions or similar provider endpoints
- Anything bypassing the retry + rate-limit + fallback infrastructure

Severity: high.
```

**Agent 65: memory-boundary-leaks** (sonnet)
File: `_audit/latest/findings/65-memory-boundary-leaks.md`

```text
All memory/recall operations must go through src/synthorg/memory/.

Outside src/synthorg/memory/, find:
- Direct mem0 SDK calls
- qdrant_client imports or usage
- Raw vector store client instantiation
- Embedding API calls bypassing the memory abstraction

Severity: medium.

```

**Agent 66: queue-boundary-leaks** (sonnet)
File: `_audit/latest/findings/66-queue-boundary-leaks.md`

```text
Inter-component messaging must go through the project's event bus / message
bus abstraction.

Find:
- Direct nats / nats.aio client usage outside the messaging module
- Bare asyncio.Queue used for cross-subsystem communication (single-function
  use is fine)
- Redis pub/sub or similar bypassing the abstraction

Severity: medium.

```

**Agent 67: process-spawn-leaks** (sonnet)
File: `_audit/latest/findings/67-process-spawn-leaks.md`

```text
Process spawning and container orchestration must go through the sandbox /
orchestration layer.

Outside sandbox / execution / CLI orchestrator modules, find:
- subprocess.run / Popen
- asyncio.create_subprocess_exec / asyncio.create_subprocess_shell
- docker.DockerClient / aiodocker.Docker instantiation
- os.system or similar shell-outs

Severity: high for arbitrary code paths, medium for admin tools.

```

**Agent 68: state-mutation-leaks** (sonnet)
File: `_audit/latest/findings/68-state-mutation-leaks.md`

```text
API controllers should delegate to service-layer methods, not reach into
persistence internals.

In src/synthorg/api/controllers/, find:
- Direct repository method calls for write operations (should go through a service)
- Raw DB session access
- Transaction management inside controllers
- Anything bypassing the service layer to mutate state

Severity: medium.
```

**Agent 69: hardcoded-backend-selection** (sonnet)
File: `_audit/latest/findings/69-hardcoded-backend-selection.md`

```text
Backend choices must be driven by config factories, not hardcoded in business
logic.

Outside config/, settings/, and factory modules, grep for string literals used
in branching logic: "sqlite", "postgres", "nats", "mem0", "qdrant",
"in_memory", "in-process". Flag patterns like `if backend == "sqlite":` in
business code.

Severity: medium.
```

**Agent 70: pluggable-impl-coverage** (opus)
File: `_audit/latest/findings/70-pluggable-impl-coverage.md`

```text
Per CLAUDE.md, every pluggable subsystem uses Protocol + concrete strategies +
factory + config discriminator.

For every *_protocol.py, *_factory.py, and *_config.py discriminator in
src/synthorg/:
1. Enumerate all discriminator values (enum members or Literal types)
2. Verify each value has a registered factory mapping
3. Verify each value has at least one test case
4. Verify the discriminator is documented

Flag:
- Discriminator values with no factory entry
- Factory entries with no impl
- Impls with no test

Severity: medium.
```

**Agent 71: abstraction-swap-readiness** (opus)
File: `_audit/latest/findings/71-abstraction-swap-readiness.md`

```text
Adding a new backend should require zero changes outside the owning module.

Find code that would break that invariant:
- isinstance() checks against concrete backend classes in business logic
- `if backend_type == "X":` branches outside factories
- Concrete impl type hints (e.g. SQLiteRepo) leaking into public APIs where
  the Protocol type should be used
- Direct instantiation of concrete impls outside factories

Severity: medium.
```

**Agent 72: dependency-inversion-violations** (opus)
File: `_audit/latest/findings/72-dependency-inversion-violations.md`

```text
High-level modules (engine, api, communication) should depend on Protocol
types, not concrete impls.

For each imported symbol in src/synthorg/engine/, src/synthorg/api/, and
src/synthorg/communication/, check if it's a concrete class when a Protocol
exists for the same role. Flag imports of concrete classes where a Protocol
type is available and would satisfy the call site.

Severity: low.
```

### Wave 15: Documentation Truth & Freshness (9 agents)

**Agent 73: roadmap-currency** (sonnet)
File: `_audit/latest/findings/73-roadmap-currency.md`

```text
Read `roadmap.md`, all files matching `docs/*roadmap*`, `docs/future-vision*`,
`docs/vision*`. For each:

1. Check version themes vs. `gh release list` and `gh issue list` -- flag
   themes listing closed issues as open work, shipped work described as
   future, version numbers not matching released tags in pyproject.toml,
   missing entries for shipped versions.

2. Extract every numeric/temporal claim in narrative text (test counts,
   agent counts, "since vX", "as of <date>", "X+ design pages", etc.) and
   verify against live source.

3. Check every "future" / "planned" / "upcoming" claim against shipped
   work -- if the feature is shipped, the claim is stale.

## Evidence Requirement
You MUST emit Bash output for every numeric/temporal claim you verify:
- Test count: paste output of `uv run python -m pytest tests/ --collect-only -q | tail -1`
- Release list: paste output of `gh release list --limit 10`
- Subagent file count (counts files in `.claude/agents/`, the on-disk dev subagents -- distinct from the codebase-audit skill's inline agent prompts): paste `ls .claude/agents | wc -l`
- pyproject.toml version: paste the matching line

Findings without evidence are inadmissible.

## Severity Calibration
- Medium for version-theme drift on internal pages.
- HIGH for stale numeric claims on pages reachable from synthorg.io top
  nav (homepage, roadmap, comparison, vision).
- HIGH for shipped work described as future.
```

**Agent 74: comparison-page-accuracy** (sonnet)
File: `_audit/latest/findings/74-comparison-page-accuracy.md`

```text
Read docs/comparison*.md and scripts/generate_comparison.py. For every feature
we claim to support, verify the claim against actual code in src/synthorg/.

Flag:
- Checkmarks for features with no implementation
- Missing checkmarks for features we do implement
- Competitor feature claims without cited sources
- "We support X" statements without the supporting code

Severity: medium for inaccurate self-claims, low for competitor drift.

```

**Agent 75: landing-page-metrics** (sonnet)
File: `_audit/latest/findings/75-landing-page-metrics.md`

```text
Walk every page in `mkdocs.yml` nav (not just landing-style pages). For
each page, enumerate every numeric claim and verify against live source.
Roadmap, vision, comparison, architecture, design pages, blog posts all
qualify -- do NOT limit to "landing-style" pages.

For each number, verify against live source:
- Test count via `uv run python -m pytest tests/ --collect-only -q | tail -1`
- Provider count via `providers/presets.py`
- Backend count via `persistence/` subdirectories
- Tool count via `tools/registry.py`
- Subagent file count (`.claude/agents/` on-disk dev subagents) via `ls .claude/agents`
- Supported model count via the model registry
- Line count / file count claims via `find` + `wc`

Flag EVERY stale number, not just obvious ones.

## Evidence Requirement
You MUST emit Bash output for every numeric claim you verify. Findings
without evidence are inadmissible -- the validation phase rejects
evidence-free numeric findings with severity downgrade to info.

## Severity Calibration
- HIGH for stale numbers on pages reachable from synthorg.io top nav
  (homepage, roadmap, comparison, vision, architecture, decisions,
  getting-started).
- Medium otherwise.

Stale public-facing numbers are an investor / user trust issue, not a
low-priority finding.
```

**Agent 76: superseded-decisions** (sonnet)
File: `_audit/latest/findings/76-superseded-decisions.md`

```text
Read docs/decisions*.md, ADR files, and docs/design/ pages. For each accepted
decision, check: was it later reversed or superseded?

Flag:
- Decisions marked "accepted" that code no longer follows
- ADRs without status updates (missing "Superseded by" cross-link)
- "Decided to use X" claims where code now uses Y
- Contradicting decisions across multiple ADRs with no resolution

Severity: medium.
```

**Agent 77: config-reference-drift** (sonnet)
File: `_audit/latest/findings/77-config-reference-drift.md`

```text
Compare docs/reference/*.md env var / settings reference against
src/synthorg/settings/definitions/ and src/synthorg/config/.

Flag:
- Documented settings that don't exist in code
- Real settings with no doc entry
- Default values in docs that disagree with code defaults
- Type / validation mismatches

Severity: medium.
```

**Agent 78: cli-reference-drift** (sonnet)
File: `_audit/latest/findings/78-cli-reference-drift.md`

```text
Compare CLI reference pages in docs/reference/ (and cli/CLAUDE.md command
listings) against actual cobra definitions in cli/cmd/*.go.

Flag:
- Documented flags or commands that don't exist
- Real flags or commands with no docs entry
- Description drift between docs and cobra Long / Short fields

Severity: medium.
```

**Agent 79: api-reference-drift** (sonnet)
File: `_audit/latest/findings/79-api-reference-drift.md`

```text
Compare API reference pages (docs/reference/api*.md, if present) against
actual Litestar routes in src/synthorg/api/controllers/.

Flag:
- Documented endpoints not registered in code
- Registered endpoints with no reference docs
- Request / response shape drift between docs and DTOs

Severity: medium.
```

**Agent 80: example-config-validity** (sonnet)
File: `_audit/latest/findings/80-example-config-validity.md`

```text
For every fenced code block in docs/ tagged yaml / toml / json / env / dotenv
that looks like a config example, validate it against the current Pydantic
schema.

Flag:
- Snippets with deprecated keys
- Missing required fields
- Type mismatches
- Keys no longer recognized by the schema

Severity: medium.
```

**Agent 81: design-spec-contradictions** (opus)
File: `_audit/latest/findings/81-design-spec-contradictions.md`

```text
Cross-reference pages in docs/design/. Find internal contradictions.

Flag:
- Two pages making contradictory claims about the same subsystem (e.g. page A
  says "all writes go through the engine", page B says "workers write directly")
- Terminology drift (same concept named differently across pages)
- `§cross-references` to sections that no longer exist

Severity: medium.
```

### Wave 16: Docs Scope & Rot (5 agents)

**Agent 82: docs-scope-creep** (sonnet)
File: `_audit/latest/findings/82-docs-scope-creep.md`

```text
Read each docs/ page and assess whether it has grown beyond its stated purpose.

Flag:
- Pages whose scope (as described in their intro) doesn't match the actual
  content (e.g. architecture page full of HR details)
- Topics that should live on a dedicated page
- Pages over 800 lines mixing unrelated subjects

Severity: low.
```

**Agent 83: stale-code-examples** (sonnet)
File: `_audit/latest/findings/83-stale-code-examples.md`

```text
For every fenced code block in docs/ tagged python / typescript / javascript /
go / bash that calls project code, verify the APIs referenced still exist
with the same signatures.

Flag:
- Renamed functions or classes
- Moved imports
- Changed parameter names or types
- Removed methods

Severity: medium.
```

**Agent 84: removed-features-still-mentioned** (sonnet)
File: `_audit/latest/findings/84-removed-features-still-mentioned.md`

```text
Read docs/ narrative prose for feature / module / concept names. For each
reference, verify the named thing still exists in code.

Flag:
- "How it works" sections describing subsystems that have been deleted
- Feature walkthroughs for removed capabilities
- Prose referencing renamed modules by their old names

Severity: medium.
```

**Agent 85: docs-seo-freshness** (haiku)
File: `_audit/latest/findings/85-docs-seo-freshness.md`

```text
Check page titles, meta descriptions (front matter), and opening paragraphs
in docs/ for:
- Outdated version numbers (e.g. "as of v0.5")
- Dates that have passed (e.g. "coming in Q1 2026")
- "Latest version" claims pointing to a version no longer latest

Severity: low.
```

**Agent 86: issue-pr-link-rot** (haiku)
File: `_audit/latest/findings/86-issue-pr-link-rot.md`

```text
Grep docs/ for `#NNN` references to GitHub issues / PRs. For each, verify via
`gh issue view` / `gh pr view` that the reference is still valid.

Flag:
- Deleted issues / PRs
- Renamed issues where the cited title no longer matches
- Links to issues reassigned under a different scope

Severity: low.
```

### Wave 17: Security Deep-Dive (6 agents)

**Agent 87: http-security-headers** (sonnet)
File: `_audit/latest/findings/87-http-security-headers.md`

```text
Check HTTP responses for missing security headers: Content-Security-Policy,
Strict-Transport-Security (HSTS), X-Frame-Options, X-Content-Type-Options,
Referrer-Policy, Permissions-Policy.

Scope: api/app.py middleware, Litestar response plugins, reverse proxy configs.

Severity: medium.
```

**Agent 88: cookie-auth-security** (sonnet)
File: `_audit/latest/findings/88-cookie-auth-security.md`

```text
Audit authentication and cookie hygiene:
- Cookies without HttpOnly / Secure / SameSite flags
- JWT usage without alg/exp/iss/aud validation
- OAuth flows missing state/nonce parameters
- CSRF protection gaps on state-mutating endpoints

Severity: high.
```

**Agent 89: crypto-hygiene** (sonnet)
File: `_audit/latest/findings/89-crypto-hygiene.md`

```text
Find cryptographic anti-patterns:
- `random` / `random.choice` for security-sensitive randomness (should be `secrets`)
- `==` comparison on secrets/tokens (should use `hmac.compare_digest`)
- Weak hash algorithms (MD5, SHA-1) for security
- Hardcoded IVs / nonces

Severity: high.
```

**Agent 90: secrets-in-logs** (sonnet)
File: `_audit/latest/findings/90-secrets-in-logs.md`

```text
Check the telemetry privacy allowlist in `src/synthorg/telemetry/privacy.py`
against actual `logger.*` calls. Flag:
- Logger kwargs that could leak PII, tokens, passwords, API keys
- Field names matching forbidden patterns (key, token, secret, password,
  bearer, auth, credential) being logged without scrubbing
- Exception messages that may contain sensitive context logged verbatim

Severity: high.
```

**Agent 91: path-traversal-ssrf-xxe-redos** (sonnet)
File: `_audit/latest/findings/91-path-traversal-ssrf-xxe-redos.md`

```text
Find injection-class vulnerabilities:
- Path traversal: user input passed to `open()` / `Path()` without sanitization
- SSRF: user-supplied URLs fetched without an allowlist
- XXE: XML parsing with external entity resolution enabled
- ReDoS: user-supplied strings matched against catastrophic-backtracking regexes

Severity: high.
```

**Agent 92: prompt-injection-defenses** (opus)
File: `_audit/latest/findings/92-prompt-injection-defenses.md`

```text
Audit LLM prompt handling:
- System prompts concatenated with user input without delimiters/tagging
- LLM output used as tool arguments without schema validation
- Agent-to-agent message content treated as trusted
- Missing `<untrusted-*>` tag wrapping for external content in prompts
- Output schema validation on LLM responses

Severity: high.
```

### Wave 18: Performance & Resource Efficiency (5 agents)

**Agent 93: n-plus-one-queries** (sonnet)
File: `_audit/latest/findings/93-n-plus-one-queries.md`

```text
Find N+1 query patterns in persistence and service layers: loops that call
repository methods per item instead of batch loads, per-row fetches in render
paths, missing eager-load joins for related entities.

Severity: high.
```

**Agent 94: missing-indices** (sonnet)
File: `_audit/latest/findings/94-missing-indices.md`

```text
Cross-reference WHERE / JOIN / ORDER BY columns in persistence queries with
schema indices. Flag query patterns that would benefit from an index that
doesn't exist. Check both SQLite and Postgres schemas.

Severity: medium.
```

**Agent 95: missing-pagination** (sonnet)
File: `_audit/latest/findings/95-missing-pagination.md`

```text
Find API endpoints and repository methods that return unbounded lists.
Everything that can grow should accept cursor/offset+limit parameters.

Severity: medium.
```

**Agent 96: blocking-io-hot-paths** (sonnet)
File: `_audit/latest/findings/96-blocking-io-hot-paths.md`

```text
Find blocking I/O inside async hot paths:
- Sync `open()` / `requests.*` / `subprocess.run` in async functions
- CPU-bound work without `run_in_executor`
- Missing gzip/brotli compression on large payload endpoints
- Missing ETag / Cache-Control on cacheable responses

Severity: high.
```

**Agent 97: memory-leak-patterns** (sonnet)
File: `_audit/latest/findings/97-memory-leak-patterns.md`

```text
Find leak patterns:
- Event listeners / subscriptions added without teardown
- Zustand stores scheduling timers without cleanup
- Closures holding references to large objects
- Unclosed async generators / contextvars

Scope: src/synthorg/ AND web/src/.

Severity: high.
```

### Wave 19: Test Quality (3 agents)

**Agent 99: tests-with-sleeps** (haiku)
File: `_audit/latest/findings/99-tests-with-sleeps.md`

```text
Grep tests/ and web/src/__tests__/ for hardcoded sleeps / setTimeout /
asyncio.sleep in tests. These indicate timing-dependent tests that should
use deterministic waits (Event, condition variables, fake clocks).

Severity: medium.
```

**Agent 100: mock-drift** (sonnet)
File: `_audit/latest/findings/100-mock-drift.md`

```text
Compare mock shapes (MagicMock, unittest.mock patches, vi.mock) against the
real interfaces they stand in for. Flag method names, signatures, or return
types on mocks that don't match the real class/function today.

Severity: medium.
```

**Agent 101: e2e-critical-flow-gaps** (sonnet)
File: `_audit/latest/findings/101-e2e-critical-flow-gaps.md`

```text
Identify critical user flows (setup wizard, agent creation, workflow run,
budget check, approval, memory recall) and verify each has at least one E2E
test in web/e2e/ or tests/e2e/. Flag missing coverage.

Severity: medium.
```

### Wave 20: Operational & Data Readiness (5 agents)

**Agent 102: graceful-shutdown** (sonnet)
File: `_audit/latest/findings/102-graceful-shutdown.md`

```text
Check shutdown path:
- SIGTERM handler installed?
- In-flight HTTP requests drained?
- Background tasks cancelled with timeout?
- Provider / DB connections closed cleanly?
- Postgres connection pools shutdown?

Severity: high.
```

**Agent 103: data-integrity-and-leaks** (sonnet)
File: `_audit/latest/findings/103-data-integrity-and-leaks.md`

```text
SynthOrg is an internal / self-hosted framework, not a public-facing SaaS.
GDPR / data-retention / right-to-erasure / consent flows are explicitly
OUT OF SCOPE -- do NOT flag missing user-export endpoints, missing message
retention windows, missing per-PII deletion APIs, or "no audit trail for PII
reads" as findings. Operators carry that burden if their deployment falls
under a regulatory regime; the framework intentionally does not ship those
primitives.

What IS in scope: data integrity and accidental leaks. Find:

1. **Data leaks** -- domain data that escapes its intended boundary:
   - Persistent fields serialized into API responses they should not be in
     (e.g. internal-only audit IDs, encrypted-at-rest values, raw secret
     payloads, model-cost intermediate fields)
   - DTOs that include backend-only fields by default
   - Logger calls that emit secrets / tokens / API keys / bearer headers
     (Agent 90 covers most; flag what 90 misses, e.g. structured logs with
     full request/response bodies)
   - Error responses that leak internal stack traces, file paths, DB
     connection strings, or framework internals to clients
   - Exception messages re-raised verbatim through the API surface
   - Telemetry events with un-allowlisted properties (the scrubber drops
     them -- but flag the call site as wasted instrumentation)

2. **Missing persistence** -- domain state that should survive restart but
   does not:
   - Module-level dicts/lists used for cross-request state (event registries,
     in-flight tracking, idempotency keys) that are NOT also persisted to
     the repository layer
   - Caches that hold authoritative data the rest of the system depends on
     (e.g. an in-memory ticket store that loses tickets on restart while
     the rest of the system thinks they're valid)
   - Counters / gauges / running totals computed in memory and never
     reconciled with persisted source of truth
   - Workflow / task / approval state held in a service field instead of
     repo
   - In-memory rate-limiter buckets that disappear on restart, allowing
     burst on every redeploy
   - Singletons holding "should-be-durable" state without a persistence
     fallback flagged in their docstring

3. **Stale / orphan persisted data** -- the inverse: data that IS persisted
   but no longer has a consumer:
   - Tables / columns referenced nowhere (DDL exists, no read or write)
   - Row archetypes (status="LEGACY_X") never produced by current code
   - Keys in idempotency / nonce / lock stores with no expiry path
   - Audit-chain entries with op codes no live caller emits

4. **Repository read/write asymmetry** -- writes without reads (write-only
   sink that nobody queries) or reads without writes (orphan read path).

For each finding, name the model / table / field and explain whether it's a
LEAK (data escapes), MISSING_PERSISTENCE (in-memory only when it shouldn't
be), STALE_PERSISTED (no consumer), or ASYMMETRY (write-only / read-only).

Severity:
- high for secrets / tokens / credentials in API responses or logs
- high for in-memory state that other components treat as authoritative
- medium for DTO field leakage of internal IDs / metadata
- medium for missing persistence on state that survives a restart in user
  expectation
- low for stale persisted data
- low for repository asymmetry that's not actively harmful

Skip:
- GDPR / right-to-erasure / consent / "no PII delete endpoint" framing.
  This is an internal tool; that work is operator-side.
- Telemetry allowlist completeness (the scrubber is the contract, not the
  call site).
- Anything that an operator would reasonably implement themselves at the
  deployment boundary.
```

**Agent 104: monitoring-dashboards** (sonnet)
File: `_audit/latest/findings/104-monitoring-dashboards.md`

```text
Check Prometheus metrics emitted by the code against documented Grafana /
Logfire dashboards. Flag metrics without dashboards and dashboards referring
to metrics that no longer exist.

Severity: medium.
```

**Agent 105: prompt-eval-coverage** (sonnet)
File: `_audit/latest/findings/105-prompt-eval-coverage.md`

```text
List LLM prompts in src/synthorg/ (agents, tools, quality graders, etc.) and
check each has:
- An eval suite with before/after examples
- Explicit model version pinning
- Temperature / top_p set explicitly (not model default)

Severity: medium.
```

**Agent 106: health-readiness-probes** (sonnet)
File: `_audit/latest/findings/106-health-readiness-probes.md`

```text
Verify the API exposes `/healthz` (liveness) and `/readyz` (readiness) with
distinct semantics. Readiness should check DB / provider / queue connectivity;
liveness should only confirm the process is alive.

Severity: medium.
```

### Wave 21: Developer Experience & Reproducibility (2 agents)

**Agent 107: slow-precommit-hooks** (haiku)
File: `_audit/latest/findings/107-slow-precommit-hooks.md`

```text
Profile `.pre-commit-config.yaml` hooks by measuring runtime. Flag hooks
taking >3s on a typical `git commit`. Suggest moving expensive ones to
pre-push or CI.

Severity: low.
```

**Agent 108: claude-md-reproducibility** (sonnet)
File: `_audit/latest/findings/108-claude-md-reproducibility.md`

```text
For every fenced command in CLAUDE.md, web/CLAUDE.md, cli/CLAUDE.md, and
`.claude/skills/*/SKILL.md`, verify the command actually works today. Flag
commands referencing removed scripts, changed flag names, or missing tools.

Severity: medium.
```

### Wave 22: Code Quality & Duplication (6 agents)

**Agent 109: typescript-strictness** (sonnet)
File: `_audit/latest/findings/109-typescript-strictness.md`

```text
In web/src/, count and flag: `any` type usage, `@ts-ignore` / `@ts-expect-error`
comments, non-null assertions (`!`), and type assertions (`as X`) where a type
guard would be safer. Verify tsconfig `strict` is fully enabled.

Severity: medium.
```

**Agent 110: duplicate-business-logic** (sonnet)
File: `_audit/latest/findings/110-duplicate-business-logic.md`

```text
Find blocks of business logic duplicated across 2+ modules. Focus on
substantive duplication (>10 lines of non-trivial code), not boilerplate.

Severity: medium.
```

**Agent 111: duplicate-types** (sonnet)
File: `_audit/latest/findings/111-duplicate-types.md`

```text
Find types defined on both backend (Pydantic models / dataclasses) and
frontend (TypeScript types / interfaces) that should be generated from a
single source instead. Focus on DTOs, API request/response shapes, and
shared enums.

Severity: medium.
```

**Agent 112: duplicate-error-codes** (sonnet)
File: `_audit/latest/findings/112-duplicate-error-codes.md`

```text
Cross-reference custom exception names, RFC 9457 error `type` fields, and
frontend error code enums. Flag the same conceptual error defined in multiple
places.

Severity: medium.
```

**Agent 113: feature-flag-coverage** (sonnet)
File: `_audit/latest/findings/113-feature-flag-coverage.md`

```text
Find feature flags / settings that gate risky behavior. Flag:
- Risky features with no kill-switch
- Flags that are always-on or always-off (dead flags)
- Flags referenced in code but not defined in settings

Severity: medium.
```

**Agent 114: default-config-sanity** (sonnet)
File: `_audit/latest/findings/114-default-config-sanity.md`

```text
Review default values across `src/synthorg/config/` and
`settings/definitions/`. Flag defaults that are unsafe for production or
surprise the user (debug=True, verbose logging, open CORS, etc.).

Severity: medium.
```

### Wave 23: CI & Supply Chain (5 agents)

**Agent 115: workflow-permissions** (sonnet)
File: `_audit/latest/findings/115-workflow-permissions.md`

```text
Audit `.github/workflows/*.yml` for:
- Over-broad `GITHUB_TOKEN` permissions (should be least-privilege per job)
- Missing `permissions:` block (inherits repo default, usually too broad)
- Missing environment protection rules on prod deploys

Severity: high.
```

**Agent 116: ci-flakiness** (sonnet)
File: `_audit/latest/findings/116-ci-flakiness.md`

```text
Analyze recent CI run history (via `gh run list`) for patterns:
- Tests failing intermittently on unchanged code
- Jobs hitting timeout consistently
- Cache misses that slow runs significantly

Severity: medium.
```

**Agent 117: unused-deps** (sonnet)
File: `_audit/latest/findings/117-unused-deps.md`

```text
Cross-reference dependencies declared in `pyproject.toml`, `web/package.json`,
and `cli/go.mod` with actual import statements. Flag deps that are declared
but never imported anywhere.

Severity: medium.
```

**Agent 118: duplicate-deps** (sonnet)
File: `_audit/latest/findings/118-duplicate-deps.md`

```text
Find redundant libraries doing the same job (e.g. lodash + ramda, axios +
fetch wrappers, multiple date libraries). Recommend consolidation.

Severity: low.
```

**Agent 119: license-compat** (sonnet)
File: `_audit/latest/findings/119-license-compat.md`

```text
Check dependency licenses against project BUSL-1.1 license. Flag deps with
GPL, AGPL, or other copyleft licenses that conflict with the project license
grant.

Severity: high.
```

### Wave 24: Client Robustness (2 agents)

**Agent 120: rate-limit-client** (sonnet)
File: `_audit/latest/findings/120-rate-limit-client.md`

```text
Audit client-side handling of HTTP 429 responses:
- Web dashboard: retry-with-backoff on API calls?
- Python SDK / provider clients: retry-after header respected?
- CLI: graceful degradation on rate-limited endpoints?

Severity: medium.
```

**Agent 121: ws-sse-robustness** (sonnet)
File: `_audit/latest/findings/121-ws-sse-robustness.md`

```text
Check WebSocket and Server-Sent Events implementations:
- Reconnection logic with exponential backoff
- Heartbeat / ping-pong to detect stalled connections
- Backpressure handling when client can't keep up
- Message schema versioning
- Graceful fallback if WS/SSE is blocked by proxies

Severity: medium.
```

### Wave 25: Git History & Drift (2 agents)

**Agent 122: git-history-secrets-and-bloat** (sonnet)
File: `_audit/latest/findings/122-git-history-secrets-and-bloat.md`

```text
Two narrow checks on full git history (not just current tree):
1. Run `gitleaks detect --log-opts=--all` over full history. Pre-commit
   gitleaks only covers the current commit; this catches secrets that slipped
   in before the hook existed.
2. List top-20 largest blobs in git via
   `git rev-list --objects --all | git cat-file --batch-check='%(objectsize) %(objectname) %(rest)' | sort -rn | head -20`.
   Flag blobs >1MB that should be in LFS or removed.

Severity: critical for secrets, medium for bloat.
```

**Agent 123: temporal-drift-wording** (sonnet)
File: `_audit/latest/findings/123-temporal-drift-wording.md`

```text
Scan docs/, CLAUDE.md, web/CLAUDE.md, cli/CLAUDE.md, README.md, and
.claude/skills/*/SKILL.md for temporal / reference-drift wording that will
go stale or is already stale:
- "new in vX.Y", "added in version X", "recently added"
- "as of <date>", "as of <version>"
- "coming soon", "in the next release"
- "the new X" when X is now baseline
- "legacy" markers for code that's now the only implementation
- "temporary" / "workaround" that persisted
- "TODO: remove after <date>" where date has passed
- Positional references like "above", "below", "this here" that rot after reorganisation
- `#NNN` issue references that no longer match the cited topic

Severity: low for stylistic drift, medium for misleading claims.

```

### Wave 26: SynthOrg-Specific Invariants (7 agents)

**Agent 124: mcp-handler-contract** (sonnet)
File: `_audit/latest/findings/124-mcp-handler-contract.md`

```text
Audit src/synthorg/meta/mcp/handlers/ against the contract documented in
docs/reference/mcp-handler-contract.md. Every handler must:
- Implement the ToolHandler protocol
- Return responses via envelope helpers (ok / err / capability_gap /
  not_supported) from common.py, not raw dicts
- Validate args via require_arg
- Call require_admin_guardrails(arguments, actor) as the lexically first
  call in the handler body (descending into a single outer try: is fine,
  but no other Call may precede it) on every handler registered with
  admin_tool, OR carry the full opt-out grammar
  `# lint-allow: mcp-admin-guardrail -- <non-empty reason>` somewhere on
  the function-header span (the lines from `async def` through the
  closing `) -> ...:`). Sanctioned opt-outs cover approval-queue
  routing, parameterless reconnects, non-mutating registrations, and
  partial PATCH semantics where another path enforces the full
  guardrail; any other rationale, an annotation missing the `--`
  separator, or an empty reason is a violation.
- Route through service-layer facades (ArtifactService, WorkflowService,
  MemoryService, CustomRulesService, UserService) -- never reach into
  app_state.persistence.* directly

Flag handlers that build raw dict responses, miss guardrails on
admin_tool without the lint-allow annotation in the correct grammar and
header-span position, place a non-guardrail Call before the guardrail in
the body, or bypass services to hit repos.

Severity: high.

```

**Agent 125: sec1-prompt-safety-call-sites** (sonnet)
File: `_audit/latest/findings/125-sec1-prompt-safety-call-sites.md`

```text
Audit the SEC-1 untrusted-content fence inventory documented in
docs/reference/sec-prompt-safety.md. Every LLM call site that interpolates
attacker-controllable strings (tool results, agent messages, web content,
user-supplied prompts) must:
- Wrap untrusted content via wrap_untrusted(tag, content) from
  synthorg.engine.prompt_safety
- Append untrusted_content_directive(tags) to the system prompt

Enumerate every LLM call site (calls into BaseCompletionProvider or its
subclasses) and verify each. Cross-reference against the documented
inventory to catch sites that were added later without the same treatment.

Severity: critical for missing wrap_untrusted, high for missing directive.

```

**Agent 126: currency-aggregation-invariant** (sonnet)
File: `_audit/latest/findings/126-currency-aggregation-invariant.md`

```text
Every aggregation site over cost-bearing models (CostRecord,
TaskMetricRecord, LlmCalibrationRecord, AgentRuntimeState) must enforce
a same-currency invariant and raise MixedCurrencyAggregationError on
mismatch.

Find every aggregation method (sum, total, average, group-by, reduce) over
these models in src/synthorg/. Verify each rejects mixed currencies with
the documented exception. Flag silent currency mixing -- arithmetic across
records with different currency: CurrencyCode values without the guard.

Known aggregation sites to audit at minimum: CostTracker, ReportGenerator,
CostOptimizer, HR WindowMetrics. Discover any newer aggregators.

Severity: high.

```

**Agent 127: lifecycle-lock-pattern** (sonnet)
File: `_audit/latest/findings/127-lifecycle-lock-pattern.md`

```text
Per docs/reference/lifecycle-sync.md, every service with async start() and
stop() methods must:
- Use a dedicated self._lifecycle_lock: asyncio.Lock (separate from any
  hot-path lock the service may hold for normal operation)
- Hold the lifecycle lock across the full body of both start() and stop()
- On stop() timeout, mark the service unrestartable

Find all classes with async start() or async stop() methods in
src/synthorg/. Verify the pattern. Flag:
- Single shared lock used for both lifecycle and hot path
- Lock not held across the full method body
- Missing unrestartable flag on timeout
- Services without any lifecycle locking at all

Severity: high.

```

**Agent 128: cost-tracking-coverage** (sonnet)
File: `_audit/latest/findings/128-cost-tracking-coverage.md`

```text
Every LLM completion must produce a CostRecord. Trace every code path that
calls BaseCompletionProvider.complete() (or its async variant) and verify
a CostRecord is emitted on success.

Flag paths that bypass cost recording:
- Tool-internal LLM calls (some tools call providers directly inside their
  execute method)
- Agent self-reflection loops
- Eval pipelines (shadow eval, calibration runs)
- Verification stages
- Quality grader calls

For each bypass, suggest where the CostRecord should be emitted.

Severity: high.

```

**Agent 129: audit-chain-coverage** (sonnet)
File: `_audit/latest/findings/129-audit-chain-coverage.md`

```text
Security-sensitive operations must emit to synthorg.observability.audit_chain.
Build an inventory of operations that should:
- Auth login / logout
- Permission grants / revokes
- Settings changes (especially security-relevant settings)
- Secret reads / writes
- Approval grants
- Autonomy-level changes
- User CRUD (create, update, delete)
- Custom rule edits
- API key issuance / revocation

For each, find the implementing code path and verify an audit_chain
emission exists. Flag silent mutations of security state.

Severity: high.

```

**Agent 130: pre-alpha-rename-completeness** (sonnet)
File: `_audit/latest/findings/130-pre-alpha-rename-completeness.md`

```text
Per the project's pre-alpha rule, when a symbol is renamed every caller
must use the new name in the same change. No aliases, no dual-codepath
wrappers, no parallel field names retained.

PEP 758 reminder: `except A, B:` without parentheses is valid Python 3.14
syntax when not binding to a name. Do not flag it as a syntax error or
suggest adding parentheses -- it is correct as written.

Find telltale patterns of legacy support:
- Deprecated passthrough functions: def old_name(*a, **kw): return new_name(*a, **kw)
- Dual-codepath if/else on version flags
- Comments hinting at retained legacy support
- Re-exports of moved modules (from old.path import X as X)
- DTOs populating both an old field and a new field for the same value
- Conditional imports of the form try: from new import X; except: from old import X
- Type aliases pointing at moved types kept in the old location

Severity: medium.

```

### Wave 27: Generic Correctness Gaps (5 agents)

**Agent 131: websocket-sse-auth** (sonnet)
File: `_audit/latest/findings/131-websocket-sse-auth.md`

```text
Agent 121 covers WebSocket / SSE robustness (reconnection, heartbeat,
backpressure). This agent covers auth specifically.

For every WebSocket upgrade handler and Server-Sent Events endpoint in
src/synthorg/api/, verify:
- Auth is enforced at handshake time (not after the connection is open)
- Bearer token / session cookie is validated against the same auth chain
  REST endpoints use
- Connection is closed with a 4xx status on auth failure (not silently
  accepted)
- Long-lived connections re-validate auth periodically (token expiry
  handling) -- a 24-hour-old WebSocket should not survive a token revocation

Flag endpoints with no auth check, with auth checked only on the first
message instead of at handshake, or with no token-expiry handling on
long-lived connections.

Severity: high.

```

**Agent 132: prometheus-label-cardinality** (sonnet)
File: `_audit/latest/findings/132-prometheus-label-cardinality.md`

```text
Audit Prometheus metric definitions in
src/synthorg/observability/prometheus_collector.py and any Counter /
Histogram / Gauge instantiations elsewhere.

Flag labels with unbounded cardinality (causes memory explosion in
production):
- User IDs as labels
- Request IDs as labels
- Free-form strings as labels (error messages, file paths, URL paths
  with embedded IDs)
- Timestamps as labels
- Anything that grows linearly with traffic

For each, suggest a bounded alternative: bucket the value (latency_bucket
instead of latency_ms), use exemplars instead of labels for high-cardinality
context, or move the data to logs.

## Evidence Requirement
For each flagged metric, paste the metric definition (with file:line) and
note the unbounded label.

Severity: high for unbounded user/request IDs, medium for less risky
high-cardinality.

```

**Agent 133: idempotency-retry-safety** (sonnet)
File: `_audit/latest/findings/133-idempotency-retry-safety.md`

```text
Workers and message handlers must be idempotent because retries can deliver
the same message twice. Audit:
- NATS task handlers in workers/ and engine/
- Webhook receivers in api/controllers/
- Async task protocol consumers
- Any code subscribing to retry-eligible queues
- Background job runners (backup, eval, calibration)

For each handler, verify one of:
- Idempotency keys (message ID stored, duplicate detected and skipped)
- Deduplication checks before mutation
- Pure idempotency (writes use upsert; mutations are commutative; sends
  are de-duped downstream)

Flag handlers that on redelivery would: double-charge a budget,
double-create a row, double-emit an event, double-call a side-effecting
external API.

Severity: high.

```

**Agent 134: time-clock-injection** (haiku)
File: `_audit/latest/findings/134-time-clock-injection.md`

```text
Project test convention requires deterministic timing tests via clock
injection (mock time.monotonic, asyncio.sleep, etc.). Find production
code that reads the wall clock directly without an injection seam.

Grep src/synthorg/ for bare:
- time.monotonic()
- time.time()
- datetime.utcnow()
- datetime.now() (without tz argument)
- asyncio.get_event_loop().time()

Skip:
- Files that already accept a clock parameter or use a Clock protocol
- Tests (tests/)
- Observability / telemetry (legitimate wall-clock use for metric
  timestamps)

Flag business-logic uses (rate limiters, timeouts, expirations, scheduling)
that read the wall clock directly -- these block deterministic testing.

Severity: medium.

```

**Agent 135: pydantic-deep-checks** (sonnet)
File: `_audit/latest/findings/135-pydantic-deep-checks.md`

```text
Agent 31 covers basic Pydantic conventions (frozen, allow_inf_nan,
NotBlankStr). This agent goes deeper.

For every Pydantic model in src/synthorg/, flag:
- extra="allow" outside DTOs that intentionally accept passthrough fields
  (security smell -- accepts arbitrary keys from external input)
- model_validator(mode="before") that mutates the input dict in place
  (returning a new dict is correct; in-place mutation breaks reuse)
- Validators that catch Exception broadly and silently coerce instead of
  raising ValidationError
- Field(..., default_factory=lambda: <mutable>) where the lambda body
  closes over shared mutable state (the "mutable default" trap in disguise)
- Fields typed as plain dict / list without item type parameters in
  business-domain models (escape hatch that defeats validation)
- Computed fields that perform I/O (database calls, HTTP requests) -- they
  run on every serialization

Severity: medium. Flag extra="allow" on input DTOs as high.

```

### Wave 28: Centralization & Architectural Rework (15 agents)

These agents look for systemic patterns indicating a centralized system, missing abstraction, or fundamental rework is needed, not single-file bugs. Findings from this wave feed into the Architectural Recommendations section of INDEX.md and the REWORK.md synthesis (Phase 3.5) rather than per-finding GitHub issues.

**Agent 136: repeated-workarounds** (sonnet)
File: `_audit/latest/findings/136-repeated-workarounds.md`

```text
Cluster all HACK / FIXME / WORKAROUND / XXX / TEMPORARY comments in
src/synthorg/, web/src/, and cli/ by what they reference: library name,
function name, root cause symbol. Three or more workarounds pointing at
the same upstream cause is a signal to fix the root, not patch N callers.

Output GROUPS, not individual TODOs (Agent 17 already covers individuals).
Each group entry should list:
- The shared root cause as a one-line description
- Every file:line where a workaround for it appears
- A proposal: what fix at the root would let all the workarounds be removed

Severity: medium when 3 or more cluster on one cause; low for pairs.

```

**Agent 137: centralization-opportunities** (opus)
File: `_audit/latest/findings/137-centralization-opportunities.md`

```text
Find duplicated helper functions across modules: same logic implemented in
two or more places under different names. Look for clusters of:
- safe_get / get_or_default / dict_get_or
- to_iso / format_timestamp / iso_format
- normalize_id / canonicalize_id / clean_id
- chunked / batched / partition
- merge_dicts / dict_merge / deep_merge
- env_var coercion (str_to_bool, int_or_default)
- Retry decorators / backoff helpers
- ID prefix / suffix strippers

For each cluster, propose a single home (e.g. synthorg.core.utils, or a
domain-specific module) and list every caller that should migrate.

Output groups, not individual functions. A group of 1 is not a finding.

Severity: medium.

```

**Agent 138: inline-cross-cutting-concerns** (sonnet)
File: `_audit/latest/findings/138-inline-cross-cutting-concerns.md`

```text
Find cross-cutting concerns implemented at call sites instead of centrally.
Each cluster suggests a missing decorator / middleware / aspect.

Look for:
- Inline auth checks (if not user.is_admin: raise) instead of route guards
  or decorators
- Inline retry loops (for attempt in range(3): ...) instead of going
  through BaseCompletionProvider or a tenacity-style decorator
- Inline rate-limit checks instead of the rate-limiter middleware
- Inline error-to-HTTP mapping (try/except converting to JSON response)
  instead of an exception handler chain
- Inline logging-context construction (every caller building the same
  structured kwargs) instead of contextvars or a logger adapter

For each pattern, count occurrences. 5 or more occurrences across distinct
modules is a finding worth surfacing as a missing abstraction.

Severity: medium.

```

**Agent 139: fragmented-dispatch** (sonnet)
File: `_audit/latest/findings/139-fragmented-dispatch.md`

```text
Find if/elif (Python) or switch (TS) chains on the same enum, type, or
discriminator repeated across 3 or more call sites. Examples:
- if backend == "sqlite": ... elif backend == "postgres": ... repeated in
  many modules
- if event.type == "X": ... elif event.type == "Y": ... in multiple
  handlers
- match user.role: case "admin": ... case "user": ... duplicated

Each repetition is a missed polymorphism / strategy-registry opportunity.
Group findings by discriminator. Propose a registry or polymorphism that
would replace the repeated dispatch.

This is adjacent to Agents 69 and 71 but distinct: 69 flags specific
"sqlite" / "postgres" leaks, 71 flags isinstance and concrete-class hints,
this one flags the pattern of repeated dispatch.

Severity: medium.

```

**Agent 140: ambient-parameter-threading** (sonnet)
File: `_audit/latest/findings/140-ambient-parameter-threading.md`

```text
Find parameters threaded through long call chains (5 or more functions)
that should live in a contextvar instead. Common offenders:
- actor (current user / acting principal)
- request_id, correlation_id, trace_id
- tenant_id
- current_user, session
- locale (when not the explicit subject of the function)

For each candidate, count how many functions accept and forward it WITHOUT
otherwise using it. High counts indicate a missing context layer.

Output: parameter name, longest threading chain found (count of functions),
representative call path.

Severity: medium.

```

**Agent 141: repeated-normalization-parsing** (sonnet)
File: `_audit/latest/findings/141-repeated-normalization-parsing.md`

```text
Find the same data transform implemented in multiple places. Each cluster
of 3 or more duplicates suggests a missing parser / normalizer module.

Patterns to look for:
- Timestamp parsing / formatting (ISO 8601, RFC 3339, custom formats)
- ID normalization (case, trim, strip prefix/suffix)
- Path canonicalization
- URL parsing (especially extracting query params, normalizing trailing
  slashes)
- Currency formatting (despite the regional-defaults rule, formatters may
  still drift)
- Locale resolution

For each cluster, list the duplicate implementations and propose a single
home.

Severity: medium.

```

**Agent 142: scattered-config-access** (sonnet)
File: `_audit/latest/findings/142-scattered-config-access.md`

```text
Find os.environ[...] / os.getenv(...) / direct settings.X reads scattered
through business logic instead of injected at the boundary. Per the
project's settings-service pattern, business code should accept config via
constructor injection or a settings facade, not reach out to globals.

Group by which module reads which env var or setting. Flag:
- Same env var read in 3 or more places (should be read once and injected)
- Settings reads deep in business logic (should be read at startup and
  injected)
- Conditional config reads (if env var is set then ... else ...) in business
  code (should be resolved at config-load time)

Severity: medium.

```

**Agent 143: utility-file-bloat** (sonnet)
File: `_audit/latest/findings/143-utility-file-bloat.md`

```text
Find utils.py / helpers.py / misc.py / common.ts / utils.ts files that
have grown beyond their stated purpose.

Signals:
- File over 500 lines
- 5 or more unrelated concerns under one roof
- No docstring describing the file's scope, or scope description that
  doesn't match contents
- Name mismatch (file called string_utils.py but contains date parsing)

For each bloated file, propose splits by concern.

Severity: low for moderate bloat, medium for files exceeding 800 lines
mixing 5 or more topics.

```

**Agent 144: layer-violations** (sonnet)
File: `_audit/latest/findings/144-layer-violations.md`

```text
Find architectural layer violations indicating a structural problem.

Specific violations to look for:
- API controllers importing from persistence/ directly. Agent 68 catches
  state writes via repository methods; this catches reads, type imports,
  and any other direct reach.
- Service layer importing controller types or HTTP-response types
- Domain models importing persistence-specific types (e.g. SQLAlchemy
  rows, raw connection objects)
- Engine code importing from web frontend types or CLI flags
- Tools importing from API controllers
- web/src/ importing from cli/ or vice versa

Each violation suggests the layer boundary is incorrectly drawn or being
eroded. For each, propose: which layer the symbol belongs in, or what
abstraction would let the import go away.

Severity: medium.

```

**Agent 145: abstraction-on-wrong-axis** (opus)
File: `_audit/latest/findings/145-abstraction-on-wrong-axis.md`

```text
The deepest architectural smell: an abstraction is parameterized over the
wrong dimension.

Signals to look for:
- A Protocol with N implementations where every implementation differs
  only in 1 or 2 trivial ways while another axis (caller behavior, return
  shape, error semantics) varies wildly across the codebase but is NOT
  parameterized.
- A factory that always picks the same concrete implementation in every
  observed call site (the abstraction is dead -- no real choice being
  made).
- Generic <T> parameters never instantiated with more than one type in
  practice.
- "Strategy pattern" implementations that only differ in a single config
  value (would be cleaner as a parameter than a class hierarchy).
- Two parallel hierarchies where one should be composed inside the other.
- A protocol with overlapping responsibilities that would be cleaner as
  two narrower protocols.

Read source carefully -- this is hard to spot mechanically. For each
suspected wrong-axis abstraction, explain what the right axis would be
and what migration would look like.

Severity: medium for suspected wrong-axis, high for fully dead
abstractions (factory always picks the same impl).

```

**Agent 146: configuration-soup** (sonnet)
File: `_audit/latest/findings/146-configuration-soup.md`

```text
Find values configurable through 3 or more different surfaces
simultaneously: env var + setting + ConfigDict field + CLI flag +
constructor parameter for the same logical setting. Each redundant
surface multiplies precedence rules and confuses operators about what
overrides what.

For each cluster, list:
- The logical setting
- All surfaces it's configurable through (and where each is read)
- The precedence currently in effect
- A proposal: which single surface should remain canonical

Severity: medium.

```

**Agent 147: error-mapping-inconsistency** (sonnet)
File: `_audit/latest/findings/147-error-mapping-inconsistency.md`

```text
Agent 34 covers error-handling consistency broadly. This agent specifically
maps each domain exception to its HTTP-response transformation across all
controllers.

For each custom exception class in src/synthorg/, find every controller
that catches it and how it converts to an HTTP response. Flag exceptions
converted to:
- Different status codes in different controllers (e.g. 400 in one place,
  422 in another, 409 elsewhere)
- Different response body shapes (RFC 9457 vs ad-hoc dict vs string)
- Different error type fields

This indicates missing error-handler middleware. Propose a single mapping
from each exception class to its canonical HTTP response.

Severity: medium.

```

**Agent 148: protocol-cardinality-overabstraction** (sonnet)
File: `_audit/latest/findings/148-protocol-cardinality-overabstraction.md`

```text
Agent 11 flags protocols with 0 implementations (dead). This agent flags
protocols with exactly 1 implementation that have been around for 3 or
more months and show no sign of gaining a sibling. These are premature
abstractions per YAGNI -- the protocol adds indirection without enabling
polymorphism.

For each Protocol class in src/synthorg/:
- Count concrete implementations
- If exactly 1, check git blame on the protocol file to determine age
- If older than 3 months and still single-impl, flag with a recommendation
  to either inline the protocol into the impl or remove the indirection

Cross-reference CLAUDE.md's pluggable-subsystems rule (which mandates
"ship safe defaults" but doesn't mandate every concept be a protocol).

Severity: low.

```

**Agent 149: mixed-async-sync-migration** (sonnet)
File: `_audit/latest/findings/149-mixed-async-sync-migration.md`

```text
Find modules where the same domain concept exposes both sync and async
APIs, signaling an incomplete async migration.

Examples to look for:
- repo.find() AND repo.afind() / repo.find_async()
- Service AND AsyncService variants
- Both blocking and async-aware versions of the same helper (load_config /
  aload_config)

Per the project's async-first rule, finish the migration. Group findings
by domain. List both surfaces and their callers. Propose which should be
the canonical version.

Severity: medium.

```

**Agent 150: stringly-typed-boundaries** (sonnet)
File: `_audit/latest/findings/150-stringly-typed-boundaries.md`

```text
Find module boundaries where typed domain objects exist but raw
dict[str, Any] / dict[str, str] / JSON strings cross the boundary anyway.

Common offenders:
- Tool argument passing (BaseTool.execute(arguments: dict))
- A2A messages
- MCP responses (envelope payloads as dict)
- NATS message bodies
- Telemetry events
- Audit chain entries

For each offender, identify what typed model SHOULD sit at the boundary
and which callers would need migration. Note when a typed model already
exists but isn't enforced (the worse case -- the abstraction exists,
just not used).

Severity: medium.

```

### Wave 29: Public-Facing Truth Enforcement (2 agents)

**Agent 151: docs-numeric-claims-enumeration** (sonnet)
File: `_audit/latest/findings/151-docs-numeric-claims-enumeration.md`

```text
Enumerate EVERY numeric or quantitative claim in EVERY page under docs/
(not just README + landing). Walk the mkdocs nav from mkdocs.yml; for
each page in nav, scan for:
- Test counts ("13k unit tests", "X tests")
- File / line / module counts
- Agent / tool / provider / model counts
- Page / feature counts ("20+ design pages")
- Version numbers and release dates
- Performance numbers ("3x faster", "Ns latency")
- "Since vX.Y", "as of <date>", "introduced in <version>" claims
- Any number adjacent to "+" ("100+", "10k+")

For each claim, verify against live source.

## Evidence Requirement
You MUST emit Bash output for every numeric/temporal claim you verify.
Do not assert "verified" without a corresponding Bash result. Examples:
- Test count: paste output of `uv run python -m pytest tests/ --collect-only -q | tail -1`
- Release list: paste output of `gh release list --limit 10`
- Subagent file count (counts files in `.claude/agents/`, the on-disk dev subagents -- distinct from the codebase-audit skill's inline agent prompts): paste `ls .claude/agents | wc -l`
- File count: paste `find <path> -name "*.py" | wc -l`
- Tool count: paste a grep against tools/registry.py

Findings WITHOUT evidence are inadmissible. Validation phase rejects
evidence-free numeric findings with severity downgrade to info.

## Severity Calibration
- Every stale public-facing number is severity MEDIUM minimum.
- HIGH if the page is reachable from synthorg.io top nav (homepage,
  roadmap, comparison, vision, architecture, decisions, getting-started).
- This is mandatory because Phase 5 triage prioritizes high+critical
  first; stale numbers hidden at "low" or "medium" are exactly how the
  "13k unit tests" claim survived two prior audits.

```

**Agent 152: website-published-pages-audit** (sonnet)
File: `_audit/latest/findings/152-website-published-pages-audit.md`

```text
The audit checks docs/ source. This agent ALSO checks the rendered live
site to catch claims that survive in production despite source updates
or that appear on orphaned pages no longer in source.

Use WebFetch on these synthorg.io URLs (and any others you discover via
the homepage navigation):
- https://synthorg.io/
- https://synthorg.io/docs/
- https://synthorg.io/docs/roadmap/
- https://synthorg.io/docs/comparison/
- https://synthorg.io/docs/architecture/
- https://synthorg.io/docs/decisions/
- https://synthorg.io/docs/getting-started/
- https://synthorg.io/docs/future-vision/ (if exists)
- https://synthorg.io/blog/ (if exists)

For each fetched page:
- Extract every numeric / temporal / version claim (same list as agent 151)
- Verify each against current source via Bash commands (with evidence)
- Verify the page exists in the current mkdocs.yml nav (orphaned
  published pages should be flagged or removed)
- Flag claims that contradict the source (live page says X, source page
  in docs/ says Y -- means a deploy is missing or rendering is broken)

## Evidence Requirement
Same as agent 151: you MUST paste Bash output for every numerical claim
you verify against live source.

## Severity Calibration
HIGH for any stale claim on a public synthorg.io page. Public-facing
inaccuracies are an investor / user trust issue.

```

### Wave 30: Implicit Convention Discovery (1 agent)

**Agent 153: implicit-convention-finder** (sonnet)
File: `_audit/latest/findings/153-implicit-convention-finder.md`

```text
Find patterns repeated 5 or more times across the codebase that are NOT
documented in CLAUDE.md, web/CLAUDE.md, cli/CLAUDE.md, or any
docs/design/*.md page. These are conventions that exist in practice but
live only in tribal knowledge.

Examples to look for:
- Specific naming patterns for service-layer methods (e.g. all repos use
  find_by_* not get_by_*; all services use load_/save_/delete_)
- Consistent error-wrapping patterns not in the conventions doc
- Implicit ordering rules (always validate before persist, always
  authenticate before authorize, etc.)
- Function signature patterns (e.g. all controllers return Response not
  dict; all background workers take (ctx, payload))
- Test fixture conventions (every integration test uses fixture X)
- Import-order conventions
- File-naming conventions (handlers/, services/, repositories/ all
  pluralized vs singular)

For each discovered convention:
- A short rule statement
- Sample of 5+ files that follow it (file:line)
- Where it should be documented (CLAUDE.md section, design page, or new
  reference file under docs/reference/)

Do not flag patterns that are merely common -- look for ones that are
universally followed AND would surprise a new contributor who hadn't
read the codebase.

Severity: low (informational, but actionable for documentation
completeness).

```

### Wave 31: Comment Quality (2 agents)

**Agent 154: reviewer-citation-rot** (sonnet)
File: `_audit/latest/findings/154-reviewer-citation-rot.md`

```text
Find code comments, docstrings, test docstrings, log strings, and
commit-message bodies (where visible in source) that violate the
"comments explain WHY only, never origin/review/issue context"
rule. The canonical statement of the rule lives in CLAUDE.md
"Code Conventions" and the user-memory file
`feedback_no_review_origin_in_code.md`.

Forbidden patterns to flag:

1. **Reviewer-origin citations** anywhere in src/, tests/, docstrings,
   or comments:
   - `pre-PR review #N` / `Pre-PR review finding (#N, ...)`
   - `CodeRabbit at <file>:<line>` / `(#NNNN, CodeRabbit ...)`
   - `Round-N review id NNNN` / `flagged on round N` /
     `re-flagged on round N`
   - `(CodeRabbit, YYYY-MM-DD)` (date-stamped reviewer attribution)
   - `(CodeRabbit minor at ...)` / `(CodeRabbit critical at ...)`
   - any `<reviewer> at <file>:<line>` shape

2. **In-code issue / PR / audit-run back-references**:
   - `(#NNNN)` standalone (4-digit GitHub issue numbers)
   - `(#NNNN, CodeRabbit ...)` composite
   - `(GH-NNNN)` / `(see PR #NNNN)` / `(fixes #NNNN)`
   - `as part of #NNNN` / `closes #NNNN` / `this commit closes #N`
   - `(#1599)`, `(#1682)`, etc. -- any standalone issue tag in
     a comment
   - **Identifier names** that bake an issue number into a constant,
     test name, helper, or fixture: `_AUDIT_NNNN_*`,
     `test_audit_NNNN`, `_ISSUE_NNNN_DEFAULTS`, etc. -- these go
     stale instantly when the issue closes and they leak the
     change-management trail into committed code.
   - **Audit-run callouts** in comments or docstrings:
     `Audit #NNNN`, `2026-04-30 audit`, `audit run YYYY-MM-DD`,
     `from the codebase audit`. Audit dates belong in
     `_audit/runs/` paths, never in `src/` or `tests/`.

3. **Cryptic internal-taxonomy shorthand in src/ and tests/**:
   - Naked `SEC-1` (without surrounding rationale that explains
     what SEC-1 means) anywhere under `src/synthorg/` or `tests/`
   - `SEC-1 / audit finding NN` in src/tests
   - `(SEC-1)` parenthetical
   - Tags like SEC-1 are fine in `docs/design/` and
     `docs/reference/` (their canonical home); flag only when they
     appear naked in `src/` or `tests/` where the reader cannot
     follow the link.

4. **Round / iteration narrative**:
   - `round-N review surfaced this`
   - `after round N`
   - `the round-N CodeRabbit re-flag`
   - `this iteration of the review`

For each violation:
- Quote the offending comment / docstring with file:line.
- State which forbidden pattern bucket it falls into (1-4 above).
- Propose a rewrite that explains the technical WHY without the
  citation / back-reference / taxonomy shorthand. The rewrite
  should be self-contained: the next reader must be able to verify
  the rationale against the code without following any link.
- If the WHY is genuinely already obvious from the code (e.g. the
  comment was *only* a reviewer attribution with no technical
  content), propose deletion.

DO NOT flag:
- Workflow / tooling files: `.claude/skills/*`, `.opencode/commands/*`,
  `.claude/hookify.*.md`, `.github/workflows/*` if the reference
  describes what the workflow protects against (e.g. CodeRabbit
  cost in the push-throttle script). The rule targets stale
  forensic narrative in code comments, not functional descriptions
  of external systems.
- `CLAUDE.md`, `docs/design/`, `docs/reference/`: these are the
  canonical homes for SEC-1 / SEC-N taxonomy.
- Auto-generated files (`CHANGELOG.md`, `release-please-manifest.json`).
- Bug-tracker URLs to *third-party* projects (upstream bug
  workarounds), which the rule explicitly preserves.
- Stable URLs to public RFCs, OWASP findings, etc.

Severity scale:
- **medium**: any reviewer-origin citation in `src/`, `tests/`, or
  any module docstring -- these go stale instantly when the review
  is resolved or the line number shifts, and they leak the codebase's
  internal review process into long-lived artifacts.
- **medium**: in-code issue back-references in `src/` / `tests/`.
  GitHub issue links belong in PR bodies, not in code.
- **low**: naked `SEC-N` in `src/` / `tests/` where a reader cannot
  decode the tag standing alone. Suggest spelling out the rationale.
- **info**: round / iteration narrative ("round-3 fix").

This audit is the long-term backstop for the
`feedback_no_review_origin_in_code.md` rule: a one-shot scrub
addresses the existing tree, but a future reviewer or contributor
adding a fresh `pre-PR review #N` comment will reintroduce the
pattern. Surface every new occurrence so the cleanup commit is the
last cleanup commit.

```

**Agent 155: migration-framing-rot** (sonnet)
File: `_audit/latest/findings/155-migration-framing-rot.md`

```text
Find code comments, docstrings, README sections, and commit-message
bodies (where visible) that frame current code in terms of how it
got there rather than what it does. The canonical statement of the
rule lives in the user-memory file `feedback_no_migration_framing.md`
and the "Comments explain WHY only" bullet in CLAUDE.md
"Code Conventions".

Forbidden phrasings to flag:

1. **Port / rebrand framing**:
   - `ported from <other-project>`
   - `migrated from <old-name>`
   - `renamed from <old-symbol>`
   - `previously called <old-name>`
   - `(was: <old-form>)`
   - `replaces the old <X>` / `replaces the prior <X>` (when used
     to describe a historical migration rather than a current
     constraint)

2. **Round / phase / wave framing**:
   - `moved here in round N`
   - `landed in phase 2`
   - `Phase 2 typed-args refactor`
   - `the wave-N rewrite`
   - `as part of round 7`

3. **Issue-driven implementation framing**:
   - `implemented as part of #NNNN`
   - `added in PR #NNNN`
   - `delivered by issue #NNNN`
   - `the original commit replaced X with Y` (when X no longer
     exists; the comment is documenting an absent shape)

4. **"We used to..." narratives**:
   - `previously we did X`
   - `originally this was X, now it is Y`
   - `the old code did X` (when no old code remains)
   - `the legacy <thing>` (when no non-legacy contrast exists)

For each violation:
- Quote the offending text with file:line.
- State which phrasing bucket (1-4) it falls into.
- Propose a rewrite that describes only the *current* shape and
  the *technical* reason it is shaped that way. If the comment is
  pure historical narrative with no current-state content, propose
  deletion.

DO NOT flag:
- `re-exported from <module>` -- this is a current-state factual
  description of where a symbol lives, not migration framing.
- `extracted from <function>` when the comment is naming a code
  organization choice ("extracted to keep the parent under the
  complexity ceiling") rather than narrating history.
- `inherits from <BaseClass>` / `subclasses <X>` -- structural
  facts, not migration narrative.
- Stable upstream-bug workaround comments that reference the
  bug's resolution status (e.g. "remove once
  github.com/foo/bar#123 is fixed").
- Commit-message bodies in `git log` output (out of scope; the
  rule targets long-lived artifacts).
- `CLAUDE.md`, `docs/design/`, and `docs/reference/` migration
  guides (e.g. `persistence-migrations.md`) where migration
  framing is the entire subject.
- Revision files under `src/synthorg/persistence/*/revisions/`
  (their purpose IS to record schema migrations).

Severity scale:
- **medium**: port / rebrand / "we used to" framing in `src/` or
  `tests/`. These rot the moment the migration completes and
  confuse new readers about whether the old form still exists
  somewhere.
- **medium**: round / phase / wave framing -- these tie the code's
  documentation to internal process language that loses meaning
  outside the original review window.
- **low**: issue-driven implementation framing in code (`(#NNNN)`,
  `as part of #NNNN`). The PR body is the canonical home for
  origin links.

Output should be deduplicated: when the same migration narrative
appears across many files (e.g. a docstring fragment copy-pasted
into ten test files), report it once with the full file:line
list rather than ten separate findings.

This audit is the long-term backstop for the
`feedback_no_migration_framing.md` rule. Each finding either becomes
a deletion or a "describe the current shape" rewrite.

```

### Retired Agents

These concerns are already enforced by hooks, linters, or external tooling today instead of an audit agent. Do NOT launch these agents; check the "Now enforced by" column if a related concern needs attention.

| Retired agent | Now enforced by |
|---|---|
| hardcoded-secrets | `gitleaks` pre-commit + CI |
| hardcoded-display-values | `scripts/check_web_design_system.py` + `scripts/check_backend_regional_defaults.py` PostToolUse hooks |
| design-token-violations | `scripts/check_web_design_system.py` PostToolUse hook |
| go-error-handling | `golangci-lint` (errcheck, wrapcheck, errorlint) pre-commit + CI |
| go-resource-leaks | `golangci-lint` + `go vet` pre-commit + CI |
| changelog-release-notes | `release-please` (automated) |
| changelog-releases-parity | `release-please` (automated) |
| tests-without-assertions (slot 98) | Retired 2026-04-20. Regex-based detection cannot distinguish helper-function assertions, `pytest.raises`/guard-raises patterns, or Pydantic validation-raises from truly empty tests. Produced ~93% false positives in validation (14/15 sampled findings were valid tests). Rely on coverage + mutation testing for vacuous-test detection instead. |

### Planned Retirements

These concerns have a planned hook, linter, or external-tool replacement, but the enforcement is not fully wired yet. Keep launching these audit agents until the replacement is enabled; once the TODO ships, move the row up to the Retired Agents table.

| Agent to keep active for now | Planned enforcement |
|---|---|
| wrong-logger-pattern | Custom ruff rule or pre-commit regex hook (TODO: add) |
| unstructured-logging | Custom ruff rule (TODO: add) |
| unused-web-components | `knip` in CI (TODO: wire) |
| unused-web-hooks | `knip` in CI (TODO: wire) |
| unused-web-utils | `knip` / `ts-prune` in CI (TODO: wire) |
| vendor-name-leaks | Extend `scripts/check_forbidden_literals.py` (TODO) |
| long-functions | Ruff `C901` / `PLR0915` + eslint `max-lines-per-function` (TODO: enable) |
| long-files | Ruff + eslint `max-lines` (TODO: enable) |
| future-annotations-leak | Ruff `FA100` / `FA102` (TODO: enable) |
| docs-links-refs | `lychee` in CI (TODO: wire) |

---

## Phase 3: Validate Findings (sonnet agents)

**Required on every run. Every single finding gets validated. There is no severity threshold, no scope cap, no opt-out, no "spot-check" shortcut.** Validation runs on all findings (critical, high, medium, low, AND info) uniformly. This skill is for huge audits; the false-positive filter must apply to every finding so INDEX.md is not contaminated by un-validated noise. If an audit agent emits 400 findings of one type, all 400 get validated -- not a sample, not a top-N, not a severity-gated subset.

After all launched audit agents complete, launch validation agents to verify findings. The number of audit agents depends on scope (155 for `full`, fewer for scoped runs).

### Process

1. Read all finding files present in `_audit/latest/findings/`
2. Collect ALL findings (every severity, including low and info) into a validation queue. Zero-finding files have no rows to add and are skipped at the queue stage; everything else is queued
3. Order batches by severity descending (critical first) so the highest-impact verdicts land earliest. Order is for prioritization only -- every finding still gets validated regardless of severity
4. Cluster findings by audit-agent file when possible (a single validator can process all findings from one finding file in one batch, sharing context). If a single file has more than ~25 findings, split into multiple batches of ~12-25 findings each. Otherwise one validator per finding file is the default
5. Launch **sonnet** validation agents in parallel (`run_in_background: true`). Maintain the same rolling-pool-of-10 cadence used in Phase 2 so no slot sits idle

### Batching strategy (concrete)

Run this Bash to produce a sorted list of (count, filename):

```bash
for f in _audit/latest/findings/*.md; do count=$(grep -cE "^### (critical|high|medium|low|info)" "$f"); echo "$count $(basename "$f")"; done | sort -rn
```

Then build batches:

- **Heavy files (>25 findings)**: dedicate 1 batch per ~25-finding chunk. Example: a 107-finding file splits into 4 batches of 25 + 25 + 25 + 32.
- **Mid files (10-25 findings)**: 1-2 files per batch (target ~20-30 findings).
- **Small files (1-9 findings)**: pack 5-8 files per batch, total findings per batch ~20-30.
- **Zero-finding files**: skip entirely.

Target: each validator processes ~20-30 findings. Going much higher risks the validator running out of focus; going much lower wastes overhead.

For a typical full run (~400 findings across ~100 non-empty files), expect ~17-25 validation batches. Launch them with the same rolling-pool-of-10 cadence as Phase 2.

### Validation Agent Prompt

```text
You are validating audit findings by reading the ACTUAL SOURCE CODE.

For each finding below, do:
1. Read the file at the reported line number
2. Quote the actual code (2-5 lines)
3. Check if the issue is real or a false positive
4. Check if it's intentional (read surrounding comments, docstrings)
5. Give a verdict: CONFIRMED, FALSE_POSITIVE, or INTENTIONAL

Write results to: _audit/latest/findings/validate-batch-{N}.md

Format per finding:
### [original-file]:[line] -- [CONFIRMED|FALSE_POSITIVE|INTENTIONAL]
**Original**: [description from audit agent]
**Actual code**: [quoted code]
**Verdict**: [explanation]
---

Findings to validate:
{BATCH_OF_FINDINGS}
```

### After Validation

1. Read all `validate-batch-*.md` files
2. Delete FALSE_POSITIVE findings entirely from the audit files (edit in place)
3. Mark INTENTIONAL findings as excluded (keep in file but prefix with `[INTENTIONAL]`)
4. Report: "Validated N findings. Removed M false positives (X%)."

---

## Phase 4: Build INDEX.md

After validation, read all finding files and build `_audit/latest/INDEX.md`.

**MANDATORY for the agent that builds INDEX.md**: enumerate the actual files in `_audit/latest/findings/` via `Glob` or `Bash ls _audit/latest/findings/`. Use the REAL filenames. The 2026-05-03 run produced an INDEX with hallucinated filenames (`14-web-store-architecture-drift.md`, `19-benchmark-regression.md`, etc.) that did not match the agent roster. Before writing INDEX, list `_audit/latest/findings/*.md` and copy the names verbatim. Each entry's finding count must come from actually grepping the file (`grep -cE "^### (critical|high|medium|low|info)" <path>`), not from an assumed wave grouping.

Use this template:

```markdown
# Codebase Audit Index

**Date**: {date}
**Scope**: {scope}
**Agents**: {agents_launched}
**Total findings**: {count}
**False positives removed**: {count} ({percent}%)

## By Severity

| Severity | Count |
|----------|-------|
| critical | N |
| high | N |
| medium | N |
| low | N |
| info | N |

## By Wave

| Wave | Findings | Top Issue (highest severity finding) |
|------|----------|--------------------------------------|
| 1. Observability | N | ... |
| 2. Wiring | N | ... |
| ... | ... | ... |

## Top 20 Critical + High Findings

| # | Severity | File:Line | Issue | Agent |
|---|----------|-----------|-------|-------|
| 1 | critical | ... | ... | ... |
| ... | ... | ... | ... | ... |

## Zero-Finding Categories

These agents found no issues. Review the agent prompt to understand what was
checked -- this may indicate code quality in that area, or the search pattern
may not match the codebase's conventions:
- ...

## Finding Files

- [01-missing-logger.md](findings/01-missing-logger.md) (N findings)
- [02-wrong-logger-pattern.md](findings/02-wrong-logger-pattern.md) (N findings)
- ...
```

---

## Phase 5: Triage with User

Present INDEX.md to the user with a one-paragraph summary, then **always produce the deduped issue list (Phase 5 DEFAULT OUTPUT) before asking what to do next**. Without that list, the user is staring at 300+ raw findings and cannot make a decision.

### Phase 5 DEFAULT: Deduped Issue List (MANDATORY before triage prompt)

Group every confirmed finding by its **issue class** (= the agent-finding-file source -- e.g. all 107 raw findings from `09-unwired-settings.md` collapse into ONE row "Unwired settings"). Write `_audit/latest/ISSUES.md`:

```markdown
# Deduped Issue List

One row per issue class. Multi-file patterns collapse into a single planning unit.

## Critical

| # | Issue class | Confirmed | FP removed | Top affected paths | Recommended action |
|---|-------------|-----------|------------|--------------------|---------------------|
| 1 | <agent name> | <N> | <M> | <2-3 representative paths> | <single-PR / batch-PR / RFC / track-only> |

## High
...

## Medium
...

## Low
...

## Architectural Reworks (from REWORK.md)

| # | Recommendation | Effort | Related agents | Recommended action |
```

Build the list by:

1. For each `_audit/latest/findings/<NN>-<name>.md`, count CONFIRMED findings (subtract FALSE_POSITIVE / INTENTIONAL from validate-batch-*.md verdicts).
2. Skip files where `confirmed = 0`.
3. Sort within each severity bucket by confirmed-count descending.
4. For "Top affected paths" pick the 2-3 paths whose finding cluster appears most often.
5. For "Recommended action" pick:
   - `single-PR` if confirmed <= 5 and one file pattern
   - `batch-PR` if confirmed > 5 with shared root cause
   - `RFC` if listed in REWORK.md
   - `track-only` for low / info / large-and-unclear
   - `dismiss` if the cluster is dominated by INTENTIONAL verdicts and the few CONFIRMED entries are noise.

Print the table inline to the user (above the AskUserQuestion call), AND save `_audit/latest/ISSUES.md` to disk. The inline render is what the user actually triages from.

### Triage prompt

Use AskUserQuestion with these options (in order):

1. **Report-only** (default if user already saw INDEX once): all artifacts written to disk; user takes it from there.
2. **Walk top-20 critical with me**: per-item triage, AskUserQuestion per row.
3. **Create GitHub issues for top N issue classes**: one issue per row in the deduped list above a chosen severity floor.
4. **Open RFC issues for REWORK.md only**: skip per-finding work, file architectural recommendations only.

If `--report-only` flag was passed at command time, skip the AskUserQuestion entirely and finalise as report-only.

---

## All-Round Improvements

These are structural improvements to the audit lifecycle, not new agents. They apply to every run.

### Phase 0 setup: run-history layout

Phase 0 setup uses this run-history layout:

```bash
RUN_DIR="_audit/runs/$(date +%Y-%m-%d-%H%M%S)"
mkdir -p "$RUN_DIR/findings"
ln -sfn "runs/$(basename "$RUN_DIR")" _audit/latest
```

The timestamp uses second-level precision (`%H%M%S`) so back-to-back runs in the same minute do not collide and overwrite each other's findings. On Windows, the OpenCode adapter first attempts `New-Item -ItemType SymbolicLink` (requires Developer Mode or admin); on failure it falls back to `New-Item -ItemType Junction`, which needs no special privileges. Either link type makes `_audit/latest` resolve as a directory, so downstream writes to `_audit/latest/findings/<file>` succeed regardless of which one was created. All findings, INDEX, REWORK, DIFF, and JSON live in the run-specific directory. `_audit/latest` always points at the most recent run. Older runs accumulate; never delete `_audit/runs/*`.

Verify `_audit/` is in `.gitignore` (existing behavior). The `_audit/.ignore.yaml` ignore list (see below) is also gitignored by virtue of the parent.

### Persistent ignore list

`_audit/.ignore.yaml` (gitignored):

```yaml
- finding: "src/synthorg/foo.py:42"
  agent: 21
  reason: "Intentional silent except -- cleanup path, see docstring"
  added: 2026-04-25
- finding: "docs/roadmap.md:#numeric-claim:13k"
  agent: 75
  reason: "Will fix in next docs sweep"
  added: 2026-04-25
  expires: 2026-05-25
```

When building INDEX (Phase 4), read `.ignore.yaml`. Findings with a matching `finding`/`agent` pair are filtered out before counting and triage. Add a footer: "N findings suppressed by `.ignore.yaml`."

Phase 5 triage gets a new option: "Ignore permanently" appends to `.ignore.yaml` with reason and optional expiry.

### Phase 3 update: validate every finding

Validation runs over **every finding at every severity** (critical, high, medium, low, info). There is no opt-out and no severity threshold. The previous "skip lower severities to save validation work" carve-out is removed: this skill is for HUGE audits, the false-positive filter must apply uniformly, and public-facing drift (the "13k tests" precedent) survived past audits precisely because lower-severity findings were untriaged. Phase 3.5 also promotes public-facing findings up one severity, so an unvalidated `low` becomes an unvalidated `medium` in INDEX.md, exactly the noise this validation phase exists to remove.

### Phase 3 update: evidence requirement enforcement

When validation reads a finding from agent 73, 75, 132, 151, or 152, it MUST verify that the finding includes Bash output proving the numeric claim. Findings without evidence are downgraded to severity `info` and excluded from triage.

### Phase 3.5: Synthesis & Quality Pass (NEW)

Runs after validation, before INDEX.md. Launches one **sonnet** synthesis agent that:

1. Reads every finding file in the current run.
2. Clusters findings by `file:line` (or by file alone when line numbers don't match across agents). Two agents reporting the same line collapse into one consolidated entry showing both perspectives. Single-agent findings pass through unchanged. Writes to `_audit/latest/findings-clustered.md`.
3. Applies severity normalization across the clustered findings using a single rubric:
   - `critical`: active security hole, data corruption risk, production outage potential
   - `high`: broken behavior, missing safety check, public-facing factual error
   - `medium`: convention violation, dead code, missing wiring, internal inconsistency
   - `low`: style, minor docs gaps, cosmetic
   - `info`: TODO, deferred work, opportunity
4. Adds an `effort` field to each finding: `trivial` / `small` / `medium` / `large`.
5. **Public-facing severity bump**: any finding whose file is in the public-facing set (README.md, docs/ tree reachable from mkdocs.yml nav, comparison page output) gets severity bumped one level. Reason: stale or wrong claims on synthorg.io are visible to investors and search engines.

Then a dedicated **opus** Wave 28 meta-synthesis agent reads all 15 Wave 28 finding files plus the clustered output and writes `_audit/latest/REWORK.md`:

```markdown
# Recommended Reworks

## Top N Architectural Recommendations

### 1. Centralize timestamp formatting (effort: medium)

**Pattern**: 7 different ISO-8601 formatters across the codebase
(agents 137, 141 both flag this from different angles).

**Affected files**: ...

**Proposal**: introduce `synthorg.core.formatters.iso_timestamp()` and migrate the 7 sites.

**Migration path**:
1. Land the new helper.
2. Migrate sites in alphabetical order over 2-3 PRs.
3. Add a ruff custom rule to forbid new formatters.

**Recommended next action**: open RFC issue with this proposal.

---
```

The meta-synthesis agent groups Wave 28 findings by underlying root cause (not by which agent flagged them), assigns effort, proposes migration paths, and ranks by impact-to-effort ratio. INDEX.md links to REWORK.md from its Architectural Recommendations section.

### Phase 4 update: INDEX.md Architectural Recommendations + JSON export

Append a new section to INDEX.md, populated from Wave 28 findings via REWORK.md:

```markdown
## Architectural Recommendations

These are systemic patterns -- not single-line bugs. Each is a candidate
for centralization, rework, or design change rather than a one-off fix.

See [REWORK.md](REWORK.md) for the full ranked list with proposals.

### Top 5

1. [Pattern name] -- effort: small/medium/large
   ...
```

Also write `_audit/latest/findings.json` (machine-readable):

```json
{
  "run_id": "<run-id-timestamp>",
  "scope": "full",
  "agents_launched": 155,
  "validation": {"validated": 412, "false_positives": 67, "intentional": 12},
  "findings": [
    {
      "id": "75:docs/roadmap.md:42",
      "agent_id": 75,
      "file": "docs/roadmap.md",
      "line": 42,
      "severity_raw": "medium",
      "severity_normalized": "high",
      "public_facing": true,
      "effort": "small",
      "validated": "confirmed",
      "description": "Stale test count claim: doc says 13k, actual is X",
      "evidence": "...bash output..."
    }
  ],
  "clusters": [...],
  "rework_recommendations": [...]
}
```

Enables external tooling: GitHub Actions integration, dashboards, history analysis.

### Phase 5 update: walk Architectural Recommendations separately

Phase 5 triage gets one extra step: walk REWORK.md with the user. Each recommendation gets a per-item triage option: open RFC issue / open implementation issue / skip (judged YAGNI). This is separate from the per-line finding triage so structural reworks don't compete with surface bugs for attention.

Plus the "Ignore permanently" option from the persistent ignore list above.

### Phase 6: Diff-since-last-run (NEW)

Runs after Phase 5. Generates `_audit/latest/DIFF.md`:

- For each finding in the latest run, check whether its `finding-id` (file:line:agent) appeared in the previous run.
- New findings: appeared this run, not in previous.
- Disappeared findings: appeared in previous, not this. Likely fixed.
- Persistent findings: appeared in both. Aging counter increments.

```markdown
# Diff: <date> vs. <prev-date>

## New findings (12)
- ...

## Disappeared (likely fixed) (8)
- ...

## Persistent (47)
| Age | Severity | File:Line | Issue |
| 3 runs | high | ... | ... |
```

Persistent-finding age is a quality signal: critical findings that survive 3+ runs deserve escalation to "blocker" status and proactive user attention.

### Per-agent FP metrics tracking

`_audit/metrics/agent-quality.json` (created on first run, gitignored):

```json
{
  "agent_id": "75",
  "runs": [
    {
      "date": "2026-04-25",
      "findings": 12,
      "validated": 8,
      "false_positive": 3,
      "intentional": 1,
      "fp_rate": 0.25
    }
  ],
  "rolling_fp_rate_last_5": 0.22,
  "rolling_finding_count_last_5_avg": 10
}
```

Updated at end of each run from validation results. INDEX.md gets an "Agent Quality" section listing agents with rolling FP rate >30% (candidates for prompt revision) and agents with consistent zero findings (candidates for retirement or scope re-check).

### Agent prompt regression tests

Optional, best-effort. Each agent can have a golden-input test:
- `_audit/tests/<agent-id>/seed-input.md`: short snippet that contains the issue the agent should catch
- `_audit/tests/<agent-id>/expected-finding.md`: the finding the agent must produce

A new command `/codebase-audit self-test` runs each agent against its golden input and verifies it finds the seeded issue. Catches prompt rot.

Bootstrap: not all 155 agents need golden tests upfront. Start with the 25 agents most prone to prompt drift (highest FP rates per metrics above, or doing semantic analysis). Add more over time. Tests are best-effort, not blocking.

If an agent fails its self-test, INDEX.md "Self-Test Status" section flags it.

### Phase 7: Cleanup (NEW, MANDATORY)

Runs after triage / report-only output is finalised. Sweep agent-leaked scratch files from the working tree.

The 2026-05-03 run leaked at least 14 helper scripts to disk despite the agent-prompt rule against it (e.g. `find_missing_logging.py`, `find_missing_logging_filtered.py`, `parse_audit.py`, `validate_config_examples.py`, `scripts/audit_pydantic_models.py`, `scripts/audit_pydantic_models_v2.py`, `scripts/audit_pydantic_models_v3.py`, `scripts/audit_phase35_synthesis.py`, plus `c:\tmp\*.py` files). These triggered Pyright diagnostics in the main thread on every file write, polluted git status, and required user cleanup. The skill is responsible for cleaning up after its own agents.

Run this Bash sweep:

```bash
rm -f find_missing_logging.py find_missing_logging_filtered.py parse_audit.py validate_config_examples.py audit_diff.py audit_parity.py check_docs.py check_rate_limits.py circular_dep_analyzer.py check_protocols.py debug_scanner.py detailed_check.py final_audit.py find_unwired.py test_regex.py validate_configs.py verify_final.py verify_protocols.py || true
```

(`rm -f` is silent on missing paths, so no stderr redirect is needed; `|| true` keeps the chain from aborting on edge-case errors. Per Rule #11, the project's PreToolUse hook blocks `2>/dev/null` and other redirects unconditionally.)

Then list anything else suspicious. The find DOES include `scripts/` because the 2026-05-03 run leaked `scripts/audit_pydantic_models{,_v2,_v3}.py` and `scripts/audit_phase35_synthesis.py`; those need to surface for user prompt. Use the second-newest run (the one before this run) as the `-newer` reference:

```bash
prev_run=$(ls -1t _audit/runs/ | sed -n '2p') && [ -n "$prev_run" ] && find . -maxdepth 2 -name "*.py" -newer "_audit/runs/$prev_run/findings" -not -path "./src/*" -not -path "./tests/*" -not -path "./web/*" -not -path "./cli/*"
```

Notes:
- `ls -1t` lists runs newest-first by mtime; `sed -n '2p'` picks the second line (i.e. the run BEFORE the one we just created in Phase 0). On the very first run there is no previous run, so `prev_run` is empty and the `[ -n "$prev_run" ] &&` guard short-circuits the `find` -- which is correct, since the cleanup target is "files newer than the previous run", not "files newer than this run". On the first run the `git status` check at the bottom of this section is the only safety net, which is fine.
- The path is quoted (`"_audit/runs/$prev_run/findings"`) to defend against pathological `_audit/runs/` contents.

Show the user the list before deleting anything that's not on the known leak list. Files in `c:\tmp\`, `/tmp\`, or any path outside the project root: leave them; the OS will reap them. Files inside `scripts/`: prompt the user before removal -- those may be intentional helpers, not leakage.

If `git status` shows any new untracked `.py` file at project root or in `scripts/` that didn't exist before the audit run started, flag it as a candidate for removal.

The cleanup phase is REQUIRED on every run. Skipping it means the next run's diagnostic stream is contaminated by the previous run's leakage.

---

## Rules

1. **Every agent writes to `_audit/latest/findings/`** using the Write tool, not Bash
2. **Architecture brief in every prompt**: no blind agents
3. **Validation is required** for every finding at every severity on every run; no opt-out, no threshold
4. **Batch execution**: ~10 agents per batch, wait between batches
5. **Model selection**:
   - **Haiku**: pure pattern matching with low ambiguity (grep + filter, regex over fixed token sets, listing TODOs).
   - **Sonnet** (default): cross-file reasoning, judgment calls, semantic analysis, anything where false-positive cost matters.
   - **Opus**: reserved for the small set of agents requiring cross-document architectural synthesis. Permitted only on the agents listed below; do not use Opus for any other audit agent without explicit user approval.
   - **Opus-permitted agents**: 42 (design-spec-drift), 70 (pluggable-impl-coverage), 71 (abstraction-swap-readiness), 72 (dependency-inversion-violations), 81 (design-spec-contradictions), 92 (prompt-injection-defenses), 137 (centralization-opportunities), 145 (abstraction-on-wrong-axis), plus the Wave 28 meta-synthesis agent in Phase 3.5. Total: 9.
6. **Do NOT fix anything**: audit only, findings only
7. **Rerunnable**: never delete `_audit/runs/*`; always create a fresh `_audit/runs/<timestamp>/` and repoint `_audit/latest` at it
8. **Never use em-dashes** in any output files (project convention)
9. **Report progress** after each batch completes
10. **No scratch scripts**: agents may NOT write helper Python / shell scripts to disk anywhere outside `_audit/latest/findings/<their-finding-file>.md`. Use Grep / Glob / Read inline. The Phase 7 cleanup will sweep any leakage but the prevention rule lives at the agent level.
11. **No Bash redirects**: this project's PreToolUse hook blocks `>`, `>>`, `2>`, `&>` in any Bash call. Use `||` chains and let stderr surface. Use the Write/Edit tools for file creation.
12. **Verify FP signals end-of-run**: after Phase 6, if `_audit/metrics/agent-quality.json` shows any agent at >30% rolling FP rate, list the agent and its FP rate in the final summary so the user can prioritize prompt revision before the next run.

---

## Lessons from prior runs

Document specific issues observed in named runs so future runs avoid repeating them.

### 2026-05-03 run

- **Agent 09 (`unwired-settings`) had 38% FP rate** (39/107 overturned). Root cause: prompt only checked bridge config methods; missed `ConfigResolver.get_int/float/str` direct calls, composed-config methods (`get_coordination_config()`), and Pydantic config-model embedding. Fixed in this skill version.
- **14+ scratch Python scripts leaked to project root and `c:\tmp\`** -- `find_missing_logging.py`, `find_missing_logging_filtered.py`, `parse_audit.py`, `validate_config_examples.py`, `scripts/audit_pydantic_models{,_v2,_v3}.py`, `scripts/audit_phase35_synthesis.py`, `c:\tmp\check_rate_limits.py`, `c:\tmp\circular_dep_analyzer.py`, `c:\tmp\audit_parity.py`, `c:\tmp\audit_diff.py`, `c:\tmp\check_docs.py`. These triggered Pyright diagnostic floods in the main thread on every write. Fixed via Rule #10 + Phase 7 cleanup.
- **Bash redirects (`2>/dev/null`) blocked by PreToolUse hook** during Phase 0 setup. Fixed via Rule #11 + updated Phase 0 example.
- **INDEX.md hallucinated agent filenames** -- the Phase 4 agent invented entries like `14-web-store-architecture-drift.md`, `19-benchmark-regression.md` that never existed. Fixed via "MANDATORY: enumerate actual files" instruction in Phase 4.
- **Renovate Dependency Dashboard #1730** appeared in `gh issue list` injected into agent prompts. Per memory rule `feedback_open_issues_exclude_renovate.md`, exclude any Renovate-managed issue from the issue list.
- **Phase 6 DIFF agent only validated 31% of agents** because it ran in parallel with synthesis instead of after, and assumed batches were complete. Sequence Phase 6 strictly after Phase 3 validation finishes.
- **`_audit/latest` re-link collision** when re-running: `ln -sfn` alone fails on a pre-existing real directory or Windows Junction; `rm -f` alone refuses on a directory. The Phase 0 setup command now handles all three states (symlink, real directory / Junction, missing) via `if test -d _audit/latest && ! test -L _audit/latest; then rm -rf _audit/latest; else rm -f _audit/latest; fi` before the relink.

When updating the skill in response to a new run's lessons, add a new dated subsection here. Older subsections stay so the rationale for each rule is traceable.
