import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { CatalogDetailDrawer } from './CatalogDetailDrawer'

const githubEntry: McpCatalogEntry = {
  id: 'github-mcp',
  name: 'GitHub',
  description:
    'Read and write GitHub repositories, issues, pull requests, and actions',
  npm_package: '@modelcontextprotocol/server-github',
  npm_version: '2025.4.8',
  required_connection_type: 'github',
  required_dialect: null,
  transport: 'stdio',
  capabilities: ['repository_access', 'issue_management', 'pull_requests', 'actions'],
  tags: ['vcs', 'collaboration', 'ci'],
  credential_env_map: { token: 'GITHUB_PERSONAL_ACCESS_TOKEN' },
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
  args: { entry: githubEntry, installed: false },
}

export const Installed: Story = {
  args: { entry: githubEntry, installed: true },
}
