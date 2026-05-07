/**
 * Locale source of truth for the dashboard.
 *
 * Parallel to `@/utils/currencies` -- export a fallback constant plus a
 * runtime reader that resolves, in precedence order:
 *
 *   1. The browser's language tag (`navigator.language`), so a user
 *      in Zurich lands on `de-CH` and a user in Paris lands on
 *      `fr-FR` without any configuration.
 *   2. {@link APP_LOCALE_FALLBACK} (`'en'`), a neutral language-only
 *      tag used only when no browser tag is available (SSR, unit
 *      tests).
 *
 * Every formatter helper in `@/utils/format` accepts an optional
 * `locale?: string` parameter and falls back to `getLocale()` when
 * not provided.
 */

/**
 * Last-resort fallback BCP 47 tag. Plain `'en'` is deliberate: it
 * avoids privileging a specific region (US date order, imperial
 * units, etc.) when no browser signal is available. `Intl` picks
 * locale-appropriate defaults from the language subtag alone.
 */
export const APP_LOCALE_FALLBACK = 'en'

/**
 * Pure-compute resolver: pick a locale tag from already-collected
 * inputs. Lives separately from {@link getLocale} so the bench
 * suite can measure the actual resolution work (validation +
 * trim + tag-canonicalisation) without re-entering
 * `navigator.language` on every call.
 *
 * Inputs may be ``null`` / empty; invalid BCP 47 tags throw inside
 * `Intl.getCanonicalLocales` and are caught and skipped. The
 * ``override`` slot stays so a future user-preference reader can
 * thread its value in without the resolver caring about the source.
 */
export function resolveLocale(
  override: string | null | undefined,
  browserLocale: string | null | undefined,
): string {
  if (typeof override === 'string') {
    const trimmed = override.trim()
    if (trimmed.length > 0) {
      try {
        Intl.getCanonicalLocales(trimmed)
        return trimmed
      } catch {
        // fall through
      }
    }
  }
  if (typeof browserLocale === 'string' && browserLocale.length > 0) {
    try {
      Intl.getCanonicalLocales(browserLocale)
      return browserLocale
    } catch {
      // fall through
    }
  }
  return APP_LOCALE_FALLBACK
}

function readBrowserLocale(): string | null {
  if (typeof navigator === 'undefined') return null
  const raw = navigator.language
  if (typeof raw !== 'string' || raw.length === 0) return null
  return raw
}

/**
 * Return the active locale for display formatting.
 *
 * Resolution order: browser language -> {@link APP_LOCALE_FALLBACK}.
 */
export function getLocale(): string {
  return resolveLocale(null, readBrowserLocale())
}
