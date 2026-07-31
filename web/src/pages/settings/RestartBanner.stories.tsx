import type { Meta, StoryObj } from '@storybook/react'
import { http, HttpResponse } from 'msw'
import { RestartBanner } from './RestartBanner'
import type { PendingRestartSetting } from '@/api/types/system'

const API = '/api/v1/meta/restart'

function pendingSetting(key: string): PendingRestartSetting {
  return {
    namespace: 'memory',
    key,
    description: `What ${key} does`,
    updated_at: '2026-07-31T09:00:00Z',
  }
}

/**
 * Each story sets what the backend answers rather than a prop combination:
 * the banner takes no props because the state it renders is the backend's.
 */
function statusHandler(pending: PendingRestartSetting[], supervised: boolean) {
  return http.get(API, () =>
    HttpResponse.json({ success: true, data: { pending, supervised } }),
  )
}

const meta: Meta<typeof RestartBanner> = {
  title: 'Settings/RestartBanner',
  component: RestartBanner,
}
export default meta

type Story = StoryObj<typeof meta>

export const Singular: Story = {
  beforeEach({ msw }) {
    msw.use(statusHandler([pendingSetting('embedder_model')], true))
  },
}

export const Plural: Story = {
  beforeEach({ msw }) {
    msw.use(
      statusHandler(
        [pendingSetting('embedder_model'), pendingSetting('embedder_dims')],
        true,
      ),
    )
  },
}

export const Unsupervised: Story = {
  beforeEach({ msw }) {
    msw.use(statusHandler([pendingSetting('embedder_model')], false))
  },
}

export const StatusUnreadable: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get(API, () =>
        HttpResponse.json(
          { success: false, error: 'Settings service unavailable' },
          { status: 503 },
        ),
      ),
    )
  },
}

export const Hidden: Story = {
  beforeEach({ msw }) {
    msw.use(statusHandler([], true))
  },
}
