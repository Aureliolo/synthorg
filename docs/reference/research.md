# Research & Prior Art

## Existing Frameworks Comparison

The following table compares major multi-agent frameworks that informed the design of SynthOrg. Star counts and version information as of March 2026.

| Framework | Stars | Architecture | Roles | Models | Memory | Custom Roles | Production Ready |
|-----------|-------|-------------|-------|--------|--------|-------------|-----------------|
| **MetaGPT** | 64.5k | SOP-driven pipeline | PM, Architect, Engineer, QA | OpenAI, Ollama, Groq, Azure | Limited | Partial | Research; MGX commercial |
| **ChatDev 2.0** | 31.2k | Zero-code visual workflows | CEO, CTO, Programmer, Tester, Designer | Multiple via config | Limited | Yes (YAML) | Improving (v2.0 Jan 2026) |
| **CrewAI** | ~50k+ | Role-based crews + flows | Fully custom | Multi-provider | Basic (crew memory) | Yes | Yes (100k+ developers) |
| **AutoGen** | ~40k+ | Conversation-driven async | Custom agents | OpenAI primary, others | Session-based | Yes | Transitioning to MS Agent Framework |
| **LangGraph** | Large | Graph-based DAG | Custom nodes | LangChain ecosystem | Stateful graphs | Yes (nodes) | Yes |
| **Smolagents** | Growing | Code-centric minimal | Code agent | HuggingFace ecosystem | Minimal | Yes | Rapid prototyping |

---

## What Exists vs What SynthOrg Provides

| Feature | MetaGPT | ChatDev | CrewAI | **SynthOrg** |
|---------|---------|---------|--------|--------------|
| Full company simulation | Partial | Partial | No | **Yes -- complete** |
| HR (hiring/firing) | No | No | No | **Yes** |
| Budget management (CFO) | No | No | No | **Yes** |
| Persistent agent memory | No | No | Basic | **Yes (Mem0 initial, custom stack future)** |
| Agent personalities | Basic | Basic | Basic | **Deep -- traits, styles, evolution** |
| Dynamic team scaling | No | No | Manual | **Yes -- auto + manual** |
| Multiple company types | No | No | Manual | **Yes -- templates + builder** |
| Security ops agent | No | No | No | **Yes** |
| Configurable autonomy | No | No | Limited | **Yes -- full spectrum** |
| Local + cloud providers | Partial | Partial | Partial | **Yes -- unified abstraction (LiteLLM)** |
| Cost tracking per agent | No | No | No | **Yes -- full budget system** |
| Progressive trust | No | No | No | **Yes** |
| Performance metrics | No | No | No | **Yes** |
| MCP tool integration | No | No | Partial | **Yes** |
| A2A protocol support | No | No | No | **Planned** |
| Community marketplace | MGX (commercial) | No | No | **Planned** |

---

## Agent Scaling Research

[Kim et al., "Towards a Science of Scaling Agent Systems" (2025)](https://arxiv.org/abs/2512.08296) conducted 180 controlled experiments across 3 LLM families and 4 agentic benchmarks with 5 coordination topologies. Key findings that informed the SynthOrg design:

- **Task decomposability is the primary predictor** of multi-agent success. Parallelizable tasks gain up to +81%, while sequential tasks degrade -39% to -70% under all multi-agent system variants. This directly informs the task decomposition subsystem.
- **Coordination metrics suite** (efficiency, overhead, error amplification, message density, redundancy) explains 52.4% of performance variance (R^2=0.524). Adopted in the LLM call analytics system.
- **Tiered coordination overhead** (`O%`): optimal band is 200--300%, with over-coordination above 400%. Informs the orchestration ratio metric interpretation.
- **Error taxonomy** (logical contradiction, numerical drift, context omission, coordination failure) with architecture-specific patterns. Adopted as opt-in classification in the coordination error classification pipeline.
- **Auto topology selection** achieves 87% accuracy from measurable task properties. Informs the auto topology selector in the task routing subsystem.
- **Centralized verification** contains error amplification to 4.4x vs 17.2x for independent agents.

!!! note "Applicability"

    The paper tested identical agents on individual tasks. SynthOrg uses role-differentiated agents in an organizational structure. Thresholds (e.g., 45% capability ceiling, 3--4 agent sweet spot) are directional and will be validated empirically in this context.

---

## Build vs Fork Decision

**Decision: Build from scratch, leverage libraries.**

No existing framework covers even 50% of SynthOrg's requirements. The core differentiators -- HR, budget management, security ops, deep personalities, progressive trust -- do not exist in any framework. Forking MetaGPT or CrewAI would mean fighting their architecture while adding these features.

The "company simulation" layer on top is the unique value and must be purpose-built.

### Libraries Leveraged

Rather than forking a framework, SynthOrg builds on battle-tested libraries:

| Library | Role |
|---------|------|
| **LiteLLM** | Provider abstraction (100+ providers, unified API) |
| **Mem0** | Agent memory (initial backend; custom stack future) |
| **Litestar** | API layer (see [Tech Stack](../architecture/tech-stack.md#why-litestar-over-fastapi) for rationale) |
| **MCP** | Tool integration standard |
| **Pydantic** | Config validation and data models |
| **Vue 3** | Web UI framework (see [Tech Stack](../architecture/tech-stack.md)) |

---

## Sources

- [MetaGPT](https://github.com/FoundationAgents/MetaGPT) -- Multi-agent SOP framework (64.5k stars)
- [ChatDev 2.0](https://github.com/openbmb/ChatDev) -- Zero-code multi-agent platform (31.2k stars)
- [CrewAI](https://github.com/crewAIInc/crewAI) -- Role-based agent collaboration framework
- [AutoGen](https://github.com/microsoft/autogen) -- Microsoft async multi-agent framework
- [LiteLLM](https://github.com/BerriAI/litellm) -- Unified LLM API gateway (100+ providers)
- [Mem0](https://github.com/mem0ai/mem0) -- Universal memory layer for AI agents
- [A2A Protocol](https://github.com/a2aproject/A2A) -- Agent-to-Agent protocol (Linux Foundation)
- [MCP Specification](https://modelcontextprotocol.io/specification/2025-11-25) -- Model Context Protocol
- [Langfuse Agent Comparison](https://langfuse.com/blog/2025-03-19-ai-agent-comparison) -- Framework comparison
- [Confluent Event-Driven Patterns](https://www.confluent.io/blog/event-driven-multi-agent-systems/) -- Multi-agent architecture patterns
- [Microsoft Multi-Agent Reference Architecture](https://microsoft.github.io/multi-agent-reference-architecture/) -- Enterprise patterns
- [OpenRouter](https://openrouter.ai/) -- Multi-model API gateway
- [Kim et al., "Towards a Science of Scaling Agent Systems" (2025)](https://arxiv.org/abs/2512.08296) -- Empirical agent scaling research (180 experiments, 3 LLM families)
- Cemri et al., "Multi-Agent System Failure Taxonomy (MAST)" (2025) -- MAS coordination error classification
- [Gloaguen et al., "Evaluating AGENTS.md" (2026)](https://arxiv.org/abs/2602.11988) -- Context files reduce success rates; non-inferable-only principle for system prompts
