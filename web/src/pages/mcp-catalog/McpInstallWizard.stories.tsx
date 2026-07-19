import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { connectionsList } from '@/mocks/handlers/connections'
import { mcpCatalogHandlers } from '@/mocks/handlers/mcp-catalog'
import { useConnectionsStore } from '@/stores/connections'
import { useMcpCatalogStore } from '@/stores/mcp-catalog'
import { McpInstallWizard } from './McpInstallWizard'

const searchEntry: McpCatalogEntry = {
  id: 'example-search-mcp',
  name: 'Example Search',
  description: 'Web and local search via an example search API',
  npm_package: '@example-org/example-search-mcp-server',
  npm_version: '1.0.0',
  required_connection_type: 'generic_http',
  required_dialect: null,
  transport: 'stdio',
  capabilities: ['web_search', 'local_search'],
  tags: ['search', 'web'],
  credential_env_map: { api_key: 'EXAMPLE_API_KEY' },
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
      useMcpCatalogStore.setState({ entries: [searchEntry] })
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
          entryId: 'example-search-mcp',
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
          entryId: 'example-search-mcp',
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
          entryId: 'example-search-mcp',
          connectionName: 'primary-search',
          errorMessage: null,
          result: {
            status: 'installed',
            server_name: 'Example Search',
            catalog_entry_id: 'example-search-mcp',
            tool_count: 2,
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
          entryId: 'example-search-mcp',
          connectionName: 'primary-search',
          errorMessage: 'Connection type mismatch',
          result: null,
        },
      })
      return <Story />
    },
  ],
}
