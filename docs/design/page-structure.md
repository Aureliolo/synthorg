---
title: Page Structure & Information Architecture
description: Validated page list, navigation hierarchy, URL routing map, WebSocket subscriptions, and responsive scope for the web dashboard.
---

# Page Structure & Information Architecture

## Overview

This document defines the information architecture for the web dashboard. It was validated against the backend API surface (32 controllers, 9 WebSocket channels) and the design decisions from #762 (Mission Control direction, 4 differentiators) and #765 (Warm Ops identity).

**Guiding principle**: every page maps to a real backend domain with live data. No user-facing placeholder pages or "Coming Soon" stubs. ProjectController and ArtifactController have full persistence backends (#612) and dashboard pages (#946).

---

## Page List

### Primary Navigation

High-frequency destinations, always visible in the sidebar.

#### Dashboard (`/`)

Org overview: department health indicators (green/amber/red), recent activity widget, budget snapshot with sparkline, active task summary, agent status counts, approval badge count. The central "is the company healthy?" view.

**API endpoints**: `GET /analytics/overview`, `GET /analytics/forecast`, `GET /budget/config`, `GET /departments`, `GET /departments/{name}/health`, `GET /activities`
**WS channels**: `tasks`, `agents`, `budget`, `system`, `approvals` (all aggregated into health indicators and activity feed)

#### Org Chart (`/org`)

Living org visualization with real-time agent status. Two view modes toggled via toolbar:

- **Hierarchy view** (default): Dagre-based hierarchical layout with CEO, departments, teams, agents with status dots and health overlays. Supports drag-drop agent reassignment between departments (optimistic update with rollback on API failure, ARIA live announcements, drop zone highlight with accent border).
- **Communication view**: Force-directed layout (d3-force) showing inter-agent communication patterns. Edge thickness encodes message volume, animated dashes encode frequency. Data sourced from `GET /messages` with client-side aggregation. Smooth 400ms animated transition between views.

Click agent nodes to open Agent Detail panel.

"Edit Organisation" button enters form-based edit mode (`/org/edit`) with sub-tabs: General (name, autonomy level, monthly budget, communication pattern), Agents (card grid with add/edit/delete), Departments (card grid with CRUD; nested team editing via the Department Edit drawer with create/rename/delete/reorder teams using @dnd-kit; reporting/policies editing remains deferred). This is the former Company page merged into the Org Chart; same data domain, one destination.

**API endpoints**: `GET /company`, `GET /company/departments`, `GET /departments`, `GET /departments/{name}`, `GET /departments/{name}/health`, `GET /agents`, `GET /agents/{agent_id}`, `GET /messages` (communication view), `PATCH /agents/{agent_id}` (drag-drop reassignment in hierarchy view). Edit mode adds: `PATCH /company`, `POST /departments`, `PATCH /departments/{name}`, `DELETE /departments/{name}`, `POST /company/reorder-departments`, `POST /agents`, `DELETE /agents/{agent_id}`, `POST /departments/{name}/reorder-agents`, `POST /departments/{name}/teams`, `PATCH /departments/{name}/teams/{team_name}`, `DELETE /departments/{name}/teams/{team_name}`, `PATCH /departments/{name}/teams/reorder`.
**WS channels**: `agents` (status changes, hired/fired). Communication view uses REST polling for message data (not WS).

#### Task Board (`/tasks`)

Kanban view (default) and list view toggle. Filter by status, assignee, department. Task cards show title, assignee, status, priority. Click opens task detail with full context, state transition buttons, and "Coordinate" action (triggers multi-agent coordination via `/tasks/{id}/coordinate`).

Project filter dropdown available. Dedicated Projects page shipped (#946).

`POST /tasks` is a board-entry handoff: the controller routes the filing through `TaskBoardEntryAdapter` and returns HTTP 202 with `TaskBoardSubmissionResponse` (`correlation_id`, `title`, `project`, `status: "submitted"`). The spine creates the task inside its background intake phase; the board UI subscribes to the `tasks` WS channel and inserts the spine-created task on the matching `task.created` event correlated by `correlation_id`. Empty-company (no provider / no adapter) returns 409 `AgentRuntimeNotConfiguredError`. Board column moves remain pure status walks of the spine-created task.

**API endpoints**: `GET /tasks`, `GET /tasks/{id}`, `POST /tasks` (202), `PATCH /tasks/{id}`, `POST /tasks/{id}/transition`, `POST /tasks/{id}/cancel`, `DELETE /tasks/{id}`, `POST /tasks/{id}/coordinate`
**WS channels**: `tasks`

#### Budget (`/budget`)

P&L management dashboard, not a billing tab. Current period spend vs budget, per-agent cost breakdown, per-department rollups, trend lines, cost anomaly highlights. Forecast sub-view (`/budget/forecast`) shows projected spend trajectories from the analytics engine.

**API endpoints**: `GET /budget/config`, `GET /budget/records`, `GET /budget/agents/{id}`, `GET /analytics/overview`, `GET /analytics/trends`, `GET /analytics/forecast`
**WS channels**: `budget`

#### Approvals (`/approvals`)

Pending decisions queue: agents are blocked waiting for human action, so this is the highest-urgency page. Risk-level grouping with collapsible sections, urgency countdown indicators, batch select with approve/reject actions, detail drawer with approval timeline and metadata, filter bar with URL-synced state. Sidebar badge shows live pending count.

**API endpoints**: `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/approve`, `POST /approvals/{id}/reject`
**WS channels**: `approvals`

### Secondary Navigation

Lower-frequency destinations in a collapsible "Workspace" section.

#### Agents (`/agents`)

Agent profiles as card grid. Each card shows name, role, department, status dot, hire date. Filtering by department, level, status. Search by name/role. Sort by name, department, level, status, hire date. Click navigates to the Agent Detail page at `/agents/{agentId}`.

**Agent Detail page** (`/agents/{agentId}`): single scrollable page with these sections:

- **Identity header**: Large avatar, name, role, department badge, level badge, status with pulse, autonomy level badge, hire date
- **Prose insights**: 1-3 generated narrative sentences from performance data (e.g. "Success rate of 94% across 127 completed tasks")
- **Performance metrics**: 2x2 grid of MetricCards (tasks completed, avg completion time, success rate, cost per task) with sparklines
- **Tool badges**: Horizontal flex-wrap of permitted tools
- **Collaboration override**: Active collaboration-score override (score, applied by, reason, optional expiry) with a destructive Clear action gated to CEO / Manager, shown alongside the quality-score override panel
- **Career timeline**: Vertical timeline with coloured dots (hired=green, promoted=blue, demoted=yellow, fired=red)
- **Task history**: Gantt-style horizontal bars sorted by time, type-coloured, pulse on in-progress tasks, duration labels
- **Activity log**: Paginated chronological event list with type icons, descriptions, timestamps

**Deferred to future iteration**: Spending breakdown and tabbed layout (Access tab).

**API endpoints**: `GET /agents`, `GET /agents/{agent_id}`, `GET /agents/{agent_id}/performance`, `GET /agents/{agent_id}/activity`, `GET /agents/{agent_id}/history`, `GET /agents/{agent_id}/health`, `GET /agents/{agent_id}/collaboration/score`, `GET /agents/{agent_id}/collaboration/override`, `POST /agents/{agent_id}/collaboration/override`, `DELETE /agents/{agent_id}/collaboration/override`, `GET /agents/{agent_id}/autonomy`, `POST /agents/{agent_id}/autonomy`
**WS channels**: `agents`, `tasks` (detail page)

#### Projects (`/projects`)

Project list with card grid, search, and status filter. "Create Project" button opens a slide-in drawer with name, description, team (tag input), lead, deadline, and budget fields. Each card shows status badge, description, task count, budget, team size, and deadline.

Detail page (`/projects/{projectId}`) shows project header with status badge and key metrics, team section with avatar grid and lead badge, and a linked task list with status indicators.

Living-documentation page (`/projects/{projectId}/docs`, optional `/{slug}`) is the per-project wiki: a two-column layout with a doc-list sidebar (type-filter chips for status report / deliverable / knowledge note /
codebase analysis / run narrative) and a `DocViewer` that renders a `LivingDocument`'s typed blocks one renderer per block kind. Docs are written by agents (tools + MCP), git-versioned on the `synthorg/docs` branch, and chunked into the `PROJECT_DOC` memory namespace for retrieval. See [Living Documentation](living-documentation.md).

**Features**:

- **Card grid**: responsive 3/2/1 column layout with hover effects, stagger animation
- **Search**: client-side filtering by name, description, and ID
- **Status filter**: dropdown filtering by project status (planning, active, on_hold, completed, cancelled)
- **Create drawer**: 6-field form with validation, optimistic state update
- **Team section**: avatar grid with links to agent detail pages, lead badge
- **Task list**: linked tasks with status indicators and assignee display

**API endpoints**: `GET /projects`, `GET /projects/{id}`, `POST /projects`, `DELETE /projects/{id}`, `GET /projects/{id}/docs`, `GET /projects/{id}/docs/{slug}`, `GET /projects/{id}/docs/{slug}/history`, `GET /projects/{id}/docs/search`
**WS channels**: `projects` (emits `project.created`, `project.deleted`), `tasks`

#### Artifacts (`/artifacts`)

Artifact list with card grid, search, and filters for type, content type, agent, task, and project. Each card shows path, type badge, content type badge, file size, creation time, and creator.

Detail page (`/artifacts/{artifactId}`) shows metadata grid (type, size, content type, path, task, creator, project), inline content preview (text via CodeMirror with syntax highlighting, images as blob URLs), download button, and delete with confirmation dialog.

**Features**:

- **Card grid**: responsive 3/2/1 column layout with hover effects, stagger animation
- **Search**: client-side filtering by path, description, and ID
- **Type filter**: dropdown for artifact type (code, tests, documentation)
- **Content type filter**: dropdown for MIME type prefix (text, image, JSON, PDF, application)
- **Agent/task/project filters**: text inputs for filtering by creator, task ID, and project ID
- **Content preview**: text content in CodeMirror (JSON/YAML syntax highlighting), image content as inline preview, other types show "Preview not available" with download action
- **File size display**: human-readable size on cards and detail page, "No content" for 0 bytes
- **Download**: blob download with filename derived from artifact path
- **Delete**: confirmation dialog, toast notification, redirect to list

**API endpoints**: `GET /artifacts`, `GET /artifacts/{id}`, `POST /artifacts`, `DELETE /artifacts/{id}`, `GET /artifacts/{id}/content`, `PUT /artifacts/{id}/content`
**WS channels**: `artifacts`

#### Messages (`/messages`)

Channel-filtered message feed for inspecting agent-to-agent communications. Two-column layout: channel list sidebar on the left, main message feed on the right. This is an investigative tool: users examine delegation chains, audit coordination, debug inter-agent communication. Not a chat interface.

**Features**:

- **Channel sidebar**: grouped by type (topic/direct/broadcast), unread badge per channel, active channel highlight
- **Message feed**: sender avatar + name, message type badge, priority indicator (high/urgent), relative timestamps
- **Timestamp grouping**: messages grouped by date with "Today"/"Yesterday"/date dividers
- **Threading**: messages with the same `task_id` grouped visually with expand/collapse
- **Client-side filtering**: type, priority, and text search with URL-synced filter pills
- **Detail drawer**: right-side panel showing full message metadata (channel, sender, task/project links, token usage, cost, extra key-value pairs, attachments)
- **Real-time**: WebSocket-driven new message arrival with auto-scroll-to-bottom (when user is near bottom)
- **Pagination**: "Load earlier messages" button for fetching older messages

**URL params**: `?channel={name}`, `?type={messageType}`, `?priority={level}`, `?search={query}`, `?message={id}` (detail drawer). All filter params are optional and combinable independently; `channel` is not required for type/priority/search filters.

**API endpoints**: `GET /messages`, `GET /messages/channels`
**WS channels**: `messages`

#### Meetings (`/meetings`)

Meeting history list with status/type filters. Click opens meeting detail (`/meetings/{id}`) with transcript and outcomes. "Trigger Meeting" action creates event-based meetings.

**API endpoints**: `GET /meetings`, `GET /meetings/{id}`, `POST /meetings/trigger`
**WS channels**: `meetings`

#### Providers (`/providers`)

LLM provider management. CRUD cards for configured providers with health status display (up/degraded/down/unknown) and 24-hour health metrics (average response time, error rate percentage, call count, total tokens, cost). Connection test button. Preset-based creation flow with subscription auth support requiring ToS acceptance for applicable providers. Model auto-discovery with capability badges (tools, vision, streaming) per model. Provider list supports filtering and sorting by health status, name, and model count. Provider detail/edit at `/providers/{name}`.

No WebSocket subscription; provider changes are low-frequency admin operations. TanStack Query polling is sufficient.

**API endpoints**: `GET /providers`, `GET /providers/{name}`, `GET /providers/{name}/models`, `GET /providers/{name}/health`, `POST /providers`, `PUT /providers/{name}`, `DELETE /providers/{name}`, `POST /providers/{name}/test`, `GET /providers/presets`, `POST /providers/from-preset`, `POST /providers/{name}/discover-models`, `POST /providers/probe-local`, `GET /providers/discovery-policy`, `POST /providers/discovery-policy/entries`, `POST /providers/discovery-policy/remove-entry`, `POST /providers/{name}/models` (manual add), `POST /providers/{name}/models/sync` (bulk sync), `POST /providers/{name}/credentials/rotate`, `GET /providers/{name}/rate-limits`, `PATCH /providers/{name}/rate-limits`, `GET /providers/presets/{preset_name}/override`, `PATCH /providers/presets/{preset_name}/override`, `DELETE /providers/presets/{preset_name}/override`, `GET /providers/{name}/audit`

#### Workflows (`/workflows`)

Workflow definition list with card grid, search, and type filter. "Create Workflow" button opens a creation drawer with blank or blueprint picker mode (5 starter blueprints). Each card shows name, description, node count, edge count, creation time, and last-modified time. Actions per card: duplicate, delete (with confirmation dialog). Click navigates to the Workflow Editor at `/workflows/editor?id={workflowId}`.

**API endpoints**: `GET /workflows`, `POST /workflows`, `DELETE /workflows/{id}`, `GET /workflows/blueprints`, `POST /workflows/from-blueprint`
**WS channels**: (none)

#### Workflow Editor (`/workflows/editor`)

Visual workflow designer: a DAG-based editor for creating and editing workflow definitions that orchestrate multi-step agent pipelines. Operators build workflows by placing nodes on a canvas, connecting them with edges, and configuring each node's properties via a side drawer.

**Key features**:

- **8 node types**: start, end, task, agent_assignment, conditional, parallel_split, parallel_join, subworkflow
- **4 edge types**: sequential, conditional_true, conditional_false, parallel_branch
- **Undo/redo**: full history stack for node/edge additions, deletions, moves, and config changes
- **YAML preview**: live read-only YAML export of the current workflow graph
- **Bidirectional YAML editing**: toggle between visual and YAML editor modes; parse YAML back into the visual graph with inline error/warning display and position preservation
- **Validation**: client-side structural validation with inline error display
- **Minimap**: pannable/zoomable overview of the full graph for navigation in large workflows
- **Copy/paste**: clipboard operations for selected node groups with ID remapping and internal edge preservation
- **Condition builder**: structured expression editor (Builder mode with field/operator/value rows and AND/OR/NOT logical operators, Advanced mode with free-text) for conditional nodes
- **Workflow selector**: switch between saved workflow definitions from the editor toolbar
- **Multi-workflow support**: "Save as New" duplicate action, quick-switch between workflows

- **Version history**: slide-in drawer listing all saved versions with compare (diff viewer) and restore (rollback) actions per entry
- **Diff viewer**: modal overlay showing node changes, edge changes, and metadata changes between any two versions
- **Rollback**: restore a previous version's content as a new version (no history lost)

No WebSocket subscription; workflow definitions are persisted via REST and do not require real-time collaboration.

**API endpoints**: `GET /workflows`, `POST /workflows`, `GET /workflows/{id}`, `PATCH /workflows/{id}`, `DELETE /workflows/{id}`, `POST /workflows/{id}/validate`, `POST /workflows/{id}/export`, `GET /workflows/{id}/versions`, `GET /workflows/{id}/versions/{version_num}`, `GET /workflows/{id}/diff?from_version=N&to_version=M`, `POST /workflows/{id}/rollback`
**WS channels**: (none)

#### Subworkflows (`/subworkflows`)

Registry listing of reusable subworkflow components. Card grid with search, I/O signature summary (input count / output count), version count, and latest version badge. Click opens a detail drawer showing version history, parent references, and delete action (blocked when parents pin the version).

**API endpoints**: `GET /subworkflows`, `GET /subworkflows/search?q=`, `GET /subworkflows/{id}/versions`, `GET /subworkflows/{id}/versions/{version}`, `GET /subworkflows/{id}/versions/{version}/parents`, `POST /subworkflows`, `DELETE /subworkflows/{id}/versions/{version}`
**WS channels**: (none; refreshes via polling, 30s)

#### Settings (`/settings`)

Configuration for 7 namespaces: api, memory, budget, security, coordination, observability, backup. Navigation uses a horizontal **namespace tab bar** across the top of the settings page, with each namespace as a clickable tab. Within each namespace, settings are displayed in a single-column layout with sub-group headings, basic/advanced mode, GUI/Code edit toggle (JSON/YAML within Code mode). Each namespace is URL-addressable (`/settings/{namespace}`) for deep linking from other pages (e.g. Dashboard budget warning links to `/settings/budget`).

The **Code edit mode** uses a **split-pane diff editor view**: the left pane shows the current persisted configuration (read-only), and the right pane is an editable CodeMirror editor for composing changes. This allows operators to see exactly what will change before saving.

Enabling advanced mode for the first time shows a confirmation dialog warning about misconfiguration risk. The warning is deduplicated per session via `sessionStorage`. Advanced mode preference persists in `localStorage`.

Dependency indicators are driven by a frontend-maintained `SETTING_DEPENDENCIES` map in `web/src/utils/constants.ts` (not by backend schema). When a controller setting is disabled, its dependent settings display in a muted state.

The **observability namespace** includes a dedicated **Sinks** sub-page (`/settings/observability/sinks`) for managing log sink configuration. The sinks page displays all active sinks (console and file) as cards showing identifier, log level, format, rotation policy, and routing prefixes. Operators can edit sink overrides and define custom sinks with a test-before-save workflow.

The **coordination namespace** includes a dedicated **Ceremony Policy** sub-page (`/settings/coordination/ceremony-policy`) for managing ceremony scheduling configuration. The page displays strategy selection (8 strategy types with descriptions and velocity unit indicator), velocity-calculator selection (paired to strategy via defaults), strategy-specific config panels, auto-transition toggle and threshold, department overrides with inherit/override toggles, per-ceremony overrides, and a strategy change warning banner. A resolved-policy view (populated from `GET /ceremony-policy/resolved`) annotates each field with a `PolicySourceBadge` showing the origin level (project, department, or default). Department overrides are read and written through `GET /departments/{name}/ceremony-policy` and `PUT /departments/{name}/ceremony-policy`, which store data in the `dept_ceremony_policies` JSON setting and resolve against the project-level policy.

The **memory namespace** includes a dedicated **Fine-Tuning** sub-page (`/settings/memory/fine-tuning`) for managing the domain-specific embedding fine-tuning pipeline. The page displays pipeline status (5-stage stepper with live progress bar), run history, preflight validation (dependencies, GPU, documents, disk space), and controls for starting/cancelling fine-tuning runs with optional advanced parameter overrides (epochs, learning rate, batch size). A **Checkpoints** section lists all fine-tuned model checkpoints with evaluation metrics (NDCG@10, Recall@10), deploy/rollback/delete actions (deploy activates the checkpoint and updates embedder settings; rollback restores the pre-deployment backup config; delete is rejected for the active checkpoint), and an active-checkpoint indicator. All checkpoint actions require CEO or SYSTEM role.

The **backup namespace** covers backup configuration settings (schedule, retention, path) and links to a dedicated **Admin Backups** page at `/admin/backups` (a standalone admin route, reached via a Settings action card from the backup namespace). That page surfaces the full backup lifecycle: create, cursor-paginated list, restore (with a restart-required notice), and delete.

System-managed settings (e.g. `api/setup_complete`) are hidden from the GUI. Environment-sourced settings display as read-only.

**API endpoints**: `GET /settings/_schema`, `GET /settings/_schema/{ns}`, `GET /settings`, `GET /settings/{ns}`, `GET /settings/{ns}/{key}`, `PUT /settings/{ns}/{key}`, `DELETE /settings/{ns}/{key}`, `GET /settings/observability/sinks`, `POST /settings/observability/sinks/_test`, `GET /settings/security/export`, `POST /settings/security/import`, `GET /security/audit`, `GET /coordination/metrics`, `GET /ceremony-policy`, `GET /ceremony-policy/resolved?department=`, `GET /ceremony-policy/active`, `GET /departments/{name}/ceremony-policy`, `PUT /departments/{name}/ceremony-policy`, `DELETE /departments/{name}/ceremony-policy`, `POST /admin/backups`, `GET /admin/backups`, `GET /admin/backups/{id}`, `DELETE /admin/backups/{id}`, `POST /admin/backups/restore`
**WS channels**: `system` (restart-required notifications)

#### Documentation (`/docs/`)

Served as static MkDocs HTML by Caddy, not a React page. The `/docs/` Caddy handle block serves pre-built documentation directly, bypassing the SPA's `try_files` fallback. The sidebar "Docs" link renders a plain `<a href>` (full-page navigation) instead of a React Router `<NavLink>`. MkDocs Material's own search, navigation, and dark mode function independently of the React app. Theme colours are customised via `docs/overrides/extra.css` to match the dashboard design system.

**API endpoints**: (none; static HTML served by Caddy)
**WS channels**: (none)

### Standalone Pages

Not in sidebar navigation.

#### Login (`/login`)

Full-page authentication. JWT-based. On success, redirects to `/` (Dashboard) or `/setup` if setup is not complete (based on `GET /setup/status`).

**API endpoints**: `POST /auth/setup`, `POST /auth/login`, `GET /setup/status`, `GET /auth/sessions`, `DELETE /auth/sessions/{session_id}`, `POST /auth/logout`

#### Setup Wizard (`/setup`)

Multi-step first-run flow. After account creation (conditional), a mode selection gate asks the user to choose **Guided Setup** (recommended, full wizard) or **Quick Setup** (minimal: provider + company name, configure rest later in Settings). Providers are configured before company creation and agents so model assignment is available downstream. Each step is URL-addressable (`/setup/{step}`). The mode selection step is hidden from the progress bar. Redirects to `/` if setup is already complete.

| Step | Route | Guided | Quick |
|------|-------|--------|-------|
| Account | `/setup/account` | conditional | conditional |
| Mode selection | `/setup/mode` | yes (hidden from progress) | yes (hidden from progress) |
| Template | `/setup/template` | yes | no |
| Providers | `/setup/providers` | yes | yes |
| Company | `/setup/company` | yes | yes |
| Agents | `/setup/agents` | yes | no |
| Theme | `/setup/theme` | yes | no |
| Complete | `/setup/complete` | yes | yes |

**Provider step layout** (`web/src/pages/setup/ProvidersStep.tsx`): a three-section picker reused on both the wizard and the Settings → Providers page. (a) **Cloud providers** -- a logo-and-name grid for hosted providers; click a card to open the credential form pre-filled with that preset. (b) **Detected on this machine** -- only renders when an auto-detect probe found a reachable local server; rows include the URL, model count, and `[Add local]` / `[Add cloud]` buttons (the cloud variant is offered when a local preset has a hosted counterpart, e.g. local Ollama → Ollama Cloud). The probe is a single batch call to `POST /providers/probe-local` issued once on mount, with a manual rescan button. (c) **Configure manually** -- opens the credential form in custom-endpoint mode. The "Detected" section is hidden entirely when nothing was detected; vLLM is intentionally omitted from auto-detect because its default port (8000) collides with the SynthOrg backend.

**API endpoints**: `GET /setup/status`, `GET /setup/templates`, `POST /setup/company`, `POST /setup/agent`, `GET /setup/agents`, `PUT /setup/agents/{agent_index}/name`, `PUT /setup/agents/{agent_index}/model`, `PUT /setup/agents/{agent_index}/personality`, `POST /setup/agents/{agent_index}/randomize-name`, `GET /setup/personality-presets`, `GET /setup/name-locales/available`, `GET /setup/name-locales`, `PUT /setup/name-locales`, `POST /setup/complete`

### Overlays

Not pages; triggered by user interaction, rendered over current page.

#### Command Palette

**Trigger**: Cmd+K (macOS) / Ctrl+K (Windows/Linux)
Global search overlay: navigate to any page, search agents by name, search tasks, jump to settings namespaces. Built with `cmdk-base` (cmdk port on Base UI Dialog).

#### Notifications Panel

**Trigger**: Bell icon in sidebar bottom + unread badge
Slide-in drawer aggregating system notifications: budget alerts, approval arrivals, agent status changes, system errors. Sources from WS `system`, `approvals`, and `budget` channels.

**Category taxonomy**: 17 notification categories with routing defaults and severity mappings:

| Category | Default Route | Severity |
|----------|--------------|----------|
| `approvals.pending` | toast + drawer + browser | warning |
| `approvals.expiring` | toast + drawer | warning |
| `approvals.decided` | drawer only | info |
| `budget.threshold` | toast + drawer | warning |
| `budget.exhausted` | toast + drawer + browser | critical |
| `system.error` | toast + drawer | error |
| `system.restart_required` | toast + drawer | warning |
| `system.shutdown` | toast + drawer + browser | critical |
| `agents.personality_trimmed` | toast only | info |
| `agents.hired` | drawer only | info |
| `agents.fired` | drawer only | info |
| `tasks.failed` | toast + drawer | error |
| `tasks.blocked` | drawer only | warning |
| `providers.down` | toast + drawer + browser | error |
| `providers.degraded` | toast + drawer | warning |
| `connection.lost` | toast only | critical |
| `connection.exhausted` | toast + drawer + browser | error |

"toast + drawer" categories trigger a brief toast notification in addition to appearing in the drawer. "drawer only" categories appear silently in the drawer and increment the unread badge. Categories with "browser" also fire a browser Notification when the tab is backgrounded. Users can override routing defaults per category via the Notifications section in Settings (per-category toggles for toast, drawer, and browser routes).

#### Agent Detail Page

**Trigger**: Click agent in Agents list, Org Chart node, or any agent name link
Navigates to a dedicated full page at `/agents/{agentId}`. Single scrollable page with sections: Identity header, Prose insights, Performance metrics, Tool badges, Collaboration override, Career timeline, Task history, Activity log. See the Agents section above for the full layout description.

---

## Navigation Hierarchy

Sidebar layout (220px expanded, 56px icon rail):

- **Brand**: Logo / brand mark
- **Primary**:
  - Dashboard, `LayoutDashboard`, `/`
  - Org Chart, `GitBranch`, `/org`
  - Roles, `Briefcase`, `/roles`
  - Task Board, `KanbanSquare`, `/tasks`
  - Budget, `DollarSign`, `/budget` (amber dot when >85% spent)
  - Mission Control, `Radio`, `/mission-control` (live cockpit, steering, flight recorder)
  - Approvals, `ShieldCheck`, `/approvals` (badge: pending count)
- **Workspace** (collapsible label):
  - Agents, `Users`, `/agents`
  - Projects, `FolderKanban`, `/projects`
  - Workflows, `Workflow`, `/workflows`
  - Subworkflows, `Layers`, `/subworkflows`
  - Artifacts, `Package`, `/artifacts`
  - Messages, `MessageSquare`, `/messages` (badge: unread count)
  - Meetings, `Video`, `/meetings`
  - Providers, `Cpu`, `/providers`
  - Docs, `BookOpen`, `/docs/` (external; static HTML, not SPA)
  - Fine-Tuning, `Sparkles`, `/settings/memory/fine-tuning`
  - Settings, `Settings`, `/settings`
- **Bottom**:
  - Collapse toggle
  - Notifications bell + badge
  - `Cmd+K` hint
  - Connection status dot from `/health`
  - User avatar / role badge / logout

**Icon source**: Lucide React (already a project dependency).

**Visual separators**: Thin border lines between Primary, Workspace, and Bottom sections. No section headers for Primary; items speak for themselves. "Workspace" label for secondary section, hidden when sidebar is collapsed to icon rail.

**Badge behaviours**:

- **Approvals**: Live count of pending approvals from WS `approvals` channel. Red badge. Disappears at zero.
- **Messages**: Unread message count from WS `messages` channel. Muted badge. Disappears at zero.
- **Budget**: Amber dot (no number) when budget exceeds 85% threshold. Source: WS `budget` channel alerts.
- **Notifications bell**: Aggregate unread count from system + approvals + budget alerts. Disappears at zero.

---

## URL Routing Map

### Application Routes

| Route | Page | Notes |
|-------|------|-------|
| `/` | Dashboard | Home. Redirects to `/setup` if not configured |
| `/login` | Login | No sidebar, full page |
| `/setup` | Setup Wizard | No sidebar, full page. Redirects to `/` if already complete |
| `/setup/:step` | Setup Wizard step | **Guided**: `account` (conditional), `mode`, `template`, `providers`, `company`, `agents`, `theme`, `complete`<br>**Quick**: `account` (conditional), `mode`, `providers`, `company`, `complete` |
| `/org` | Org Chart | Interactive visualization with Hierarchy (default, drag-drop agent reassignment) and Communication (d3-force) views, 400ms animated transitions |
| `/org/edit` | Org Chart (edit mode) | Form-based company config CRUD. Query params: `?tab=general` (default), `?tab=agents`, `?tab=departments` switch sub-tabs |
| `/roles` | Roles | Distinct role definitions derived from the org structure (no backend `GET /roles`); each links to its version history |
| `/roles/:roleName/versions` | Role versions | Read-only role-definition version timeline (list + get; no diff/rollback) |
| `/tasks` | Task Board | Kanban default |
| `/tasks?view=list` | Task Board (list) | List view toggle |
| `/tasks?status=:status` | Task Board (filtered) | Filter by task status |
| `/tasks/:taskId` | Task detail | Full-page detail view (direct navigation / deep linking) |
| `/tasks?selected=:taskId` | Task detail (panel) | Panel overlay on board view |
| `/budget` | Budget | P&L dashboard |
| `/budget/forecast` | Budget forecast | Projection charts |
| `/mission-control` | Mission Control | Tabbed: live cockpit (stuck/runaway flags, pause/kill), mid-flight steering, and the flight recorder (time-travel seek/replay + durable red-team verdict). The active tab is in-page state, not URL-addressable |
| `/mission-control?executionId=:id` | Flight recorder (deep link) | Auto-loads the execution into the recorder (from a live cockpit agent row); `?taskId=:id` is accepted as a fallback key |
| `/mission-control?project=:id` | Steering (deep link) | Seeds the steering project from the URL |
| `/approvals` | Approvals | Pending queue |
| `/approvals?status=:status` | Approvals (filtered) | Filter by approval status |
| `/approvals?risk=:level` | Approvals (filtered) | Filter by risk level |
| `/approvals?type=:type` | Approvals (filtered) | Filter by action type |
| `/approvals?search=:query` | Approvals (filtered) | Search by title/description |
| `/approvals?selected=:id` | Approvals (detail) | Side panel overlay for approval detail |
| `/agents` | Agents | Profile list |
| `/agents/:agentId` | Agent detail | Full page with scrollable sections |
| `/projects` | Projects | List with search/filter |
| `/projects/:projectId` | Project detail | Full page with team, tasks |
| `/projects/:projectId/docs` | Living docs | Per-project wiki: doc list + viewer (status reports, deliverables, knowledge notes, codebase analyses, run narratives) |
| `/projects/:projectId/docs/:slug` | Living doc detail | Full doc view with typed-block rendering |
| `/workflows` | Workflows | Card grid list with search, type filter, create (blank or from blueprint)/duplicate/delete |
| `/workflows/editor` | Workflow Editor | Visual DAG editor for workflow definitions (8 node types incl. subworkflow, 4 edge types, YAML preview, validation, version history with diff/rollback) |
| `/subworkflows` | Subworkflows | Registry card grid with search, I/O signature, version count, detail drawer with parents and delete |
| `/artifacts` | Artifacts | List with search/filter |
| `/artifacts/:artifactId` | Artifact detail | Full page with metadata, content preview |
| `/messages` | Messages | Channel feed |
| `/messages?channel=:name` | Messages (filtered) | Filtered by channel |
| `/messages?channel=:name&type=:type` | Messages (filtered) | Filtered by message type |
| `/messages?channel=:name&priority=:level` | Messages (filtered) | Filtered by priority |
| `/messages?channel=:name&search=:query` | Messages (filtered) | Search by content/sender |
| `/messages?channel=:name&message=:id` | Messages (detail) | Side drawer for message detail |
| `/meetings` | Meetings | Meeting history |
| `/meetings/:meetingId` | Meeting detail | Transcript and outcomes |
| `/providers` | Providers | Provider list |
| `/providers/:providerName` | Provider detail | Edit/test provider |
| `/settings` | Settings | Namespace overview (tab bar navigation) |
| `/settings/:namespace` | Settings (filtered) | Single namespace view via tab bar |
| `/settings/observability/sinks` | Settings Sinks | Observability sink management (card grid with edit/test) |
| `/settings/security/sessions` | Active Sessions | Active-session list with per-row revoke (current device disabled); reached via a Settings action card from the security namespace |
| `/settings/coordination/ceremony-policy` | Ceremony Policy | Strategy selection with resolved-policy source badges, department overrides with inherit/override toggle, per-ceremony overrides, velocity-calculator auto-selection per strategy |
| `/settings/memory/fine-tuning` | Fine-Tuning | Embedding fine-tuning pipeline management (status, run history, preflight checks, start/cancel) |
| `/admin/backups` | Admin Backups | System backup lifecycle: create, cursor-paginated list, restore (restart notice), delete; reached via a Settings action card from the backup namespace |
| `/clients` | Client List | Synthetic-client roster with search/filter |
| `/clients/:clientId` | Client Detail | Per-client profile, request history, satisfaction |
| `/clients/requests` | Request Queue | Client-request intake lifecycle (submit, scope, approve, reject). Approve returns `202`; the request runs through the work pipeline in the background and reaches its terminal state asynchronously |
| `/clients/simulations` | Simulation Dashboard | Simulation run list, start, detail, cancel |
| `/clients/reviews/:taskId` | Review Pipeline | Per-task review-pipeline stage progress and decisions |
| `/docs/` | Documentation | Static MkDocs HTML served by Caddy (bypasses React Router) |
| `*` | 404 Not Found | Catch-all |

### Route Guards

| Rule | Behaviour |
|------|----------|
| No JWT token | Redirect to `/login` (all routes except `/login` and `/setup`) |
| Setup not complete | Redirect to `/setup` (all routes except `/login` and `/setup`) |
| Setup already complete | Redirect to `/` (when navigating to `/setup`) |
| `/login` with valid JWT | Redirect to `/` |

---

## WebSocket Channel Subscription Map

Single WebSocket connection per session, established after login. Each page subscribes only to the channels it needs (see table below). Events are dispatched client-side to Zustand stores based on channel.

| Page | Channels | Events of Interest |
|------|----------|--------------------|
| **Dashboard** | `tasks`, `agents`, `budget`, `system`, `approvals` | All; aggregated into health indicators, activity feed, badge counts |
| **Org Chart** (hierarchy) | `agents` | Agent hired/fired, status changes |
| **Org Chart** (communication) | `agents` | Agent status changes. Message data via REST polling (`GET /messages`) |
| **Org Chart** (edit mode) | `agents` | Agent hired/fired (triggers config refresh) |
| **Task Board** | `tasks` | Task created/updated/transitioned/cancelled |
| **Budget** | `budget` | Cost records added, budget alerts |
| **Approvals** | `approvals` | Approval submitted/approved/rejected/expired |
| **Agents** (list) | `agents` | Agent status changes |
| **Agents** (detail) | `agents`, `tasks` | Agent status and task changes for selected agent |
| **Messages** | `messages` | New messages sent |
| **Meetings** | `meetings` | Meeting started/completed/failed |
| **Projects** (list) | `projects` | Project creation events |
| **Projects** (detail) | `projects`, `tasks` | Project and task changes |
| **Artifacts** (list) | `artifacts` | Artifact creation, deletion, upload events |
| **Artifacts** (detail) | `artifacts` | Artifact changes for selected artifact |
| **Providers** | (none) | N/A; polling via TanStack Query |
| **Workflows** (list) | (none) | N/A |
| **Subworkflows** | (none) | N/A; refreshes via polling (30s) |
| **Workflow Editor** | (none) | N/A; REST API only, no real-time collaboration |
| **Settings** | `system` | Restart-required notifications |
| **Notifications panel** | `system`, `approvals`, `budget` | System errors, new approvals, budget alerts |

**Global subscriptions** (active regardless of current page):

- `system`: Connection status, shutdown notices
- `approvals`: Badge count for sidebar
- `budget`: Threshold alert for sidebar indicator
- `messages`: Unread count for sidebar badge

---

## Responsive Scope

Desktop-first with minimal tablet support. Mobile layout is not currently in scope.

| Breakpoint | Sidebar | Content | Rationale |
|------------|---------|---------|-----------|
| >=1280px | Full (220px) | Multi-column layouts | Standard desktop |
| 1024--1279px | Auto-collapses to icon rail (56px) | Full width minus rail | Smaller desktops, split-screen |
| 768--1023px | Hidden (hamburger toggle, 240px overlay) | Single column | Tablet landscape |
| <768px | Hidden | "Use desktop or CLI" message | Not designed for mobile; Go CLI covers quick-check use cases |

At tablet (768-1023px), the sidebar hamburger trigger is rendered in the StatusBar (top bar), not in the sidebar itself (which is hidden at that breakpoint). The overlay sidebar uses the shared `Drawer` component (`role="dialog"`, `aria-modal="true"`) with a blurred semi-transparent backdrop and auto-closes on navigation.

The density system (Dense/Balanced/Medium/Sparse from [Brand & UX](brand-and-ux.md)) provides additional user control over information density at any breakpoint.

---

## Resolved Questions

### Activity feed: Dashboard widget (not global drawer)

The activity feed is a Dashboard widget, not a persistent global element. Rationale:

- A persistent drawer competes with page content for attention (violates principle #3: "navigation recedes, content shines")
- The Dashboard is the natural home for activity summaries; users who want more detail click through to the relevant page (principle #5: progressive disclosure)
- Linear, Vercel, and Grafana all use dashboard widgets for activity, not persistent drawers
- The Dashboard already subscribes to all WS channels for its health indicators; the activity widget is a natural aggregation

### Messages: Own page (not drawer)

Messages have a dedicated API (`/messages` + channel filtering) and WebSocket channel; this is a first-class domain. Agent-to-agent communications require investigation (delegation chains, coordination audits), which needs filters, scrolling, and context that a drawer cannot provide. The channel filtering model maps naturally to a page with a channel sidebar and message feed.

### Org Chart + Company: Merged with mode separation

Both deal with the same data domain (departments, teams, agents, reporting lines). The default is an interactive visualization with two view modes: hierarchy (dagre, with drag-drop agent reassignment as an inline mutating action) and communication (d3-force). An "Edit Organisation" button enters form-based edit mode using the sub-tab structure (General, Agents, Departments) as a panel overlay for bulk configuration changes.

---

## Controller-to-Page Map

Every backend controller has a home in the page structure. No orphans.

| Controller | Page |
|------------|------|
| HealthController | Sidebar (connection status dot) |
| AuthController | Login |
| SetupController | Setup Wizard |
| CompanyController | Org Chart |
| DepartmentController | Org Chart |
| ActivityController | Dashboard |
| AgentController | Agents, Org Chart |
| TaskController | Task Board |
| MessageController | Messages |
| MeetingController | Meetings |
| BudgetController | Budget, Dashboard |
| AnalyticsController | Dashboard, Budget |
| ProviderController | Providers |
| ApprovalsController | Approvals, Dashboard |
| SettingsController | Settings |
| BackupController | Admin Backups page (`/admin/backups`) |
| AutonomyController | Agent Detail page (planned) |
| CollaborationController | Agent Detail page (Collaboration override panel) |
| CoordinationController | Task Board (task detail action) |
| CoordinationMetricsController | Coordination Metrics page (per-run efficiency / overhead / redundancy table) |
| ExperimentsController | Meta page (read-only Experiments explorer: variants + assignment history) |
| MetaAnalyticsController | Meta Analytics page (Cross-deployment patterns section) |
| OntologyController / OntologyAdminController | Ontology page (entity catalog + Admin section: re-derive, sync org memory) |
| InterruptController | Mission Control (offline interrupts fallback panel) |
| MemoryEntriesController | Agent Detail page (memory administration: delete entry, CEO/SYSTEM only) |
| AuditController | Settings (security namespace) |
| ProjectController | Projects page (list, detail, create), Task Board (project filter) |
| ArtifactController | Artifacts page (list, detail, content preview, download) |
| WorkflowController | Workflows, Workflow Editor |
| WorkflowVersionController | Workflows, Workflow Editor |
| ClientController | Client List, Client Detail |
| RequestController | Request Queue |
| SimulationController | Simulation Dashboard |
| ReviewController | Review Pipeline |

---

## Design Principle Compliance

How this page structure supports the 10 design principles from #762:

| # | Principle | How the structure supports it |
|---|-----------|-------------------------------|
| 1 | Data is never just a number | Dashboard: sparklines + deltas via `/analytics/overview` (7-day trend). Budget: forecast projections. Agent detail: career arc narrative |
| 2 | Real-time means visible | WS subscription map ensures every data-bearing page has live updates. Sidebar badges update in real time |
| 3 | Navigation recedes, content shines | Collapsible sidebar (220px to 56px). No persistent drawers competing with content |
| 4 | Status arrives, doesn't flash | WS events update Zustand stores; animation profile is "status-driven" (only changed elements animate) |
| 5 | Progressive disclosure | Dashboard summary to page detail. Agent list to agent detail page. Org chart node to agent detail |
| 6 | Keyboard-first | Cmd+K command palette. URL-addressable everything for bookmark/share. Arrow key nav in Task Board |
| 7 | Typography carries information | Geist Mono for metrics/values/agent names. Geist Sans for labels/descriptions |
| 8 | Prose alongside metrics | Agent detail: prose insights alongside performance metrics. Budget: cost context explanations |
| 9 | Every pixel earns its place | No placeholder pages. Every page maps to a live backend domain |
| 10 | One component, one look | Consistent card/panel/badge patterns across all pages via shadcn/ui primitives |

---

## Reference Materials

| Resource | Location |
|----------|----------|
| Brand identity and UX design system | [Brand & UX](brand-and-ux.md) |
| UX research and competitor analysis | `research/762-ux-mockups` branch, `docs/design/ux-research.md` |
| Design exploration mockups | `feat/765-design-exploration` branch, `mockups-v2/` |
| Winning prototype (Mission Control) | `research/762-ux-mockups` branch, `mockups/direction-cd/` |
| WebSocket channel definitions | `src/synthorg/api/channels.py` |
| API controller registry | `src/synthorg/api/controllers/__init__.py` |
| API surface | [Human Interaction Layer](../guides/human-interaction.md) |
| Parent UX overhaul issue | [#762](https://github.com/Aureliolo/synthorg/issues/762) |
| Design exploration issue | [#765](https://github.com/Aureliolo/synthorg/issues/765) |
| Page structure issue | [#766](https://github.com/Aureliolo/synthorg/issues/766) |
