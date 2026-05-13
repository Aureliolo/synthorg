import type { Meta, StoryObj } from '@storybook/react'
import { http, HttpResponse } from 'msw'
import type { getHealth } from '@/api/endpoints/health'
import { ErrorCategory, ErrorCode } from '@/api/types/errors'
import { apiError, successFor } from '@/mocks/handlers/helpers'
import { Button } from './button'
import { HealthPopover } from './health-popover'

const meta = {
  title: 'Overlays/HealthPopover',
  component: HealthPopover,
  tags: ['autodocs'],
  parameters: {
    layout: 'centered',
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof HealthPopover>

export default meta
type Story = StoryObj<typeof meta>

const BASE_PAYLOAD = {
  status: 'ok' as const,
  persistence: true,
  message_bus: true,
  telemetry: 'disabled' as const,
  version: '0.6.4',
  uptime_seconds: 847_200,
}

export const AllSystemsOk: Story = {
  args: {
    children: <Button size="sm">All systems normal</Button>,
  },
  parameters: {
    msw: {
      handlers: [
        http.get('/api/v1/readyz', () =>
          HttpResponse.json(successFor<typeof getHealth>(BASE_PAYLOAD)),
        ),
      ],
    },
  },
}

export const Degraded: Story = {
  args: {
    children: <Button size="sm">System degraded</Button>,
  },
  parameters: {
    msw: {
      handlers: [
        http.get('/api/v1/readyz', () =>
          HttpResponse.json(
            successFor<typeof getHealth>({
              ...BASE_PAYLOAD,
              status: 'unavailable',
              message_bus: false,
            }),
          ),
        ),
      ],
    },
  },
}

export const Down: Story = {
  args: {
    children: <Button size="sm">System down</Button>,
  },
  parameters: {
    msw: {
      handlers: [
        http.get('/api/v1/readyz', () =>
          HttpResponse.json(
            successFor<typeof getHealth>({
              ...BASE_PAYLOAD,
              status: 'unavailable',
              persistence: false,
              message_bus: false,
            }),
          ),
        ),
      ],
    },
  },
}

export const LoadError: Story = {
  args: {
    children: <Button size="sm">Health unavailable</Button>,
  },
  parameters: {
    msw: {
      handlers: [
        http.get('/api/v1/readyz', () =>
          HttpResponse.json(
            apiError('Service unavailable: dependency probe failed.', {
              error_code: ErrorCode.SERVICE_UNAVAILABLE,
              error_category: ErrorCategory.INTERNAL,
              retryable: true,
              retry_after: 30,
              instance: '/storybook',
              title: 'Service unavailable',
              type: 'about:blank',
            }),
            { status: 503 },
          ),
        ),
      ],
    },
  },
}

export const Loading: Story = {
  args: {
    children: <Button size="sm">Fetching health...</Button>,
  },
  parameters: {
    msw: {
      handlers: [
        http.get('/api/v1/readyz', async () => {
          await new Promise((resolve) => { setTimeout(resolve, 10_000) })
          return HttpResponse.json(successFor<typeof getHealth>(BASE_PAYLOAD))
        }),
      ],
    },
  },
}

// Hover: HealthPopover opens on click (via Base UI Popover), not hover.
// There is no distinct hover visual state beyond the button's own hover ring,
// so this intentionally reuses the happy-path story for visual-regression
// coverage rather than exposing a separate "hover" artefact.
export const Hover = AllSystemsOk

// Empty: the popover always renders a health summary while the probe resolves
// or after it succeeds. There is no "no data" surface to document -- the empty
// state is represented by `Loading` (probe in flight) and `LoadError` (probe
// rejected).
export const Empty = Loading
