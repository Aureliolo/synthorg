/**
 * CodSpeed bench for `getLocale()`.
 *
 * Called per format-helper invocation -- so on every table cell, every
 * metric card, every chart label. A regression here multiplies across
 * the whole UI.
 */
import { bench, describe } from 'vitest'

import { getLocale } from '@/utils/locale'

describe('locale resolution', () => {
  bench('getLocale x1000 (cache-warm path)', () => {
    for (let i = 0; i < 1000; i++) {
      getLocale()
    }
  })
})
