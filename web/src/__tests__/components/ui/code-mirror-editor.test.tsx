import { render, screen } from '@testing-library/react'
import fc from 'fast-check'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'

// ---------------------------------------------------------------------------
// Mock CodeMirror -- jsdom lacks layout APIs that EditorView needs
// ---------------------------------------------------------------------------

const mockDispatch = vi.fn()
const mockDestroy = vi.fn()
/** Captured updateListener callback from the component's mount effect. */
let capturedUpdateListener: ((update: { docChanged: boolean; state: { doc: { toString: () => string } } }) => void) | null = null

vi.mock('@codemirror/view', () => {
  class MockEditorView {
    state: { doc: { toString: () => string; length: number } }
    dispatch: ReturnType<typeof vi.fn>
    destroy: ReturnType<typeof vi.fn>
    private _docString: string

    constructor({ state, parent }: { state: { doc: { toString: () => string; length: number } }; parent: HTMLElement }) {
      this._docString = state.doc.toString()

      const editorEl = document.createElement('div')
      editorEl.className = 'cm-editor'
      parent.appendChild(editorEl)

      this.state = {
        doc: {
          toString: () => this._docString,
          get length() { return this.toString().length },
        },
      }
      // eslint-disable-next-line @typescript-eslint/no-this-alias
      const self = this
      this.dispatch = mockDispatch.mockImplementation((tr: { changes?: { insert?: string }; effects?: unknown }) => {
        if (tr.changes && 'insert' in tr.changes) {
          self._docString = tr.changes.insert as string
          // Invoke the captured updateListener (mirrors real CodeMirror behavior)
          capturedUpdateListener?.({
            docChanged: true,
            state: { doc: { toString: () => self._docString } },
          })
        }
      })
      this.destroy = mockDestroy.mockImplementation(() => {
        editorEl.remove()
      })

      MockEditorView._instances.push(this)
    }

    static _instances: MockEditorView[] = []
    static theme = vi.fn().mockReturnValue([])
    static updateListener = {
      of: vi.fn().mockImplementation((cb: typeof capturedUpdateListener) => {
        capturedUpdateListener = cb
        return []
      }),
    }
    static lineWrapping = []
  }

  return {
    EditorView: MockEditorView,
    lineNumbers: vi.fn().mockReturnValue([]),
    drawSelection: vi.fn().mockReturnValue([]),
    keymap: { of: vi.fn().mockReturnValue([]) },
  }
})

vi.mock('@codemirror/state', () => {
  class MockCompartment {
    of = vi.fn().mockReturnValue([])
    reconfigure = vi.fn().mockReturnValue([])
  }

  return {
    EditorState: {
      create: vi.fn().mockImplementation(({ doc }: { doc: string }) => ({
        doc: { toString: () => doc, length: doc.length },
      })),
      readOnly: { of: vi.fn().mockReturnValue([]) },
    },
    Compartment: MockCompartment,
  }
})

vi.mock('@codemirror/language', () => ({
  bracketMatching: vi.fn().mockReturnValue([]),
  syntaxHighlighting: vi.fn().mockReturnValue([]),
  HighlightStyle: { define: vi.fn().mockReturnValue([]) },
}))

vi.mock('@codemirror/lang-json', () => ({
  json: vi.fn().mockReturnValue([]),
}))

vi.mock('@codemirror/lang-yaml', () => ({
  yaml: vi.fn().mockReturnValue([]),
}))

vi.mock('@codemirror/commands', () => ({
  defaultKeymap: [],
  history: vi.fn().mockReturnValue([]),
  historyKeymap: [],
}))

vi.mock('@lezer/highlight', () => ({
  tags: {
    propertyName: 'propertyName',
    keyword: 'keyword',
    string: 'string',
    number: 'number',
    bool: 'bool',
    null: 'null',
    punctuation: 'punctuation',
    comment: 'comment',
  },
}))

// Import after mocks
import { CodeMirrorEditor } from '@/components/ui/code-mirror-editor'
import { EditorView } from '@codemirror/view'

// The vi.mock above attaches a `_instances` array to the EditorView
// constructor for assertion purposes. Express that as a module-local
// extension so we only need a single cast (not `as unknown as`).
interface MockedEditorViewExtras {
  _instances: { dispatch: ReturnType<typeof vi.fn>; destroy: ReturnType<typeof vi.fn> }[]
}
const MockEditorView = EditorView as typeof EditorView & MockedEditorViewExtras

describe('CodeMirrorEditor', () => {
  const defaultProps = {
    value: '{"key": "value"}',
    onChange: vi.fn(),
    language: 'json' as const,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    MockEditorView._instances = []
    capturedUpdateListener = null
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders a container element', () => {
    render(<CodeMirrorEditor {...defaultProps} />)
    const container = screen.getByRole('textbox')
    expect(container).toBeInTheDocument()
  })

  it('creates EditorView on mount', () => {
    render(<CodeMirrorEditor {...defaultProps} />)
    expect(MockEditorView._instances).toHaveLength(1)
  })

  it('destroys EditorView on unmount', () => {
    const { unmount } = render(<CodeMirrorEditor {...defaultProps} />)
    unmount()
    expect(mockDestroy).toHaveBeenCalledTimes(1)
  })

  it('passes aria-label to the container', () => {
    render(<CodeMirrorEditor {...defaultProps} aria-label="JSON editor" />)
    const container = screen.getByRole('textbox')
    expect(container).toHaveAttribute('aria-label', 'JSON editor')
  })

  it('sets aria-readonly when readOnly is true', () => {
    render(<CodeMirrorEditor {...defaultProps} readOnly />)
    const container = screen.getByRole('textbox')
    expect(container).toHaveAttribute('aria-readonly', 'true')
  })

  it('applies opacity class when readOnly', () => {
    render(<CodeMirrorEditor {...defaultProps} readOnly />)
    const container = screen.getByRole('textbox')
    expect(container.className).toContain('opacity-60')
  })

  it('syncs external value changes via dispatch', () => {
    const { rerender } = render(<CodeMirrorEditor {...defaultProps} />)
    mockDispatch.mockClear()

    rerender(<CodeMirrorEditor {...defaultProps} value='{"new": "value"}' />)

    expect(mockDispatch).toHaveBeenCalledWith(
      expect.objectContaining({
        changes: expect.objectContaining({
          from: 0,
          to: defaultProps.value.length,
          insert: '{"new": "value"}',
        }) as unknown,
      }),
    )
  })

  it('reconfigures language compartment when language changes', () => {
    const { rerender } = render(<CodeMirrorEditor {...defaultProps} />)
    mockDispatch.mockClear()

    rerender(<CodeMirrorEditor {...defaultProps} language="yaml" />)

    expect(mockDispatch).toHaveBeenCalledWith(
      expect.objectContaining({
        effects: expect.anything() as unknown,
      }),
    )
  })

  it('reconfigures readOnly compartment when readOnly changes', () => {
    const { rerender } = render(<CodeMirrorEditor {...defaultProps} />)
    mockDispatch.mockClear()

    rerender(<CodeMirrorEditor {...defaultProps} readOnly />)

    expect(mockDispatch).toHaveBeenCalledWith(
      expect.objectContaining({
        effects: expect.anything() as unknown,
      }),
    )
  })

  it('sets aria-multiline attribute', () => {
    render(<CodeMirrorEditor {...defaultProps} />)
    const container = screen.getByRole('textbox')
    expect(container).toHaveAttribute('aria-multiline', 'true')
  })

  it('applies custom className', () => {
    render(<CodeMirrorEditor {...defaultProps} className="custom-class" />)
    const container = screen.getByRole('textbox')
    expect(container.className).toContain('custom-class')
  })

  it('does not call onChange during programmatic value sync', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <CodeMirrorEditor value="a" onChange={onChange} language="json" />,
    )
    onChange.mockClear()

    rerender(<CodeMirrorEditor value="b" onChange={onChange} language="json" />)

    // dispatch should have been called to sync the new value
    expect(mockDispatch).toHaveBeenCalledWith(
      expect.objectContaining({
        changes: expect.objectContaining({ from: 0, to: 'a'.length, insert: 'b' }) as unknown,
      }),
    )
    // but onChange must NOT fire because isProgrammaticRef blocks it
    expect(onChange).not.toHaveBeenCalled()
  })

  describe('fast-check property tests', () => {
    it('dispatches changes for arbitrary external values', () => {
      fc.assert(
        fc.property(
          fc.string({ minLength: 1, maxLength: 100 }).filter((s) => s !== 'initial'),
          (newValue) => {
            const onChange = vi.fn()
            const { rerender, unmount } = render(
              <CodeMirrorEditor value="initial" onChange={onChange} language="json" />,
            )
            try {
              mockDispatch.mockClear()

              rerender(<CodeMirrorEditor value={newValue} onChange={onChange} language="json" />)

              expect(mockDispatch).toHaveBeenCalledWith(
                expect.objectContaining({
                  changes: expect.objectContaining({
                    from: 0,
                    to: 'initial'.length,
                    insert: newValue,
                  }) as unknown,
                }),
              )
            } finally {
              unmount()
            }
          },
        ),
      )
    })
  })
})
