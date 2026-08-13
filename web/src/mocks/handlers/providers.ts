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
import { capabilitySourcesHandlers } from './providers/capability-sources'

export const providersHandlers = [
  // ``crudHandlers`` goes last because it owns ``/providers/:name``, and MSW
  // matches in registration order: a single-segment pattern registered first
  // captures every sibling literal (``capability-assignments``,
  // ``discovery-policy``, ``serviceability``) and answers it with a provider.
  // The backend has no such hazard, since Litestar ranks a literal segment
  // above a path parameter regardless of declaration order.
  ...capabilityAssignmentsHandlers,
  ...capabilitySourcesHandlers,
  ...healthHandlers,
  ...modelsHandlers,
  ...auditHandlers,
  ...rateLimitsHandlers,
  ...presetsHandlers,
  ...credentialsHandlers,
  ...crudHandlers,
]

export {
  buildCloudPreset,
  buildLocalPreset,
  buildProvider,
} from './providers/crud'
