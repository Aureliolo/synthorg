import { http, HttpResponse } from 'msw'
import type { rotateProviderCredentials } from '@/api/endpoints/providers'
import { successFor } from '../helpers'
import { buildProvider } from './crud'

export const credentialsHandlers = [
  http.post('/api/v1/providers/:name/credentials/rotate', () =>
    HttpResponse.json(
      successFor<typeof rotateProviderCredentials>(buildProvider()),
    ),
  ),
]
