import { http, HttpResponse } from 'msw'
import type { listInterrupts, resumeInterrupt } from '@/api/endpoints/interrupts'
import { successFor } from './helpers'

export const interruptsHandlers = [
  http.get('/api/v1/interrupts', () =>
    HttpResponse.json(successFor<typeof listInterrupts>([])),
  ),
  http.post('/api/v1/interrupts/:id/resume', () =>
    HttpResponse.json(successFor<typeof resumeInterrupt>({ status: 'resumed' })),
  ),
]
