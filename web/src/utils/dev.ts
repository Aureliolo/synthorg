/**
 * Dev-only auth bypass flag.
 *
 * When true, AuthGuard and SetupGuard are bypassed with a fake token
 * and user. Only active when both conditions are met:
 * - Running in Vite dev mode (import.meta.env.DEV)
 * - VITE_DEV_AUTH_BYPASS=true in web/.env
 */
export const IS_DEV_AUTH_BYPASS =
  import.meta.env.DEV && import.meta.env['VITE_DEV_AUTH_BYPASS'] === 'true'
