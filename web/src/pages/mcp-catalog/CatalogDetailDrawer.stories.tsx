import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { CatalogDetailDrawer } from './CatalogDetailDrawer'

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
  args: { entry: searchEntry, installed: false },
}

export const Installed: Story = {
  args: { entry: searchEntry, installed: true },
}
