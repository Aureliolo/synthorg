import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse } from 'msw'
import { fn } from 'storybook/test'
import { successFor } from '@/mocks/handlers/helpers'
import type { scanAccessibleRepos } from '@/api/endpoints/connections'
import type { ForgeAccessibleRepo } from '@/api/types/integrations'
import { RepoScopePicker } from './RepoScopePicker'

const scanHandler = (repos: readonly ForgeAccessibleRepo[]) =>
  http.get('/api/v1/connections/:name/accessible-repos', () =>
    HttpResponse.json(successFor<typeof scanAccessibleRepos>(repos)),
  )

const meta = {
  title: 'Pages/Connections/RepoScopePicker',
  component: RepoScopePicker,
  tags: ['autodocs'],

  beforeEach({ msw }) {
    msw.use(scanHandler([
      { owner: 'acme', repo: 'web-app', permission: 'admin', private: true },
      { owner: 'acme', repo: 'api-service', permission: 'write', private: false },
      { owner: 'acme', repo: 'docs-site', permission: 'read', private: false },
    ]))
  },

  args: {
    connectionName: 'primary-forge',
    selected: [],
    onChange: fn(),
  }
} satisfies Meta<typeof RepoScopePicker>

export default meta
type Story = StoryObj<typeof meta>

export const Empty: Story = {}

export const WithSelection: Story = {
  args: {
    selected: ['acme/web-app'],
  },
}

export const WithWildcardScope: Story = {
  args: {
    selected: ['acme/*'],
  },
}

export const WithStaleSelection: Story = {
  args: {
    selected: ['acme/web-app', 'acme/removed-repo'],
  },
}

export const NoReachableRepos: Story = {
  beforeEach({ msw }) {
    msw.use(scanHandler([]))
  }
}

export const ScanFails: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('/api/v1/connections/:name/accessible-repos', () =>
      HttpResponse.json({ error: { message: 'Token lacks repo scope' } }, { status: 403 }),
    ))
  }
}
