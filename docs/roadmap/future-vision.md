# Future Vision

These features are not part of the MVP. They represent the longer-term direction for SynthOrg once the core framework is stable.

## Future Features

| Feature | Priority | Description |
|---------|----------|-------------|
| Plugin system | High | Third-party plugins for new tools, roles, and providers. |
| Multi-project support | High | Company handles multiple projects simultaneously. |
| Self-improving company | High | The AI company developing the AI company framework (meta). |
| Community marketplace | Medium | Share and download company templates, roles, and workflows. |
| Network hosting | Medium | Expose on LAN/internet with multi-user access. |
| Agent evolution | Medium | Agents improve over time based on feedback. |
| Benchmarking suite | Medium | Compare company configurations on standard tasks. |
| Visual workflow editor | Medium | Drag-and-drop workflow design in the Web UI. |
| Agent promotions (extended) | Medium | Advanced promotion features: peer review integration, multi-dimensional criteria weighting, team-wide calibration. Core promotion system is [implemented](../design/agents.md#promotions-demotions). |
| Reporting system | Medium | Weekly/monthly automated company reports. |
| Training mode | Medium | New agents learn from senior agents' past work. |
| Integration APIs | Medium | Connect to real Slack, GitHub, Jira, Linear. |
| Inter-company communication | Low | Two AI companies collaborating on a project. |
| Voice interface | Low | Talk to the AI company via voice. |
| Mobile app | Low | Monitor the company from a phone. |
| Client simulation | Low | AI "clients" that give requirements and review output. |
| Shift system | Low | Agents "work" in shifts, different agents for different hours. |

---

## Scaling Path

SynthOrg is designed to scale incrementally from a local single-process deployment to a fully hosted cloud platform.

```text
Phase 1: Local Single-Process
  └── Async runtime, embedded DB, in-memory bus, 1-10 agents

Phase 2: Local Multi-Process
  └── External message bus, production DB, sandboxed execution, 10-30 agents

Phase 3: Network/Server
  └── Full API, multi-user, distributed agents, 30-100 agents

Phase 4: Cloud/Hosted
  └── Container orchestration, horizontal scaling, marketplace, 100+ agents
```

Each phase builds on the previous one. The pluggable protocol interfaces throughout the codebase (persistence, memory, message bus, sandbox) are designed to make these transitions configuration changes rather than rewrites.
