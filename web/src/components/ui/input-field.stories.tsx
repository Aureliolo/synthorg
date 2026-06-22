import { Search } from 'lucide-react'
import type { Meta, StoryObj } from '@storybook/react'
import { InputField, PasswordVisibilityGroup } from './input-field'

const meta = {
  title: 'UI/InputField',
  component: InputField,
  tags: ['autodocs'],
  parameters: {
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof InputField>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: { label: 'Company Name', placeholder: 'Enter company name' },
}

export const Required: Story = {
  args: { label: 'Company Name', required: true, placeholder: 'Required field' },
}

export const WithError: Story = {
  args: { label: 'Company Name', error: 'Company name is required', required: true },
}

export const WithHint: Story = {
  args: { label: 'Description', hint: 'Max 1000 characters', placeholder: 'Optional description' },
}

export const Disabled: Story = {
  args: { label: 'Company Name', disabled: true, value: 'Acme Corp' },
}

export const Multiline: Story = {
  args: { label: 'Description', multiline: true, rows: 4, placeholder: 'Describe your company...' },
}

export const WithLeadingIcon: Story = {
  args: {
    label: 'Search',
    placeholder: 'Search agents...',
    leadingIcon: <Search className="h-4 w-4" aria-hidden="true" />,
  },
}

export const WithLeadingIconAndError: Story = {
  args: {
    label: 'Search',
    placeholder: 'Search agents...',
    leadingIcon: <Search className="h-4 w-4" aria-hidden="true" />,
    error: 'No results matched your query',
  },
}

export const Password: Story = {
  args: { label: 'Password', type: 'password', required: true, placeholder: 'Enter password' },
  parameters: {
    docs: {
      description: {
        story:
          'Password fields render an eye / eye-off toggle by default; pressing it reveals the value while keeping the original `type="password"` semantic at the call site.',
      },
    },
  },
}

export const PasswordDisabled: Story = {
  args: {
    label: 'Password',
    type: 'password',
    disabled: true,
    value: 'unchanged',
  },
}

export const PasswordWithError: Story = {
  args: {
    label: 'Password',
    type: 'password',
    required: true,
    value: 'short',
    error: 'Password must be at least 12 characters',
  },
}

export const PasswordNoToggle: Story = {
  args: {
    label: 'Recovery code',
    type: 'password',
    hidePasswordToggle: true,
    placeholder: 'Toggle suppressed',
  },
  parameters: {
    docs: {
      description: {
        story:
          'Opt out of the built-in eye toggle with `hidePasswordToggle`. Reserved for the rare caller that supplies its own visibility affordance; every other password / secret field MUST keep the default toggle.',
      },
    },
  },
}

export const PasswordCustomTrailing: Story = {
  args: {
    label: 'API key',
    type: 'password',
    trailingElement: (
      <button type="button" aria-label="Copy">
        <span aria-hidden="true">⧉</span>
      </button>
    ),
  },
  parameters: {
    docs: {
      description: {
        story:
          'Supplying a `trailingElement` on a `type="password"` field replaces the built-in eye toggle entirely. Use this for fields where a different secondary action (copy, regenerate) takes priority.',
      },
    },
  },
}

export const PasswordGrouped: Story = {
  args: { label: 'Password', type: 'password', required: true, placeholder: 'Enter password' },
  render: () => (
    <PasswordVisibilityGroup>
      <div className="flex flex-col gap-4">
        <InputField label="Password" type="password" required placeholder="Enter password" />
        <InputField label="Confirm Password" type="password" required placeholder="Repeat password" />
      </div>
    </PasswordVisibilityGroup>
  ),
  parameters: {
    controls: { disable: true },
    docs: {
      description: {
        story:
          'Wrap semantically-paired password fields in `<PasswordVisibilityGroup>` so a single toggle reveals or hides every field in the group at once. Args are ignored; this story renders its own composition.',
      },
    },
  },
}
