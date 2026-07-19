import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { CatalogDetailDrawer } from './CatalogDetailDrawer'

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
  title: 'Pages/McpCatalog/CatalogDetailDrawer',
  component: CatalogDetailDrawer,
  tags: ['autodocs'],
  args: {
    onClose: fn(),
    onInstall: fn(),
    onUninstall: fn(),
  },
} satisfies Meta<typeof CatalogDetailDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const NotInstalled: Story = {
  args: { entry: braveEntry, installed: false },
}

export const Installed: Story = {
  args: { entry: braveEntry, installed: true },
}
