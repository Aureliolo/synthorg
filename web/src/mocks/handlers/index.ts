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
 * - A story that wants a whole domain's defaults imports the per-domain
 *   array straight from its source file (e.g.
 *   `import { setupHandlers } from '@/mocks/handlers/setup'`); a story
 *   that wants a specific scenario imports the named export below.
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
  emptyPage,
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
import { auditHandlers } from './audit'
import { authHandlers } from './auth'
import { backupHandlers } from './backup'
import { budgetHandlers } from './budget'
import { capabilitiesHandlers } from './capabilities'
import { charterHandlers } from './charter'
import { ceremonyPolicyHandlers } from './ceremony-policy'
import { clientsHandlers } from './clients'
import { cockpitHandlers } from './cockpit'
import { collaborationHandlers } from './collaboration'
import { companyHandlers } from './company'
import { connectionsHandlers } from './connections'
import { coordinationHandlers } from './coordination'
import { customRulesHandlers } from './custom-rules'
import { escalationsHandlers } from './escalations'
import { fineTuningHandlers } from './fine-tuning'
import { healthHandlers } from './health'
import { integrationHealthHandlers } from './integration-health'
import { knowledgeHandlers } from './knowledge'
import { learningHandlers } from './learning'
import { mcpCatalogDefaultHandlers } from './mcp-catalog'
import { meetingsHandlers } from './meetings'
import { messagesHandlers } from './messages'
import { metaHandlers } from './meta'
import { oauthDefaultHandlers } from './oauth'
import { ontologyHandlers } from './ontology'
import { personalitiesHandlers } from './personalities'
import { projectBrainHandlers } from './projectBrain'
import { projectDocsHandlers } from './projectDocs'
import { projectsHandlers } from './projects'
import { providersHandlers } from './providers'
import { qualityHandlers } from './quality'
import { reportsHandlers } from './reports'
import { rolesHandlers } from './roles'
import { scalingHandlers } from './scaling'
import { settingsHandlers } from './settings'
import { setupHandlers } from './setup'
import { steeringHandlers } from './steering'
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
  ...auditHandlers,
  ...authHandlers,
  ...backupHandlers,
  ...budgetHandlers,
  ...capabilitiesHandlers,
  ...charterHandlers,
  ...ceremonyPolicyHandlers,
  ...clientsHandlers,
  ...cockpitHandlers,
  ...collaborationHandlers,
  ...companyHandlers,
  ...connectionsHandlers,
  ...coordinationHandlers,
  ...customRulesHandlers,
  ...escalationsHandlers,
  ...fineTuningHandlers,
  ...healthHandlers,
  ...integrationHealthHandlers,
  ...knowledgeHandlers,
  ...learningHandlers,
  ...mcpCatalogDefaultHandlers,
  ...meetingsHandlers,
  ...messagesHandlers,
  ...metaHandlers,
  ...oauthDefaultHandlers,
  ...ontologyHandlers,
  ...personalitiesHandlers,
  ...projectBrainHandlers,
  ...projectDocsHandlers,
  ...projectsHandlers,
  ...providersHandlers,
  ...qualityHandlers,
  ...reportsHandlers,
  ...rolesHandlers,
  ...scalingHandlers,
  ...settingsHandlers,
  ...setupHandlers,
  ...steeringHandlers,
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

// ── Named scenario exports consumed by Storybook stories. ──

export { setupStatusComplete, setupStatusNeedsAdmin } from './setup'
export { authLoginSuccess, authSetupSuccess } from './auth'

// ── Entity builders consumed by store unit tests. ──

export { buildAuditEntry } from './audit'
export { buildCharter } from './charter'
export { buildConnection } from './connections'
export { buildCustomRule } from './custom-rules'
export { buildCloudPreset, buildLocalPreset } from './providers'
export { buildSimulation } from './clients'
