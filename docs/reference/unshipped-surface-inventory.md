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
- **DEAD**: dead code or stale strings; removed.

## UNFINISHED (now wired)

All backing classes existed in `src/`; the fix was construction + slice
wiring plus a `scripts/_ghost_wiring_manifest.txt` `ENFORCED` line and the
owning `feature.py` `ghost_wired_symbols` claim.

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
| `UserFacadeService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_user_facade_service` (over the auth service) | `synthorg_users_*` |
| `BackupFacadeService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_backup_facade_service` (over the started backup service) | `synthorg_backup_*` |
| `OntologyFacadeService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_ontology_facade_service` | `synthorg_ontology_*` |
| `MCPCatalogFacadeService` | `api/lifecycle_helpers/persistence_autowire.py::_wire_mcp_catalog_facade_service` | `synthorg_mcp_catalog_*` |
| `ProviderReadService` | `api/lifecycle_helpers/settings_dependent_services.py::_wire_provider_read_facade` | `synthorg_providers_*` |
| `SettingsReadService` | `api/lifecycle_helpers/settings_dependent_services.py::_wire_settings_read_facade` | `synthorg_settings_*` |

`SettingsReadService` (slice `SettingsStateSlice`, class in
`infrastructure/services/_read_facades.py`) wires in
`_wire_settings_read_facade`; the discovery gate covers it.

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
| `CoordinationService` | `coordination/_construction.py::wire_construction` | `synthorg_coordination_*` |

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
| `SelfImprovementService` (`meta.py::_WHY_SELF_IMPROVEMENT`, `synthorg_meta_*`) | the self-improvement meta loop is not enabled for the deployment |

## INTENTIONAL-BACKEND-DEPENDENT (documented)

| Surface | Location | Behaviour |
| --- | --- | --- |
| gRPC-OTLP export | `observability/otlp_handler.py` | Fail-loud + already documented; unavailable without the gRPC exporter. |
| Role-version history on a backend without it | `_map_capability` path | Surfaced via `CapabilityNotSupportedError`, distinct from the `None`-slice `capability_gap`. |

## Dead (removed) and corrected strings

Classification **DEAD**: the scaffold is removed; the reason strings now
report the real runtime condition.

| Item | Current state |
| --- | --- |
| `service_fallback` + `MCP_HANDLER_SERVICE_FALLBACK` | Removed: the helper and its event had zero `src/` call sites. Live handlers route through `not_supported` (backend cannot perform the op) or `capability_gap` (wired handler, primitive gap). |
| `ToolInvocationTracker` construction | Constructed only in tests, not wired at boot; a separate dead-construction cleanup, outside the `capability_gap` surface this inventory covers. |
| `api/controllers/meta.py::get_signals` | Reports real per-domain availability from the wired `SignalsService`. |
| `agents_training.py` `_WHY_*` capability reasons | Read the "service not wired in this deployment" runtime condition for the training / personality services. |
| `agents_crud.py` `_WHY_ACTIVITY` / `_WHY_HISTORY` / `_WHY_HEALTH` reasons | Each reads the actual runtime condition ("`<service>` is not wired on `app_state` in this deployment") for the activity-feed / agent-version / agent-health services. |

## Idioms 3-7 sweep of `src/synthorg`

Beyond the `capability_gap` handler surface (idioms 1-2, in the tables
above), this inventory covers the wider "declared but unshipped" idioms 3-7
across `src/synthorg`. No unshipped concrete surface remains:

- **Idiom 3 (`NotImplementedError` in a concrete class).** None exist in
  `src/synthorg/`. Abstract seams declare themselves with `abc.ABC` plus
  `@abstractmethod`, and a genuinely absent capability raises
  `FeatureNotImplementedError`, so a bare `raise NotImplementedError` is
  banned outright and enforced by `check_no_stubs.py`.
- **Idioms 4-5 (`TODO` / `FIXME` / `HACK` / `XXX` markers).** None exist in
  `src/synthorg/`; forensic and deferral markers in source are forbidden by
  convention and enforced by the comment gates.
- **Idiom 6 (`placeholder` / `stub` / "not implemented" text).** The textual
  hits are docstrings, comments, and identifiers (template-field
  placeholders, test-double "stub" naming, prose describing behaviour), not
  unfinished production code.
- **Idiom 7 (ghost 503 strings / unwired subsystems).** This *is* the
  `capability_gap` surface catalogued in the UNFINISHED and
  DEPLOYMENT-OPTIONAL tables above, and is now regression-guarded by
  `scripts/check_mcp_capability_gap_documented.py`.
