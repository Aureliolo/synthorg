/**
 * E2E mock-data factories.
 *
 * Each factory returns a deterministic, typed payload that mirrors
 * the dashboard's API response shape. Tests pass overrides to vary
 * single fields without rebuilding the whole object. Factories are
 * pure data builders -- no I/O, no Playwright dependency -- so they
 * are usable from any test or fixture.
 *
 * Issue #1604 / W5a.
 */

export * from './agents'
export * from './approvals'
export * from './budget'
export * from './connections'
export * from './fine-tuning'
export * from './meetings'
export * from './memory'
export * from './messages'
export * from './providers'
export * from './settings'
export * from './setup'
export * from './tasks'
export * from './workflows'
