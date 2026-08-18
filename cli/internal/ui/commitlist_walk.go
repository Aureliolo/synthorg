package ui

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"

	"charm.land/bubbles/v2/viewport"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
)

// CommitWalkOutcome is what RunCommitWalk returns.
type CommitWalkOutcome int

const (
	// CommitWalkDone means the user pressed enter and is happy to proceed.
	CommitWalkDone CommitWalkOutcome = iota
	// CommitWalkQuit means the user pressed q or ctrl+c.
	CommitWalkQuit
)

// CommitWalkInput drives RunCommitWalk -- the dev-channel walk shows a
// single combined commit list (no per-version batching) because dev
// pre-releases have no Highlights blocks.
type CommitWalkInput struct {
	Installed string
	Target    string
	Commits   selfupdate.CommitRange
	Width     int
	Height    int
	Options   Options
	// Output is the writer bubbletea drives. Falls back to os.Stdout when
	// nil so existing call sites that haven't been updated still work.
	Output io.Writer
}

// RenderCommitWalkStatic renders the same commit list as RunCommitWalk into a
// plain string: title line plus the full listing, no viewport and no key
// footer. Nothing here is height-bounded -- the caller prints the block and
// the terminal's scrollback holds it.
func RenderCommitWalkStatic(in CommitWalkInput) string {
	w, _ := initialDimensions(in.Width, in.Height)
	return commitWalkTitle(in.Installed, in.Target, in.Commits.TotalCommits, in.Options) +
		"\n" + RenderCommitList(in.Commits, w, in.Options)
}

// RunCommitWalk runs the dev-channel commit list view in a single bubbletea
// program and returns the outcome.
func RunCommitWalk(ctx context.Context, in CommitWalkInput) (CommitWalkOutcome, error) {
	m := newCommitWalkModel(in)
	out := in.Output
	if out == nil {
		out = os.Stdout
	}
	p := tea.NewProgram(m, tea.WithContext(ctx), tea.WithOutput(out))
	final, err := p.Run()
	if err != nil {
		return CommitWalkQuit, err
	}
	cm, ok := final.(commitWalkModel)
	if !ok {
		return CommitWalkQuit, fmt.Errorf("commit walk: unexpected final model type %T", final)
	}
	return cm.outcome, nil
}

// commitWalkModel is the bubbletea model for the dev-channel walk.
type commitWalkModel struct {
	installed string
	target    string
	commits   selfupdate.CommitRange
	viewport  viewport.Model
	width     int
	height    int
	opts      Options
	outcome   CommitWalkOutcome
}

// commitWalkFooterText is the keymap line at the bottom of the commit walk.
// Kept as a package constant so viewportHeight can probe its visible width
// without re-rendering.
const commitWalkFooterText = "[j/k] scroll  [g/G] top/bottom  [enter] continue  [q] quit"

// titleVisibleWidth returns the visible width of the rendered title line
// `<prefix><title>  <count>` so the viewportHeight chrome calculation can
// detect when the title wraps on narrow terminals.
func (m commitWalkModel) titleVisibleWidth() int {
	return lipgloss.Width(commitWalkTitle(m.installed, m.target, m.commits.TotalCommits, m.opts))
}

// viewportHeight reserves chrome for the dev-channel walk layout: the
// title line + viewport trailing newline + footer line. On narrow
// terminals the title or footer can each wrap to multiple rows; we use
// wrappedLines so the chrome budget accounts for the actual row count
// instead of assuming one line each. This keeps the viewport from
// overflowing on small windows or with long dev-version labels.
func (m commitWalkModel) viewportHeight() int {
	chrome := 1 // viewport trailing newline
	chrome += wrappedLines(m.titleVisibleWidth(), m.width)
	chrome += wrappedLines(lipgloss.Width(commitWalkFooterText), m.width)
	return viewportHeightForChrome(m.height, chrome)
}

// newCommitWalkModel pre-renders the commit list and wires up the viewport.
func newCommitWalkModel(in CommitWalkInput) commitWalkModel {
	w, h := initialDimensions(in.Width, in.Height)
	m := commitWalkModel{
		installed: in.Installed,
		target:    in.Target,
		commits:   in.Commits,
		width:     w,
		height:    h,
		opts:      in.Options,
		outcome:   CommitWalkQuit, // default if program exits unexpectedly
	}
	vp := viewport.New(viewport.WithWidth(w), viewport.WithHeight(m.viewportHeight()))
	vp.SoftWrap = true
	vp.SetContent(RenderCommitList(in.Commits, w, in.Options))
	m.viewport = vp
	return m
}

// Init implements tea.Model.
func (commitWalkModel) Init() tea.Cmd {
	return tea.RequestWindowSize
}

// Update implements tea.Model.
func (m commitWalkModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		return m.handleResize(msg), nil
	case tea.KeyPressMsg:
		if model, cmd, handled := m.handleKey(msg.String()); handled {
			return model, cmd
		}
	}
	var cmd tea.Cmd
	m.viewport, cmd = m.viewport.Update(msg)
	return m, cmd
}

// handleResize updates the viewport for a new terminal size and
// re-renders the commit list so subject truncation tracks the width.
func (m commitWalkModel) handleResize(msg tea.WindowSizeMsg) commitWalkModel {
	m.width = msg.Width
	m.height = msg.Height
	m.viewport.SetWidth(msg.Width)
	m.viewport.SetHeight(m.viewportHeight())
	m.viewport.SetContent(RenderCommitList(m.commits, msg.Width, m.opts))
	return m
}

// handleKey processes a key press. Returns handled=false for keys this
// walk does not own, so the caller can forward them to the viewport for
// default scroll handling.
func (m commitWalkModel) handleKey(key string) (tea.Model, tea.Cmd, bool) {
	switch key {
	case "ctrl+c", "q":
		m.outcome = CommitWalkQuit
		return m, tea.Quit, true
	case "enter":
		m.outcome = CommitWalkDone
		return m, tea.Quit, true
	case "j", "down":
		m.viewport.ScrollDown(1)
	case "k", "up":
		m.viewport.ScrollUp(1)
	case "pgdown", " ", "space":
		m.viewport.PageDown()
	case "pgup":
		m.viewport.PageUp()
	case "g", "home":
		m.viewport.GotoTop()
	case "G", "end":
		m.viewport.GotoBottom()
	default:
		return m, nil, false
	}
	return m, nil, true
}

// View implements tea.Model.
func (m commitWalkModel) View() tea.View {
	return tea.NewView(m.renderView())
}

func (m commitWalkModel) renderView() string {
	plain := m.opts.NoColor || m.opts.Plain
	muted := lipgloss.NewStyle()
	if !plain {
		muted = muted.Foreground(colorMuted)
	}

	var sb strings.Builder
	sb.WriteString(commitWalkTitle(m.installed, m.target, m.commits.TotalCommits, m.opts))
	sb.WriteByte('\n')
	sb.WriteString(m.viewport.View())
	sb.WriteByte('\n')
	sb.WriteString(muted.Render(commitWalkFooterText))
	return sb.String()
}

// commitWalkTitle renders the "── dev channel: v1 -> v2  N commits" line that
// heads both the interactive walk and its static render, so the two cannot
// drift apart.
func commitWalkTitle(installed, target string, total int, opts Options) string {
	plain := opts.NoColor || opts.Plain
	muted := lipgloss.NewStyle()
	header := lipgloss.NewStyle()
	if !plain {
		muted = muted.Foreground(colorMuted)
		header = header.Foreground(colorBrand).Bold(true)
	}

	prefix := "── "
	if opts.Plain {
		prefix = "-- "
	}

	return muted.Render(prefix) +
		header.Render(fmt.Sprintf("dev channel: %s -> %s", installed, target)) +
		"  " + muted.Render(fmt.Sprintf("%d commits", total))
}
