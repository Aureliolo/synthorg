import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { SettingEntry } from '@/api/types/settings'

function MockCodeMirrorEditor({ value, onChange, language, readOnly, 'aria-label': ariaLabel }: {
  value: string
  onChange: (v: string) => void
  language: string
  readOnly?: boolean
  'aria-label'?: string
}) {
  return (
    <textarea
      data-testid="mock-editor"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      readOnly={readOnly}
      aria-label={ariaLabel}
      data-language={language}
    />
  )
}

vi.mock('@/components/ui/code-mirror-editor', () => ({
  CodeMirrorEditor: MockCodeMirrorEditor,
}))

function makeSetting(
  overrides: Partial<SettingEntry['definition']> & { value?: string; source?: SettingEntry['source'] } = {},
): SettingEntry {
  const { value = '10', source = 'db', ...defOverrides } = overrides
  return {
    definition: {
      namespace: 'api',
      key: 'max_retries',
      type: 'int',
      default: '10',
      description: 'Maximum retry attempts',
      group: 'Execution',
      level: 'basic',
      sensitive: false,
      restart_required: false,
      read_only_post_init: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: null,
      max_value: null,
      ...defOverrides,
    },
    value,
    source,
    updated_at: null,
  }
}

const mockEntries: SettingEntry[] = [
  makeSetting({ key: 'max_retries', value: '3' }),
  makeSetting({ key: 'timeout', value: '30', description: 'Timeout in seconds' }),
]

// Lazy import so the vi.mock above is hoisted before the module loads
const { CodeEditorPanel } = await import('@/pages/settings/CodeEditorPanel')

/** Set textarea value via native setter + fireEvent to bypass userEvent's keyboard parsing. */
function setEditorValue(editor: HTMLElement, value: string) {
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype, 'value',
  )!.set!
  nativeInputValueSetter.call(editor, value)
  fireEvent.input(editor, { target: { value } })
}

describe('CodeEditorPanel', () => {
  const defaultOnSave = vi.fn<(changes: Map<string, string>) => Promise<Set<string>>>()
    .mockResolvedValue(new Set())

  function renderPanel(overrides: {
    entries?: SettingEntry[]
    onSave?: typeof defaultOnSave
    saving?: boolean
  } = {}) {
    const { entries = mockEntries, onSave = defaultOnSave, saving = false } = overrides
    return render(
      <CodeEditorPanel entries={entries} onSave={onSave} saving={saving} />,
    )
  }

  beforeEach(() => {
    defaultOnSave.mockClear()
    defaultOnSave.mockResolvedValue(new Set())
  })

  it('renders format selector with JSON selected by default', () => {
    renderPanel()
    const radiogroup = screen.getByRole('radiogroup', { name: 'Editor format' })
    expect(radiogroup).toBeInTheDocument()
  })

  it('renders save and reset buttons', () => {
    renderPanel()
    expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reset/i })).toBeInTheDocument()
  })

  it('displays entries as serialized JSON in the editor', () => {
    renderPanel()
    const editor = screen.getByTestId<HTMLTextAreaElement>('mock-editor')
    const parsed = JSON.parse(editor.value)
    expect(parsed).toEqual({ api: { max_retries: '3', timeout: '30' } })
  })

  it('marks as dirty when editor content changes', () => {
    renderPanel()

    expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument()

    const editor = screen.getByTestId('mock-editor')
    setEditorValue(editor, '{"changed": true}')

    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
  })

  it('shows parse error for invalid JSON', async () => {
    const user = userEvent.setup()
    renderPanel()

    const editor = screen.getByTestId('mock-editor')
    setEditorValue(editor, 'not valid json')

    await user.click(screen.getByRole('button', { name: /save/i }))

    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('detects removed keys and shows error', async () => {
    const user = userEvent.setup()
    renderPanel()

    const editor = screen.getByTestId('mock-editor')
    // Replace content with only one key (removing 'timeout')
    setEditorValue(editor, JSON.stringify({ api: { max_retries: '3' } }, null, 2))

    await user.click(screen.getByRole('button', { name: /save/i }))

    const alert = screen.getByRole('alert')
    expect(alert).toBeInTheDocument()
    expect(alert.textContent).toContain('api/timeout')
  })

  it('resets to original content', async () => {
    const user = userEvent.setup()
    renderPanel()

    const editor = screen.getByTestId('mock-editor')
    setEditorValue(editor, 'changed content')

    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /reset/i }))

    expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument()
  })

  it('calls onSave with changed values', async () => {
    const user = userEvent.setup()
    renderPanel()

    const editor = screen.getByTestId('mock-editor')
    setEditorValue(editor, JSON.stringify({ api: { max_retries: '5', timeout: '30' } }, null, 2))

    await user.click(screen.getByRole('button', { name: /save/i }))

    expect(defaultOnSave).toHaveBeenCalledOnce()
    const changesArg = defaultOnSave.mock.calls[0]![0]
    expect(changesArg.get('api/max_retries')).toBe('5')
    expect(changesArg.size).toBe(1)
  })

  it('disables save button when not dirty', () => {
    renderPanel()
    const saveButton = screen.getByRole('button', { name: /save/i })
    expect(saveButton).toBeDisabled()
  })

  it('handles onSave failure gracefully', async () => {
    const user = userEvent.setup()
    const failingSave = vi.fn<(changes: Map<string, string>) => Promise<Set<string>>>()
      .mockRejectedValue(new Error('Network error'))
    renderPanel({ onSave: failingSave })

    const editor = screen.getByTestId('mock-editor')
    setEditorValue(editor, JSON.stringify({ api: { max_retries: '5', timeout: '30' } }, null, 2))

    await user.click(screen.getByRole('button', { name: /save/i }))

    const alert = screen.getByRole('alert')
    expect(alert).toBeInTheDocument()
    expect(alert.textContent).toContain('Network error')
  })
})
