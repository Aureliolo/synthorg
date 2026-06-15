# Retry Patterns

Three retry-pattern families live in the codebase. They are intentionally distinct: a single helper that tried to cover all three would either obscure the semantics or expose so many knobs that the abstraction is worse than three small ones. Use this page when you are about to add a retry loop and want to know which pattern fits.

The canonical helper for transient-I/O backoff is `synthorg.core.resilience.GeneralRetryHandler`; its module docstring carries the same carve-out list mirrored here, so a developer reading the helper sees the same boundaries.

## Pattern A -- Transient I/O (use `GeneralRetryHandler`)

**When**: a downstream call (HTTP POST, NATS publish, file read on a flaky volume) failed for a reason that is independent of the request body, like a connection reset, a momentary 5xx, or a kernel-scheduling hiccup. Sleeping briefly and retrying is the right primitive; the request itself is the same on every attempt.

**How**: pass a `retryable` predicate, an `attempts` budget, an exponential backoff `base` / `cap`, and (optionally) `jitter` to `GeneralRetryHandler`. The helper sleeps via the injected `Clock` so `FakeClock` advances cleanly in tests.

**Sites**:

- `src/synthorg/workers/dispatcher.py`: NATS publish. The canonical "default" example.
- `src/synthorg/telemetry/collector.py`: peer-ID file read on local-disk paths. The retry covers the brief window where the file is being atomically replaced by a sibling process.
- `src/synthorg/engine/workspace/git_backend/external_remote.py`: git push/fetch against a forge remote. The `retryable` predicate retries transient transport failures, forge rate-limits (`GitBackendRateLimitError`), and transient forge-API errors; it never retries auth failures or a confirmed-missing remote (the latter triggers lazy forge-repo creation, not backoff).

**Anti-pattern**: tuning `base=0` to bypass backoff so you can shoehorn semantic self-correction (Pattern B) through the same helper. The retry would observe the same error every attempt because nothing about the request changed; that is what Pattern B exists to address.

## Pattern B -- Semantic self-correction

**When**: an LLM produced an unparseable response (malformed JSON, missing required field, validation failure). The fault is not transient: sleeping and re-asking the same question would yield the same broken response. Each attempt sends a *richer* prompt that includes the prior failed output and a corrective instruction.

**How**: an inline `for attempt in range(max_attempts)` loop with no sleep between attempts. The prompt is mutated each iteration to incorporate prior-attempt context. There is no temporal backoff because there is no transient condition to wait out.

**Sites**:

- `src/synthorg/engine/decomposition/llm.py`: task decomposition self-correction loop.
- `src/synthorg/engine/workspace/semantic_llm.py`: workspace operation self-correction loop.

**Why this is not `GeneralRetryHandler`**: forcing this through the transient-I/O helper would require `base=0`, `jitter=0`, and a `retryable` predicate that always returns `True`. The resulting call would be a confused mix of "retry on anything, no sleep" wrapped in a helper whose name and docstring promise temporal backoff. If a third self-correction loop appears, factor out a dedicated `LlmSelfCorrectionLoop` primitive rather than collapsing it into Pattern A.

## Pattern C -- Contention loops + sync logging-thread

Two distinct sub-cases share this section because both are inline-by-necessity for distinct reasons.

### C/CAS -- Optimistic concurrency / version-race retry

**When**: two writers race to insert a row whose unique constraint is `(scope, version)` and the database picks one winner via `UniqueViolation`. The losing writer needs to recompute its version and retry; *other* unique-constraint failures must propagate as `DuplicateRecordError` immediately because they indicate genuine duplicates.

**How**: an inline retry that branches on `exc.diag.constraint_name` (or equivalent driver-specific signal) to distinguish the version race from a true duplicate. The error classification is intricate; abstracting it generically would either pollute the helper API with database-driver knowledge or hide the failure-mode discrimination that makes the loop correct.

**Sites**:

- `src/synthorg/persistence/postgres/decision_repo.py` `_execute_insert`: version-race retry for the decision-history append path.

### C/Sync -- Bootstrap-tier sync logging-thread retry

**When**: code runs inside a stdlib `logging.Handler` worker thread using synchronous `urllib.request`. There is no event loop available; `await GeneralRetryHandler.run(...)` would either deadlock or panic.

**How**: a tight synchronous loop with bounded exponential backoff. The backoff sleep is done using `time.sleep(delay)` so that retries run to completion during shutdown rather than being dropped mid-flight. Bootstrap-tier code keeps its own retry primitive because the async helper is unreachable from this execution context.

**Sites**:

- `src/synthorg/observability/http_handler.py` `HttpBatchHandler._send_with_retries`: HTTP collector POST from inside the stdlib logging-handler thread (4xx non-retryable; bounded exponential backoff between attempts).
- `src/synthorg/observability/otlp_handler.py` `OtlpHandler._send_with_retries`: OTLP/JSON collector POST from inside the stdlib logging-handler thread (same retry + backoff semantics as the HTTP sink, so a transient collector hiccup does not drop a whole batch).

## Decision tree

| If your loop is...                                                  | Reach for                            |
|---------------------------------------------------------------------|--------------------------------------|
| Bounded, exponential-backoff retry on a transient I/O failure       | `GeneralRetryHandler` (Pattern A)    |
| LLM re-prompted with prior-attempt context, no sleep                | Inline loop (Pattern B)              |
| CAS / version-race retry that branches on driver constraint name    | Inline loop (Pattern C/CAS)          |
| Sync code inside a stdlib `logging.Handler` thread                  | Inline loop (Pattern C/Sync)         |
| None of the above                                                   | Stop and ask before adding a fourth family |

## Adding a new retry site

1. Classify the new site against the four cells in the decision tree above.
2. If it lands in Pattern A, use `GeneralRetryHandler` and pass a `retryable` predicate plus your backoff parameters. Add a comment of the form `# See docs/reference/retry-patterns.md: Pattern A` if the site is not obviously a transient-I/O retry.
3. If it lands in Pattern B or C, add the comment so the next reader can match the inline loop to the rationale on this page.
4. If it does not fit any of the four cells, the page is wrong. Update this page first, get the new family agreed, then add the loop.
5. **Update the per-pattern Sites lists above** so this page stays synchronised with the codebase. A stale list teaches the next reader the wrong assumption (e.g. "there are only 2 Pattern A sites") and the doc-link comments at each site only point back here, so the page is the single source of truth for the inventory.

## See also

- `src/synthorg/core/resilience/general_retry.py`: module docstring mirrors the carve-out list.
- `src/synthorg/providers/resilience.py`: provider-boundary `RetryHandler`, coupled to `ProviderError.is_retryable`. Distinct from `GeneralRetryHandler`; do not unify the two without a separate design discussion.
- `src/synthorg/providers/drivers/mappers.py` `extract_retry_after`: parses a 429/503 `Retry-After` header into a backoff hint, accepting both RFC 9110 forms: a delta-seconds integer and an HTTP-date (via `email.utils.parsedate_to_datetime`, with a clock seam for testability). Non-finite, negative, or already-past values are discarded so a malformed header never produces a negative or absurd sleep.
