/**
 * Providers endpoints barrel. Sub-resource modules live under
 * ``./providers/`` and split the surface into crud, health, models,
 * audit, rate-limits, presets, and credentials so each file fits
 * the four ESLint caps. Consumer imports
 * (``@/api/endpoints/providers``) keep working unchanged.
 */

export {
  createFromPreset,
  createProvider,
  deleteProvider,
  getProvider,
  getProviderModels,
  listPresets,
  listProviders,
  testConnection,
  updateProvider,
} from './providers/crud'

export {
  addAllowlistEntry,
  discoverModels,
  getDiscoveryPolicy,
  getProviderHealth,
  probeLocal,
  removeAllowlistEntry,
} from './providers/health'

export {
  addProviderModel,
  deleteModel,
  pullModel,
  syncProviderModels,
  updateModelConfig,
} from './providers/models'

export { listProviderAudit } from './providers/audit'
export { getProviderRateLimits, updateProviderRateLimits } from './providers/rate-limits'
export {
  deletePresetOverride,
  getPresetOverride,
  updatePresetOverride,
} from './providers/presets'
export { rotateProviderCredentials } from './providers/credentials'
