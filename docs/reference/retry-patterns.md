# Retry Patterns

Five retry-pattern families live in the codebase. They are intentionally distinct: a single helper that tried to cover all five would either obscure the semantics or expose so many knobs that the abstraction is worse than five small ones. Use this page when you are about to add a retry loop and want to know which pattern fits.

The canonical helper for transient-I/O backoff is `synthorg.core.resilience.GeneralRetryHandler`; its module docstring carries the same carve-out list mirrored here, so a developer reading the helper sees the same boundaries.

## Pattern A -- Transient I/O (use `GeneralRetryHandler`)

**When**: a downstream call (HTTP POST, NATS publish, file read on a flaky volume) failed for a reason that is independent of the request body, like a connection reset, a momentary 5xx, or a kernel-scheduling hiccup. Sleeping briefly and retrying is the right primitive; the request itself is the same on every attempt.

**How**: pass a `retryable` predicate, an `attempts` budget, an exponential backoff `base` / `cap`, and (optionally) `jitter` to `GeneralRetryHandler`. The helper sleeps via the injected `Clock` so `FakeClock` advances cleanly in tests.

**Sites**:

- `src/synthorg/workers/dispatcher.py`: NATS publish. The canonical "default" example.
- `src/synthorg/engine/workspace/git_backend/external_remote.py`: git push/fetch against a forge remote. The `retryable` predicate retries transient transport failures, forge rate-limits (`GitBackendRateLimitError`), and transient forge-API errors; it never retries auth failures or a confirmed-missing remote (the latter triggers lazy forge-repo creation, not backoff).
- `src/synthorg/engine/task_sync_review.py::_persist_with_retry` (called from `create_review_approval`): the approval-store write for a review/failed item. The `retryable` predicate retries any transient store fault so a FAILED-outcome approval (the only surface carrying a hard failure to the operator) is not dropped on the first try, but excludes `ConflictError`: the write is not idempotent under blind retry, so a duplicate-id `ConflictError` means a prior attempt's write already landed (its ack lost) and is treated as success rather than a retryable fault. After the budget is exhausted the drop is logged (ERROR for FAILED, WARNING otherwise) and swallowed so the run result is never lost.
- `src/synthorg/tools/web/providers/http_search_provider.py::HttpWebSearchProvider`: the native web-search REST call. The `retryable` predicate retries `WebSearchTransientError` (a transport failure or a `429`/`5xx` upstream status); a `429`'s `Retry-After` header is parsed and honoured through the handler's `delay_override` so the server's own cooldown overrides the fixed exponential schedule. Configuration/response/egress-blocked errors are non-retryable and raised immediately.
- `src/synthorg/memory/embedding/text_embedder.py::ProviderTextEmbedder.embed_many`: the embedding REST call on the memory read + write path. The `retryable` predicate retries transient provider faults (rate-limit, connection, timeout, 5xx) and never retries a deterministic misconfiguration (auth, bad request, content-policy) so a permanent failure surfaces immediately instead of burning the backoff budget on the hot path.
- `src/synthorg/memory/embedding/fine_tune_docker_runner.py::_connect`: the Docker-daemon connection for a fine-tune run, retried through `GeneralRetryHandler` while the sidecar comes up.

**Anti-pattern**: tuning `base=0` to bypass backoff so you can shoehorn semantic self-correction (Pattern B) through the same helper. The retry would observe the same error every attempt because nothing about the request changed; that is what Pattern B exists to address.

**Deliberately not retried (fail-open)**: `providers/management/live_discovery_probe.py::LiveDiscoveryProbe.discover_report` reaches the live catalogue through `providers/discovery.py::discover_models`, whose `_await_fetch` already catches `httpx.ConnectError` / `TimeoutException` / `HTTPStatusError` and returns an empty result. A transient blip therefore surfaces as an empty discovered set -- a documented no-op the refresh strategies treat as "nothing changed this cycle" rather than flagging every configured model absent. Wrapping the probe in `GeneralRetryHandler` would be dead code (the call never raises a transient error to retry); the next cycle re-probes, so do not add a retry here.

Also fail-open: `providers/ollama_usage_tier.py::_scrape_tier` makes a single best-effort `GET` against the Ollama cloud model page and, on any exception or non-200, immediately falls back to the parameter-count tier approximation. The page structure is brittle and the fallback is always available, so a transient blip is absorbed as "use the approximation" rather than retried; the next enrichment re-scrapes.

## Pattern B -- Semantic self-correction

**When**: an LLM produced an unparseable response (malformed JSON, missing required field, validation failure). The fault is not transient: sleeping and re-asking the same question would yield the same broken response. Each attempt sends a *richer* prompt that includes the prior failed output and a corrective instruction.

**How**: an inline `for attempt in range(max_attempts)` loop with no sleep between attempts. The prompt is mutated each iteration to incorporate prior-attempt context. There is no temporal backoff because there is no transient condition to wait out.

**Sites**:

- `src/synthorg/engine/decomposition/llm.py`: task decomposition self-correction loop.
- `src/synthorg/engine/workspace/semantic_llm.py`: workspace operation self-correction loop.
- `src/synthorg/memory/retrieval/hierarchical/default_retriever.py::retrieve`: the reflective-retry loop that reformulates a memory query and re-runs it when the first result set is judged insufficient. Bounded, no sleep, mutates the query each iteration.

**Why this is not `GeneralRetryHandler`**: forcing this through the transient-I/O helper would require `base=0`, `jitter=0`, and a `retryable` predicate that always returns `True`. The resulting call would be a confused mix of "retry on anything, no sleep" wrapped in a helper whose name and docstring promise temporal backoff. If a third self-correction loop appears, factor out a dedicated `LlmSelfCorrectionLoop` primitive rather than collapsing it into Pattern A.

## Pattern C -- Contention loops + sync logging-thread

Two distinct sub-cases share this section because both are inline-by-necessity for distinct reasons.

### C/CAS -- Optimistic concurrency / version-race retry

**When**: two writers race to insert a row whose unique constraint is `(scope, version)` and the database picks one winner via `UniqueViolation`. The losing writer needs to recompute its version and retry; *other* unique-constraint failures must propagate as `DuplicateRecordError` immediately because they indicate genuine duplicates.

**How**: an inline retry that branches on `exc.diag.constraint_name` (or equivalent driver-specific signal) to distinguish the version race from a true duplicate. The error classification is intricate; abstracting it generically would either pollute the helper API with database-driver knowledge or hide the failure-mode discrimination that makes the loop correct.

**Sites**:

- `src/synthorg/persistence/postgres/decision_repo.py` `_execute_insert`: version-race retry for the decision-history append path (inline constraint-name branch).
- `src/synthorg/persistence/sqlite/conversation_repo/_turns.py` `append`: sequence-collision retry on the `(conversation_id, sequence)` uniqueness race, exhaustion raising a retryable `TurnSequenceConflictError`.
- `src/synthorg/persistence/postgres/conversation_repo.py` `append`: the Postgres twin of the same sequence-collision retry (fresh connection per attempt; correctness rests on the unique constraint plus the bounded retry).
- `src/synthorg/core/concurrency/cas_retry.py` `CASRetryHandler`: the shared version-token (`expected_updated_at`) compare-and-set retry handler. Retries a read-modify-write on `VersionConflictError` and re-raises after a bounded attempt count; the settings-blob sites below drive it rather than hand-rolling the loop.
- `src/synthorg/organization/team_navigation.py` (`mutate_company_departments` / `with_company_departments_cas`) and `src/synthorg/api/controllers/template_packs.py` (`_apply_pack_to_settings`): CAS over the `company.departments` / `company.agents` settings blob through `CASRetryHandler`; the retry budget resolves per call from `coordination.company_departments_cas_retry_attempts`.
- `src/synthorg/api/controllers/departments/_shared.py`: CAS over the `dept_ceremony_policies` setting through `CASRetryHandler`.
- `src/synthorg/api/controllers/_plan_review_resume.py` `_sync_plan_status`: CAS over the durable plan's status when reflecting an approval decision, through `CASRetryHandler`; exhaustion is swallowed-and-logged (the decision already persisted on the approval), not re-raised.
- `src/synthorg/engine/pipeline/service.py` `_staff_owner_locked`, and `src/synthorg/engine/initiative/project_writes.py` `link_project_to_plan` / `advance_project_status`: CAS over a `Project` row, hand-rolled rather than driven through `CASRetryHandler`. Two reasons the handler does not fit as written: the repository raises `PersistenceVersionConflictError` (the persistence-layer error) while `CASRetryHandler` catches the API-boundary `VersionConflictError`, and `advance_project_status` has branches that must skip the write entirely (already at target, target unreachable) plus a multi-hop walk that writes once per hop. If a fourth `Project`-row CAS site appears, generalise `CASRetryHandler` over the conflict type and the skip-write case rather than adding another loop.
- `src/synthorg/engine/initiative/rollup.py` `_advance_plan`: bounded retry over the audited plan-status write, re-deriving the target from freshly read items on each attempt because a conflict means the plan changed underneath. An illegal transition (`ConflictError`) is a derivation bug and is surfaced at ERROR rather than retried.
- `src/synthorg/api/controllers/_project_cascade.py` `_supersede_plan`: bounded re-read of a plan the initiative rollup may be writing concurrently, so a project delete racing a task completion does not abort the cascade.

### C/Sync -- Sync retry where `GeneralRetryHandler` is unreachable

**When**: synchronous code using `urllib.request` runs where `await GeneralRetryHandler.run(...)` cannot. Two sub-contexts qualify: inside a stdlib `logging.Handler` worker thread (no event loop -- the await would deadlock or panic); and standalone `scripts/` CI gates that must not import `synthorg.core` (they run as bare `python3 scripts/x.py` in CI with only the stdlib, so the async helper is not importable).

**How**: a tight synchronous loop with bounded backoff. The logging-thread and `to_thread` sites sleep via `time.sleep(delay)` so retries complete during shutdown rather than being dropped mid-flight. The CI-script sites split transient (network error + 5xx, retried) from terminal (4xx, returned immediately) so a registry blip does not red the gate while a genuine 404/auth answer fails fast.

**Sites**:

- `src/synthorg/telemetry/collector.py` `_read_peer_deployment_id`: peer-ID file read on local-disk paths, retried over the brief window where a sibling process atomically replaces the file. A synchronous helper run via `asyncio.to_thread` (no event loop to `await GeneralRetryHandler` on), so it uses a bounded `for attempt in range(...)` + `time.sleep` loop; `general_retry.py`'s carve-out list names it explicitly.
- `src/synthorg/observability/http_handler.py` `HttpBatchHandler._send_with_retries`: HTTP collector POST from inside the stdlib logging-handler thread (4xx non-retryable; bounded exponential backoff between attempts).
- `src/synthorg/observability/otlp_handler.py` `OtlpHandler._send_with_retries`: OTLP/JSON collector POST from inside the stdlib logging-handler thread (same retry + backoff semantics as the HTTP sink, so a transient collector hiccup does not drop a whole batch).
- `scripts/check_image_signatures.py` `_request_with_retry`: bounded retry on the token-mint and tag->digest HEAD against GHCR (transient network error + 5xx retried, 4xx returned immediately). A standalone CI gate that cannot import `synthorg.core`.
- `scripts/check_image_signatures.py` `signature_present`: eventual-consistency poll for a freshly-published cosign referrer tag (retries both a transient network error and a propagation-window 404, on its own short `SIG_PROPAGATION_*` budget; a persistent non-404 registry error raises rather than reporting a false "unsigned" verdict). Same CI-script context.

## Pattern D -- Long-lived consumer poll loops

**When**: an unbounded background consumer that repeatedly polls a message-bus channel for the life of the process. This is not a bounded-attempt retry of a single operation (Pattern A): there is no "budget" to exhaust because the loop's job is to run until the app shuts down. A transient poll error must not tear the consumer down, so the loop logs the error, sleeps a bounded backoff, and continues; a separate `consecutive_errors` ceiling breaks the loop (channel-dead) so a genuinely dead channel does not spin forever.

**How**: an inline `while not stopped` poll loop with a constant error-backoff `asyncio.sleep` and a `consecutive_errors` counter that `break`s past a ceiling. `GeneralRetryHandler` is the wrong tool: it wraps one operation in a finite attempt budget and re-raises when the budget is spent, whereas a consumer loop must survive an unbounded number of transient errors and only stop on the dead-channel ceiling or a shutdown signal. Forcing this through Pattern A would either drop the consumer on the first error-budget exhaustion or require an infinite `attempts`, which the helper is not built for.

**Sites**:

- `src/synthorg/settings/dispatcher.py` `SettingsChangeDispatcher._poll_loop`: polls the settings-change channel; a poll error backs off `_ERROR_BACKOFF` and continues, breaking only when `consecutive_errors` hits the channel-dead ceiling.
- `src/synthorg/api/bus_bridge.py` the per-channel bridge poll loop: polls a bus channel to fan WebSocket events out; a poll error backs off `poll_timeout` and continues, breaking on the same consecutive-error ceiling.

## Pattern E -- Deliberate no-retry (governed one-shot)

**When**: an agent-facing tool call that egresses to a bound connection exactly once per invocation, routing writes (and reads on a connection the operator marked sensitive) through the identity-bound approval flow while an ordinary read fast-allows. The `forge_*`, `chat_*` and `deploy_*` tools (`src/synthorg/tools/forge/`, `src/synthorg/tools/chat/`, `src/synthorg/tools/deploy/`) fall here, as does the `external_api` tool they mirror. A transient upstream failure is surfaced to the agent, not retried inside the tool.

**How**: no loop at all. The tool dispatches once; `_dispatch_guarded` maps an upstream failure to a typed `ToolError` leaf, which `execute()` catches and returns as a `ToolExecutionResult(is_error=True)` rather than letting the exception escape to the caller (rate-limit failures carry `retry_after_seconds` in that result's metadata so the agent, not the tool, decides whether to try again). This is deliberate, not an omission: a write consumes a one-shot approval token bound to `agent_id`+`task_id`, so a silent in-tool retry would either re-egress an already-approved side effect or re-park a fresh approval the operator never sanctioned; a non-sensitive read is a single fast-allow egress whose failure the agent should see rather than have masked (a read on a connection the operator marked sensitive parks for approval exactly like a write). Provider-layer resilience (`BaseCompletionProvider`) does not apply because these are direct connection calls, not LLM dispatches. The bounded-attempt budget of Pattern A is therefore the wrong shape: the correct budget is one.

**Sites**:

- `src/synthorg/tools/forge/forge_tools.py`, `src/synthorg/tools/chat/chat_tools.py`: each `_dispatch_guarded` call egresses once; a `ForgeRateLimitedError` / `ChatRateLimitedError` surfaces `retry_after_seconds` to the caller instead of sleeping.
- `src/synthorg/tools/deploy/deploy_tools.py` (via `tools/deploy/_base.py::_dispatch_guarded`): the destructive release and the read observers egress once; a `DeployRateLimitedError` surfaces `retry_after_seconds` rather than retrying a release token in-tool.
- `src/synthorg/tools/external_api/`: the pre-existing governed tool this pattern generalises.

## Decision tree

| If your loop is...                                                  | Reach for                            |
|---------------------------------------------------------------------|--------------------------------------|
| Bounded, exponential-backoff retry on a transient I/O failure       | `GeneralRetryHandler` (Pattern A)    |
| LLM re-prompted with prior-attempt context, no sleep                | Inline loop (Pattern B)              |
| CAS / version-race retry that branches on driver constraint name    | Inline loop (Pattern C/CAS)          |
| Sync code in a stdlib `logging.Handler` thread, or a `scripts/` CI gate that cannot import `synthorg.core` | Inline loop (Pattern C/Sync)         |
| Unbounded background consumer polling a bus channel for the process lifetime | Inline poll loop (Pattern D)         |
| A governed one-shot agent tool (write or sensitive-connection read gated; other reads fast-allow) | No loop; surface the error (Pattern E) |
| None of the above                                                   | Stop and ask before adding a sixth family |

## Adding a new retry site

1. Classify the new site against the cells in the decision tree above.
2. If it lands in Pattern A, use `GeneralRetryHandler` and pass a `retryable` predicate plus your backoff parameters. Add a comment of the form `# See docs/reference/retry-patterns.md: Pattern A` if the site is not obviously a transient-I/O retry.
3. If it lands in Pattern B or C, add the comment so the next reader can match the inline loop to the rationale on this page.
4. If it does not fit any of the cells, the page is wrong. Update this page first, get the new family agreed, then add the loop.
5. **Update the per-pattern Sites lists above** so this page stays synchronised with the codebase. A stale list teaches the next reader the wrong assumption (e.g. "there are only 2 Pattern A sites") and the doc-link comments at each site only point back here, so the page is the single source of truth for the inventory.

## See also

- `src/synthorg/core/resilience/general_retry.py`: module docstring mirrors the carve-out list.
- `src/synthorg/providers/resilience/retry.py`: provider-boundary `RetryHandler`, coupled to `ProviderError.is_retryable`. Distinct from `GeneralRetryHandler`; do not unify the two without a separate design discussion.
- `src/synthorg/providers/drivers/mappers.py` `extract_retry_after`: parses a 429/503 `Retry-After` header into a backoff hint, accepting both RFC 9110 forms: a delta-seconds integer and an HTTP-date (via `email.utils.parsedate_to_datetime`, with a clock seam for testability). Non-finite, negative, or already-past values are discarded so a malformed header never produces a negative or absurd sleep.
