import type { Meta, StoryObj } from '@storybook/react'
import { ErrorTechnicalDetails } from './error-technical-details'

const SAMPLE_STACK = [
  'TypeError: process is not defined',
  '    at TemplateCompareDrawer (TemplateCompareDrawer.tsx:113:8)',
  '    at renderWithHooks (react-dom.js:4213:19)',
  '    at updateFunctionComponent (react-dom.js:5569:16)',
].join('\n')

const meta = {
  title: 'UI/ErrorTechnicalDetails',
  component: ErrorTechnicalDetails,
  tags: ['autodocs'],
} satisfies Meta<typeof ErrorTechnicalDetails>

export default meta
type Story = StoryObj<typeof meta>

// Collapsed by default; the user opts in to the diagnostic detail.
export const Collapsed: Story = {
  args: { technical: SAMPLE_STACK },
}

// A short HTTP-style error body rather than a JS stack.
export const HttpError: Story = {
  args: { technical: '404 Not Found\nThe requested resource could not be loaded.' },
}

// Long stack to exercise the scrollable, wrapped panel.
export const LongStack: Story = {
  args: {
    technical: Array.from({ length: 40 }, (_, i) => `    at frame${i} (module-${i}.js:${i + 1}:${i})`).join(
      '\n',
    ),
  },
}
