import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { BlueprintPicker } from './BlueprintPicker'

const MOCK_BLUEPRINTS = [
  {
    name: 'feature-pipeline',
    display_name: 'Feature Pipeline',
    description: 'A sequential pipeline for building features end-to-end.',
    source: 'builtin' as const,
    tags: ['development', 'pipeline'],
    workflow_type: 'sequential_pipeline',
    node_count: 7,
    edge_count: 6,
  },
  {
    name: 'research-sprint',
    display_name: 'Research Sprint',
    description: 'A parallel research workflow with multiple investigation tracks.',
    source: 'builtin' as const,
    tags: ['research', 'parallel'],
    workflow_type: 'parallel_execution',
    node_count: 9,
    edge_count: 10,
  },
]

const meta = {
  title: 'Pages/Workflows/BlueprintPicker',
  component: BlueprintPicker,

  args: {
    selectedBlueprint: null,
    onSelect: () => {},
  },

  beforeEach({ msw }) {
    msw.use(http.get('*/api/v1/workflows/blueprints', () =>
      HttpResponse.json({
        data: MOCK_BLUEPRINTS,
        error: null,
        error_detail: null,
        success: true,
      }),
    ))
  },

  parameters: {
    a11y: { test: 'error' }
  }
} satisfies Meta<typeof BlueprintPicker>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/api/v1/workflows/blueprints', async () => {
      await delay('infinite')
      return HttpResponse.json({
        data: [],
        error: null,
        error_detail: null,
        success: true,
      })
    }))
  }
}

export const Selected: Story = {
  args: {
    selectedBlueprint: 'feature-pipeline',
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/api/v1/workflows/blueprints', () =>
      HttpResponse.json({
        data: [],
        error: null,
        error_detail: null,
        success: true,
      }),
    ))
  }
}

export const Error: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/api/v1/workflows/blueprints', () =>
      new HttpResponse(null, { status: 500 }),
    ))
  }
}
