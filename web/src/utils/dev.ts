/**
 * Dev-only auth bypass flag.
 *
 * When true, the app auto-logs-in as the existing admin on startup (via the
 * gated, password-free `POST /auth/dev-login`) so dev work skips the login
 * screen WITHOUT weakening the backend: the session is a real JWT cookie, the
 * backend still enforces auth on every request, and the dev-login endpoint
 * only exists when the backend itself has `SYNTHORG_DEV_AUTH_BYPASS` set. The
 * admin account must already exist (it is never fabricated); if auto-login
 * fails the normal login screen is shown. It does NOT bypass the setup flow:
 * setup status is read from the backend, so the first-run wizard stays
 * reachable. Only active when both conditions are met:
 * - Running in Vite dev mode (import.meta.env.DEV)
 * - VITE_DEV_AUTH_BYPASS=true in web/.env
 *
 * Bracket access (not dot) is deliberate: Vite statically inlines
 * ``import.meta.env.DOT_ACCESS`` at transform time, which would bake the
 * committed ``web/.env`` value into the test bundle and force the bypass on in
 * vitest. Bracket access reads the runtime env object instead, which vitest
 * leaves unset -- so tests get the bypass OFF unless they mock it.
 */
export const IS_DEV_AUTH_BYPASS =
  import.meta.env.DEV && import.meta.env['VITE_DEV_AUTH_BYPASS'] === 'true'
