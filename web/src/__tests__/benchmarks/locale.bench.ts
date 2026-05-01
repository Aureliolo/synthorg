/**
 * CodSpeed bench for the locale-resolver hot path.
 *
 * Called per format-helper invocation -- so on every table cell, every
 * metric card, every chart label. A regression here multiplies across
 * the whole UI.
 *
 * The benchmark targets ``resolveLocale``, the pure-compute resolver
 * extracted from ``getLocale``. Benching ``getLocale`` directly is
 * incorrect: it re-reads ``useSettingsStore`` state and
 * ``navigator.language`` on every call, so the measurement conflates
 * three things -- store access, ``navigator`` access, and the actual
 * resolution work. The pure resolver lets the bench isolate
 * validation + trim + ``Intl.getCanonicalLocales`` over three
 * deterministic input shapes.
 */
import { bench, describe } from 'vitest'

import { resolveLocale } from '@/utils/locale'

const VALID_OVERRIDE = 'fr-FR'
const VALID_BROWSER = 'en-GB'
const INVALID_OVERRIDE = '!!not-a-tag!!' // exercises the catch + fall-through path
const PADDED_OVERRIDE = '   de-CH   ' // exercises the trim path

describe('resolveLocale', () => {
  bench('resolveLocale x1000 (override-hit)', () => {
    for (let i = 0; i < 1000; i++) {
      resolveLocale(VALID_OVERRIDE, VALID_BROWSER)
    }
  })

  bench('resolveLocale x1000 (override-trim path)', () => {
    for (let i = 0; i < 1000; i++) {
      resolveLocale(PADDED_OVERRIDE, VALID_BROWSER)
    }
  })

  bench('resolveLocale x1000 (browser fallback after invalid override)', () => {
    for (let i = 0; i < 1000; i++) {
      resolveLocale(INVALID_OVERRIDE, VALID_BROWSER)
    }
  })

  bench('resolveLocale x1000 (full-fallback to APP_LOCALE_FALLBACK)', () => {
    for (let i = 0; i < 1000; i++) {
      resolveLocale(null, null)
    }
  })
})
