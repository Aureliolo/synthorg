import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse } from 'msw'
import { apiSuccess } from '@/mocks/handlers/helpers'
import { ProviderConfigDiagnosticsBanner } from './ProviderConfigDiagnosticsBanner'
import type { ProviderConfigDiagnostics } from '@/api/types/providers'

const meta = {
  title: 'Providers/ProviderConfigDiagnosticsBanner',
  component: ProviderConfigDiagnosticsBanner,
  parameters: { layout: 'padded' },
} satisfies Meta<typeof ProviderConfigDiagnosticsBanner>

export default meta
type Story = StoryObj<typeof meta>

function diagnosticsHandler(data: ProviderConfigDiagnostics) {
  return [
    http.get('/api/v1/providers/config-diagnostics', () =>
      HttpResponse.json(apiSuccess(data)),
    ),
  ]
}

/** The overwhelming case: nothing to say, so nothing is rendered. */
export const Clean: Story = {
  parameters: {
    msw: {
      handlers: diagnosticsHandler({
        status: 'ok',
        rejected: [],
        coerced: [],
        detail: null,
      }),
    },
  },
}

/** Some connections were dropped; the rest are serving. */
export const Partial: Story = {
  parameters: {
    msw: {
      handlers: diagnosticsHandler({
        status: 'partial',
        rejected: [{ name: 'example-local', reason: 'driver: too short' }],
        coerced: [],
        detail: null,
      }),
    },
  },
}

/** Nothing usable could be read, and this is not an empty company. */
export const Unreadable: Story = {
  parameters: {
    msw: {
      handlers: diagnosticsHandler({
        status: 'unreadable',
        rejected: [],
        coerced: [],
        detail: 'schema_version: Field required',
      }),
    },
  },
}

/** An unreadable blob whose entries each failed on their own. */
export const UnreadableNamingEveryConnection: Story = {
  parameters: {
    msw: {
      handlers: diagnosticsHandler({
        status: 'unreadable',
        rejected: [
          { name: 'example-local', reason: 'driver: too short' },
          { name: 'example-cloud', reason: 'connection_name: Field required' },
        ],
        coerced: [],
        detail: null,
      }),
    },
  },
}
