// Package ui provides styled CLI output using lipgloss. It defines a
// writer-bound UI type with methods for rendering status lines (success, error,
// warning, step, hint), key-value pairs, boxes, inline spinners, and the
// SynthOrg Unicode logo with consistent colors and icons.
//
// Output modes (plain, quiet, JSON, no-color) are configured via Options and
// affect all rendering methods consistently.
package ui

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"

	"charm.land/lipgloss/v2"
	"github.com/mattn/go-isatty"
	"github.com/mattn/go-runewidth"
)

// Color palette for CLI styling.
//
// True-color hex values that share DNA with the web dashboard's "Warm Ops"
// brand (soft blue #38bdf8, semantic state colors) while being optimized
// for terminal rendering. Lipgloss downgrades automatically on 256-color
// and 16-color terminals.
var (
	colorBrand   = lipgloss.Color("#818cf8") // indigo-400 (midpoint of logo gradient)
	colorSuccess = lipgloss.Color("#34d399") // emerald-400
	colorWarn    = lipgloss.Color("#fbbf24") // amber-400
	colorError   = lipgloss.Color("#f87171") // red-400
	colorMuted   = lipgloss.Color("#9ca3af") // gray-400
	colorLabel   = lipgloss.Color("#38bdf8") // sky-400 (web dashboard accent)
)

// Unicode icons for styled output.
const (
	IconSuccess    = "\u2713" // checkmark
	IconInProgress = "\u25cf" // filled circle
	IconWarning    = "!"      // exclamation
	IconError      = "\u2717" // cross
	IconHint       = "\u2192" // right arrow
)

// Plain-mode ASCII substitutes for Unicode icons.
const (
	PlainIconSuccess    = "PASS"
	PlainIconInProgress = "INFO"
	PlainIconWarning    = "WARN"
	PlainIconError      = "FAIL"
	PlainIconHint       = "-->"
)

// Options configures UI output modes.
type Options struct {
	Quiet   bool   // suppress non-essential output (Step, Hint, Blank, Logo, Section)
	Verbose int    // verbosity level (0=normal, 1=verbose, 2=trace)
	NoColor bool   // disable ANSI color/styling
	Plain   bool   // ASCII-only: no Unicode icons, no box-drawing, no spinners
	JSON    bool   // suppress all human output (commands emit JSON themselves)
	Hints   string // hint mode: "always", "auto", "never"
}

// sessionTipsSeen deduplicates HintTip messages within a CLI invocation.
// Package-level so all UI instances (stdout, stderr) share the same store.
var sessionTipsSeen sync.Map

// UI provides styled CLI output bound to a specific writer.
// Binding to a writer (rather than defaulting to os.Stdout) enables
// testability and correct stderr/stdout separation in Cobra commands.
type UI struct {
	w         io.Writer
	isTTY     bool
	plain     bool
	quiet     bool
	jsonMode  bool
	hints     string
	brand     lipgloss.Style
	brandBold lipgloss.Style
	success   lipgloss.Style
	warn      lipgloss.Style
	err       lipgloss.Style
	muted     lipgloss.Style
	label     lipgloss.Style
	bold      lipgloss.Style
}

// NewUI creates a UI bound to the given writer with default options.
// The renderer auto-detects whether the writer is a terminal and adjusts
// color output accordingly (no ANSI codes when piped or redirected).
func NewUI(w io.Writer) *UI {
	return NewUIWithOptions(w, Options{})
}

// NewUIWithOptions creates a UI with the specified output mode options.
func NewUIWithOptions(w io.Writer, opts Options) *UI {
	hints := opts.Hints
	if hints == "" {
		hints = "auto"
	}

	// In no-color or plain mode, create unstyled (empty) styles so that
	// Render() returns text without ANSI codes. In normal mode, apply
	// foreground colors and bold attributes.
	brand := lipgloss.NewStyle()
	brandBold := lipgloss.NewStyle()
	success := lipgloss.NewStyle()
	warn := lipgloss.NewStyle()
	errStyle := lipgloss.NewStyle()
	muted := lipgloss.NewStyle()
	label := lipgloss.NewStyle()
	bold := lipgloss.NewStyle()

	if !opts.NoColor && !opts.Plain && writerIsTTY(w) {
		brand = brand.Foreground(colorBrand)
		brandBold = brandBold.Foreground(colorBrand).Bold(true)
		success = success.Foreground(colorSuccess)
		warn = warn.Foreground(colorWarn)
		errStyle = errStyle.Foreground(colorError)
		muted = muted.Foreground(colorMuted)
		label = label.Foreground(colorLabel)
		bold = bold.Bold(true)
	}

	return &UI{
		w:         w,
		isTTY:     writerIsTTY(w),
		plain:     opts.Plain,
		quiet:     opts.Quiet || opts.JSON,
		jsonMode:  opts.JSON,
		hints:     hints,
		brand:     brand,
		brandBold: brandBold,
		success:   success,
		warn:      warn,
		err:       errStyle,
		muted:     muted,
		label:     label,
		bold:      bold,
	}
}

// Writer returns the underlying writer for direct output.
func (u *UI) Writer() io.Writer { return u.w }

// BrandText returns text styled in brand color for embedding in messages.
func (u *UI) BrandText(s string) string {
	if u.plain {
		return s
	}
	return u.brandBold.Render(s)
}

// IsTTY reports whether the underlying writer is a terminal.
func (u *UI) IsTTY() bool { return u.isTTY }

// IsPlain reports whether the UI is in plain (ASCII-only) mode.
func (u *UI) IsPlain() bool { return u.plain }

// IsQuiet reports whether the UI is in quiet mode.
func (u *UI) IsQuiet() bool { return u.quiet }

// icon returns the appropriate icon for the current mode.
func (u *UI) icon(unicode, plain string) string {
	if u.plain {
		return plain
	}
	return unicode
}

// Logo renders the SynthOrg gradient banner with a version tag.
// The banner uses a 6-line block-letter wordmark with a blue-to-purple
// gradient (one color per row). Plain mode prints "SynthOrg <version>".
func (u *UI) Logo(version string) {
	if u.quiet {
		return
	}
	ver := stripControl(version)
	if u.plain {
		_, _ = fmt.Fprintf(u.w, "SynthOrg %s\n\n", ver)
		return
	}
	for i, line := range LogoLines {
		style := lipgloss.NewStyle().Foreground(lipgloss.Color(LogoGradientHex[i])).Bold(true)
		_, _ = fmt.Fprintf(u.w, "  %s\n", style.Render(line))
	}
	_, _ = fmt.Fprintf(u.w, "  %s\n\n", u.muted.Render("v"+ver))
}

// LogoCompact renders a single-line brand wordmark for commands that
// don't warrant the full banner (start, stop, status, etc.).
func (u *UI) LogoCompact(version string) {
	if u.quiet {
		return
	}
	ver := stripControl(version)
	if u.plain {
		_, _ = fmt.Fprintf(u.w, "SynthOrg %s\n\n", ver)
		return
	}
	_, _ = fmt.Fprintf(u.w, "  %s %s\n\n",
		u.brandBold.Render(logoCompact),
		u.muted.Render("v"+ver))
}

// printLine prints a styled icon followed by a styled message.
func (u *UI) printLine(style lipgloss.Style, icon, msg string) {
	if u.plain {
		_, _ = fmt.Fprintf(u.w, "%s %s\n", icon, stripControl(msg))
		return
	}
	_, _ = fmt.Fprintf(u.w, "%s %s\n", style.Render(icon), style.Render(stripControl(msg)))
}

// Step prints an in-progress status line (brand purple).
func (u *UI) Step(msg string) {
	if u.quiet {
		return
	}
	icon := u.icon(IconInProgress, PlainIconInProgress)
	if u.plain {
		_, _ = fmt.Fprintf(u.w, "%s %s\n", icon, stripControl(msg))
		return
	}
	_, _ = fmt.Fprintf(u.w, "%s %s\n", u.brand.Render(icon), u.bold.Render(stripControl(msg)))
}

// StepAlways prints a step that survives --quiet.
//
// For the step whose whole value is that it was not a prompt: an install
// that proceeded because a config key answered for the operator leaves no
// other trace of having been unattended, and --quiet is exactly the mode
// such a run is invoked in. Still suppressed in JSON mode, where human text
// on stdout would corrupt the document.
func (u *UI) StepAlways(msg string) {
	if u.jsonMode {
		return
	}
	icon := u.icon(IconInProgress, PlainIconInProgress)
	if u.plain {
		_, _ = fmt.Fprintf(u.w, "%s %s\n", icon, stripControl(msg))
		return
	}
	_, _ = fmt.Fprintf(u.w, "%s %s\n", u.brand.Render(icon), u.bold.Render(stripControl(msg)))
}

// Success prints a success status line (green).
// Suppressed in quiet mode (--quiet = errors only) and JSON mode.
func (u *UI) Success(msg string) {
	if u.quiet {
		return
	}
	u.printLine(u.success, u.icon(IconSuccess, PlainIconSuccess), msg)
}

// Warn prints a warning status line (orange).
// Suppressed in quiet mode (--quiet = errors only) and JSON mode.
func (u *UI) Warn(msg string) {
	if u.quiet {
		return
	}
	u.printLine(u.warn, u.icon(IconWarning, PlainIconWarning), msg)
}

// WarnAlways prints a warning that survives --quiet.
//
// For warnings whose whole value is that they cannot be lost: a scripted
// pipeline running with --quiet that never sees them would proceed against
// unverified artefacts with no record either way. Still suppressed in JSON
// mode, where human text on stdout would corrupt the document.
func (u *UI) WarnAlways(msg string) {
	if u.jsonMode {
		return
	}
	u.printLine(u.warn, u.icon(IconWarning, PlainIconWarning), msg)
}

// Error prints an error status line (red).
// Visible in quiet mode (--quiet = errors only). Suppressed in JSON mode
// to avoid leaking human text into JSON stdout.
func (u *UI) Error(msg string) {
	if u.jsonMode {
		return
	}
	u.printLine(u.err, u.icon(IconError, PlainIconError), msg)
}

// KeyValue prints a labeled key-value pair with indentation.
func (u *UI) KeyValue(key, value string) {
	if u.quiet {
		return
	}
	if u.plain {
		_, _ = fmt.Fprintf(u.w, "  %s: %s\n", stripControl(key), stripControl(value))
		return
	}
	_, _ = fmt.Fprintf(u.w, "  %s %s\n", u.label.Render(stripControl(key)+":"), stripControl(value))
}

// HintError prints a hint for error recovery. Always shown unless --quiet.
func (u *UI) HintError(msg string) {
	if u.quiet {
		return
	}
	u.printHint(msg)
}

// HintNextStep prints a hint for the natural next action. Always shown unless --quiet.
func (u *UI) HintNextStep(msg string) {
	if u.quiet {
		return
	}
	u.printHint(msg)
}

// HintTip prints a one-time suggestion. In "auto" mode, shown once per session
// per unique message. In "never" mode, suppressed entirely.
func (u *UI) HintTip(msg string) {
	if u.quiet {
		return
	}
	if u.hints == "never" {
		return
	}
	if u.hints == "auto" {
		if _, loaded := sessionTipsSeen.LoadOrStore(msg, struct{}{}); loaded {
			return // already shown this session
		}
	}
	u.printHint(msg)
}

// HintGuidance prints contextual guidance. Only shown in "always" mode.
func (u *UI) HintGuidance(msg string) {
	if u.quiet {
		return
	}
	if u.hints != "always" {
		return
	}
	u.printHint(msg)
}

// printHint is the shared implementation for all hint methods.
func (u *UI) printHint(msg string) {
	icon := u.icon(IconHint, PlainIconHint)
	if u.plain {
		_, _ = fmt.Fprintf(u.w, "%s %s\n", icon, stripControl(msg))
		return
	}
	_, _ = fmt.Fprintf(u.w, "%s %s\n", u.muted.Render(icon), u.muted.Render(stripControl(msg)))
}

// Section prints a bold section header.
func (u *UI) Section(title string) {
	if u.quiet {
		return
	}
	if u.plain {
		_, _ = fmt.Fprintln(u.w, stripControl(title))
		return
	}
	_, _ = fmt.Fprintln(u.w, u.brandBold.Render(stripControl(title)))
}

// Link prints a labeled URL in muted color.
func (u *UI) Link(label, url string) {
	if u.quiet {
		return
	}
	if u.plain {
		_, _ = fmt.Fprintf(u.w, "  %s: %s\n", stripControl(label), stripControl(url))
		return
	}
	_, _ = fmt.Fprintf(u.w, "  %s %s\n", u.label.Render(stripControl(label)+":"), u.muted.Render(stripControl(url)))
}

// Blank prints an empty line for visual separation.
func (u *UI) Blank() {
	if u.quiet {
		return
	}
	_, _ = fmt.Fprintln(u.w)
}

// Plain prints an undecorated message (e.g. for passthrough output).
func (u *UI) Plain(msg string) {
	_, _ = fmt.Fprintln(u.w, stripControl(msg))
}

// Divider prints a horizontal rule for visual separation.
func (u *UI) Divider() {
	if u.quiet {
		return
	}
	if u.plain {
		_, _ = fmt.Fprintln(u.w, strings.Repeat("-", 40))
		return
	}
	_, _ = fmt.Fprintln(u.w, u.muted.Render(strings.Repeat("\u2500", 40)))
}

// InlineKV prints compact inline key-value pairs on a single line.
// Pairs are given as alternating key, value strings.
// Example: InlineKV("Docker", "29.2.1 "+IconSuccess, "Compose", "5.1.0 "+IconSuccess)
func (u *UI) InlineKV(pairs ...string) {
	if u.quiet {
		return
	}
	if len(pairs)%2 != 0 {
		pairs = pairs[:len(pairs)-1] // drop unpaired trailing key
	}
	var b strings.Builder
	b.WriteString("  ")
	for i := 0; i+1 < len(pairs); i += 2 {
		if i > 0 {
			b.WriteString("   ")
		}
		if u.plain {
			b.WriteString(stripControl(pairs[i]))
		} else {
			b.WriteString(u.label.Render(stripControl(pairs[i])))
		}
		b.WriteString(" ")
		b.WriteString(stripControl(pairs[i+1]))
	}
	_, _ = fmt.Fprintln(u.w, b.String())
}

// SuccessIcon returns a styled green checkmark for embedding in strings.
func (u *UI) SuccessIcon() string {
	icon := u.icon(IconSuccess, PlainIconSuccess)
	if u.plain {
		return icon
	}
	return u.success.Render(icon)
}

// ErrorIcon returns a styled red cross for embedding in strings.
func (u *UI) ErrorIcon() string {
	icon := u.icon(IconError, PlainIconError)
	if u.plain {
		return icon
	}
	return u.err.Render(icon)
}

// WarnIcon returns a styled orange exclamation for embedding in strings.
func (u *UI) WarnIcon() string {
	icon := u.icon(IconWarning, PlainIconWarning)
	if u.plain {
		return icon
	}
	return u.warn.Render(icon)
}

// JSONOutput marshals v as indented JSON and writes it to the UI writer.
func (u *UI) JSONOutput(v any) error {
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(u.w, string(data))
	return err
}

// writerIsTTY reports whether w is a terminal file descriptor.
func writerIsTTY(w io.Writer) bool {
	f, ok := w.(*os.File)
	if !ok {
		return false
	}
	return isatty.IsTerminal(f.Fd()) || isatty.IsCygwinTerminal(f.Fd())
}

// Table prints rows as a fixed-width table with a header.
// All values are sanitized to prevent terminal control injection.
func (u *UI) Table(headers []string, rows [][]string) {
	if u.quiet {
		return
	}
	if len(headers) == 0 {
		return
	}
	sanHeaders, sanRows := sanitizeTable(headers, rows)
	widths := calcColumnWidths(sanHeaders, sanRows)
	u.printTableRow(widths, sanHeaders)
	sep := make([]string, len(sanHeaders))
	sepChar := "\u2500"
	if u.plain {
		sepChar = "-"
	}
	for i, w := range widths {
		sep[i] = strings.Repeat(sepChar, w)
	}
	u.printTableRow(widths, sep)
	for _, row := range sanRows {
		u.printTableRow(widths, row)
	}
}

// sanitizeTable strips control chars and collapses whitespace in all cells.
func sanitizeTable(headers []string, rows [][]string) ([]string, [][]string) {
	sanitize := func(s string) string {
		s = stripControl(s)
		s = strings.ReplaceAll(s, "\n", " ")
		s = strings.ReplaceAll(s, "\t", " ")
		return s
	}
	sh := make([]string, len(headers))
	for i, h := range headers {
		sh[i] = sanitize(h)
	}
	sr := make([][]string, len(rows))
	for i, row := range rows {
		r := make([]string, len(row))
		for j, cell := range row {
			r[j] = sanitize(cell)
		}
		sr[i] = r
	}
	return sh, sr
}

// calcColumnWidths computes the maximum visual width per column.
func calcColumnWidths(headers []string, rows [][]string) []int {
	widths := make([]int, len(headers))
	for i, h := range headers {
		widths[i] = runewidth.StringWidth(h)
	}
	for _, row := range rows {
		for i := range widths {
			if i < len(row) {
				if w := runewidth.StringWidth(row[i]); w > widths[i] {
					widths[i] = w
				}
			}
		}
	}
	return widths
}

// printTableRow prints a single table row with aligned columns.
func (u *UI) printTableRow(widths []int, cells []string) {
	var b strings.Builder
	b.WriteString("  ")
	for i, w := range widths {
		cell := ""
		if i < len(cells) {
			cell = cells[i]
		}
		if i > 0 {
			b.WriteString("  ")
		}
		b.WriteString(cell)
		pad := w - runewidth.StringWidth(cell)
		if pad > 0 {
			b.WriteString(strings.Repeat(" ", pad))
		}
	}
	_, _ = fmt.Fprintln(u.w, b.String())
}

// stripControl removes ASCII control characters (except tab and newline),
// DEL, and C1 control bytes (0x80-0x9F) to prevent terminal escape
// sequence injection in displayed values.
func stripControl(s string) string {
	return strings.Map(func(r rune) rune {
		if (r < 0x20 && r != '\t' && r != '\n') || r == 0x7F || (r >= 0x80 && r <= 0x9F) {
			return -1
		}
		return r
	}, s)
}

// stripControlStrict removes all ASCII control characters (< 0x20) including
// tab, newline, and ESC, plus DEL (0x7F) and C1 control bytes (0x80-0x9F).
// Use for single-line contexts (box content, spinner messages) where embedded
// newlines or tabs would corrupt layout.
func stripControlStrict(s string) string {
	return strings.Map(func(r rune) rune {
		if r < 0x20 || r == 0x7F || (r >= 0x80 && r <= 0x9F) {
			return -1
		}
		return r
	}, s)
}
