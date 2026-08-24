import type { Meta, StoryObj } from '@storybook/react'
import { AgentCard } from './agent-card'

const meta = {
  title: 'UI/AgentCard',
  component: AgentCard,
  tags: ['autodocs'],
  argTypes: {
    status: {
      control: 'select',
      options: ['active', 'idle', 'error', 'offline'],
    },
  },
} satisfies Meta<typeof AgentCard>

export default meta
type Story = StoryObj<typeof meta>

export const Active: Story = {
  args: {
    name: 'Alice Smith',
    role: 'Software Engineer',
    department: 'Engineering',
    status: 'active',
    currentTask: 'Fix authentication bug in login flow',
    timestamp: '2m ago',
  },
}

export const Idle: Story = {
  args: {
    name: 'Bob Jones',
    role: 'Marketing Manager',
    department: 'Marketing',
    status: 'idle',
    timestamp: '15m ago',
  },
}

export const Error: Story = {
  args: {
    name: 'Carol White',
    role: 'Data Analyst',
    department: 'Analytics',
    status: 'error',
    currentTask: 'Generate quarterly report',
    timestamp: '1m ago',
  },
}

export const Offline: Story = {
  args: {
    name: 'Dave Brown',
    role: 'Designer',
    department: 'Design',
    status: 'offline',
    timestamp: '2h ago',
  },
}

export const WithTimestampTitle: Story = {
  args: {
    name: 'Frank Green',
    role: 'Platform Engineer',
    department: 'Engineering',
    status: 'active',
    timestamp: '3 days ago',
    timestampIso: '2026-06-15T09:30:00Z',
  },
}

export const LongTaskName: Story = {
  args: {
    name: 'Eve Thompson',
    role: 'Full Stack Developer',
    department: 'Engineering',
    status: 'active',
    currentTask: 'Implement comprehensive end-to-end testing suite for the authentication module with full coverage',
    timestamp: '5m ago',
  },
}

export const FullProfile: Story = {
  args: {
    name: 'Zita Sedliak',
    role: 'CEO',
    department: 'Executive',
    status: 'active',
    model: 'example-expert-001',
    capability: 'expert',
    capabilities: ['reasoning', 'vision'],
    timestamp: '2m ago',
  },
}

export const CapabilitiesUnverified: Story = {
  args: {
    name: 'Rodrigo Sepúlveda',
    role: 'UX Designer',
    department: 'Design',
    status: 'active',
    model: 'example-expert-001',
    capability: 'expert',
    capabilitiesUnverified: true,
    timestamp: '6m ago',
  },
}

export const ToolCallingUnavailable: Story = {
  args: {
    name: 'Isobel Lúnam',
    role: 'Automation Engineer',
    department: 'Quality Assurance',
    status: 'active',
    model: 'example-basic-001',
    capability: 'basic',
    capabilities: ['reasoning'],
    toolCallsFailed: true,
    timestamp: '8m ago',
  },
}

export const ModelBindingUnresolved: Story = {
  args: {
    name: 'Grete Hermann',
    role: 'Data Engineer',
    department: 'Data Analytics',
    status: 'active',
    model: 'example-removed-001',
    modelBindingUnresolved: true,
    timestamp: '10m ago',
  },
}

export const CapabilitiesUnavailable: Story = {
  args: {
    name: 'Wangari Maathai',
    role: 'Platform Engineer',
    department: 'Infrastructure',
    status: 'active',
    model: 'example-capable-001',
    capability: 'capable',
    capabilitiesUnavailable: true,
    timestamp: '12m ago',
  },
}

export const TierWithoutModel: Story = {
  args: {
    name: 'Grace Hopper',
    role: 'Principal Engineer',
    department: 'Engineering',
    status: 'active',
    capability: 'capable',
    timestamp: '4m ago',
  },
}

export const AgentGrid: Story = {
  args: { name: 'Alice Smith', role: 'Engineer', department: 'Engineering', status: 'active' },
  render: () => (
    <div className="grid grid-cols-3 gap-grid-gap max-w-3xl">
      <AgentCard
        name="Alice Smith"
        role="Software Engineer"
        department="Engineering"
        status="active"
        currentTask="Fix auth bug"
        timestamp="2m ago"
      />
      <AgentCard
        name="Bob Jones"
        role="Marketing Manager"
        department="Marketing"
        status="idle"
        timestamp="15m ago"
      />
      <AgentCard
        name="Carol White"
        role="Data Analyst"
        department="Analytics"
        status="error"
        currentTask="Generate report"
        timestamp="1m ago"
      />
    </div>
  ),
}
