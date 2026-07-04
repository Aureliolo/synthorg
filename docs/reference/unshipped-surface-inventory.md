# Unshipped Surface Inventory

An audit ledger of every MCP tool / handler / service that was registered
and callable but returned `not_supported` / 503 in every deployment because
its backing service slice was never constructed in the boot path (the
`capability_gap` signal). Each entry is classified and resolved: wired,
documented as intentional, or owned elsewhere.

The class is now guarded against regression by
`scripts/check_mcp_capability_gap_documented.py`, which *discovers* every
service an MCP handler depends on (through a `capability_gap` guard or a
`*_of` `require_service` accessor) and fails when a backing class is neither
constructed in `src/` nor tracked in `scripts/_ghost_wiring_manifest.txt`.
That closes the blind spot in `check_no_ghost_wiring.py`, which can only
check symbols already named in its manifest and so cannot discover a
brand-new never-wired service.

## Classifications

- **UNFINISHED**: a real, already-implemented service that was never wired
  into the boot path. Resolution: wire it. Every one of these existed in
  `src/`; none needed building.
- **DEPLOYMENT-OPTIONAL**: conditionally wired; the `capability_gap` fires
  only when a deployment lacks a prerequisite (persistence, a feature flag,
  a configured provider). Resolution: document the trigger; no re-wiring.
- **INTENTIONAL-BACKEND-DEPENDENT**: a genuine backend-primitive gap
  surfaced through `CapabilityNotSupportedError` (a fail-loud, documented
  path distinct from the `None`-slice `capability_gap`).
- **DEAD / OWNED-ELSEWHERE**: dead code or stale strings; removed, or owned
  by the companion boot-warning audit (#2533).

## UNFINISHED (now wired)

All backing classes existed in `src/`; the fix was construction + slice
wiring plus a `scripts/_ghost_wiring_manifest.txt` `ENFORCED` line and the
owning `feature.py` `ghost_wired_symbols` claim.

### Infrastructure facades (`FacadesStateSlice`)

| Service | Construction site | Tools |
| --- | --- | --- |
| `SetupFacadeService` | `infrastructure/_construction.py::wire_construction` | `synthorg_setup_*` |
| `ProjectFacadeService` | `infrastructure/_construction.py::wire_construction` | `synthorg_projects_*` |
| `RequestsFacadeService` | `infrastructure/_construction.py::wire_construction` | `synthorg_requests_*` |
| `TemplatePackFacadeService` | `infrastructure/_construction.py::wire_construction` | `synthorg_template_packs_*` |
| `ClientFacadeService` | `infrastructure/_construction.py::wire_construction` | `synthorg_clients_*` |
| `AuditReadService` | `infrastructure/_construction.py::wire_construction` (over the construction `AuditLog`) | `synthorg_audit_*` |
| `EventsReadService` | `infrastructure/_construction.py::wire_construction` (over the `EventStreamHub`) | `synthorg_events_*` |
| `ArtifactFacadeService` | `infrastructure/_construction.py::wire_construction` (over the artifact storage backend) | `synthorg_artifacts_*` |
| `IntegrationHealthFacadeService` | `infrastructure/_construction.py::wire_construction` (over the health prober) | `synthorg_integration_health_*` |
| `OAuthFacadeService` | `infrastructure/_construction.py::wire_construction` (in-process registry, optional token manager) | `synthorg_oauth_*` |
| `SimulationFacadeService` | `infrastructure/_construction.py::wire_construction` (over `ClientStateSlice.simulation_state`; facades `depends_on=("client",)`) | `synthorg_simulations_*` |
| `UserFacadeService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_user_facade_service` (over the auth service) | `synthorg_users_*` |
| `BackupFacadeService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_backup_facade_service` (over the started backup service) | `synthorg_backup_*` |
| `OntologyFacadeService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_ontology_facade_service` | `synthorg_ontology_*` |
| `MCPCatalogFacadeService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_mcp_catalog_facade_service` | `synthorg_mcp_catalog_*` |
| `ProviderReadService` | `api/lifecycle_helpers/settings_dependent_services.py::_wire_provider_read_facade` | `synthorg_providers_*` |
| `SettingsReadService` | `api/lifecycle_helpers/settings_dependent_services.py::_wire_settings_read_facade` | `synthorg_settings_*` |

`SettingsReadService` (slice `SettingsStateSlice`, class in
`infrastructure/services/_read_facades.py`) was the last hold-out, found by
the new discovery gate rather than by hand.

### Org domain (`OrganizationStateSlice`)

| Service | Construction site | Tools | Notes |
| --- | --- | --- | --- |
| `CompanyReadService` | `api/lifecycle_helpers/organization_wiring.py::_wire_company_read_service` | `synthorg_company_*` | Redesigned to read the durable sources (config resolver + `OrgMutationService` + company-version repo) instead of probing methods that `OrgMutationService` never implemented. |
| `RoleVersionService` | `api/lifecycle_helpers/organization_wiring.py::_wire_role_version_service` | `synthorg_role_versions_*` | Redesigned onto the durable `persistence.role_versions` repository. |
| `TeamService` | `api/lifecycle_helpers/organization_wiring.py::_wire_team_service` | `synthorg_teams_*` | Redesigned from an ephemeral in-memory UUID store to the settings-backed `company.departments[*].teams` the REST / dashboard surface uses, sharing one write lock. Tools re-keyed on `(department, name)`. |

### HR + coordination (`HrStateSlice`, `CoordinationStateSlice`)

| Service | Construction site | Tools |
| --- | --- | --- |
| `PersonalityService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_personality_service` | `synthorg_personalities_*` |
| `ActivityFeedService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_activity_feed_service` | `synthorg_agents_get_activity`, `synthorg_activities_list` |
| `AgentHealthService` | `hr/_construction.py::wire_construction` | `synthorg_agents_get_health` |
| `ScalingDecisionService` | `api/lifecycle_helpers/scaling_wiring.py::_wire` | `synthorg_scaling_*` |
| `CoordinationService` | `coordination/_construction.py::wire_construction` | `synthorg_coordination_*` |
| `CeremonyPolicyService` | `coordination/_construction.py::wire_construction` | `synthorg_ceremony_policy_*` |

### Integrations (`IntegrationsStateSlice`)

| Service | Construction site | Tools |
| --- | --- | --- |
| `ConnectionService` | `integrations/_construction.py::wire_construction` (enabled-gated connection catalog) | `synthorg_connections_*` |
| `WebhookService` | `api/lifecycle_runner_support.py::_wire_webhook_request_services` (in-process definition store) | `synthorg_webhooks_*` |

## DEPLOYMENT-OPTIONAL (documented)

Each already has a construction site (so the discovery gate passes it) and
`capability_gap`s only when its deployment prerequisite is absent.

| Service | Fires `capability_gap` when |
| --- | --- |
| `DepartmentService` | persistence / department-config prerequisites absent |
| `WorkflowExecutionService` | the workflow-execution feature prerequisite is absent |
| `SubworkflowService` | the subworkflow feature prerequisite is absent |
| `WorkflowVersionService` | persistence lacks the workflow-version repo |
| `AgentVersionService` | persistence lacks the agent-version repo |
| `TrainingService` | the training feature prerequisite is absent |

## INTENTIONAL-BACKEND-DEPENDENT (documented)

| Surface | Location | Behaviour |
| --- | --- | --- |
| gRPC-OTLP export | `observability/otlp_handler.py` | Fail-loud + already documented; unavailable without the gRPC exporter. |
| Role-version history on a backend without it | `_map_capability` path | Surfaced via `CapabilityNotSupportedError`, distinct from the `None`-slice `capability_gap`. |

## Dead or owned elsewhere

Classification **DEAD / OWNED-ELSEWHERE**.

| Item | Disposition |
| --- | --- |
| `service_fallback` + `MCP_HANDLER_SERVICE_FALLBACK` | Zero call sites. Owned by #2533 / WS-G0 (the `capability_gap` routing rework); recorded here only. |
| `SelfImprovementService` dead constructor params (`memory_backend`, `provider`, `config_provider`, `snapshot_builder`) | Owned by #2533 (D2); recorded here only. |
| `ToolInvocationTracker` construction | Owned by #2533 (D4). |
| `api/controllers/meta.py::get_signals` placeholder data | Fixed: now reports real per-domain availability from the wired `SignalsService`. |
| `agents_training.py` stale `_WHY_*` capability reasons | Fixed: corrected to the "service not wired" reason once the training / personality services were wired. |
| `communication/meeting/enums.py` `EMBEDDING` docstring | Fixed: `EmbeddingSimilarityDetector` is fully implemented; the "placeholder / NotImplementedError" claim was stale. |
