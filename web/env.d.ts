/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string | undefined
  /**
   * Build identifier baked into the bundle at build time by
   * `vite.config.ts` (defaults to `package.json#version`; CI can
   * override with `SYNTHORG_BUILD_ID`). Consumed by
   * `@/utils/app-version` to gate the post-upgrade stale-cookie
   * recovery flow.
   */
  readonly VITE_APP_BUILD_ID: string | undefined
  /**
   * Dev-only auth bypass (see `@/utils/dev`). When `'true'` in a Vite dev
   * build, the app auto-logs-in as the existing admin via the gated,
   * password-free `/auth/dev-login` endpoint. Never set in a production build.
   */
  readonly VITE_DEV_AUTH_BYPASS: string | undefined
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
