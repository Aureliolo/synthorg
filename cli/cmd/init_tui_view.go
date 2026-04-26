package cmd

import (
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// ── View ────────────────────────────────────────────────────────────

func (m setupTUI) View() tea.View {
	var lines []string

	for i, art := range ui.LogoLines {
		style := lipgloss.NewStyle().Foreground(lipgloss.Color(ui.LogoGradientHex[i])).Bold(true)
		lines = append(lines, style.Render(art))
	}
	lines = append(lines, sVersion.Render("v"+m.version))
	lines = append(lines, "")

	switch m.phase {
	case phaseReinit:
		lines = append(lines, m.viewReinit()...)
	case phaseSetup:
		lines = append(lines, m.viewSetup()...)
	case phaseTelemetry:
		lines = append(lines, m.viewTelemetry()...)
	case phaseSummary:
		lines = append(lines, m.viewSummary()...)
	}

	// Center horizontally
	maxW := 0
	for _, l := range lines {
		if w := lipgloss.Width(l); w > maxW {
			maxW = w
		}
	}
	lp := (m.width - maxW) / 2
	if lp < 1 {
		lp = 1
	}
	indent := strings.Repeat(" ", lp)
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

// ── Phase views ─────────────────────────────────────────────────────

func (m setupTUI) viewReinit() []string {
	content := []string{
		"",
		sLabel.Render("Configuration already exists at:"),
		sCmd.Render(m.reinitPath),
		"",
		sWarn.Render("\u26a0") + "  A new JWT secret will be generated.",
		"   Running containers will need a restart.",
		"",
		"", // placeholder for buttons -- needs box width to center
		"",
	}
	w := contentBoxWidth(content)
	content[7] = btnPair("Overwrite", "Cancel", m.focus == fReinitOverwrite, w)
	o := renderBox("Existing Configuration", content, w)
	o = append(o, sDim.Render("\u2190\u2192 toggle  enter select  esc cancel"))
	return o
}

func (m setupTUI) viewSetup() []string {
	// Toggle rows depend on the final box width because they distribute their
	// own internal padding. We compute width from the non-toggle content first
	// (longest contributor is the data-directory path), then render toggles at
	// that width.
	prelim := []string{
		"",
		flabel("Data directory", m.focus == fDataDir),
		"  " + m.dataDir.View(),
	}
	if m.advExpanded {
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
	}
	w := contentBoxWidth(prelim)

	content := []string{
		"",
		flabel("Data directory", m.focus == fDataDir),
		"  " + m.dataDir.View(),
		"",
		m.persistenceToggle(w),
		"",
		m.busToggle(w),
		"",
		m.fineTuningToggle(w),
	}
	if m.fineTuning {
		// Variant row appears only when fine-tuning is enabled. The dependent
		// relationship is signalled by the "  Variant" label in
		// fineTuneVariantToggle, which keeps the toggle column aligned with
		// its parent row.
		content = append(content, m.fineTuneVariantToggle(w))
	}
	content = append(content, "")

	arrow := "\u25b8"
	if m.advExpanded {
		arrow = "\u25be"
	}
	togTxt := arrow + " Advanced settings"
	if m.focus == fAdvToggle {
		content = append(content, sBrand.Render(togTxt))
	} else {
		content = append(content, sDim.Render(togTxt))
	}

	if m.advExpanded {
		content = append(content,
			"",
			m.sandboxToggle(w),
			"",
			m.encryptSecretsToggle(w),
			"",
			flabel("Backend port", m.focus == fBackendPort),
			"  "+m.backendPort.View(),
			"",
			flabel("Dashboard port", m.focus == fWebPort),
			"  "+m.webPort.View(),
		)
		if m.persistence == 1 {
			content = append(content,
				"",
				flabel("Postgres port", m.focus == fPostgresPort),
				"  "+m.postgresPort.View(),
			)
		}
		if m.busBackend == 1 {
			content = append(content,
				"",
				flabel("NATS port", m.focus == fNatsPort),
				"  "+m.natsPort.View(),
			)
		}
	}

	content = append(content,
		"",
		btnCenter("Continue", m.focus == fContinue, w),
		"",
	)
	main := renderBox("Setup", content, w)

	help := "\u2191\u2193 navigate  enter select  esc quit"
	isToggle := m.focus == fSandbox || m.focus == fBusBackend || m.focus == fPersistence || m.focus == fFineTuning || m.focus == fFineTuneVariant || m.focus == fEncryptSecrets
	if isToggle {
		help = "\u2191\u2193 navigate  \u2190\u2192/space toggle  esc quit"
	}
	main = append(main, sDim.Render(help))

	// Side help panel (only if terminal is wide enough)
	helpLines := m.helpForFocus()
	if len(helpLines) > 0 && m.width >= 100 {
		hw := 28
		panel := make([]string, 0, len(helpLines)+4)
		panel = append(panel, boxTop("", hw))
		panel = append(panel, brow("", hw))
		for _, hl := range helpLines {
			panel = append(panel, brow(sMuted.Render(hl), hw))
		}
		panel = append(panel, brow("", hw))
		panel = append(panel, boxBottom(hw))

		return sideBySide(main, panel, 2)
	}

	return main
}

// helpForFocus returns contextual help lines for the currently focused field.
func (m setupTUI) helpForFocus() []string {
	switch m.focus {
	case fDataDir:
		return []string{
			"Where SynthOrg stores",
			"configuration, database,",
			"and agent memory files.",
		}
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
	case fFineTuning:
		if m.fineTuning {
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
	case fFineTuneVariant:
		if m.fineTuneVariant == 1 {
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
	case fSandbox:
		if m.sandbox {
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
	case fEncryptSecrets:
		if m.encryptSecrets {
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
	content = append(content, "", "", "") // spacer, button placeholder, spacer
	w := contentBoxWidth(content)
	content[len(content)-2] = btnPairEx("Yes", "No", m.focus == fTelYes, btnWarn, w)
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
		"", // button placeholder
		"",
	)
	w := contentBoxWidth(content)
	content[len(content)-2] = btnPair("Yes, start", "No, exit", m.focus == fStartYes, w)
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

// contentBoxWidth returns the inner width to use when wrapping a slice of
// content lines in a bordered box: the larger of boxW and the widest
// rendered content row, so a long path or wide toggle row widens the whole
// box uniformly instead of pushing one row's right border past the others.
func contentBoxWidth(content []string) int {
	w := boxW
	for _, line := range content {
		if cw := lipgloss.Width(line); cw > w {
			w = cw
		}
	}
	return w
}

// renderBox wraps content lines in a bordered box of the given inner width.
// Width is the caller's responsibility (typically via contentBoxWidth) so
// that toggles and buttons -- which distribute their own internal padding --
// can be rendered at the final width before being passed in.
func renderBox(title string, content []string, w int) []string {
	out := make([]string, 0, len(content)+2)
	out = append(out, boxTop(title, w))
	for _, line := range content {
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
