/**
 * Providers MSW handlers barrel. Mirrors the endpoints split 1:1 per
 * the `web/CLAUDE.md` MSW handler MANDATORY: each endpoint sub-file
 * under `@/api/endpoints/providers/` has a sibling handler sub-file
 * here, exporting its own ``*Handlers`` array. The combined
 * ``providersHandlers`` array preserves the historical import path
 * (``@/mocks/handlers/providers``) used by ``test-setup.tsx`` and
 * per-test ``server.use(...)`` overrides.
 */

import { crudHandlers } from './providers/crud'
import { healthHandlers } from './providers/health'
import { modelsHandlers } from './providers/models'
import { auditHandlers } from './providers/audit'
import { rateLimitsHandlers } from './providers/rate-limits'
import { presetsHandlers } from './providers/presets'
import { credentialsHandlers } from './providers/credentials'
import { capabilityAssignmentsHandlers } from './providers/capability-assignments'

export const providersHandlers = [
  // Static ``/providers/capability-assignments`` routes must precede the
  // ``/providers/:name`` detail handler in ``crudHandlers``: MSW matches
  // in registration order, and ``:name`` would otherwise capture the
  // literal ``capability-assignments`` segment and shadow these.
  ...capabilityAssignmentsHandlers,
  ...crudHandlers,
  ...healthHandlers,
  ...modelsHandlers,
  ...auditHandlers,
  ...rateLimitsHandlers,
  ...presetsHandlers,
  ...credentialsHandlers,
]

export {
  buildCloudPreset,
  buildLocalPreset,
  buildProvider,
} from './providers/crud'
