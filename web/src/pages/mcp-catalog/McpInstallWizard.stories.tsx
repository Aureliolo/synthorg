import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { integrationsHandlers } from '@/mocks/handlers/integrations'
import { useConnectionsStore } from '@/stores/connections'
import { useMcpCatalogStore } from '@/stores/mcp-catalog'
import { McpInstallWizard } from './McpInstallWizard'

const githubEntry: McpCatalogEntry = {
  id: 'github-mcp',
  name: 'GitHub',
  description:
    'Read and write GitHub repositories, issues, pull requests, and actions',
  npm_package: '@modelcontextprotocol/server-github',
  required_connection_type: 'github',
  transport: 'stdio',
  capabilities: ['repository_access', 'issue_management', 'pull_requests', 'actions'],
  tags: ['vcs', 'collaboration', 'ci'],
}

const meta = {
  title: 'Pages/McpCatalog/McpInstallWizard',
  component: McpInstallWizard,
  tags: ['autodocs'],
  parameters: {
    msw: { handlers: integrationsHandlers },
  },
  args: {
    onRequestCreateConnection: fn(),
  },
  decorators: [
    (Story) => {
      useMcpCatalogStore.setState({ entries: [githubEntry] })
      useConnectionsStore.setState({
        connections: [
          {
            id: 'conn-primary-github',
            name: 'primary-github',
            connection_type: 'github',
            auth_method: 'bearer_token',
            base_url: null,
            health_check_enabled: true,
            health_status: 'healthy',
            last_health_check_at: null,
            metadata: {},
            rate_limiter: null,
            secret_refs: [],
            webhook_receipt_retention_days: null,
            sensitive: false,
            created_at: '2026-04-01T09:00:00Z',
            updated_at: '2026-04-12T08:00:00Z',
          },
        ],
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof McpInstallWizard>

export default meta
type Story = StoryObj<typeof meta>

export const PickingConnection: Story = {
  decorators: [
    (Story) => {
      useMcpCatalogStore.setState({
        installFlow: 'picking-connection',
        installContext: {
          entryId: 'github-mcp',
          connectionName: null,
          errorMessage: null,
          result: null,
        },
      })
      return <Story />
    },
  ],
}

export const Installing: Story = {
  decorators: [
    (Story) => {
      useMcpCatalogStore.setState({
        installFlow: 'installing',
        installContext: {
          entryId: 'github-mcp',
          connectionName: 'primary-github',
          errorMessage: null,
          result: null,
        },
      })
      return <Story />
    },
  ],
}

export const Done: Story = {
  decorators: [
    (Story) => {
      useMcpCatalogStore.setState({
        installFlow: 'done',
        installContext: {
          entryId: 'github-mcp',
          connectionName: 'primary-github',
          errorMessage: null,
          result: {
            status: 'installed',
            server_name: 'GitHub',
            catalog_entry_id: 'github-mcp',
            tool_count: 4,
          },
        },
      })
      return <Story />
    },
  ],
}

export const ErrorState: Story = {
  decorators: [
    (Story) => {
      useMcpCatalogStore.setState({
        installFlow: 'error',
        installContext: {
          entryId: 'github-mcp',
          connectionName: 'primary-github',
          errorMessage: 'Connection type mismatch',
          result: null,
        },
      })
      return <Story />
    },
  ],
}
