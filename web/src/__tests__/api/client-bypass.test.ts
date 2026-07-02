import type { AxiosResponse } from 'axios'
import { vi } from 'vitest'

// Mock dev auth bypass ON. Separate file because vi.mock is file-scoped
// and client.test.ts mocks it OFF.
vi.mock('@/utils/dev', () => ({ IS_DEV_AUTH_BYPASS: true }))

import { setUnauthorizedHandler } from '@/api/unauthorized-handler'
import { apiClient } from '@/api/client'

describe('apiClient 401 response interceptor (dev bypass active)', () => {
  it('notifies the unauthorized handler on 401 even with bypass active', async () => {
    // The auth store decides what a 401 means (dev bypass re-mints the
    // session in place); the interceptor swallowing it would leave an
    // expired dev session permanently broken.
    const handler = vi.fn()
    const unsubscribe = setUnauthorizedHandler(handler)
    try {
      const error = new (await import('axios')).AxiosError(
        'Unauthorized',
        'ERR_BAD_RESPONSE',
        undefined,
        undefined,
        { status: 401, data: {}, headers: {}, statusText: 'Unauthorized', config: {} as AxiosResponse['config'] } as AxiosResponse,
      )

      await expect(apiClient.interceptors.response.handlers?.[0]?.rejected?.(error)).rejects.toBeDefined()
      expect(handler).toHaveBeenCalledTimes(1)
    } finally {
      unsubscribe()
    }
  })
})
