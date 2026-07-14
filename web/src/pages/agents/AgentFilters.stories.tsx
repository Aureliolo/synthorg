import type { Meta, StoryObj } from '@storybook/react'
import { MemoryRouter } from 'react-router'
import { AgentFilters } from './AgentFilters'
import { useAgentsStore } from '@/stores/agents'

const meta = {
  title: 'Agents/AgentFilters',
  component: AgentFilters,
  decorators: [
    (Story) => {
      useAgentsStore.setState({
        searchQuery: '',
        departmentFilter: null,
        statusFilter: null,
        sortBy: 'name',
      })
      return (
        <MemoryRouter>
          <div className="p-6 max-w-4xl">
            <Story />
          </div>
        </MemoryRouter>
      )
    },
  ],
} satisfies Meta<typeof AgentFilters>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const WithSearchQuery: Story = {
  decorators: [
    (Story) => {
      useAgentsStore.setState({ searchQuery: 'alice' })
      return <Story />
    },
  ],
}

export const WithFiltersApplied: Story = {
  decorators: [
    (Story) => {
      useAgentsStore.setState({
        departmentFilter: 'engineering',
        statusFilter: 'active',
        sortBy: 'department',
      })
      return <Story />
    },
  ],
}
