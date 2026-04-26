/**
 * CodSpeed bench for `getCsrfToken()`.
 *
 * Reads `document.cookie` via the synchronous shim installed by
 * `test-setup.tsx`. Called on every mutating API request that goes
 * through the axios client.
 */
import { bench, describe } from 'vitest'

import { getCsrfToken } from '@/utils/csrf'

describe('CSRF token reader', () => {
  bench('getCsrfToken x500 (cookie present)', () => {
    // Match the seed in test-setup.tsx
    document.cookie = 'csrf_token=test-csrf-token'
    for (let i = 0; i < 500; i++) {
      getCsrfToken()
    }
  })

  bench('getCsrfToken x500 (multiple cookies, target deep)', () => {
    document.cookie = 'pad_a=1'
    document.cookie = 'pad_b=2'
    document.cookie = 'pad_c=3'
    document.cookie = 'csrf_token=test-csrf-token'
    document.cookie = 'pad_d=4'
    for (let i = 0; i < 500; i++) {
      getCsrfToken()
    }
  })
})
