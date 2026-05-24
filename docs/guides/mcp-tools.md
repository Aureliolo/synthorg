---
title: Tool Integration (MCP)
description: Connect external tools to SynthOrg via MCP servers.
---

# Tool Integration (MCP)

SynthOrg agents can use external tools via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). The MCP bridge connects to one or more MCP servers, discovers their tools, and makes them available to agents through the standard tool registry. All MCP tool invocations pass through the [SecOps security pipeline](security.md).

---

## MCP Configuration

MCP servers are configured under the `mcp` key in your company configuration:

```yaml
mcp:
  servers:
    - name: "filesystem"
      transport: stdio
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
      timeout_seconds: 30.0
```

The `mcp.servers` list contains one entry per MCP server. Server names must be unique.

---

## Transport Types

MCP supports two transport types for connecting to servers:

=== "stdio"

    Launches a local process and communicates via stdin/stdout. Best for tools that run alongside SynthOrg.

    ```yaml
    mcp:
      servers:
        - name: "filesystem"
          transport: stdio
          command: "npx"
          args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
          env:
            NODE_ENV: "production"
    ```

    | Field | Type | Default | Description |
    |-------|------|---------|-------------|
    | `command` | string | *(required for stdio)* | Command to launch the server |
    | `args` | list | `[]` | Command-line arguments |
    | `env` | dict | `{}` | Environment variables passed to the process |

=== "streamable_http"

    Connects to a remote server via HTTP. Best for shared or cloud-hosted tools.

    ```yaml
    mcp:
      servers:
        - name: "remote-api"
          transport: streamable_http
          url: "https://mcp.example.com/v1"
          headers:
            Authorization: "Bearer sk-..."
    ```

    | Field | Type | Default | Description |
    |-------|------|---------|-------------|
    | `url` | string | *(required for streamable_http)* | Server URL |
    | `headers` | dict | `{}` | HTTP headers (e.g. authentication) |

---

## Server Configuration Reference

Complete field reference for `MCPServerConfig`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | *(required)* | Unique server identifier |
| `transport` | string | *(required)* | `"stdio"` or `"streamable_http"` |
| `command` | string | `null` | Stdio server command |
| `args` | list | `[]` | Command-line arguments |
| `env` | dict | `{}` | Environment variables |
| `url` | string | `null` | HTTP server URL |
| `headers` | dict | `{}` | HTTP headers |
| `enabled_tools` | list | `null` | Tool allowlist (`null` = all tools) |
| `disabled_tools` | list | `[]` | Tool denylist |
| `timeout_seconds` | float | `30.0` | Timeout per tool invocation (max 600) |
| `connect_timeout_seconds` | float | `10.0` | Timeout for initial connection (max 120) |
| `result_cache_ttl_seconds` | float | `60.0` | TTL for cached tool results |
| `result_cache_max_size` | int | `256` | Maximum entries in result cache |
| `enabled` | bool | `true` | Whether the server is active |

---

## Tool Filtering

Control which tools from a server are available to agents:

### Allowlist

Set `enabled_tools` to only expose specific tools:

```yaml
mcp:
  servers:
    - name: "filesystem"
      transport: stdio
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
      enabled_tools:
        - "read_file"
        - "list_directory"
      # All other tools from this server are hidden
```

When `enabled_tools` is `null` (the default), all tools from the server are available.

### Denylist

Set `disabled_tools` to hide specific tools while allowing the rest:

```yaml
mcp:
  servers:
    - name: "filesystem"
      transport: stdio
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
      disabled_tools:
        - "delete_file"
        - "write_file"
      # All other tools from this server are available
```

!!! warning

    `enabled_tools` and `disabled_tools` must not overlap. If a tool name appears in both lists, a validation error is raised at config load time.

---

## Timeouts & Caching

### Timeouts

Two timeout values control server responsiveness:

- **`connect_timeout_seconds`** (default: 10s, max: 120s): how long to wait for the initial connection to the MCP server. Increase this for remote servers with slow startup.
- **`timeout_seconds`** (default: 30s, max: 600s): how long to wait for a single tool invocation to complete. Increase this for tools that perform long-running operations.

### Result Caching

Tool results are cached to avoid redundant invocations:

- **`result_cache_ttl_seconds`** (default: 60s): how long cached results remain valid. Set to `0` to disable caching for a server.
- **`result_cache_max_size`** (default: 256): maximum number of cached entries. Set to `0` to disable caching.

Caching is key-based (same tool name + same arguments = cache hit). Tune the TTL based on how often the underlying data changes.

---

## Multiple Servers

You can connect multiple MCP servers simultaneously. Each server is independent with its own transport, credentials, and tool filtering:

```yaml
mcp:
  servers:
    - name: "filesystem"
      transport: stdio
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
    - name: "github"
      transport: stdio
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_TOKEN: "ghp_..."
    - name: "remote-analytics"
      transport: streamable_http
      url: "https://analytics.example.com/mcp"
      headers:
        Authorization: "Bearer ..."
      timeout_seconds: 60.0
```

Disable a server without removing its configuration by setting `enabled: false`:

```yaml
mcp:
  servers:
    - name: "github"
      transport: stdio
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-github"]
      enabled: false  # temporarily disabled
```

---

## Security Considerations

MCP tools are treated like any other tool in SynthOrg:

- **All invocations pass through SecOps**: the rule engine evaluates tool calls against security policies, autonomy levels, and trust configuration
- **MCP tools are categorised as `mcp`** in the tool category taxonomy, which means they are gated by the agent's tool access level
- **Output scanning** applies to MCP tool results: secrets and PII are redacted according to the configured output scan policy
- **Audit logging** records every MCP tool invocation when `security.audit_enabled` is `true`

To restrict MCP tool access to specific agents, configure tool access levels and trust policies. See [Security & Trust Policies](security.md) for details.

---

## See Also

- [Company Configuration](company-config.md): full configuration reference
- [Security & Trust Policies](security.md): tool access levels and security policies
- [Design: Tools](../design/tools.md): tool architecture in the design spec
