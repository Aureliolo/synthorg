import type { BuildOptions } from 'vite'

/**
 * Log codes the build gate lets through.
 *
 * `CIRCULAR_DEPENDENCY` is owned by a dedicated gate (`npm run
 * lint:circular`), which reports the whole cycle rather than the one edge
 * the bundler happens to notice first. `THIS_IS_UNDEFINED` fires on
 * transpiled dependency code we do not author. Both are the codes Vite's
 * own default handler already drops, so escalating them here would fail
 * the build on output nobody has ever seen.
 */
export const NON_BLOCKING_LOG_CODES: ReadonlySet<string> = new Set([
  'CIRCULAR_DEPENDENCY',
  'THIS_IS_UNDEFINED',
])

type BuildLogHandler = NonNullable<NonNullable<BuildOptions['rolldownOptions']>['onLog']>

/**
 * Turn every bundler warning into a build failure.
 *
 * Vite's default handler only prints a warning, so a name collision between
 * star re-exports (`NAMESPACE_CONFLICT`), a sourcemap a plugin dropped, or
 * any future diagnostic scrolls past a green build and ships. Failing here
 * is what makes those codes actionable rather than decorative.
 *
 * Lives outside `vite.config.ts` so a test can import it without executing
 * the config, which resolves paths from `import.meta.url` and needs a real
 * file URL to do it.
 */
export const failBuildOnWarning: BuildLogHandler = (level, log, defaultHandler) => {
  if (level === 'warn' && !NON_BLOCKING_LOG_CODES.has(log.code ?? '')) {
    const code = log.code ?? 'UNCODED_WARNING'
    throw new Error(`Bundler warning ${code} promoted to an error: ${log.message}`)
  }
  defaultHandler(level, log)
}
