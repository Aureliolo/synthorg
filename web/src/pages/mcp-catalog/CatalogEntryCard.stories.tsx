import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { CatalogEntryCard } from './CatalogEntryCard'

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

const filesystemEntry: McpCatalogEntry = {
  id: 'filesystem-mcp',
  name: 'Filesystem',
  description: 'Read, write, and manage files on the local filesystem',
  npm_package: '@modelcontextprotocol/server-filesystem',
  npm_version: null,
  required_connection_type: null,
  required_dialect: null,
  transport: 'stdio',
  capabilities: ['file_read', 'file_write', 'directory_listing'],
  tags: ['filesystem', 'local'],
  credential_env_map: {},
}

const meta = {
  title: 'Pages/McpCatalog/CatalogEntryCard',
  component: CatalogEntryCard,
  tags: ['autodocs'],
  args: {
    onSelect: fn(),
    onInstall: fn(),
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof CatalogEntryCard>

export default meta
type Story = StoryObj<typeof meta>

export const WithConnection: Story = {
  args: { entry: braveEntry, installed: false },
}

export const Connectionless: Story = {
  args: { entry: filesystemEntry, installed: false },
}

export const Installed: Story = {
  args: { entry: braveEntry, installed: true },
}
