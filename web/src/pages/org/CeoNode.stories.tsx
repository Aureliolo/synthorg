import type { Meta, StoryObj } from '@storybook/react'
import { ReactFlow, ReactFlowProvider } from '@xyflow/react'
import { CeoNode } from './CeoNode'
import type { CeoNodeData } from './build-org-tree'

const nodeTypes = { ceo: CeoNode }

function Wrapper({ data }: { data: CeoNodeData }) {
  return (
    <ReactFlowProvider>
      <div style={{ width: 400, height: 200 }}>
        <ReactFlow
          nodes={[
            {
              id: '1',
              type: 'ceo',
              position: { x: 80, y: 40 },
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
  title: 'OrgChart/CeoNode',
  component: Wrapper,
  tags: ['autodocs'],
  parameters: {
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof Wrapper>

export default meta
type Story = StoryObj<typeof meta>

export const Active: Story = {
  args: {
    data: {
      agentId: 'ceo-1',
      name: 'Amara Okafor',
      role: 'CEO',
      department: 'executive',
      runtimeStatus: 'active',
      companyName: 'SynthOrg',
    },
  },
}

export const Idle: Story = {
  args: {
    data: {
      agentId: 'ceo-1',
      name: 'Amara Okafor',
      role: 'CEO',
      department: 'executive',
      runtimeStatus: 'idle',
      companyName: 'Acme Corp',
    },
  },
}

export const Error: Story = {
  args: {
    data: {
      agentId: 'ceo-1',
      name: 'Amara Okafor',
      role: 'CEO',
      department: 'executive',
      runtimeStatus: 'error',
      companyName: 'SynthOrg',
    },
  },
}

export const Offline: Story = {
  args: {
    data: {
      agentId: 'ceo-1',
      name: 'Amara Okafor',
      role: 'CEO',
      department: 'executive',
      runtimeStatus: 'offline',
      companyName: 'SynthOrg',
    },
  },
}
