/**
 * Per-route browser-tab title lookup.
 *
 * AppLayout watches ``location.pathname`` and applies the resolved
 * title to ``document.title`` on every navigation; without this, the
 * tab title remained whatever the last page-level ``document.title =
 * ...`` assignment set (e.g. "MCP Catalog · SynthOrg" persisting into
 * Settings).
 *
 * The lookup is two-stage:
 *  1. Exact path match (covers the static top-level routes).
 *  2. Pattern match for ``/<segment>/<param>`` shaped detail routes,
 *     where the leading segment maps to a section title.
 *
 * Anything unmapped falls back to the bare "SynthOrg" label rather
 * than a stale per-page title.
 */

import { ROUTES } from './routes'

const SUFFIX = ' · SynthOrg'

/** Exact-match route titles, keyed by pathname. */
const EXACT_TITLES: Record<string, string> = {
  [ROUTES.DASHBOARD]: 'Dashboard',
  [ROUTES.LOGIN]: 'Sign in',
  [ROUTES.SETUP]: 'Setup',
  [ROUTES.ORG]: 'Org Chart',
  [ROUTES.ORG_EDIT]: 'Edit Organization',
  [ROUTES.TASKS]: 'Tasks',
  [ROUTES.BUDGET]: 'Budget',
  [ROUTES.BUDGET_FORECAST]: 'Budget Forecast',
  [ROUTES.REPORTS]: 'Reports',
  [ROUTES.APPROVALS]: 'Approvals',
  [ROUTES.SCALING]: 'Scaling',
  [ROUTES.META]: 'Meta',
  [ROUTES.AGENTS]: 'Agents',
  [ROUTES.TRAINING]: 'Training',
  [ROUTES.MESSAGES]: 'Messages',
  [ROUTES.MEETINGS]: 'Meetings',
  [ROUTES.PROVIDERS]: 'Providers',
  [ROUTES.PROJECTS]: 'Projects',
  [ROUTES.ARTIFACTS]: 'Artifacts',
  [ROUTES.WORKFLOWS]: 'Workflows',
  [ROUTES.WORKFLOW_EDITOR]: 'Workflow Editor',
  [ROUTES.SUBWORKFLOWS]: 'Subworkflows',
  [ROUTES.WEBHOOK_RECEIPTS]: 'Webhook Receipts',
  [ROUTES.COORDINATION_METRICS]: 'Coordination Metrics',
  [ROUTES.META_ANALYTICS]: 'Meta Analytics',
  [ROUTES.PERSONALITIES_ADMIN]: 'Personalities',
  [ROUTES.BUDGET_VERSIONS]: 'Budget Versions',
  [ROUTES.COMPANY_VERSIONS]: 'Org Versions',
  [ROUTES.EVALUATION_VERSIONS]: 'Evaluation Versions',
  [ROUTES.ONTOLOGY]: 'Ontology',
  [ROUTES.CUSTOM_RULES]: 'Custom Rules',
  [ROUTES.ESCALATIONS]: 'Escalations',
  [ROUTES.USERS]: 'Users',
  [ROUTES.CONNECTIONS]: 'Connections',
  [ROUTES.OAUTH_APPS]: 'OAuth Apps',
  [ROUTES.MCP_CATALOG]: 'MCP Catalog',
  [ROUTES.SETTINGS]: 'Settings',
  [ROUTES.SETTINGS_SINKS]: 'Log Sinks',
  [ROUTES.SETTINGS_CEREMONY_POLICY]: 'Ceremony Policy',
  [ROUTES.SETTINGS_FINE_TUNING]: 'Fine-Tuning',
  [ROUTES.CLIENTS]: 'Clients',
  [ROUTES.REQUEST_QUEUE]: 'Request Queue',
  [ROUTES.SIMULATION_DASHBOARD]: 'Simulations',
}

/**
 * First-segment → section title.  Used as the title for any detail
 * route under the section (we don't have the entity name in scope
 * here; pages can still call ``document.title = ...`` themselves if
 * they want to surface it once the entity loads).
 */
const SECTION_TITLES: Record<string, string> = {
  agents: 'Agent',
  tasks: 'Task',
  meetings: 'Meeting',
  providers: 'Provider',
  projects: 'Project',
  artifacts: 'Artifact',
  workflows: 'Workflow',
  clients: 'Client',
  settings: 'Settings',
  setup: 'Setup',
}

export function titleForPath(pathname: string): string {
  const exact = EXACT_TITLES[pathname]
  if (exact) return exact + SUFFIX
  // First non-empty segment is the section discriminator; this catches
  // detail routes like ``/projects/abc-123`` -> ``Project · SynthOrg``.
  const firstSegment = pathname.split('/').find((seg) => seg.length > 0)
  if (firstSegment && SECTION_TITLES[firstSegment]) {
    return SECTION_TITLES[firstSegment] + SUFFIX
  }
  return 'SynthOrg'
}
