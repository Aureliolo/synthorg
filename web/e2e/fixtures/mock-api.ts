import type { Page } from '@playwright/test'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import type {
  SetupCompanyResponse,
  SetupStatusResponse,
} from '@/api/types/setup'

/** Wrap a payload in the dashboard's ``ApiResponse`` success envelope. */
function setupOk(data: unknown) {
  return { success: true, data, error: null, error_detail: null }
}

const SETUP_STATUS_DEFAULTS: SetupStatusResponse = {
  needs_admin: false,
  needs_setup: true,
  has_providers: false,
  has_name_locales: true,
  has_company: false,
  has_agents: false,
  min_password_length: 12,
}

/**
 * Stub ``GET /setup/status`` so the wizard's backend reconcile derives the
 * step-completion map from real backend signals rather than the (removed)
 * client persistence. The wizard is a pure API consumer: it has no
 * localStorage seed, so the only way to land it mid-flow is to mock the
 * backend state it hydrates from. ``has_providers`` / ``has_company`` /
 * ``has_agents`` drive which steps reconcile to complete (see
 * ``reconcileCompletionFromBackend``); a post-company step (agents, complete)
 * becomes directly reachable, while a pre-company step is reached by driving
 * the mode picker (which advances to the first incomplete step).
 */
export async function mockSetupStatus(
  page: Page,
  overrides: Partial<SetupStatusResponse> = {},
): Promise<void> {
  const status: SetupStatusResponse = { ...SETUP_STATUS_DEFAULTS, ...overrides }
  await page.route('**/api/v1/setup/status', (route) =>
    route.fulfill({ json: setupOk(status) }),
  )
}

/**
 * Stub ``GET /setup/company`` so the resume reconcile hydrates a real company
 * (``has_company: true`` makes the wizard fetch it). Without it the reconcile
 * gets a 404 and the Complete step renders its skip-wizard fallback instead of
 * the review UI. POST falls through so a spec can still stub its own create.
 */
export async function mockSetupCompany(
  page: Page,
  company: SetupCompanyResponse,
): Promise<void> {
  await page.route('**/api/v1/setup/company', (route) => {
    if (route.request().method() !== 'GET') {
      route.fallback()
      return
    }
    route.fulfill({ json: setupOk(company) })
  })
}

/**
 * Mock all API endpoints with deterministic data for screenshot testing.
 *
 * Intercepts /api/* requests and returns static JSON responses.
 * This ensures visual regression screenshots are deterministic
 * regardless of backend state.
 */
export async function mockApiRoutes(page: Page) {
  // Catch-all FIRST so the specific stubs below override it. Playwright
  // matches route handlers in LIFO order (last-registered wins), so the
  // broad ``**/api/v1/**`` must be registered before the narrower routes
  // -- otherwise it shadows them and, e.g., ``/analytics/overview`` would
  // resolve to the empty-list envelope, leaving ``overview`` an array and
  // crashing the StatusBar (``overview.tasks_by_status.in_review``). Specs
  // that register their own route after calling ``mockApiRoutes`` still
  // win, since they register later still.
  await page.route('**/api/v1/**', (route) =>
    route.fulfill({
      json: {
        data: [],
        error: null,
        error_detail: null,
        success: true,
        pagination: { total: 0, offset: 0, limit: 10 },
      },
    }),
  )

  // Liveness -- always 200 while the process is alive.
  await page.route('**/api/v1/healthz', (route) =>
    route.fulfill({
      json: {
        success: true,
        data: {
          status: 'ok',
          version: '0.6.4',
          uptime_seconds: 0,
        },
        error: null,
        error_detail: null,
      },
    }),
  )

  // Component-health detail. The status pill in the app shell polls this on
  // every route, so the catch-all's ``data: []`` would leave the roll-up
  // reading ``memory.state`` off an array and take the whole shell down with
  // it -- the same class of break the ``/analytics/overview`` stub below
  // exists to avoid, but on every page rather than one.
  await page.route('**/api/v1/health', (route) =>
    route.fulfill({
      json: {
        success: true,
        data: {
          status: 'ok',
          persistence: true,
          message_bus: true,
          providers: true,
          backup: true,
          memory: { backend: 'sqlvector', detail: null, state: 'durable' },
          telemetry: 'disabled',
          version: '0.6.4',
          uptime_seconds: 0,
        },
        error: null,
        error_detail: null,
      },
    }),
  )

  // Readiness -- returns the full ``ApiResponse<ReadinessStatus>``
  // envelope so the dashboard's ``unwrap()`` call gets the expected
  // ``data`` shape (a bare ``{ status: 'ok' }`` response would fail
  // ``unwrap()`` validation since the wrapper expects ``success`` +
  // ``data`` at the top level).
  await page.route('**/api/v1/readyz', (route) =>
    route.fulfill({
      json: {
        success: true,
        data: {
          status: 'ok',
          persistence: true,
          message_bus: true,
          telemetry: 'disabled',
          version: '0.6.4',
          uptime_seconds: 0,
        },
        error: null,
        error_detail: null,
      },
    }),
  )

  // Auth ticket for WebSocket. Must be the full ``ApiResponse`` envelope:
  // ``getWsTicket`` calls ``unwrap()``, so a bare ``{ ticket }`` throws and
  // the SPA's WebSocket setup fails (no socket is ever constructed, and
  // event-injection tests then hang waiting for the harness stub).
  await page.route('**/api/v1/auth/ws-ticket', (route) =>
    route.fulfill({
      json: {
        success: true,
        data: { ticket: 'mock-ticket', expires_in: 3600 },
        error: null,
        error_detail: null,
      },
    }),
  )

  // Overview metrics. The real endpoint returns an ApiResponse envelope
  // and the client unwrap()s it, so a bare object would make unwrap throw
  // and silently leave the dashboard in an error state.
  await page.route('**/api/v1/analytics/overview', (route) =>
    route.fulfill({
      json: {
        success: true,
        data: {
          total_agents: 8,
          active_agents_count: 5,
          idle_agents_count: 3,
          total_tasks: 24,
          tasks_by_status: {
            created: 2,
            assigned: 4,
            in_progress: 6,
            in_review: 3,
            completed: 7,
            blocked: 1,
            failed: 0,
            interrupted: 1,
            cancelled: 0,
          },
          total_cost: 127.43,
          budget_remaining: 372.57,
          budget_used_percent: 25.5,
          currency: DEFAULT_CURRENCY,
          cost_7d_trend: [
            { timestamp: '2026-03-23T00:00:00Z', value: 15.2 },
            { timestamp: '2026-03-24T00:00:00Z', value: 18.7 },
            { timestamp: '2026-03-25T00:00:00Z', value: 22.1 },
            { timestamp: '2026-03-26T00:00:00Z', value: 19.3 },
            { timestamp: '2026-03-27T00:00:00Z', value: 25.8 },
            { timestamp: '2026-03-28T00:00:00Z', value: 12.9 },
            { timestamp: '2026-03-29T00:00:00Z', value: 13.4 },
          ],
          tasks_7d_trend: [
            { timestamp: '2026-03-23T00:00:00Z', value: 2 },
            { timestamp: '2026-03-24T00:00:00Z', value: 4 },
            { timestamp: '2026-03-25T00:00:00Z', value: 3 },
            { timestamp: '2026-03-26T00:00:00Z', value: 5 },
            { timestamp: '2026-03-27T00:00:00Z', value: 4 },
            { timestamp: '2026-03-28T00:00:00Z', value: 3 },
            { timestamp: '2026-03-29T00:00:00Z', value: 3 },
          ],
          agents_7d_trend: [
            { timestamp: '2026-03-23T00:00:00Z', value: 4 },
            { timestamp: '2026-03-24T00:00:00Z', value: 4 },
            { timestamp: '2026-03-25T00:00:00Z', value: 5 },
            { timestamp: '2026-03-26T00:00:00Z', value: 5 },
            { timestamp: '2026-03-27T00:00:00Z', value: 6 },
            { timestamp: '2026-03-28T00:00:00Z', value: 5 },
            { timestamp: '2026-03-29T00:00:00Z', value: 5 },
          ],
          review_7d_trend: [
            { timestamp: '2026-03-23T00:00:00Z', value: 1 },
            { timestamp: '2026-03-24T00:00:00Z', value: 2 },
            { timestamp: '2026-03-25T00:00:00Z', value: 2 },
            { timestamp: '2026-03-26T00:00:00Z', value: 3 },
            { timestamp: '2026-03-27T00:00:00Z', value: 2 },
            { timestamp: '2026-03-28T00:00:00Z', value: 3 },
            { timestamp: '2026-03-29T00:00:00Z', value: 3 },
          ],
          // Consistent with tasks_by_status: succeeded + empty == reviewable
          // (completed 7 + in_review 3 = 10); failed == tasks_by_status.failed (0).
          task_outcomes: {
            succeeded: 9,
            empty: 1,
            failed: 0,
          },
        },
        error: null,
        error_detail: null,
      },
    }),
  )

  // Tunnel status. The catch-all's ``data: []`` envelope is the wrong
  // shape for ``TunnelSnapshot`` (an object with a mandatory
  // ``providers`` array); the tunnel store would write ``undefined``
  // into ``providers`` and crash the Connections page's TunnelCard.
  await page.route('**/api/v1/integrations/tunnel/status', (route) =>
    route.fulfill({
      json: {
        success: true,
        data: {
          public_url: null,
          selected_provider: 'cloudflare',
          active_provider: null,
          providers: [
            {
              provider_id: 'cloudflare',
              display_name: 'Cloudflare quick tunnel',
              credential_kind: 'none',
              available: true,
              detail: null,
              credential_configured: true,
            },
          ],
        },
        error: null,
        error_detail: null,
      },
    }),
  )
}

/**
 * Freeze Date.now() for deterministic timestamp rendering.
 */
export async function freezeTime(page: Page) {
  await page.addInitScript(() => {
    const fixedTime = new Date('2026-03-29T12:00:00Z').getTime()
    Date.now = () => fixedTime
  })
}

/**
 * Wait for fonts to load before taking screenshots.
 */
export async function waitForFonts(page: Page) {
  await page.evaluate(() => document.fonts.ready)
}
