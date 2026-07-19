import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { connectionsList } from '@/mocks/handlers/connections'
import { mcpCatalogHandlers } from '@/mocks/handlers/mcp-catalog'
import { useConnectionsStore } from '@/stores/connections'
import { useMcpCatalogStore } from '@/stores/mcp-catalog'
import { McpInstallWizard } from './McpInstallWizard'

const braveEntry: McpCatalogEntry = {
  id: 'brave-search-mcp',
  name: 'Brave Search',
  description: 'Web and local search via the Brave Search API',
  npm_package: '@brave/brave-search-mcp-server',
  npm_version: '2.1.0',
  required_connection_type: 'generic_http',
  required_dialect: null,
  transport: 'stdio',
  capabilities: ['web_search', 'local_search'],
  tags: ['search', 'web'],
  credential_env_map: { api_key: 'BRAVE_API_KEY' },
}

const meta = {
  title: 'Pages/McpCatalog/McpInstallWizard',
  component: McpInstallWizard,
  tags: ['autodocs'],
  parameters: {
    msw: { handlers: [...connectionsList, ...mcpCatalogHandlers] },
  },
  args: {
    onRequestCreateConnection: fn(),
  },
  decorators: [
    (Story) => {
      useMcpCatalogStore.setState({ entries: [braveEntry] })
      useConnectionsStore.setState({
        connections: [
          {
            id: 'conn-primary-search',
            name: 'primary-search',
            connection_type: 'generic_http',
            auth_method: 'api_key',
            base_url: null,
            health_check_enabled: true,
            health: { status: 'healthy', last_check_at: null },
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
          entryId: 'brave-search-mcp',
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
          entryId: 'brave-search-mcp',
          connectionName: 'primary-search',
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
          entryId: 'brave-search-mcp',
          connectionName: 'primary-search',
          errorMessage: null,
          result: {
            status: 'installed',
            server_name: 'GitHub',
            catalog_entry_id: 'brave-search-mcp',
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
          entryId: 'brave-search-mcp',
          connectionName: 'primary-search',
          errorMessage: 'Connection type mismatch',
          result: null,
        },
      })
      return <Story />
    },
  ],
}
