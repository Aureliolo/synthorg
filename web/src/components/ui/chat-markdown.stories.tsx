import type { Meta, StoryObj } from '@storybook/react'

import { ChatMarkdown } from './chat-markdown'

const meta = {
  title: 'UI/ChatMarkdown',
  component: ChatMarkdown,
  tags: ['autodocs'],
  parameters: { layout: 'padded', a11y: { test: 'error' } },
  args: {
    content: 'The runway is **7 months** at the current burn.',
  },
} satisfies Meta<typeof ChatMarkdown>

export default meta
type Story = StoryObj<typeof meta>

export const Inline: Story = {}

export const RichBlocks: Story = {
  args: {
    content: [
      '## Reduce cloud spend',
      '',
      'Three levers, ranked by impact:',
      '',
      '1. **Rightsize the databases** (biggest saving).',
      '2. Kill idle staging environments.',
      '3. Move batch jobs to spot instances.',
      '',
      '| Lever | Monthly saving |',
      '| --- | --- |',
      '| Rightsize DBs | $4,000 |',
      '| Idle envs | $2,000 |',
      '',
      '> Approve the plan before anything runs.',
      '',
      'Run `terraform plan` to preview:',
      '',
      '```bash',
      'terraform plan -out=tfplan',
      '```',
      '',
      'See the [runbook](https://example.com/runbook).',
    ].join('\n'),
  },
}

// The renderer strips any HTML the model emits, so raw markup never injects
// into the page.
export const HtmlIsStripped: Story = {
  args: {
    content:
      'Safe text. <img src=x onerror="alert(1)"> <script>alert(1)</script> Still safe.',
  },
}
