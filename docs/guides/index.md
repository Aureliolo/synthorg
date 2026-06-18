---
title: Guides
description: Practical how-to guides for configuring and operating SynthOrg.
---

# Guides

Practical guides for configuring, operating, and extending your synthetic organisation. Each guide is self-contained with a clear goal. Start with the Quickstart Tutorial if you are new to SynthOrg. Note: the platform, configuration surface, and the agent runtime that executes work are available today, exercised by deterministic e2e harnesses with a scripted provider; operator-facing maturity and real-provider acceptance are in active development (see the [Roadmap](../roadmap/index.md)).

!!! tip "New to SynthOrg?"

    Start with the [Quickstart Tutorial](quickstart.md) to stand up the platform and configure a company in about 5 minutes.

---

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Quickstart Tutorial**

    ---

    Install the CLI, pick a template, and stand up the platform in about 5 minutes.

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

-   :material-file-cog:{ .lg .middle } **Company Configuration**

    ---

    Complete YAML reference for every configuration option.

    [:octicons-arrow-right-24: Configuration Reference](company-config.md)

-   :material-account-group:{ .lg .middle } **Agent Roles & Hierarchy**

    ---

    Define agents, seniority levels, personality, departments, and reporting lines.

    [:octicons-arrow-right-24: Agents](agents.md)

-   :material-currency-usd:{ .lg .middle } **Budget & Cost Control**

    ---

    Per-agent budgets, alert thresholds, auto-downgrade, and spending reports.

    [:octicons-arrow-right-24: Budget](budget.md)

-   :material-shield-lock:{ .lg .middle } **Security & Trust Policies**

    ---

    Trust strategies, autonomy levels, approval gates, and custom security policies.

    [:octicons-arrow-right-24: Security](security.md)

-   :material-puzzle:{ .lg .middle } **Tool Integration (MCP)**

    ---

    Connect external tools via MCP servers with stdio or HTTP transports.

    [:octicons-arrow-right-24: MCP Tools](mcp-tools.md)

-   :material-docker:{ .lg .middle } **Deployment (Docker)**

    ---

    Run SynthOrg in production with Docker, hardening, and image verification.

    [:octicons-arrow-right-24: Deployment](deployment.md)

-   :material-brain:{ .lg .middle } **Memory Configuration**

    ---

    Agent memory, shared org memory, retrieval pipeline, and consolidation.

    [:octicons-arrow-right-24: Memory](memory.md)

-   :material-file-chart:{ .lg .middle } **Centralised Logging**

    ---

    Route structured logs to syslog, HTTP, OTLP, or external log aggregators.

    [:octicons-arrow-right-24: Centralised Logging](centralized-logging.md)

-   :material-cog-outline:{ .lg .middle } **Settings Reference**

    ---

    Resolve, view, and edit the <!--RS:settings_namespaces-->28<!--/RS--> settings namespaces at runtime.

    [:octicons-arrow-right-24: Settings Reference](settings-reference.md)

-   :material-bell-ring:{ .lg .middle } **Notifications & Events**

    ---

    Configure notification sinks and subscribe to WebSocket event channels.

    [:octicons-arrow-right-24: Notifications & Events](notifications-and-events.md)

-   :material-source-branch:{ .lg .middle } **Workflow API Tutorial**

    ---

    curl tutorials for creating, versioning, activating, and executing workflows.

    [:octicons-arrow-right-24: Workflow API](workflow-api.md)

-   :material-account-cog:{ .lg .middle } **Agent Management**

    ---

    Hire, fire, promote, and customise agents through the REST API.

    [:octicons-arrow-right-24: Agent Management](agent-management.md)

-   :material-web:{ .lg .middle } **Human Interaction & API**

    ---

    REST + WebSocket API surface, rate limiting, RFC 9457 errors, Web UI features.

    [:octicons-arrow-right-24: Human Interaction](human-interaction.md)

-   :material-source-pull:{ .lg .middle } **Contributing**

    ---

    Development workflow, testing, code style, and pull request process.

    [:octicons-arrow-right-24: Contributing](contributing.md)

-   :material-server-plus:{ .lg .middle } **Adding a Provider**

    ---

    Configure LLM providers, models, and connection secrets.

    [:octicons-arrow-right-24: Adding a Provider](adding-a-provider.md)

-   :material-check-decagram:{ .lg .middle } **Approval Workflow**

    ---

    Approve or reject parked agent actions through the API and dashboard.

    [:octicons-arrow-right-24: Approval Workflow](approval-workflow.md)

-   :material-lan-connect:{ .lg .middle } **A2A Federation**

    ---

    Federate agents across organisations with the agent-to-agent protocol.

    [:octicons-arrow-right-24: A2A Federation](a2a-federation.md)

-   :material-calendar-clock:{ .lg .middle } **Ceremony Scheduling Tuning**

    ---

    Tune sprint ceremonies, cadences, and scheduling strategies.

    [:octicons-arrow-right-24: Ceremony Scheduling](ceremony-scheduling-tuning.md)

-   :material-chart-pie:{ .lg .middle } **Cost Attribution**

    ---

    Attribute spend to agents, projects, and tasks for chargeback.

    [:octicons-arrow-right-24: Cost Attribution](cost-attribution.md)

-   :material-server-network:{ .lg .middle } **Custom MCP Server Development**

    ---

    Build your own MCP server to expose bespoke tools to agents.

    [:octicons-arrow-right-24: Custom MCP Server](custom-mcp-server-dev.md)

-   :material-cog-sync:{ .lg .middle } **Custom Rules & Meta-Loop**

    ---

    Author declarative signal rules that drive the self-improvement loop.

    [:octicons-arrow-right-24: Custom Rules](custom-rules-and-meta-loop.md)

-   :material-sort-variant:{ .lg .middle } **Dynamic Scoring**

    ---

    Configure candidate ranking and model-selection scoring strategies.

    [:octicons-arrow-right-24: Dynamic Scoring](dynamic-scoring.md)

-   :material-source-fork:{ .lg .middle } **Fork Setup**

    ---

    Stand up your own fork with CI environments and required secrets.

    [:octicons-arrow-right-24: Fork Setup](fork-setup.md)

-   :material-monitor-dashboard:{ .lg .middle } **Monitoring & Dashboards**

    ---

    Observe health, metrics, and live agent activity.

    [:octicons-arrow-right-24: Monitoring](monitoring.md)

-   :material-graph:{ .lg .middle } **Ontology Extension**

    ---

    Define entity types and extend the semantic ontology.

    [:octicons-arrow-right-24: Ontology Extension](ontology-extension.md)

-   :material-database-sync:{ .lg .middle } **Persistence Migrations**

    ---

    Manage schema revisions across the SQLite and Postgres backends.

    [:octicons-arrow-right-24: Persistence Migrations](persistence-migrations.md)

-   :material-api:{ .lg .middle } **REST API Examples**

    ---

    Copy-paste curl recipes for common REST API operations.

    [:octicons-arrow-right-24: REST API Examples](rest-api-examples.md)

-   :material-webhook:{ .lg .middle } **Webhook Management**

    ---

    Register, verify, and observe inbound and outbound webhooks.

    [:octicons-arrow-right-24: Webhook Management](webhook-management.md)

-   :material-cog-play:{ .lg .middle } **Workers & Background Tasks**

    ---

    Run the worker pool and the background task queue.

    [:octicons-arrow-right-24: Workers & Background Tasks](workers-and-background-tasks.md)

</div>
