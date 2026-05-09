/**
 * MSW request handlers for Storybook and Vitest.
 *
 * - Each file under `handlers/` mirrors `web/src/api/endpoints/*.ts` 1:1
 *   and exports a `<domain>Handlers` array covering every endpoint in
 *   that module with a happy-path default response. Tests override
 *   defaults per-case via `server.use(...)`.
 * - `defaultHandlers` below aggregates every default handler into a
 *   single flat array consumed by `src/test-setup.tsx` to boot the
 *   vitest server with exhaustive coverage.
 * - Storybook-facing named exports (`setupStatusComplete`,
 *   `authLoginSuccess`, `projectsList`, `integrationsHandlers`, etc.)
 *   are preserved for existing stories. New stories should prefer the
 *   per-domain `<domain>Handlers` arrays where practical.
 *
 * Usage in stories (pick whichever matches the story's intent):
 *
 *   // Preferred for new stories: per-domain default array covering
 *   // every endpoint in that module.
 *   import { setupHandlers } from '@/mocks/handlers'
 *   export const MyStory: Story = {
 *     parameters: { msw: { handlers: setupHandlers } },
 *   }
 *
 *   // Legacy: targeted named exports for existing stories that want
 *   // a specific scenario (e.g. `setupStatusComplete` vs
 *   // `setupStatusNeedsAdmin`). Backward compatible.
 *   import { setupStatusComplete } from '@/mocks/handlers'
 *   export const MyStory: Story = {
 *     parameters: { msw: { handlers: [...setupStatusComplete] } },
 *   }
 *
 * Usage in tests:
 *
 *   import { server } from '@/test-setup'
 *   import { http, HttpResponse } from 'msw'
 *   import { successFor } from '@/mocks/handlers'
 *   import type { getTask } from '@/api/endpoints/tasks'
 *
 *   server.use(
 *     http.get('/api/v1/tasks/:id', () =>
 *       HttpResponse.json(successFor<typeof getTask>(myTask)),
 *     ),
 *   )
 */

export {
  apiError,
  apiPaginatedError,
  apiSuccess,
  buildValidationError,
  emptyPage,
  paginatedEnvelopeFor,
  paginatedFor,
  successFor,
  voidSuccess,
} from './helpers'

// ── Default test handler arrays (per endpoint module). ──

import { activitiesHandlers } from './activities'
import { agentsHandlers } from './agents'
import { analyticsHandlers } from './analytics'
import { approvalsHandlers } from './approvals'
import { artifactsHandlers } from './artifacts'
import { authHandlers } from './auth'
import { backupHandlers } from './backup'
import { budgetHandlers } from './budget'
import { capabilitiesHandlers } from './capabilities'
import { ceremonyPolicyHandlers } from './ceremony-policy'
import { clientsHandlers } from './clients'
import { collaborationHandlers } from './collaboration'
import { companyHandlers } from './company'
import { connectionsHandlers } from './connections'
import { coordinationHandlers } from './coordination'
import { customRulesHandlers } from './custom-rules'
import { escalationsHandlers } from './escalations'
import { fineTuningHandlers } from './fine-tuning'
import { healthHandlers } from './health'
import { integrationHealthHandlers } from './integration-health'
import { mcpCatalogDefaultHandlers } from './mcp-catalog'
import { meetingsHandlers } from './meetings'
import { messagesHandlers } from './messages'
import { metaHandlers } from './meta'
import { oauthDefaultHandlers } from './oauth'
import { ontologyHandlers } from './ontology'
import { projectsHandlers } from './projects'
import { providersHandlers } from './providers'
import { qualityHandlers } from './quality'
import { scalingHandlers } from './scaling'
import { settingsHandlers } from './settings'
import { setupHandlers } from './setup'
import { subworkflowsHandlers } from './subworkflows'
import { tasksHandlers } from './tasks'
import { templatePacksHandlers } from './template-packs'
import { trainingHandlers } from './training'
import { tunnelDefaultHandlers } from './tunnel'
import { usersHandlers } from './users'
import { webhooksHandlers } from './webhooks'
import { workflowExecutionsHandlers } from './workflow-executions'
import { workflowsHandlers } from './workflows'

/**
 * Flat list of happy-path handlers used by `setupServer` in
 * `web/src/test-setup.tsx`. Order matters only when two handlers
 * share a URL pattern -- later entries take precedence. All handler
 * files use unique URLs, so append order is effectively alphabetical.
 */
export const defaultHandlers = [
  ...activitiesHandlers,
  ...agentsHandlers,
  ...analyticsHandlers,
  ...approvalsHandlers,
  ...artifactsHandlers,
  ...authHandlers,
  ...backupHandlers,
  ...budgetHandlers,
  ...capabilitiesHandlers,
  ...ceremonyPolicyHandlers,
  ...clientsHandlers,
  ...collaborationHandlers,
  ...companyHandlers,
  ...connectionsHandlers,
  ...coordinationHandlers,
  ...customRulesHandlers,
  ...escalationsHandlers,
  ...fineTuningHandlers,
  ...healthHandlers,
  ...integrationHealthHandlers,
  ...mcpCatalogDefaultHandlers,
  ...meetingsHandlers,
  ...messagesHandlers,
  ...metaHandlers,
  ...oauthDefaultHandlers,
  ...ontologyHandlers,
  ...projectsHandlers,
  ...providersHandlers,
  ...qualityHandlers,
  ...scalingHandlers,
  ...settingsHandlers,
  ...setupHandlers,
  ...subworkflowsHandlers,
  ...tasksHandlers,
  ...templatePacksHandlers,
  ...trainingHandlers,
  ...tunnelDefaultHandlers,
  ...usersHandlers,
  ...webhooksHandlers,
  ...workflowExecutionsHandlers,
  ...workflowsHandlers,
]

// ── Per-domain default handler arrays (re-exported for ad-hoc use). ──

export {
  activitiesHandlers,
  agentsHandlers,
  analyticsHandlers,
  approvalsHandlers,
  artifactsHandlers,
  authHandlers,
  backupHandlers,
  budgetHandlers,
  capabilitiesHandlers,
  ceremonyPolicyHandlers,
  clientsHandlers,
  collaborationHandlers,
  companyHandlers,
  connectionsHandlers,
  coordinationHandlers,
  customRulesHandlers,
  escalationsHandlers,
  fineTuningHandlers,
  healthHandlers,
  integrationHealthHandlers,
  mcpCatalogDefaultHandlers,
  meetingsHandlers,
  messagesHandlers,
  metaHandlers,
  oauthDefaultHandlers,
  ontologyHandlers,
  projectsHandlers,
  providersHandlers,
  qualityHandlers,
  scalingHandlers,
  settingsHandlers,
  setupHandlers,
  subworkflowsHandlers,
  tasksHandlers,
  templatePacksHandlers,
  trainingHandlers,
  tunnelDefaultHandlers,
  usersHandlers,
  webhooksHandlers,
  workflowExecutionsHandlers,
  workflowsHandlers,
}

// ── Storybook-facing named exports (preserved for existing stories). ──

export { setupStatusComplete, setupStatusNeedsAdmin } from './setup'
export { authLoginSuccess, authSetupSuccess } from './auth'
export { artifactsList } from './artifacts'
export { projectsList } from './projects'
export { templatePacksList } from './template-packs'
export {
  connectionsList,
  emptyConnectionsList,
  integrationHealthList,
  integrationsHandlers,
  mcpCatalogHandlers,
  oauthHandlers,
  tunnelHandlers,
} from './integrations'

// ── Entity builders (exported for per-test use when constructing overrides). ──

export { buildAgent } from './agents'
export { buildApproval } from './approvals'
export { buildArtifact } from './artifacts'
export { buildAuthUser } from './auth'
export { buildManifest as buildBackupManifest, buildBackupInfo } from './backup'
export { buildBudgetConfig } from './budget'
export { buildCeremonyPolicy } from './ceremony-policy'
export {
  buildProfile as buildClientProfile,
  buildRequirement as buildClientRequirement,
  buildRequest as buildClientRequest,
  buildSimulation as buildClientSimulation,
} from './clients'
export { buildCompanyConfig, buildDepartment, buildTeam } from './company'
export { buildConnection } from './connections'
export { buildCheckpoint } from './fine-tuning'
export { buildCustomRule } from './custom-rules'
export { buildEscalation } from './escalations'
export { buildMcpCatalogEntry } from './mcp-catalog'
export { buildMeeting } from './meetings'
export { buildMessage, buildChannel } from './messages'
export { buildEntity } from './ontology'
export { buildProject } from './projects'
export {
  buildCloudPreset,
  buildLocalPreset,
  buildProvider,
  buildProviderPreset,
} from './providers'
export { buildSettingEntry } from './settings'
export { buildAgentSummary as buildSetupAgentSummary } from './setup'
export { buildSubworkflow } from './subworkflows'
export { buildTask } from './tasks'
export { buildPlan as buildTrainingPlan, buildResult as buildTrainingResult } from './training'
export { buildUser } from './users'
export { buildWorkflow } from './workflows'
