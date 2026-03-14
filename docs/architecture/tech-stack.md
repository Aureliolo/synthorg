# Tech Stack

## High-Level Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│                        SynthOrg Engine                      │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Company Mgr  │  │ Agent Engine  │  │ Task/Workflow Eng. │  │
│  │ (Config,     │  │ (Lifecycle,   │  │ (Queue, Routing,   │  │
│  │  Templates,  │  │  Personality, │  │  Dependencies,     │  │
│  │  Hierarchy)  │  │  Execution)   │  │  Scheduling)       │  │
│  └──────────────┘  └──────────────┘  └────────────────────┘  │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Comms Layer  │  │ Memory Layer  │  │ Tool/Capability    │  │
│  │ (Message Bus,│  │ (Pluggable,  │  │ System (MCP,       │  │
│  │  Meetings,   │  │  Retrieval,  │  │  Sandboxing,       │  │
│  │  A2A)        │  │  Archive)    │  │  Permissions)      │  │
│  └──────────────┘  └──────────────┘  └────────────────────┘  │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Provider Lyr │  │ Budget/Cost  │  │ Security/Approval  │  │
│  │ (Unified,   │  │ Engine       │  │ System             │  │
│  │  Routing,    │  │ (Tracking,   │  │ (SecOps Agent,     │  │
│  │  Fallbacks)  │  │  Limits,     │  │  Audit Log,        │  │
│  │              │  │  CFO Agent)  │  │  Human Queue)      │  │
│  └──────────────┘  └──────────────┘  └────────────────────┘  │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              API Layer (Async Framework + WebSocket)      │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌──────────────────────┐  ┌─────────────────────────────┐  │
│  │     Web UI (Local)    │  │         CLI Tool            │  │
│  │     Web Dashboard      │  │    synthorg <command>     │  │
│  └──────────────────────┘  └─────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

The SynthOrg engine is structured as a set of loosely coupled subsystems. Each box represents a major component that communicates through well-defined protocol interfaces. The API layer sits below the engine, exposing REST and WebSocket endpoints to the Web UI and CLI.

---

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Language** | Python 3.14+ | Best AI/ML ecosystem; all major frameworks use it. LiteLLM, MCP, and memory layer candidates are all Python-native. PEP 649 native lazy annotations, PEP 758 except syntax. |
| **API Framework** | Litestar | Async-native with built-in channels (pub/sub WebSocket), auto OpenAPI 3.1 docs, class-based controllers, native route guards, built-in rate limiting / CSRF / compression middleware, explicit DI, Pydantic v2 support via plugin. See the [design decision](#why-litestar-over-fastapi) below. |
| **LLM Abstraction** | LiteLLM | 100+ providers, unified API, built-in cost tracking, retries/fallbacks. |
| **Agent Memory** | Mem0 (Qdrant + SQLite) initially, custom stack (Neo4j + Qdrant) planned | Mem0 runs in-process as the initial backend behind a pluggable `MemoryBackend` protocol ([Decision Log](decisions.md)). Qdrant embedded + SQLite for persistence. Custom stack as a future upgrade. Config-driven backend selection. |
| **Message Bus** | Internal (async queues), Redis planned | Start with Python asyncio queues; upgrade to Redis for multi-process/distributed deployments. |
| **Task Queue** | Internal, Celery/Redis planned | Start simple, scale with Celery when needed. |
| **Database** | SQLite (aiosqlite), PostgreSQL / MariaDB planned | Pluggable `PersistenceBackend` protocol. SQLite ships first via aiosqlite async driver. PostgreSQL and MariaDB as future backends -- swap via config, no app code changes. |
| **Web UI** | Vue 3 + Vite | Modern, fast, good ecosystem. Simpler than React for dashboards. |
| **Real-time** | WebSocket (Litestar channels plugin) | Built-in pub/sub broadcasting, per-channel history, backpressure management. Real-time agent activity, task updates, chat feed. |
| **Containerization** | Docker + Docker Compose | Chainguard Python distroless runtime (non-root, CIS Docker Benchmark v1.6.0 hardened, minimal attack surface, continuously scanned in CI). `nginxinc/nginx-unprivileged` web tier. GHCR registry, cosign image signing, Trivy + Grype vulnerability scanning, SBOM + SLSA L3 provenance. Also used for isolated code execution sandboxing. |
| **Docker API** | aiodocker | Async-native Docker API client for the `DockerSandbox` backend. |
| **Tool Integration** | MCP SDK (`mcp`) | Industry standard for LLM-to-tool integration. See [Industry Standards](../reference/standards.md). |
| **Agent Communication** | A2A Protocol compatible | Future-proof inter-agent communication. See [Industry Standards](../reference/standards.md). |
| **Authentication** | PyJWT + argon2-cffi | JWT (HMAC HS256/384/512) for session tokens, Argon2id for password hashing, HMAC-SHA256 for API key storage (keyed with server secret). |
| **Config Format** | YAML + Pydantic validation | Human-readable config with strict validation. |
| **CLI** | Go (Cobra + charmbracelet/huh) | Cross-platform binary for Docker lifecycle management: `init`, `start`, `stop`, `status`, `logs`, `update`, `doctor`, `uninstall`, `version`. Distributed via GoReleaser + install scripts (`curl \| bash`, `irm \| iex`). SLSA Level 3 provenance attestations on all release archives. |

---

## Key Design Decisions

| Decision | Choice | Alternatives Considered | Rationale |
|----------|--------|------------------------|-----------|
| Language | Python 3.14+ | TypeScript, Go, Rust | AI ecosystem; LiteLLM, MCP, and memory layer candidates are Python-native. PEP 649 lazy annotations, PEP 758 except syntax. |
| API | Litestar | FastAPI, Flask, Django, aiohttp | Built-in channels (pub/sub WebSocket), class-based controllers, native route guards, middleware (rate limiting, CSRF, compression), explicit DI. FastAPI considered but Litestar provides more batteries-included for less custom code. |
| LLM Layer | LiteLLM | Direct APIs, OpenRouter only | 100+ providers, cost tracking, fallbacks, load balancing built-in. |
| Memory | Mem0 (initial), custom stack (future) + SQLite | Graphiti, Letta, Cognee, custom | Mem0 in-process as initial backend behind a pluggable `MemoryBackend` protocol ([Decision Log](decisions.md)). Custom stack (Neo4j + Qdrant) as a future upgrade. Must support episodic, semantic, and procedural memory types. |
| Message Bus | asyncio queues, Redis planned | Kafka, RabbitMQ, NATS | Start simple; Redis is well-supported; Kafka is overkill for local deployments. |
| Config | YAML + Pydantic | JSON, TOML, Python dicts | Human-friendly, strict validation, good IDE support. |
| Web UI | Vue 3 | React, Svelte, HTMX | Simpler than React for dashboards. |
| Persistence | Pluggable protocol + repository protocols | ORM (SQLAlchemy), raw SQL, hybrid | Same frozen Pydantic models in and out (no DTOs), async throughout, backend-swappable via config. Repository protocols decouple app code from storage engine. |
| Sandboxing | Layered: subprocess + Docker | Docker-only, subprocess-only, WASM | Risk-proportionate: fast subprocess for file/git, Docker isolation for code execution. Pluggable `SandboxBackend` protocol enables K8s migration later. |
| Container Packaging | Chainguard distroless + GHCR | Alpine, Debian-slim, scratch, Docker Hub | Minimal attack surface, non-root by default, continuously scanned in CI. GHCR for tighter GitHub integration. cosign keyless signing for supply-chain integrity. Trivy + Grype dual scanning. SLSA L3 provenance attestations on container images and CLI binaries via slsa-github-generator. |

<a id="why-litestar-over-fastapi"></a>
!!! info "Design Decision: Why Litestar over FastAPI?"

    Both are async-native Python frameworks with auto-generated OpenAPI docs and Pydantic support. FastAPI has a larger ecosystem and more community resources. However, Litestar provides significantly more built-in functionality that would otherwise need to be written and maintained separately:

    1. **Channels plugin** -- pub/sub WebSocket broadcasting with per-channel subscriptions, backpressure management, and subscriber backlog. FastAPI requires hand-rolling all WebSocket connection management.
    2. **Class-based controllers** -- group routes with shared guards, middleware, and configuration. The 13 route groups map naturally to controllers. FastAPI only supports loose functions on routers.
    3. **Native route guards** -- declarative authorization at controller/route level. Essential for the approval queue and security features. FastAPI requires `Depends()` on every route.
    4. **Built-in middleware** -- rate limiting, CSRF protection, GZip/Brotli compression, session handling, request logging. FastAPI requires third-party packages or custom code for each.
    5. **Explicit dependency injection** -- pytest-style named dependencies with scope control. Matches the project's testing approach. FastAPI's DI is implicit (function parameter magic).

    The ecosystem size gap is acceptable: the API is an internal orchestration interface, not a public web service. The bottleneck is LLM latency (seconds), not framework overhead (microseconds). Litestar's approximately 2x performance advantage in micro-benchmarks is a bonus, not the deciding factor. Python 3.14 is supported by both.

---

## Engineering Conventions

These conventions are used throughout the codebase. For full details on each, see the relevant design documentation.

| Convention | Status | Summary |
|------------|--------|---------|
| **Immutability strategy** | Adopted | `copy.deepcopy()` at construction + `MappingProxyType` wrapping for non-Pydantic collections. `frozen=True` + boundary `deepcopy()` for Pydantic models. |
| **Config vs runtime split** | Adopted | Frozen models for config/identity; `model_copy(update=...)` for runtime state transitions (e.g., `TaskExecution`, `AgentContext`). |
| **Derived fields** | Adopted | `@computed_field` instead of stored + validated redundant fields. |
| **String validation** | Adopted | `NotBlankStr` type from `core.types` for all identifier/name fields, eliminating per-model validator boilerplate. |
| **Shared field groups** | Adopted | Common field sets extracted into base models (e.g., `_SpendingTotals`) to prevent duplication. |
| **Event constants** | Adopted | Per-domain submodules under `observability/events/`. Direct imports: `from synthorg.observability.events.<domain> import CONSTANT`. |
| **Parallel tool execution** | Adopted | `asyncio.TaskGroup` in `ToolInvoker.invoke_all` with optional `max_concurrency` semaphore and structured error collection. |
| **Parallel agent execution** | Adopted | `ParallelExecutor` with `TaskGroup` + `Semaphore` concurrency limits, `ResourceLock` for exclusive file-path claims, progress tracking, and shutdown awareness. |
| **Tool permission checking** | Adopted | Category-level gating based on `ToolAccessLevel`. Priority-based resolution: denied list, allowed list, level categories, then deny. |
| **Tool sandboxing** | Adopted | Layered: in-process path validation for file system tools, `SubprocessSandbox` for git tools, `DockerSandbox` planned for code execution. |
| **Crash recovery** | Adopted | Pluggable `RecoveryStrategy` protocol. Current: `FailAndReassignStrategy`. Planned: `CheckpointStrategy` for per-turn state persistence. |
| **Personality compatibility** | Adopted | Weighted composite scoring: 60% Big Five similarity, 20% collaboration alignment, 20% conflict approach. |
| **Agent behavior testing** | Planned | Scripted `FakeProvider` for unit tests; behavioral outcome assertions for integration tests. |
| **LLM call analytics** | Adopted | Proxy metrics (`turns_per_task`, `tokens_per_task`) and data models for call categorization, coordination metrics, and orchestration ratio. |
| **Cost tiers and quota tracking** | Adopted | Configurable `CostTierDefinition` with merge/override semantics. `QuotaTracker` enforces per-provider request/token quotas with window-based rotation. |
| **Shared org memory** | Adopted | `OrgMemoryBackend` protocol with `HybridPromptRetrievalBackend`. Seniority-based write access control. Core policies in system prompts; extended facts retrieved on demand. |
| **Memory consolidation** | Adopted | `ConsolidationStrategy` protocol with deduplication + summarization. `RetentionEnforcer` for age-based cleanup. `ArchivalStore` for cold storage. |
| **State coordination** | Adopted | Centralized single-writer `TaskEngine` with `asyncio.Queue`. Agents submit requests; engine applies `model_validate` / `with_transition` sequentially and publishes snapshots. |
| **Workspace isolation** | Adopted | Pluggable `WorkspaceIsolationStrategy` protocol. Default: git worktrees with sequential merge on completion. |
| **Graceful shutdown** | Adopted | Pluggable `ShutdownStrategy` protocol with cooperative 30-second timeout. Force-cancel after timeout with `INTERRUPTED` status. |
| **Template inheritance** | Adopted | `extends` field triggers parent resolution at render time with deep merge by field type. Circular chain detection included. |
| **Communication foundation** | Adopted | `MessageBus` protocol with pull-model `receive()`, `MessageDispatcher` for concurrent handler routing, `AgentMessenger` per-agent facade. |
| **Delegation and loop prevention** | Adopted | `DelegationGuard` orchestrates five mechanisms (ancestry, depth, dedup, rate limit, circuit breaker) in sequence with short-circuit on first rejection. |
| **Task assignment** | Adopted | `TaskAssignmentStrategy` protocol with six strategies: Manual, RoleBased, LoadBalanced, CostOptimized, Hierarchical, and Auction. |
| **Conflict resolution** | Adopted | `ConflictResolver` protocol with four strategies: Authority, Debate, Human Escalation, and Hybrid. |
| **Pydantic alias for YAML directives** | Adopted | `Field(alias="_remove")` in `TemplateAgentConfig` -- YAML uses `_remove: true`, Python accesses `agent.remove`. Keeps YAML human-readable while avoiding leading-underscore attributes. |
