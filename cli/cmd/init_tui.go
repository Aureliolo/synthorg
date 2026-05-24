package cmd

import (
	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
)

// ── Styles ──────────────────────────────────────────────────────────

var (
	sLabel   = lipgloss.NewStyle().Foreground(lipgloss.Color("#38bdf8"))
	sBrand   = lipgloss.NewStyle().Foreground(lipgloss.Color("#818cf8")).Bold(true)
	sOn      = lipgloss.NewStyle().Foreground(lipgloss.Color("#34d399"))
	sOff     = lipgloss.NewStyle().Foreground(lipgloss.Color("#6b7280"))
	sWarn    = lipgloss.NewStyle().Foreground(lipgloss.Color("#f87171"))
	sDim     = lipgloss.NewStyle().Foreground(lipgloss.Color("#6b7280"))
	sBorder  = lipgloss.NewStyle().Foreground(lipgloss.Color("#6b7280"))
	sMuted   = lipgloss.NewStyle().Foreground(lipgloss.Color("#9ca3af"))
	sVersion = lipgloss.NewStyle().Foreground(lipgloss.Color("#9ca3af"))
	sSuccess = lipgloss.NewStyle().Foreground(lipgloss.Color("#34d399"))
	sCmd     = lipgloss.NewStyle().Foreground(lipgloss.Color("#818cf8")) // commands/code
)

const (
	hzC = "\u2500"
	vtC = "\u2502"
	cTL = "\u256d"
	cTR = "\u256e"
	cBL = "\u2570"
	cBR = "\u256f"
)

// ── Phases & fields ─────────────────────────────────────────────────

const (
	phaseReinit = iota
	phaseSetup
	phaseTelemetry
	phaseSummary
)

const (
	fDataDir = iota
	fAdvToggle
	fBackendPort
	fWebPort
	fSandbox
	fBusBackend
	fPersistence
	fContinue
	fTelYes
	fTelNo
	fFineTuning
	fFineTuneVariant
	fPostgresPort
	fNatsPort
	fEncryptSecrets
	fReinitOverwrite
	fReinitCancel
	fStartYes
	fStartNo
)

// ── Model ───────────────────────────────────────────────────────────

type setupTUI struct {
	dataDir         textinput.Model
	backendPort     textinput.Model
	webPort         textinput.Model
	postgresPort    textinput.Model
	natsPort        textinput.Model
	sandbox         bool
	busBackend      int  // 0=internal, 1=nats
	persistence     int  // 0=sqlite, 1=postgres
	fineTuning      bool // embedding fine-tuning sidecar (GPU ~4 GB / CPU ~1.7 GB)
	fineTuneVariant int  // 0=gpu (default), 1=cpu -- only meaningful when fineTuning=true
	encryptSecrets  bool // Fernet-encrypt connection secrets at rest
	telemetry       bool

	focus       int
	advExpanded bool
	phase       int
	submitted   bool
	cancelled   bool
	startNow    bool
	width       int
	height      int
	version     string

	needReinit   bool
	reinitPath   string
	reinitDenied bool
}

const boxW = 54

func newSetupTUI(dataDir, backendPort, webPort, ver string, sandbox bool) setupTUI {
	di := textinput.New()
	di.SetValue(dataDir)
	di.Focus()
	di.CharLimit = 256
	di.Prompt = ""

	bp := textinput.New()
	bp.SetValue(backendPort)
	bp.CharLimit = 5
	bp.Prompt = ""

	wp := textinput.New()
	wp.SetValue(webPort)
	wp.CharLimit = 5
	wp.Prompt = ""

	pp := textinput.New()
	pp.SetValue("3002")
	pp.CharLimit = 5
	pp.Prompt = ""

	np := textinput.New()
	np.SetValue("3003")
	np.CharLimit = 5
	np.Prompt = ""

	return setupTUI{
		dataDir:        di,
		backendPort:    bp,
		webPort:        wp,
		postgresPort:   pp,
		natsPort:       np,
		sandbox:        sandbox,
		busBackend:     1,
		persistence:    1, // default: postgres
		encryptSecrets: true,
		focus:          fDataDir,
		phase:          phaseSetup,
		version:        ver,
		width:          80,
		height:         24,
	}
}

// ── Focus ───────────────────────────────────────────────────────────

func (m *setupTUI) fields() []int {
	switch m.phase {
	case phaseReinit:
		return []int{fReinitOverwrite, fReinitCancel}
	case phaseTelemetry:
		return []int{fTelYes, fTelNo}
	case phaseSummary:
		return []int{fStartYes, fStartNo}
	default:
		f := []int{fDataDir, fPersistence, fBusBackend, fFineTuning}
		if m.fineTuning {
			f = append(f, fFineTuneVariant)
		}
		f = append(f, fAdvToggle)
		if m.advExpanded {
			f = append(f, fSandbox, fEncryptSecrets, fBackendPort, fWebPort)
			if m.persistence == 1 {
				f = append(f, fPostgresPort)
			}
			if m.busBackend == 1 {
				f = append(f, fNatsPort)
			}
		}
		return append(f, fContinue)
	}
}

func (m *setupTUI) indexOf(id int) int {
	for i, f := range m.fields() {
		if f == id {
			return i
		}
	}
	return 0
}

func (m *setupTUI) focusNext() {
	ff := m.fields()
	i := m.indexOf(m.focus)
	if i < len(ff)-1 {
		m.focus = ff[i+1]
	}
	m.syncFocus()
}

func (m *setupTUI) focusPrev() {
	ff := m.fields()
	i := m.indexOf(m.focus)
	if i > 0 {
		m.focus = ff[i-1]
	}
	m.syncFocus()
}

func (m *setupTUI) syncFocus() {
	inputs := []struct {
		field int
		model *textinput.Model
	}{
		{fDataDir, &m.dataDir},
		{fBackendPort, &m.backendPort},
		{fWebPort, &m.webPort},
		{fPostgresPort, &m.postgresPort},
		{fNatsPort, &m.natsPort},
	}
	for _, inp := range inputs {
		if m.focus == inp.field {
			inp.model.Focus()
		} else {
			inp.model.Blur()
		}
	}
}

// ── Tea interface ───────────────────────────────────────────────────

func (setupTUI) Init() tea.Cmd { return textinput.Blink }

func (m setupTUI) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		return m, nil
	case tea.KeyMsg:
		switch m.phase {
		case phaseReinit:
			return m.updateReinit(msg)
		case phaseSetup:
			return m.updateSetup(msg)
		case phaseTelemetry:
			return m.updateTelemetry(msg)
		case phaseSummary:
			return m.updateSummary(msg)
		}
	}
	var cmd tea.Cmd
	switch m.focus {
	case fDataDir:
		m.dataDir, cmd = m.dataDir.Update(msg)
	case fBackendPort:
		m.backendPort, cmd = m.backendPort.Update(msg)
	case fWebPort:
		m.webPort, cmd = m.webPort.Update(msg)
	}
	return m, cmd
}

func (m setupTUI) updateReinit(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "ctrl+c", "esc":
		m.cancelled = true
		return m, tea.Quit
	case "tab", "down", "right":
		m.focusNext()
	case "shift+tab", "up", "left":
		m.focusPrev()
	case "enter":
		if m.focus == fReinitOverwrite {
			m.phase = phaseSetup
			m.focus = fDataDir
			m.syncFocus()
		} else {
			m.reinitDenied = true
			m.cancelled = true
			return m, tea.Quit
		}
	}
	return m, nil
}

func (m setupTUI) updateSetup(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if next, cmd, handled := m.handleSetupNavKey(msg.String()); handled {
		return next, cmd
	}
	if msg.String() == "enter" {
		if next, handled := m.handleSetupEnter(); handled {
			return next, nil
		}
	}
	if msg.String() == "left" || msg.String() == "right" || msg.String() == " " {
		if next, handled := m.handleSetupToggle(msg.String()); handled {
			return next, nil
		}
	}
	return m.forwardSetupKeyToInput(msg)
}

// handleSetupNavKey processes navigation and quit keys that are not
// focus-specific. Returns handled=false when the key is not owned by
// this layer.
func (m setupTUI) handleSetupNavKey(key string) (tea.Model, tea.Cmd, bool) {
	switch key {
	case "ctrl+c", "esc":
		m.cancelled = true
		return m, tea.Quit, true
	case "tab", "down":
		m.focusNext()
		return m, nil, true
	case "shift+tab", "up":
		m.focusPrev()
		return m, nil, true
	}
	return m, nil, false
}

// handleSetupEnter processes the enter key, which differs by focus:
// fAdvToggle expands/collapses, fContinue advances to the telemetry
// phase, anything else is unhandled (the input field receives it).
func (m setupTUI) handleSetupEnter() (tea.Model, bool) {
	switch m.focus {
	case fAdvToggle:
		m.advExpanded = !m.advExpanded
		return m, true
	case fContinue:
		m.phase = phaseTelemetry
		m.focus = fTelNo // default: not opted in
		return m, true
	}
	return m, false
}

// handleSetupToggle processes left/right/space against the currently
// focused toggle. Returns handled=false when focus is not on a toggle.
func (m setupTUI) handleSetupToggle(key string) (tea.Model, bool) {
	switch m.focus {
	case fSandbox:
		return m.toggleSandbox(), true
	case fBusBackend:
		m.busBackend = 1 - m.busBackend
		return m, true
	case fPersistence:
		m.persistence = 1 - m.persistence
		return m, true
	case fFineTuning:
		return m.toggleFineTuning(), true
	case fFineTuneVariant:
		m.fineTuneVariant = 1 - m.fineTuneVariant
		return m, true
	case fEncryptSecrets:
		m.encryptSecrets = !m.encryptSecrets
		return m, true
	case fAdvToggle:
		if key == " " {
			m.advExpanded = !m.advExpanded
		}
		return m, true
	}
	return m, false
}

// toggleSandbox flips sandbox and auto-disables fine-tuning if sandbox
// is being turned off (State.Validate forbids fine_tuning without
// sandbox).
func (m setupTUI) toggleSandbox() setupTUI {
	m.sandbox = !m.sandbox
	if !m.sandbox && m.fineTuning {
		m.fineTuning = false
	}
	return m
}

// toggleFineTuning flips fine-tuning and auto-enables sandbox when
// turning on (State.Validate requires sandbox for fine-tuning).
func (m setupTUI) toggleFineTuning() setupTUI {
	m.fineTuning = !m.fineTuning
	if m.fineTuning && !m.sandbox {
		m.sandbox = true
	}
	return m
}

// forwardSetupKeyToInput delegates an unowned key to the text-input
// component for the currently focused field. Non-input focuses produce
// a no-op (cmd is nil).
func (m setupTUI) forwardSetupKeyToInput(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	var cmd tea.Cmd
	switch m.focus {
	case fDataDir:
		m.dataDir, cmd = m.dataDir.Update(msg)
	case fBackendPort:
		m.backendPort, cmd = m.backendPort.Update(msg)
	case fWebPort:
		m.webPort, cmd = m.webPort.Update(msg)
	case fPostgresPort:
		m.postgresPort, cmd = m.postgresPort.Update(msg)
	case fNatsPort:
		m.natsPort, cmd = m.natsPort.Update(msg)
	}
	return m, cmd
}

func (m setupTUI) updateTelemetry(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "ctrl+c", "esc":
		m.cancelled = true
		return m, tea.Quit
	case "tab", "down", "right":
		m.focusNext()
	case "shift+tab", "up", "left":
		m.focusPrev()
	case "enter":
		m.telemetry = m.focus == fTelYes
		m.phase = phaseSummary
		m.focus = fStartYes
	case "y", "Y":
		m.telemetry = true
		m.phase = phaseSummary
		m.focus = fStartYes
	case "n", "N":
		m.telemetry = false
		m.phase = phaseSummary
		m.focus = fStartYes
	}
	return m, nil
}

func (m setupTUI) updateSummary(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "ctrl+c", "esc":
		m.cancelled = true
		return m, tea.Quit
	case "tab", "down", "right":
		m.focusNext()
	case "shift+tab", "up", "left":
		m.focusPrev()
	case "enter":
		m.startNow = m.focus == fStartYes
		m.submitted = true
		return m, tea.Quit
	}
	return m, nil
}
