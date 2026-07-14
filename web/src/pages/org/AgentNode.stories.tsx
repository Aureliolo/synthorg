import type { Meta, StoryObj } from '@storybook/react'
import { ReactFlow, ReactFlowProvider } from '@xyflow/react'
import { AgentNode } from './AgentNode'
import type { AgentNodeData } from './build-org-tree'

const nodeTypes = { agent: AgentNode }

function Wrapper({ data }: { data: AgentNodeData }) {
  return (
    <ReactFlowProvider>
      <div style={{ width: 400, height: 200 }}>
        <ReactFlow
          nodes={[
            {
              id: '1',
              type: 'agent',
              position: { x: 100, y: 50 },
              data,
            },
          ]}
          edges={[]}
          nodeTypes={nodeTypes}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          zoomOnScroll={false}
          panOnDrag={false}
        />
      </div>
    </ReactFlowProvider>
  )
}

const meta = {
  title: 'OrgChart/AgentNode',
  component: Wrapper,
  tags: ['autodocs'],
  parameters: {
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof Wrapper>

export default meta
type Story = StoryObj<typeof meta>

const baseData: AgentNodeData = {
  agentId: 'agent-1',
  name: 'Kenji Matsuda',
  role: 'Full-Stack Developer',
  department: 'engineering',
  runtimeStatus: 'idle',
}

export const Active: Story = {
  args: { data: { ...baseData, runtimeStatus: 'active' } },
}

export const Idle: Story = {
  args: { data: baseData },
}

export const Error: Story = {
  args: { data: { ...baseData, runtimeStatus: 'error', name: 'Sofia Reyes' } },
}

export const Offline: Story = {
  args: { data: { ...baseData, runtimeStatus: 'offline', name: 'Liam Chen' } },
}
