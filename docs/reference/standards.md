# Industry Standards

SynthOrg aligns with emerging industry standards for agent-to-tool and agent-to-agent communication. This page describes the standards used and how they integrate into the framework.

## Standards Overview

| Standard | Owner | Purpose | SynthOrg Usage |
|----------|-------|---------|----------------|
| **MCP** (Model Context Protocol) | Anthropic, now Linux Foundation (AAIF) | Standardized LLM-to-tool integration | Tool system backbone |
| **A2A** (Agent-to-Agent Protocol) | Google, now Linux Foundation | Agent-to-agent communication | Future agent interoperability |
| **OpenAI API format** | OpenAI (de facto standard) | LLM API interface | Via provider abstraction layer (LiteLLM) |

---

## Model Context Protocol (MCP)

MCP provides a standardized interface for LLM agents to discover and invoke external tools. SynthOrg uses the official MCP SDK (`mcp` Python package) as the backbone of its tool integration system.

The MCP bridge subsystem (`tools/mcp/`) connects to MCP-compliant tool servers, discovers available tools at runtime, and exposes them through the same `BaseTool` interface used by built-in tools. This means agents interact with MCP tools identically to native tools -- through the `ToolInvoker` with the same permission checking and sandboxing applied.

Key integration points:

- **`MCPToolFactory`** connects to configured MCP servers in parallel and creates `MCPBridgeTool` wrappers
- **`MCPBridgeTool`** implements `BaseTool`, mapping MCP tool schemas to the internal tool interface
- **Result caching** with configurable TTL and LRU eviction reduces redundant tool calls

---

## Agent-to-Agent Protocol (A2A)

The A2A protocol defines how autonomous agents discover each other's capabilities and delegate tasks across organizational boundaries. SynthOrg's communication layer is designed to be A2A-compatible for future inter-agent interoperability.

The framework currently uses an internal message bus for inter-agent communication within a single organization. A2A support is planned for scenarios where multiple synthetic organizations need to collaborate, or where SynthOrg agents need to interact with agents from other frameworks.

---

## OpenAI API Format

The OpenAI chat completions API format has become the de facto standard for LLM interactions. SynthOrg accesses this format through LiteLLM, which provides a unified interface across 100+ providers that all speak the OpenAI API format (or are translated to it).

This means SynthOrg is not coupled to any single LLM provider. Switching between providers is a configuration change, not a code change. The provider abstraction layer handles request/response mapping, cost tracking, retries, fallbacks, and rate limiting transparently.
