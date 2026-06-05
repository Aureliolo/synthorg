import { createLogger } from '@/lib/logger'

const log = createLogger('csp')

const UNREAD: unique symbol = Symbol('unread')
let cached: string | undefined | typeof UNREAD = UNREAD

/**
 * Read the CSP nonce from the `<meta name="csp-nonce">` tag in `index.html`.
 *
 * The nonce is injected at serve time by Caddy's `templates` directive
 * (substituting `{{placeholder "http.request.uuid"}}` with a per-request
 * UUID). At runtime, this reader parses the meta tag and returns the nonce
 * string, which is passed to Base UI's `CSPProvider` and Motion's
 * `MotionConfig` so every dynamically injected `<style>` element carries
 * the nonce.
 *
 * See `docs/security.md#csp-nonce-infrastructure` for the full flow.
 *
 * The value is read once on first call and cached for the lifetime of the
 * page -- both present and absent results are cached. Missing or invalid
 * values are logged so deployment misconfigurations are visible:
 *
 * - In production, the un-substituted Go template placeholder reaching
 *   the client indicates Caddy's `templates` directive is broken -- this
 *   is logged at ERROR level because the CSP will block every injected
 *   `<style>` tag.
 * - In the Vite dev server (`import.meta.env.DEV`), the placeholder is
 *   always present (Caddy never runs), so the same condition is logged at
 *   DEBUG level to avoid false-positive noise on every local page load.
 *
 * **Threat model note:** The nonce is readable by all same-origin JavaScript,
 * so it does not prevent an attacker who has already achieved XSS from
 * reusing it. Its purpose is to permit Base UI and Motion's
 * dynamically injected `<style>` tags under a CSP that forbids
 * `'unsafe-inline'` on `style-src-elem`. The nonce must be per-request and
 * unpredictable (Caddy's `{http.request.uuid}` placeholder, a 128-bit
 * UUID generated per request) to prevent replay across requests.
 */
/**
 * Log any deployment-readiness issue surfaced by the meta-tag content.
 * Split from the main reader so the caller stays under the complexity
 * cap; the logging branches are themselves a small isolated ladder.
 */
function _logCspNonceIssue(
  meta: HTMLMetaElement | null,
  value: string | undefined,
): void {
  if (!meta) {
    // Missing meta tag: local dev without Caddy in the path, or a
    // deployment misconfiguration. Inline <style> tags will be unsigned.
    log.warn('CSP nonce meta tag missing', {
      impact: 'inline <style> elements will not carry a nonce',
    })
    return
  }
  if (value?.includes('{{placeholder')) {
    // Go template placeholder survived: in production this means Caddy's
    // templates directive is misconfigured and the CSP will block every
    // injected <style>. In the Vite dev server the placeholder is always
    // present because Caddy never runs, so downgrade to DEBUG.
    if (import.meta.env.DEV) {
      log.debug('CSP nonce placeholder present (dev server)', {
        note: 'expected outside Caddy; styles unsigned in dev',
      })
    } else {
      log.error('CSP nonce placeholder not substituted', {
        impact: 'Caddy templates directive is misconfigured: CSP will block inline styles',
      })
    }
    return
  }
  if (!value) {
    log.warn('CSP nonce meta tag present but empty')
  }
}

/**
 * Reject the un-substituted Caddy template placeholder: if
 * `{{placeholder "..."}}` appears literally, the templates directive is
 * misconfigured (or we are in dev), and the value is not a real nonce.
 */
function _validNonce(value: string | undefined): string | undefined {
  if (!value) return undefined
  if (value.includes('{{placeholder')) return undefined
  return value
}

export function getCspNonce(): string | undefined {
  if (cached !== UNREAD) return cached
  const meta = document.querySelector<HTMLMetaElement>(
    'meta[name="csp-nonce"]',
  )
  const value = meta?.content.trim()
  _logCspNonceIssue(meta, value)
  cached = _validNonce(value)
  return cached
}
