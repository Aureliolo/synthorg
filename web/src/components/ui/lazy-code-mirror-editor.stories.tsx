import type { Meta, StoryObj } from '@storybook/react'
import { useState } from 'react'
import { LazyCodeMirrorEditor } from './lazy-code-mirror-editor'

const meta: Meta<typeof LazyCodeMirrorEditor> = {
  title: 'UI/LazyCodeMirrorEditor',
  component: LazyCodeMirrorEditor,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof LazyCodeMirrorEditor>

export const JsonMode: Story = {
  args: {
    value: '{\n  "name": "SynthOrg",\n  "version": "0.5.0"\n}',
    language: 'json',
    readOnly: false,
  },
}

export const YamlMode: Story = {
  args: {
    value: 'company:\n  name: SynthOrg\n  departments:\n    - engineering\n    - design',
    language: 'yaml',
    readOnly: false,
  },
}

export const ReadOnly: Story = {
  args: {
    value: '{\n  "status": "readonly"\n}',
    language: 'json',
    readOnly: true,
  },
}

/**
 * Editable variant: demonstrates the `onChange` callback round-trip.
 * Stateful wrapper threads the latest value back into the editor so
 * keystrokes are visible (the editor itself is uncontrolled when no
 * onChange is supplied, so without this wrapper the story would render
 * static text).
 */
export const Editable: Story = {
  render: (args) => {
    const Wrapper = () => {
      const [value, setValue] = useState(args.value)
      return (
        <div className="space-y-3">
          <LazyCodeMirrorEditor
            {...args}
            value={value}
            onChange={setValue}
          />
          <div className="rounded border border-border bg-surface-muted p-3 text-xs text-text-secondary">
            <div className="font-medium text-foreground">onChange fired with:</div>
            <pre className="mt-1 whitespace-pre-wrap break-all">{value}</pre>
          </div>
        </div>
      )
    }
    return <Wrapper />
  },
  args: {
    value: '{\n  "edit": "me"\n}',
    language: 'json',
    readOnly: false,
  },
}
