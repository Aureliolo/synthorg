package cmd

import (
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/charmbracelet/x/ansi"
)

// ── View ────────────────────────────────────────────────────────────

func (m setupTUI) View() tea.View {
	lines := renderSetupTUILogo(m.version)
	lines = append(lines, m.phaseLines()...)
	indent := computeCenteringIndent(lines, m.width)
	for i, l := range lines {
		lines[i] = indent + l
	}
	content := strings.Join(lines, "\n")
	tp := (m.height - len(lines)) / 2
	if tp < 0 {
		tp = 0
	}
	v := tea.NewView(strings.Repeat("\n", tp) + content)
	v.AltScreen = true
	return v
}

func renderSetupTUILogo(version string) []string {
	lines := make([]string, 0, len(ui.LogoLines)+2)
	for i, art := range ui.LogoLines {
		style := lipgloss.NewStyle().Foreground(lipgloss.Color(ui.LogoGradientHex[i])).Bold(true)
		lines = append(lines, style.Render(art))
	}
	lines = append(lines, sVersion.Render("v"+version))
	lines = append(lines, "")
	return lines
}

// phaseLines dispatches to the phase-specific renderer. Unknown phases
// return an empty slice rather than crash.
func (m setupTUI) phaseLines() []string {
	switch m.phase {
	case phaseReinit:
		return m.viewReinit()
	case phaseSetup:
		return m.viewSetup()
	case phaseTelemetry:
		return m.viewTelemetry()
	case phaseSummary:
		return m.viewSummary()
	}
	return nil
}

// computeCenteringIndent returns the leading-space indent that centres
// the widest line in width columns. Always returns at least one space.
func computeCenteringIndent(lines []string, width int) string {
	maxW := 0
	for _, l := range lines {
		if w := lipgloss.Width(l); w > maxW {
			maxW = w
		}
	}
	lp := (width - maxW) / 2
	if lp < 1 {
		lp = 1
	}
	return strings.Repeat(" ", lp)
}

// ── Phase views ─────────────────────────────────────────────────────

func (m setupTUI) viewReinit() []string {
	content := make([]string, 0, 9)
	content = append(content,
		"",
		sLabel.Render("Configuration already exists at:"),
		sCmd.Render(m.reinitPath),
		"",
		sWarn.Render("\u26a0")+"  A new JWT secret will be generated.",
		"   Running containers will need a restart.",
		"",
	)
	btnIdx := len(content)
	content = append(content, "", "") // button placeholder + trailing blank
	w := contentBoxWidth(content, m.width)
	content[btnIdx] = btnPair("Overwrite", "Cancel", m.focus == fReinitOverwrite, w)
	o := renderBox("Existing Configuration", content, w)
	o = append(o, sDim.Render("\u2190\u2192 toggle  enter select  esc cancel"))
	return o
}

func (m setupTUI) viewSetup() []string {
	// Toggle rows depend on the final box width because they distribute
	// their own internal padding. We compute width from the non-toggle
	// content first (longest contributor is the data-directory path),
	// then render toggles at that width.
	dataDirLabel := flabel("Data directory", m.focus == fDataDir)
	dataDirValue := "  " + m.dataDir.View()
	w := contentBoxWidth(m.preliminaryContentLines(dataDirLabel, dataDirValue), m.width)

	content := m.buildSetupContent(dataDirLabel, dataDirValue, w)
	main := renderBox("Setup", content, w)
	main = append(main, sDim.Render(m.setupHelpFooter()))

	helpLines := m.helpForFocus()
	if len(helpLines) == 0 || m.width < 100 {
		return main
	}
	return sideBySide(main, renderHelpPanel(helpLines, 28), 2)
}

// preliminaryContentLines builds the width-determining content (data dir
// plus any expanded ports) so the toggle rows below can be sized to it.
func (m setupTUI) preliminaryContentLines(dataDirLabel, dataDirValue string) []string {
	prelim := []string{"", dataDirLabel, dataDirValue}
	if !m.advExpanded {
		return prelim
	}
	prelim = append(prelim,
		"  "+m.backendPort.View(),
		"  "+m.webPort.View(),
	)
	if m.persistence == 1 {
		prelim = append(prelim, "  "+m.postgresPort.View())
	}
	if m.busBackend == 1 {
		prelim = append(prelim, "  "+m.natsPort.View())
	}
	return prelim
}

// buildSetupContent renders the full setup box body for the given
// content width.
func (m setupTUI) buildSetupContent(dataDirLabel, dataDirValue string, w int) []string {
	content := []string{
		"",
		dataDirLabel,
		dataDirValue,
		"",
		m.persistenceToggle(w),
		"",
		m.busToggle(w),
		"",
		m.fineTuningToggle(w),
	}
	if m.fineTuning {
		// Variant row appears only when fine-tuning is enabled. The
		// dependent relationship is signalled by the "  Variant" label
		// in fineTuneVariantToggle, which keeps the toggle column
		// aligned with its parent row.
		content = append(content, m.fineTuneVariantToggle(w))
	}
	content = append(content, "")
	content = append(content, m.advancedSettingsToggleLine())
	if m.advExpanded {
		content = append(content, m.advancedSettingsBlock(w)...)
	}
	content = append(content,
		"",
		btnCenter("Continue", m.focus == fContinue, w),
		"",
	)
	return content
}

// advancedSettingsToggleLine renders the "Advanced settings" expander
// line with the correct arrow + focus styling.
func (m setupTUI) advancedSettingsToggleLine() string {
	arrow := "\u25b8"
	if m.advExpanded {
		arrow = "\u25be"
	}
	txt := arrow + " Advanced settings"
	if m.focus == fAdvToggle {
		return sBrand.Render(txt)
	}
	return sDim.Render(txt)
}

// advancedSettingsBlock renders the expanded advanced-settings panel
// (toggles + port inputs).
func (m setupTUI) advancedSettingsBlock(w int) []string {
	block := []string{
		"",
		m.sandboxToggle(w),
		"",
		m.encryptSecretsToggle(w),
		"",
		flabel("Backend port", m.focus == fBackendPort),
		"  " + m.backendPort.View(),
		"",
		flabel("Dashboard port", m.focus == fWebPort),
		"  " + m.webPort.View(),
	}
	if m.persistence == 1 {
		block = append(block,
			"",
			flabel("Postgres port", m.focus == fPostgresPort),
			"  "+m.postgresPort.View(),
		)
	}
	if m.busBackend == 1 {
		block = append(block,
			"",
			flabel("NATS port", m.focus == fNatsPort),
			"  "+m.natsPort.View(),
		)
	}
	return block
}

// setupHelpFooter returns the keyboard-shortcut help line that varies
// by whether the currently focused field is a toggle or an input.
func (m setupTUI) setupHelpFooter() string {
	if isToggleFocus(m.focus) {
		return "\u2191\u2193 navigate  \u2190\u2192/space toggle  esc quit"
	}
	return "\u2191\u2193 navigate  enter select  esc quit"
}

// isToggleFocus reports whether f names one of the toggle-style focus
// targets (where left/right or space cycles the value).
func isToggleFocus(f int) bool {
	switch f {
	case fSandbox, fBusBackend, fPersistence, fFineTuning, fFineTuneVariant, fEncryptSecrets:
		return true
	}
	return false
}

// renderHelpPanel wraps helpLines in a side-by-side panel of width hw.
func renderHelpPanel(helpLines []string, hw int) []string {
	panel := make([]string, 0, len(helpLines)+4)
	panel = append(panel, boxTop("", hw))
	panel = append(panel, brow("", hw))
	for _, hl := range helpLines {
		panel = append(panel, brow(sMuted.Render(hl), hw))
	}
	panel = append(panel, brow("", hw))
	panel = append(panel, boxBottom(hw))
	return panel
}

// helpForFocus returns contextual help lines for the currently focused
// field. Per-focus blocks live in helper functions: input fields, two-
// choice toggles, and feature toggles each follow a different shape, so
// keeping them separate avoids one giant switch.
func (m setupTUI) helpForFocus() []string {
	if lines := helpForInputField(m.focus); lines != nil {
		return lines
	}
	if lines := m.helpForBackendChoice(); lines != nil {
		return lines
	}
	return m.helpForFeatureToggle()
}

// helpForInputField returns the help text for a text-input field or
// button focus that has no state-dependent variation.
func helpForInputField(focus int) []string {
	switch focus {
	case fDataDir:
		return []string{
			"Where SynthOrg stores",
			"configuration, database,",
			"and agent memory files.",
		}
	case fBackendPort:
		return []string{
			"Port for the REST API and",
			"WebSocket connections.",
		}
	case fWebPort:
		return []string{
			"Port for the web dashboard",
			"user interface.",
		}
	case fPostgresPort:
		return []string{
			"Port for the PostgreSQL",
			"container. Must not",
			"conflict with other ports.",
		}
	case fNatsPort:
		return []string{
			"Port for NATS JetStream",
			"client connections. Must",
			"not conflict with other",
			"ports.",
		}
	case fAdvToggle:
		return []string{
			"Configure ports, sandbox,",
			"and service-specific",
			"settings. Defaults work",
			"for most deployments.",
		}
	}
	return nil
}

// helpForBackendChoice returns help text for the two cyclable backend
// pickers (persistence + message bus) which depend on the current
// selection index.
func (m setupTUI) helpForBackendChoice() []string {
	switch m.focus {
	case fPersistence:
		if m.persistence == 1 {
			return []string{
				"Dedicated PostgreSQL 18",
				"container. Best for",
				"production and high",
				"concurrency workloads.",
			}
		}
		return []string{
			"In-process SQLite database.",
			"Zero setup, lightweight.",
			"Ideal for single-node and",
			"development environments.",
		}
	case fBusBackend:
		if m.busBackend == 1 {
			return []string{
				"NATS JetStream in a ~20 MB",
				"container. Crash-safe",
				"queues, multi-process",
				"agents, stream replay.",
			}
		}
		return []string{
			"In-process asyncio queues.",
			"Zero setup, microsecond",
			"latency. Messages lost",
			"on crash, single process.",
		}
	}
	return nil
}

// helpForFeatureToggle returns help text for the feature toggles
// (fine-tuning + variant, sandbox, encrypt secrets). Per-toggle copy
// lives in helper functions so the dispatcher stays small.
func (m setupTUI) helpForFeatureToggle() []string {
	switch m.focus {
	case fFineTuning:
		return helpFineTuning(m.fineTuning)
	case fFineTuneVariant:
		return helpFineTuneVariant(m.fineTuneVariant == 1)
	case fSandbox:
		return helpSandbox(m.sandbox)
	case fEncryptSecrets:
		return helpEncryptSecrets(m.encryptSecrets)
	}
	return nil
}

func helpFineTuning(enabled bool) []string {
	if enabled {
		return []string{
			"Sidecar that trains",
			"embedding models on your",
			"agents' memory for better",
			"retrieval quality.",
			"",
			"Pick GPU or CPU below:",
			"GPU ~4 GB, fast training.",
			"CPU ~1.7 GB, slow but",
			"works anywhere.",
		}
	}
	return []string{
		"Adapts embedding models to",
		"your agents' data. Improves",
		"memory retrieval over time.",
		"",
		"Not required -- standard",
		"embeddings work well out of",
		"the box. Choose GPU or CPU",
		"image when enabled.",
	}
}

func helpFineTuneVariant(cpu bool) []string {
	if cpu {
		return []string{
			"CPU torch (~1.7 GB image).",
			"Runs on any amd64 host, no",
			"GPU driver required. Slower",
			"training but safer default",
			"for laptops / no-GPU",
			"deployments.",
		}
	}
	return []string{
		"GPU torch with bundled CUDA",
		"runtime (~4 GB image).",
		"Requires an NVIDIA GPU with",
		"a compatible host driver.",
		"Much faster training -- the",
		"default for proper rigs.",
	}
}

func helpSandbox(enabled bool) []string {
	if enabled {
		return []string{
			"Docker-based code sandbox.",
			"Agents can safely execute",
			"code, run shell commands,",
			"and use file-system tools.",
		}
	}
	return []string{
		"No code execution. Agents",
		"cannot run code, shell",
		"commands, or file-system",
		"operations.",
	}
}

func helpEncryptSecrets(enabled bool) []string {
	if enabled {
		return []string{
			"Connection secrets (API keys,",
			"OAuth tokens) are Fernet-",
			"encrypted at rest inside the",
			"database. A master key is",
			"generated and stored in",
			"config.json.",
			"",
			"Pair with disk/volume",
			"encryption for at-rest",
			"protection of non-secret",
			"data.",
		}
	}
	return []string{
		"Secrets are read from",
		"SYNTHORG_SECRET_* env vars",
		"at runtime. No at-rest",
		"storage, no OAuth token",
		"persistence.",
		"",
		"Only pick this if you",
		"manage secrets in an",
		"external system (Docker",
		"secrets, k8s Secrets,",
		"vault, etc.).",
	}
}

// sideBySide joins two sets of lines horizontally with a gap.
func sideBySide(left, right []string, gap int) []string {
	maxLeftW := 0
	for _, l := range left {
		if w := lipgloss.Width(l); w > maxLeftW {
			maxLeftW = w
		}
	}

	h := len(left)
	if len(right) > h {
		h = len(right)
	}
	for len(left) < h {
		left = append(left, "")
	}
	for len(right) < h {
		right = append(right, "")
	}

	result := make([]string, h)
	spacer := strings.Repeat(" ", gap)
	for i := range h {
		lw := lipgloss.Width(left[i])
		pad := maxLeftW - lw
		if pad < 0 {
			pad = 0
		}
		result[i] = left[i] + strings.Repeat(" ", pad) + spacer + right[i]
	}
	return result
}

func (m setupTUI) viewTelemetry() []string {
	content := []string{
		"",
		sLabel.Render("Help improve SynthOrg?"),
		"",
		"Send anonymous usage stats (agent count,",
		"feature usage, error rates).",
		"",
		sOn.Render("\u2713") + " No API keys, content, or personal data.",
		"",
	}
	// Dynamic: show opposite command based on current selection.
	if m.focus == fTelYes {
		content = append(content,
			sMuted.Render("Disable later: ")+sCmd.Render("synthorg config set"),
			sCmd.Render("telemetry_opt_in false"),
		)
	} else {
		content = append(content,
			sMuted.Render("Enable later: ")+sCmd.Render("synthorg config set"),
			sCmd.Render("telemetry_opt_in true"),
		)
	}
	content = append(content, "")
	btnIdx := len(content)
	content = append(content, "", "") // button placeholder + trailing blank
	w := contentBoxWidth(content, m.width)
	content[btnIdx] = btnPairEx("Yes", "No", m.focus == fTelYes, btnWarn, w)
	o := renderBox("Telemetry", content, w)
	o = append(o, sDim.Render("\u2190\u2192 toggle  enter select  y/n shortcut"))
	return o
}

func (m setupTUI) viewSummary() []string {
	data := m.buildSummary()
	entries := summaryEntries(data)
	content := make([]string, 0, len(entries)+8)
	content = append(content,
		"",
		sSuccess.Render("\u2713 SynthOrg initialized"),
		"",
	)
	for _, e := range entries {
		var val string
		switch e.kind {
		case entryOK:
			val = sOn.Render(e.value)
		case entryBad:
			val = sWarn.Render(e.value)
		case entryMode:
			val = sLabel.Render(e.value)
		default:
			val = e.value
		}
		content = append(content, sLabel.Render(fmt.Sprintf("%-16s", e.label))+val)
	}
	content = append(content,
		"",
		sLabel.Render("Start SynthOrg now?"),
		"",
	)
	btnIdx := len(content)
	content = append(content, "", "") // button placeholder + trailing blank
	w := contentBoxWidth(content, m.width)
	content[btnIdx] = btnPair("Yes, start", "No, exit", m.focus == fStartYes, w)
	o := renderBox("Ready", content, w)
	o = append(o, sDim.Render("\u2190\u2192 toggle  enter select"))
	return o
}

// ── Summary data ────────────────────────────────────────────────────

// summaryEntry is a single row in the configuration summary.
type summaryEntry struct {
	label string
	value string
	kind  entryKind // controls coloring
}

type entryKind int

const (
	entryPath   entryKind = iota // neutral, no color
	entryNumber                  // neutral, no color
	entryMode                    // blue (mode name like postgresql, nats)
	entryOK                      // green (enabled)
	entryBad                     // red (disabled)
)

// summaryData holds all config values for summary rendering.
type summaryData struct {
	dataDir     string
	backendPort string
	webPort     string
	dbMode      string
	dbPort      string // empty if sqlite
	busMode     string
	busPort     string // empty if internal
	fineTuning  string
	sandbox     string
	telemetry   string
}

// summaryEntries builds structured summary entries from config data.
// Used by both TUI and post-TUI output.
func summaryEntries(d summaryData) []summaryEntry {
	boolKind := func(v string) entryKind {
		if strings.HasPrefix(v, "enabled") {
			return entryOK
		}
		return entryBad
	}

	entries := []summaryEntry{
		{"Data", d.dataDir, entryPath},
		{"API port", d.backendPort, entryNumber},
		{"Dashboard port", d.webPort, entryNumber},
		{"Database", d.dbMode, entryMode},
	}
	if d.dbPort != "" {
		entries = append(entries, summaryEntry{"Database port", d.dbPort, entryNumber})
	}
	entries = append(entries, summaryEntry{"Bus", d.busMode, entryMode})
	if d.busPort != "" {
		entries = append(entries, summaryEntry{"Bus port", d.busPort, entryNumber})
	}
	entries = append(entries, summaryEntry{"Fine-tuning", d.fineTuning, boolKind(d.fineTuning)})
	entries = append(entries, summaryEntry{"Sandbox", d.sandbox, boolKind(d.sandbox)})
	entries = append(entries, summaryEntry{"Telemetry", d.telemetry, boolKind(d.telemetry)})
	return entries
}

// summaryLines returns plain text lines for the post-TUI box output.
func summaryLines(d summaryData) []string {
	entries := summaryEntries(d)
	lines := make([]string, len(entries))
	for i, e := range entries {
		lines[i] = fmt.Sprintf("%-16s%s", e.label, e.value)
	}
	return lines
}

func (m setupTUI) buildSummary() summaryData {
	d := summaryData{
		dataDir:     m.dataDir.Value(),
		backendPort: m.backendPort.Value(),
		webPort:     m.webPort.Value(),
	}
	if m.persistence == 1 {
		d.dbMode = "postgresql"
		d.dbPort = m.postgresPort.Value()
	} else {
		d.dbMode = "sqlite"
	}
	if m.busBackend == 1 {
		d.busMode = "nats"
		d.busPort = m.natsPort.Value()
	} else {
		d.busMode = "internal"
	}
	if m.fineTuning {
		if m.fineTuneVariant == 1 {
			d.fineTuning = "enabled (cpu)"
		} else {
			d.fineTuning = "enabled (gpu)"
		}
	} else {
		d.fineTuning = "disabled"
	}
	if m.sandbox {
		d.sandbox = "enabled"
	} else {
		d.sandbox = "disabled"
	}
	if m.telemetry {
		d.telemetry = "enabled"
	} else {
		d.telemetry = "disabled"
	}
	return d
}

// ── Box primitives ──────────────────────────────────────────────────

// boxBorderOverhead is the width of the border + padding `brow` and
// `boxTop`/`boxBottom` add around content (`│ <content> │` or
// `╭<title><hz>╮`). Used to translate terminal width into a content-cell
// ceiling for contentBoxWidth.
const boxBorderOverhead = 4

// contentBoxWidth returns the inner width to use when wrapping a slice of
// content lines in a bordered box: the larger of boxW and the widest
// rendered content row, so a long path or wide toggle row widens the whole
// box uniformly instead of pushing one row's right border past the others.
//
// terminalWidth (typically setupTUI.width) caps the result so an unusually
// long path cannot render a box wider than the terminal. The cap takes
// precedence over the boxW floor: on a terminal narrower than
// boxW + boxBorderOverhead, returning boxW would overflow anyway, so we
// shrink to fit. A terminalWidth of 0 -- the pre-WindowSizeMsg state --
// skips the cap (the next render will correct it).
func contentBoxWidth(content []string, terminalWidth int) int {
	w := boxW
	for _, line := range content {
		if cw := lipgloss.Width(line); cw > w {
			w = cw
		}
	}
	if terminalWidth > 0 {
		ceiling := terminalWidth - boxBorderOverhead
		if ceiling > 0 && w > ceiling {
			w = ceiling
		}
	}
	return w
}

// renderBox wraps content lines in a bordered box of the given inner width.
// Width is the caller's responsibility (typically via contentBoxWidth) so
// that toggles and buttons -- which distribute their own internal padding --
// can be rendered at the final width before being passed in.
//
// Each line is ANSI-aware-truncated to w before rendering: contentBoxWidth
// can clamp w below the widest content line when terminalWidth fires, and
// brow does not shrink its output -- so without truncation a clamped box
// would reproduce the very right-border-overflow bug this whole helper
// exists to fix.
func renderBox(title string, content []string, w int) []string {
	out := make([]string, 0, len(content)+2)
	out = append(out, boxTop(title, w))
	for _, line := range content {
		if lipgloss.Width(line) > w {
			line = ansi.Truncate(line, w, "")
		}
		out = append(out, brow(line, w))
	}
	out = append(out, boxBottom(w))
	return out
}

func boxTop(title string, w int) string {
	tw := lipgloss.Width(title)
	d := w - tw
	if d < 1 {
		d = 1
	}
	return fmt.Sprintf("%s %s %s%s",
		sBorder.Render(cTL), sBrand.Render(title),
		sBorder.Render(strings.Repeat(hzC, d)), sBorder.Render(cTR))
}

func boxBottom(w int) string {
	return fmt.Sprintf("%s%s",
		sBorder.Render(cBL+strings.Repeat(hzC, w+2)),
		sBorder.Render(cBR))
}

func brow(content string, w int) string {
	cw := lipgloss.Width(content)
	pad := w - cw
	if pad < 0 {
		pad = 0
	}
	return fmt.Sprintf("%s %s%s %s",
		sBorder.Render(vtC), content,
		strings.Repeat(" ", pad), sBorder.Render(vtC))
}

// ── Field helpers ───────────────────────────────────────────────────

func flabel(label string, active bool) string {
	if active {
		return sLabel.Render(label)
	}
	return sMuted.Render(label)
}

func btnCenter(label string, active bool, w int) string {
	btn := "[ " + label + " ]"
	bw := lipgloss.Width(btn)
	if active {
		btn = sBrand.Render(btn)
	} else {
		btn = sDim.Render(btn)
	}
	pad := (w - bw) / 2
	if pad < 0 {
		pad = 0
	}
	return strings.Repeat(" ", pad) + btn
}

// btnPairStyle controls the style of the right button when active.
type btnPairStyle int

const (
	btnDefault btnPairStyle = iota // right active = brand color
	btnWarn                        // right active = red/warning
)

func btnPair(left, right string, leftActive bool, w int) string {
	return btnPairEx(left, right, leftActive, btnDefault, w)
}

func btnPairEx(left, right string, leftActive bool, rightStyle btnPairStyle, w int) string {
	lb := "[ " + left + " ]"
	rb := "[ " + right + " ]"
	lbW := lipgloss.Width(lb)
	rbW := lipgloss.Width(rb)
	var lbR, rbR string
	if leftActive {
		lbR = sOn.Render(lb)
		rbR = sDim.Render(rb)
	} else {
		lbR = sDim.Render(lb)
		if rightStyle == btnWarn {
			rbR = sWarn.Render(rb)
		} else {
			rbR = sBrand.Render(rb)
		}
	}
	gap := 4
	totalW := lbW + gap + rbW
	pad := (w - totalW) / 2
	if pad < 0 {
		pad = 0
	}
	return strings.Repeat(" ", pad) + lbR + strings.Repeat(" ", gap) + rbR
}

func toggle2(label string, active bool, val bool, on, off string, warnOff bool, w int) string {
	lbl := flabel(label, active)
	lblW := lipgloss.Width(label)
	onW := lipgloss.Width(on)
	offW := lipgloss.Width(off)

	var onR, offR string
	if val {
		onR = sOn.Render(on)
		offR = sOff.Render(off)
	} else {
		onR = sOff.Render(on)
		if warnOff {
			offR = sWarn.Render(off)
		} else {
			offR = sOn.Render(off)
		}
	}

	gap := w - lblW - onW - offW - 4
	if gap < 2 {
		gap = 2
	}
	return fmt.Sprintf("%s%s%s  %s", lbl, strings.Repeat(" ", gap), onR, offR)
}

func (m setupTUI) sandboxToggle(w int) string {
	return toggle2("Agent sandbox", m.focus == fSandbox, m.sandbox, "Yes", "No", true, w)
}

func (m setupTUI) fineTuningToggle(w int) string {
	return toggle2("Fine-tuning", m.focus == fFineTuning, m.fineTuning, "Yes", "No", false, w)
}

// fineTuneVariantToggle renders the GPU/CPU choice for the fine-tune
// sidecar. Position 0 = GPU (default, ~4 GB, requires NVIDIA host + driver);
// position 1 = CPU (~1.7 GB, runs anywhere). Default-first rendering so
// GPU appears on the left as "the normal choice".
func (m setupTUI) fineTuneVariantToggle(w int) string {
	return toggle2("  Variant", m.focus == fFineTuneVariant, m.fineTuneVariant == 0, "gpu", "cpu", false, w)
}

func (m setupTUI) busToggle(w int) string {
	return toggle2("Bus backend", m.focus == fBusBackend, m.busBackend == 1, "nats", "internal", false, w)
}

func (m setupTUI) persistenceToggle(w int) string {
	return toggle2("Database", m.focus == fPersistence, m.persistence == 1, "postgres", "sqlite", false, w)
}

func (m setupTUI) encryptSecretsToggle(w int) string {
	return toggle2("Encrypt secrets", m.focus == fEncryptSecrets, m.encryptSecrets, "Yes", "No", true, w)
}
