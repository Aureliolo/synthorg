# Tech Stack

## High-Level Architecture

```d2
SynthOrg Engine: {
  grid-rows: 5

  Management: {
    grid-columns: 3
    Company Mgr: |
      Config, Templates,
      Hierarchy
    |
    Agent Engine: |
      Lifecycle, Prompting,
      Execution
    |
    Task/Workflow Eng.: |
      Queue, Routing,
      Dependencies, Scheduling
    |
  }

  Infrastructure: {
    grid-columns: 3
    Comms Layer: |
      Message Bus,
      Event Stream, A2A
    |
    Memory Layer: |
      Pluggable, Retrieval,
      Archive
    |
    Tool/Capability System: |
      MCP, Sandboxing,
      Permissions
    |
  }

  Foundation: {
    grid-columns: 3
    Provider Layer: |
      Unified, Routing,
      Fallbacks
    |
    Budget/Cost Engine: |
      Tracking, Limits,
      CFO Agent
    |
    Security/Approval: |
      SecOps, Audit Log,
      Human Queue
    |
  }

  API Layer: Async Framework + WebSocket

  Clients: {
    grid-columns: 2
    Web Dashboard: Web UI (Local)
    CLI Tool: "synthorg [command]"
  }
}
```

The SynthOrg engine is structured as a set of loosely coupled subsystems. Each box represents a major component that communicates through well-defined protocol interfaces. The API layer sits below the engine, exposing REST and WebSocket endpoints to the Web UI and CLI.

---

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Language** | Python 3.14+ | Widest AI/ML ecosystem; all major frameworks use it. LiteLLM, MCP, and memory layer candidates are all Python-native. PEP 649 native lazy annotations, PEP 758 except syntax. |
| **API Framework** | Litestar | Async-native with built-in channels (pub/sub WebSocket), auto OpenAPI 3.1 docs, class-based controllers, native route guards, built-in rate limiting / CSRF / compression middleware, explicit DI, Pydantic v2 support via plugin. See the [design decision](#why-litestar-over-fastapi) below. |
| **LLM Abstraction** | LiteLLM | <!--RS:providers_via_litellm-->95+<!--/RS--> providers, unified API, built-in cost tracking, retries/fallbacks. |
| **Agent Memory** | `sqlvector`, `composite`, `inmemory` backends (config-selected) | Three backends are config-selectable behind a pluggable `MemoryBackend` protocol ([Decision Log](decisions.md)): `sqlvector` (the default: dense vectors plus an inverted term index in the operational database, via pgvector on Postgres and sqlite-vec on SQLite), `composite` (per-namespace routing across backends), and `inmemory` (ephemeral and keyword-only, discouraged, reachable only as a deliberate opt-in). Recall is hybrid dense + BM25 sparse fused with RRF, scored in Python so both databases rank identically. |
| **Message Bus** | Internal (async queues), NATS JetStream (opt-in) | Pull-model `MessageBus` protocol with a pluggable backend factory. In-memory `asyncio` queues ship as the default for single-process deployments. NATS JetStream is the distributed backend for multi-process and multi-host deployments ([Distributed Runtime design](../design/distributed-runtime.md)). |
| **Task Queue** | NATS JetStream work-queue | Backend workers pull claims from a JetStream work-queue stream; the `synthorg worker start` CLI spawns the pool. No separate queue service. |
| **Database** | SQLite (aiosqlite, single-node default), PostgreSQL (multi-instance), MariaDB planned | Pluggable `PersistenceBackend` protocol. SQLite via aiosqlite async driver. PostgreSQL via psycopg with dual-backend conformance tests. MariaDB as future backend; swap via config, no app code changes. |
| **Web UI** | React 19 + Vite 8 + shadcn/ui + Tailwind CSS 4 | Component ownership (shadcn copy-paste model), keyboard-first UX (cmdk-base), rich animations (Motion), mature accessibility (Base UI). Per-request CSP nonce wired through `CSPProvider` (Base UI) + `MotionConfig` (Motion). Zustand state management, react-router routing, Axios HTTP client, @xyflow/react org chart visualization, Recharts charts, Lucide React icons. |
| **Real-time** | WebSocket (Litestar channels plugin) | Built-in pub/sub broadcasting, per-channel history, backpressure management. Real-time agent activity, task updates, chat feed. |
| **Containerisation** | Docker + Docker Compose | Wolfi-based apko-composed distroless runtime (non-root, CIS Docker Benchmark v1.6.0 hardened, minimal attack surface, continuously scanned in CI). Caddy web tier (pure apko, no Dockerfile). GHCR registry, cosign image signing, Trivy vulnerability scanning, SBOM + SLSA L3 provenance. Also used for isolated code execution sandboxing. |
| **Docker API** | aiodocker | Async-native Docker API client for the `DockerSandbox` backend and the devcontainer image build, both over the mounted socket. The only Docker client the backend uses: no `docker` CLI ships in the image. |
| **Structured concurrency** | `anyio` | Memory object streams, task groups, and cancel scopes. A direct dependency rather than one inherited through `mcp`, because the MCP SDK's transport contract is expressed in `anyio` types, so the container-hosted stdio transport builds and returns them itself: that makes them our own API surface. |
| **Tool Integration** | MCP SDK (`mcp`) | Industry standard for LLM-to-tool integration. See [Industry Standards](../reference/standards.md). |
| **Page Extraction** | `trafilatura` (Apache-2.0) | Boilerplate removal for the `web_fetch` tool: navigation, cookie banners, and footers are dropped while headings, fenced code, tables, and links survive. A core dependency rather than an extra, so the default fetch rung needs no API key and no third-party service. Pure Python over `lxml`, which the project already ships. See [Web Research](../design/web-research.md). |
| **Product Telemetry** | Optional: Logfire (via `logfire` SDK), NoopReporter (default) | Opt-in anonymous product telemetry (disabled by default). Pluggable `TelemetryReporter` protocol with `PrivacyScrubber` (allowlist validation). Optional dependency: `telemetry = ["logfire"]`. |
| **Agent Communication** | A2A Protocol compatible | Future-proof inter-agent communication. See [Industry Standards](../reference/standards.md). |
| **Authentication** | PyJWT + argon2-cffi | JWT (HMAC HS256/384/512) for session tokens, Argon2id for password hashing, HMAC-SHA256 for API key storage (keyed with server secret). |
| **Name Generation** | Faker | Multi-locale agent name generation for templates and setup wizard. 57 Latin-script locales across 11 world regions, cached Faker instances, deterministic seeding for reproducible names. |
| **Config Format** | YAML + Pydantic validation | Human-readable config with strict validation. |
| **CLI** | Go (Cobra + charm.land/bubbletea/v2, charm.land/bubbles/v2, charm.land/huh/v2, charm.land/lipgloss/v2) | Cross-platform binary for Docker lifecycle management: `init`, `start`, `worker`, `stop`, `status`, `logs`, `update`, `doctor`, `uninstall`, `version`, `cleanup`, `backup`, `wipe`, `config`, `new`, `completion-install`. Update channel (stable/dev) selectable via `synthorg config set channel dev`. Distributed via GoReleaser + install scripts (`curl \| bash`, `irm \| iex`). Syft generates CycloneDX JSON SBOMs per archive (via GoReleaser `sboms:` stanza). Cosign keyless signing of checksums file (`.sig` + `.pem`). SLSA Level 3 provenance attestations on all published archives. Sigstore provenance bundle (`.sigstore.json`) attached to releases. |

---

## Key Design Decisions

| Decision | Choice | Alternatives Considered | Rationale |
|----------|--------|------------------------|-----------|
| Language | Python 3.14+ | TypeScript, Go, Rust | AI ecosystem; LiteLLM, MCP, and memory layer candidates are Python-native. PEP 649 lazy annotations, PEP 758 except syntax. |
| API | Litestar | FastAPI, Flask, Django, aiohttp | Built-in channels (pub/sub WebSocket), class-based controllers, native route guards, middleware (rate limiting, CSRF, compression), explicit DI. FastAPI considered but Litestar provides more batteries-included for less custom code. |
| LLM Layer | LiteLLM | Direct APIs, OpenRouter only | <!--RS:providers_via_litellm-->95+<!--/RS--> providers, cost tracking, fallbacks, load balancing built-in. |
| Memory | `sqlvector` / `composite` / `inmemory` backends | Mem0, Graphiti, Letta, LanceDB, Chroma | Config-selectable backends behind a pluggable `MemoryBackend` protocol ([Decision Log](decisions.md)): `sqlvector` stores vectors in the operational database (pgvector on Postgres, sqlite-vec on SQLite), `composite` (per-namespace routing), `inmemory` (ephemeral, discouraged). Keeping memory in the existing database means no extra service to run, back up, or migrate. Must support episodic, semantic, and procedural memory types. |
| Message Bus | Pluggable protocol with in-memory default + NATS JetStream first distributed backend | Kafka, RabbitMQ, NATS Core, ZeroMQ | In-memory stays the default for single-host deployments. NATS JetStream chosen as the first distributed backend: pull consumers map to the pull-model protocol, single ~20 MB Go binary, file-backed streams give durability + replay + per-subject retention natively. Redis Streams/RabbitMQ/Kafka remain viable future backends under the same pluggable factory. See [Distributed Runtime design](../design/distributed-runtime.md). |
| Config | YAML + Pydantic | JSON, TOML, Python dicts | Human-friendly, strict validation, good IDE support. |
| Web UI | React 19 + shadcn/ui | Vue 3, Svelte, HTMX | Component ownership (copy-paste), keyboard-first (cmdk-base), Motion animations, mature Base UI accessibility primitives + first-class CSP nonce support, better TS error messages for AI-assisted development. |
| Persistence | Pluggable protocol + repository protocols | ORM (SQLAlchemy), raw SQL, hybrid | Same frozen Pydantic models in and out (no DTOs), async throughout, backend-swappable via config. Repository protocols decouple app code from storage engine. |
| Sandboxing | Layered: subprocess + Docker | Docker-only, subprocess-only, WASM | Risk-proportionate: fast subprocess for file/git, Docker isolation for code execution. Pluggable `SandboxBackend` protocol enables K8s migration later. Stdio MCP servers are isolated by a parallel container-stdio transport over the Docker API (`tools/mcp/container_stdio.py`), not the per-category backend, so the MCP protocol can flow over container stdio. |
| Container Packaging | Wolfi apko-composed distroless + GHCR | Chainguard free-tier, Alpine, Debian-slim, scratch, Docker Hub | Minimal attack surface via apko-composed Wolfi images (glibc, exact package pins, `apko.lock.json`). Non-root by default, continuously scanned in CI. GHCR for tighter GitHub integration. cosign keyless signing for supply-chain integrity (container images and CLI checksums file). Trivy vulnerability scanning, with the triage published as an OpenVEX attestation so a consumer reads our assessment instead of re-deriving it. SLSA L3 provenance attestations on container images and CLI binaries via `actions/attest`. Syft (`anchore/sbom-action`) generates CycloneDX JSON SBOMs per container image, attached to GitHub Releases. Web image is pure apko (Caddy, no Dockerfile); backend/sandbox use thin Dockerfiles over apko-composed bases. |
| Embedded coding harness | None: the native ReAct loop is the only inner loop | OpenHands `openhands-sdk`, Goose (Rust, no Python SDK), OpenCode (CLI/TUI), mini-swe-agent (no governance hooks), Aider, vendor `CLI` tools | An embedded OpenHands harness shipped as a second selectable loop and was removed. The premise was that the inner coding loop is commodity and the orchestration around it is the product, which the measurement did not bear out: [the recording](../research/inner-loop-ab-recording.md) had the native loop ahead in 12 of 15 cells, and the harness's own loop ran where SynthOrg's in-flight controls could not observe it, so 6 of its 45 runs finished `completed` while failing their checks. Governing a loop at its boundaries is not the same as governing it, and the boundary a harness reaches through was the wrong place to learn that. |

<a id="why-litestar-over-fastapi"></a>
!!! info "Design Decision: Why Litestar over FastAPI?"

    Both are async-native Python frameworks with auto-generated OpenAPI docs and Pydantic support. FastAPI has a larger ecosystem and more community resources. However, Litestar provides significantly more built-in functionality that would otherwise need to be written and maintained separately:

    1. **Channels plugin**: pub/sub WebSocket broadcasting with per-channel subscriptions, backpressure management, and subscriber backlog. FastAPI requires hand-rolling all WebSocket connection management.
    2. **Class-based controllers**: group routes with shared guards, middleware, and configuration. The 100+ route groups map naturally to controllers. FastAPI only supports loose functions on routers.
    3. **Native route guards**: declarative authorization at controller/route level. Essential for the approval queue and security features. FastAPI requires `Depends()` on every route.
    4. **Built-in middleware**: rate limiting, CSRF protection, GZip/Brotli compression, session handling, request logging. FastAPI requires third-party packages or custom code for each.
    5. **Explicit dependency injection**: pytest-style named dependencies with scope control. Matches the project's testing approach. FastAPI's DI is implicit (function parameter magic). **Caveat**: plugin instances must be resolved manually in WebSocket handlers via `app.plugins.get(PluginClass)` because Litestar's DI misidentifies them as query params in WS handlers.

    The ecosystem size gap is acceptable: the API is an internal orchestration interface, not a public web service. The bottleneck is LLM latency (seconds), not framework overhead (microseconds). Litestar's approximately 2x performance advantage in micro-benchmarks is a bonus, not the deciding factor. Python 3.14 is supported by both.

---

## Engineering Conventions

These conventions are used throughout the codebase. For full details on each, see the relevant design documentation.

| Convention | Status | Summary |
|------------|--------|---------|
| **Immutability strategy** | Adopted | `copy.deepcopy()` at construction + `MappingProxyType` wrapping for non-Pydantic collections. `frozen=True` + boundary `deepcopy()` for Pydantic models. |
| **Config vs runtime split** | Adopted | Frozen models for config/identity; `model_copy(update=...)` for runtime state transitions (e.g., `TaskExecution`, `AgentContext`). |
| **Derived fields** | Adopted | `@computed_field` instead of stored + validated redundant fields. |
| **Entity identifiers** | Adopted | Entity primary-key `.id` fields are `UUID = Field(default_factory=uuid4)`; persistence stores them in a `TEXT` column via `str(uuid)` / `UUID(...)`, so the wire form is unchanged. |
| **String validation** | Adopted | `NotBlankStr` type from `core.types` for name and string foreign-key reference fields, eliminating per-model validator boilerplate. |
| **Numeric field safety** | Adopted | `allow_inf_nan=False` in all `ConfigDict` declarations to reject `NaN`/`Inf` in numeric fields at validation time. |
| **Shared field groups** | Adopted | Common field sets extracted into base models (e.g., `_SpendingTotals`) to prevent duplication. |
| **Event constants** | Adopted | Per-domain submodules under `observability/events/`. Direct imports: `from synthorg.observability.events.<domain> import CONSTANT`. |
| **Parallel tool execution** | Adopted | `asyncio.TaskGroup` in `ToolInvoker.invoke_all` with optional `max_concurrency` semaphore and structured error collection. |
| **Parallel agent execution** | Adopted | `ParallelExecutor` with `TaskGroup` + `Semaphore` concurrency limits, `ResourceLock` for exclusive file-path claims, progress tracking, and shutdown awareness. |
| **Tool permission checking** | Adopted | Category-level gating based on `ToolAccessLevel`. Priority-based resolution: denied list, allowed list, level categories, then deny. |
| **Tool sandboxing** | Adopted | Layered: in-process path validation for file system tools, `SubprocessSandbox` for git tools, `DockerSandbox` for code execution. Per-category backend selection via `SandboxingConfig` and sandbox factory. |
| **Crash recovery** | Adopted | Pluggable `RecoveryStrategy` protocol. Current strategies: `FailAndReassignStrategy` and `CheckpointRecoveryStrategy` (per-turn checkpoint resume). |
| **Agent behaviour testing** | Planned | Scripted `FakeProvider` for unit tests; behavioural outcome assertions for integration tests. |
| **LLM call analytics** | Adopted | Proxy metrics (`turns_per_task`, `tokens_per_task`) and data models for call categorisation, coordination metrics, and orchestration ratio. |
| **Cost tiers and quota tracking** | Adopted | Configurable `CostTierDefinition` with merge/override semantics. `QuotaTracker` enforces per-provider request/token quotas with window-based rotation. |
| **Shared org memory** | Adopted | `OrgMemoryBackend` protocol with `HybridPromptRetrievalBackend`. Role-based write access control. Core policies in system prompts; extended facts retrieved on demand. |
| **Memory consolidation** | Adopted | Axis-split design ([ADR-0005](../decisions/0005-memory-consolidation-axis-split.md)): an `EntrySelector` (*which* entries) composed with a `ConsolidationOp` (*how*) via `CompositeConsolidationStrategy`, which satisfies the `ConsolidationStrategy` protocol. Three registered composites: `simple` (`ConcatenationOp`), `dual_mode` (density-aware `DensityRoutingOp`: abstractive LLM summary for sparse content, extractive preservation for dense), and `llm` (`LLMSynthesisOp`). `RetentionEnforcer` for age-based cleanup. `ArchivalStore` for cold storage with deterministic index-based restore. |
| **State coordination** | Adopted | Centralised single-writer `TaskEngine` with `asyncio.Queue`. Agents submit requests; engine applies `model_validate` / `with_transition` sequentially and publishes snapshots. |
| **Workspace isolation** | Adopted | Pluggable `WorkspaceIsolationStrategy` protocol. Default: git worktrees with sequential merge on completion. |
| **Graceful shutdown** | Adopted | Pluggable `ShutdownStrategy` protocol with cooperative 30-second timeout. Force-cancel after timeout with `INTERRUPTED` status. |
| **Template inheritance** | Adopted | `extends` field triggers parent resolution at render time with deep merge by field type. Circular chain detection included. |
| **Communication foundation** | Adopted | `MessageBus` protocol with pull-model `receive()`, `MessageDispatcher` for concurrent handler routing, `AgentMessenger` per-agent facade. |
| **Delegation and loop prevention** | Adopted | `DelegationGuard` orchestrates five mechanisms (ancestry, depth, dedup, rate limit, circuit breaker) in sequence with short-circuit on first rejection. |
| **Task assignment** | Adopted | `TaskAssignmentStrategy` protocol with six strategies: Manual, RoleBased, LoadBalanced, CostOptimized, Hierarchical, and Auction. |
| **Pydantic alias for YAML directives** | Adopted | `Field(alias="_remove")` in `TemplateAgentConfig`: YAML uses `_remove: true`, Python accesses `agent.remove`. Keeps YAML human-readable while avoiding leading-underscore attributes. |
