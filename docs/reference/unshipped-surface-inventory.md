# Unshipped Surface Inventory

An audit ledger of every MCP tool, handler and service that is registered and
callable but cannot succeed, or can only succeed on some deployments. The page
exists because a registered tool that always refuses is invisible from the tool
list: it looks shipped from every angle except the one where somebody calls it.

Two mechanisms produce that shape and they are not interchangeable:

- **`capability_gap`**: the handler's backing service slice is `None` on this
  `app_state`, so the tool returns `not_supported` before doing any work.
- **`CapabilityNotSupportedError`**: the service is wired, but the object it
  facades does not offer the primitive the facade calls, or the operation is
  deliberately not offered over MCP. Fail-loud, raised at the call.

The `capability_gap` class is guarded against regression by
`scripts/check_mcp_capability_gap_documented.py`, which *discovers* every service
an MCP handler depends on (through a `capability_gap` guard or a `*_of`
`require_service` accessor) and fails when a backing class is neither constructed
in `src/` nor tracked in `scripts/_ghost_wiring_manifest.txt`. That closes the
blind spot in `check_no_ghost_wiring.py`, which can only check symbols already
named in its manifest and so cannot discover a brand-new never-wired service.

**Nothing guards the `CapabilityNotSupportedError` class.** A facade reaches its
primitive through `getattr(obj, "<literal>", None)`, and
`check_no_ghost_attribute_read.py` only asks whether the name is declared on
*anything* in `src/synthorg/`, never whether it is declared on *this* object. A
facade that declares its own `get_relationships` and then reads
`get_relationships` off a service that has no such method satisfies that gate and
still cannot ever succeed. The UNSHIPPED-PRIMITIVE table below is that gap,
maintained by hand.

## Classifications

- **UNFINISHED**: a real, already-implemented service that was never wired into
  the boot path. Resolution: wire it. Every one of these existed in `src/`; none
  needed building.
- **DEPLOYMENT-OPTIONAL**: conditionally wired; the `capability_gap` fires only
  when a deployment lacks a prerequisite (persistence, a feature flag, a
  configured provider). Resolution: document the trigger; no re-wiring.
- **UNSHIPPED-PRIMITIVE**: the facade is wired on every deployment and raises
  `CapabilityNotSupportedError` on every deployment, because the object it
  facades does not have the method it calls. The tool is registered, documented
  and unreachable.
- **INTENTIONAL-REFUSAL**: the operation deliberately does not exist over MCP,
  and the surface that does own it is named in the refusal.
- **BACKEND-DEPENDENT**: a genuine backend-primitive gap surfaced through
  `CapabilityNotSupportedError`, distinct from the `None`-slice `capability_gap`.
- **DEAD**: dead code or stale strings; removed.

## UNFINISHED (wired)

All backing classes existed in `src/`; the fix was construction plus slice
wiring, a `scripts/_ghost_wiring_manifest.txt` `ENFORCED` line, and the owning
`feature.py` `ghost_wired_symbols` claim.

### Infrastructure facades (`FacadesStateSlice`)

| Service | Construction site | Tools |
| --- | --- | --- |
| `SetupFacadeService` | `infrastructure/_construction.py::wire_construction` | `synthorg_setup_*` |
| `RequestsFacadeService` | `infrastructure/_construction.py::wire_construction` | `synthorg_requests_*` |
| `TemplatePackFacadeService` | `infrastructure/_construction.py::wire_construction` | `synthorg_template_packs_*` |
| `ClientFacadeService` | `infrastructure/_construction.py::wire_construction` | `synthorg_clients_*` |
| `AuditReadService` | `infrastructure/_construction.py::wire_construction` (over the construction `AuditLog`) | `synthorg_audit_*` |
| `EventsReadService` | `infrastructure/_construction.py::wire_construction` (over the `EventStreamHub`) | `synthorg_events_*` |
| `ArtifactFacadeService` | `infrastructure/_construction.py::wire_construction` (over the artifact storage backend) | `synthorg_artifacts_*` |
| `IntegrationHealthFacadeService` | `infrastructure/_construction.py::wire_construction` (over the health prober) | `synthorg_integration_health_*` |
| `OAuthFacadeService` | `infrastructure/_construction.py::wire_construction` (in-process registry, optional token manager) | `synthorg_oauth_*` |
| `SimulationFacadeService` | `infrastructure/_construction.py::wire_construction` (over `ClientStateSlice.simulation_state`; facades `depends_on=("client",)`) | `synthorg_simulations_*` |
| `UserFacadeService` | `api/lifecycle_helpers/persistence_facade_autowire.py::_wire_user_facade_service` (over the auth service) | `synthorg_users_*` |
| `BackupFacadeService` | `api/lifecycle_helpers/persistence_facade_autowire.py::_wire_backup_facade_service` (over the started backup service) | `synthorg_backup_*` |
| `OntologyFacadeService` | `api/lifecycle_helpers/persistence_facade_autowire.py::_wire_ontology_facade_service` | `synthorg_ontology_*` |
| `MCPCatalogFacadeService` | `api/lifecycle_helpers/persistence_facade_autowire.py::_wire_mcp_catalog_facade_service` | `synthorg_mcp_catalog_*` |
| `ProviderReadService` | `api/lifecycle_helpers/settings_dependent_services.py::_wire_provider_read_facade` | `synthorg_providers_*` |
| `SettingsReadService` | `api/lifecycle_helpers/settings_dependent_services.py::_wire_settings_read_facade` | `synthorg_settings_*` |

`SettingsReadService` (slice `SettingsStateSlice`, class in
`infrastructure/services/_read_facades.py`) wires in
`_wire_settings_read_facade`; the discovery gate covers it.

Being wired is not the same as being reachable. `AuditReadService`,
`EventsReadService`, `IntegrationHealthFacadeService` and two of the four
`OntologyFacadeService` operations are wired on every deployment and still
refuse every call; see UNSHIPPED-PRIMITIVE.

### Org domain (`OrganizationStateSlice`)

| Service | Construction site | Tools | Reads |
| --- | --- | --- | --- |
| `CompanyReadService` | `api/lifecycle_helpers/organization_wiring.py::wire_company_read_service` | `synthorg_company_*` | The durable sources the REST company controllers use: the config resolver for the company snapshot and departments, `OrgMutationService` for writes, and the company version repository for history. |
| `RoleVersionService` | `api/lifecycle_helpers/organization_wiring.py::wire_role_version_service` | `synthorg_role_versions_*` | The durable `persistence.role_versions` repository. |
| `TeamService` | `api/lifecycle_helpers/organization_wiring.py::wire_team_service` | `synthorg_teams_*` | The settings-backed `company.departments[*].teams` the REST and dashboard surfaces use, sharing one write lock. Tools are keyed on `(department, name)`. |

### HR and coordination (`HrStateSlice`, `CoordinationStateSlice`)

| Service | Construction site | Tools |
| --- | --- | --- |
| `ActivityFeedService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_activity_feed_service` | `synthorg_agents_get_activity`, `synthorg_activities_list` |
| `AgentHealthService` | `hr/_construction.py::wire_construction` | `synthorg_agents_get_health` |
| `CoordinationService` | `coordination/_construction.py::wire_construction` | `synthorg_coordination_*` |

### Integrations (`IntegrationsStateSlice`)

| Service | Construction site | Tools |
| --- | --- | --- |
| `ConnectionService` | `integrations/_construction.py::wire_construction` (enabled-gated connection catalog) | `synthorg_connections_*` |
| `WebhookService` | `api/lifecycle_runner_support.py::_wire_webhook_request_services` (in-process definition store) | `synthorg_webhooks_*` |

## DEPLOYMENT-OPTIONAL (documented)

Each has a construction site (so the discovery gate passes it) and
`capability_gap`s only when its deployment prerequisite is absent.

| Service | Fires `capability_gap` when |
| --- | --- |
| `DepartmentService` | persistence / department-config prerequisites absent |
| `WorkflowExecutionService` | the workflow-execution feature prerequisite is absent |
| `SubworkflowService` | the subworkflow feature prerequisite is absent |
| `WorkflowVersionService` | persistence lacks the workflow-version repo |
| `AgentVersionService` | persistence lacks the agent-version repo |
| `SelfImprovementService` (`meta.py::_WHY_SELF_IMPROVEMENT`, `synthorg_meta_*`) | the self-improvement meta loop is not enabled for the deployment |

## UNSHIPPED-PRIMITIVE

Wired on every deployment, refused on every deployment. Each row is a facade
reading a method off a backing object that does not define it, so the
`getattr(..., None)` default is the only reachable outcome and the guard below it
is the whole behaviour. These are registered MCP tools that have never been able
to return a result.

| Tool | Facade | Reads | Off | Which does not define it |
| --- | --- | --- | --- | --- |
| `synthorg_audit_list` | `AuditReadService.list_entries` (`infrastructure/services/_status_facades.py`) | `list_entries` | `security/audit.py::AuditLog` | `AuditLog` exposes `query`, `entries`, `count`, `record`, `total_recorded`, `clear`. |
| `synthorg_events_list` | `EventsReadService.list_events` (`infrastructure/services/_status_facades.py`) | `recent_events` | `communication/event_stream/stream.py::EventStreamHub` | `recent_events` is declared nowhere in `src/synthorg/`. Tracked in `scripts/ghost_attribute_read_baseline.txt`. |
| `synthorg_integration_health_get_all`, `synthorg_integration_health_get` | `IntegrationHealthFacadeService.get_all` (`infrastructure/services/_status_facades.py`) | `snapshot` | `integrations/health/prober.py::HealthProberService` | The prober exposes `start`, `stop` and its private probe loop. `get_one` calls `get_all`, so both tools refuse. |
| `synthorg_ontology_get_entity` | `OntologyFacadeService.get_entity` (`integrations/mcp_facades/_ontology.py`) | `get_entity` | `ontology/service.py::OntologyService` | `OntologyService` exposes `get`, not `get_entity`. |
| `synthorg_ontology_get_relationships` | `OntologyFacadeService.get_relationships` (`integrations/mcp_facades/_ontology.py`) | `get_relationships` | `ontology/service.py::OntologyService` | No relationship accessor exists on the service. |

The two `OntologyFacadeService` operations that do work, `list_entities` and
`search`, name methods `OntologyService` genuinely has. That is what makes the
other two hard to see from the handler: the same facade, the same guard shape,
half of it live.

`MCPCatalogFacadeService` uses the identical guard shape over `CatalogService`
and is *not* in this table: `browse`, `search`, `get_entry`, `install` and
`uninstall` all exist on `CatalogService`, so its guards are defensive rather
than load-bearing. `ArtifactFacadeService`'s `delete` guard is likewise
defensive; `ArtifactStorageBackend` defines `delete`.

## INTENTIONAL-REFUSAL

The operation is deliberately absent from MCP and the refusal names the surface
that owns it.

| Capability | Location | Owning surface |
| --- | --- | --- |
| `user_create` | `infrastructure/services/_read_facades.py` | the onboarding flow |
| `user_update` | `infrastructure/services/_read_facades.py` | the auth controller |
| `user_delete` | `infrastructure/services/_read_facades.py` | a protected operator workflow |
| `setup_initialize` | `infrastructure/services/_status_facades.py` | the setup controller and the CLI wizard |
| `simulation_create` | `infrastructure/services/_status_facades.py` | scenarios loaded from config at start-up |

## BACKEND-DEPENDENT

| Surface | Location | Behaviour |
| --- | --- | --- |
| Company and role version history on a persistence-less deployment | `organization/services.py::_require_version_repo` | Raises `CapabilityNotSupportedError` when the durable version repository is `None`, logged under `ORG_CAPABILITY_UNSUPPORTED` with `reason="no_durable_version_repository"`. Handled in the MCP layer by `_map_capability`. |
| Memory fine-tune endpoints on a backend without fine-tune repositories | `meta/mcp/handlers/_memory_service_helpers.py` | Raises `MemoryBackendUnsupportedError` when no memory service and no memory backend are wired. Distinct from the `None`-slice `capability_gap`. |
| gRPC-OTLP export | `observability/otlp_handler.py` | Fail-loud and already documented; unavailable without the gRPC exporter. |

## DEAD (removed) and corrected strings

| Item | Current state |
| --- | --- |
| `service_fallback` helper and `MCP_HANDLER_SERVICE_FALLBACK` | Absent from `src/`. Live handlers route through `not_supported` (backend cannot perform the op) or `capability_gap` (wired handler, primitive gap). The unrelated `source="service_fallback"` string in `engine/evolution/service.py` is an evolution-source label, not this helper. |
| `api/controllers/meta.py::get_signals` | Reports real per-domain availability from the wired `SignalsService`. |
| `agents_crud.py` `_WHY_ACTIVITY` / `_WHY_HISTORY` / `_WHY_HEALTH` reasons | Each reads the actual runtime condition (`<service> is not wired on app_state in this deployment`) for the activity-feed, agent-version and agent-health services. |

`ToolInvocationTracker` is constructed at boot in
`api/construction_phase.py`, defaulted when no override is supplied, and is no
longer a dead construction.

## Idioms 3-7 sweep of `src/synthorg`

Beyond the `capability_gap` handler surface (idioms 1-2, in the tables above),
this inventory covers the wider "declared but unshipped" idioms 3-7 across
`src/synthorg`.

- **Idiom 3 (`NotImplementedError` in a concrete class).** None exist in
  `src/synthorg/`. Abstract seams declare themselves with `abc.ABC` plus
  `@abstractmethod`, and a genuinely absent capability raises
  `FeatureNotImplementedError`, so a bare `raise NotImplementedError` is banned
  outright and enforced by `check_no_stubs.py`.
- **Idioms 4-5 (`TODO` / `FIXME` / `HACK` / `XXX` markers).** None exist in
  `src/synthorg/`; forensic and deferral markers in source are forbidden by
  convention and enforced by the comment gates.
- **Idiom 6 (`placeholder` / `stub` / "not implemented" text).** The textual hits
  are docstrings, comments and identifiers (template-field placeholders,
  test-double "stub" naming, prose describing behaviour), not unfinished
  production code.
- **Idiom 7 (ghost 503 strings and unwired subsystems).** This *is* the
  `capability_gap` surface catalogued in the UNFINISHED and DEPLOYMENT-OPTIONAL
  tables above, and is regression-guarded by
  `scripts/check_mcp_capability_gap_documented.py`. Its blind spot, a wired
  facade over a primitive that does not exist, is the UNSHIPPED-PRIMITIVE table
  and is not guarded by anything.
