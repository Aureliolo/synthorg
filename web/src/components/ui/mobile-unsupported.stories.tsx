import type { Meta, StoryObj } from '@storybook/react'
import { expect, waitFor, within } from 'storybook/test'
import { MobileUnsupportedOverlay } from './mobile-unsupported'

const meta: Meta<typeof MobileUnsupportedOverlay> = {
  title: 'UI/MobileUnsupportedOverlay',
  component: MobileUnsupportedOverlay,
  tags: ['autodocs'],
  parameters: {
    viewport: { defaultViewport: 'mobile1' },
    layout: 'fullscreen',
  },
}

export default meta
type Story = StoryObj<typeof MobileUnsupportedOverlay>

export const Default: Story = {}

export const Mobile320: Story = {
  parameters: {
    viewport: {
      defaultViewport: 'mobileSmall',
      viewports: {
        mobileSmall: {
          name: 'Mobile 320',
          styles: { width: '320px', height: '568px' },
          type: 'mobile',
        },
      },
    },
  },
}

export const Mobile767: Story = {
  parameters: {
    docs: {
      description: {
        story: 'Just under the 768px threshold -- overlay should be visible.',
      },
    },
    viewport: {
      defaultViewport: 'justUnderBreakpoint',
      viewports: {
        justUnderBreakpoint: {
          name: 'Mobile 767',
          styles: { width: '767px', height: '1024px' },
          type: 'mobile',
        },
      },
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    await waitFor(() => {
      void expect(canvas.queryByRole('alert')).toBeInTheDocument()
    })
  },
}

export const Tablet768NotTriggered: Story = {
  parameters: {
    docs: {
      description: {
        story: 'At 768px the overlay should not render -- this story is intentionally empty to document that behavior.',
      },
    },
    viewport: {
      defaultViewport: 'tablet768',
      viewports: {
        tablet768: {
          name: 'Tablet 768',
          styles: { width: '768px', height: '1024px' },
          type: 'tablet',
        },
      },
    },
  },
  play: ({ canvasElement }) => {
    const canvas = within(canvasElement)
    void expect(canvas.queryByRole('alert')).not.toBeInTheDocument()
  },
}

export const Tablet1023NotTriggered: Story = {
  parameters: {
    docs: {
      description: {
        story: 'At 1023px (upper tablet range) the overlay should not render.',
      },
    },
    viewport: {
      defaultViewport: 'tablet1023',
      viewports: {
        tablet1023: {
          name: 'Tablet 1023',
          styles: { width: '1023px', height: '768px' },
          type: 'tablet',
        },
      },
    },
  },
  play: ({ canvasElement }) => {
    const canvas = within(canvasElement)
    void expect(canvas.queryByRole('alert')).not.toBeInTheDocument()
  },
}

export const OrientationLandscape: Story = {
  parameters: {
    viewport: {
      defaultViewport: 'mobileLandscape',
      viewports: {
        mobileLandscape: {
          name: 'Mobile landscape',
          styles: { width: '640px', height: '360px' },
          type: 'mobile',
        },
      },
    },
  },
}
