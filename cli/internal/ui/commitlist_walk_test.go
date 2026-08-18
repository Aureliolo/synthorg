package ui

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"

	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
)

func makeCommitInput(opts Options) CommitWalkInput {
	return CommitWalkInput{
		Installed: "v0.7.3-dev.5",
		Target:    "v0.7.3-dev.9",
		Commits:   sampleRange(),
		Width:     100,
		Height:    20,
		Options:   opts,
	}
}

func updateCommitWalk(t *testing.T, m commitWalkModel, key string) (commitWalkModel, tea.Cmd) {
	t.Helper()
	updated, cmd := m.Update(keyMsg(key))
	wm, ok := updated.(commitWalkModel)
	if !ok {
		t.Fatalf("expected commitWalkModel, got %T", updated)
	}
	return wm, cmd
}

func TestCommitWalk_renderHeader(t *testing.T) {
	m := newCommitWalkModel(makeCommitInput(Options{NoColor: true}))
	out := m.View().Content
	if !strings.Contains(out, "v0.7.3-dev.5") {
		t.Errorf("missing installed tag\n--- got ---\n%s", out)
	}
	if !strings.Contains(out, "v0.7.3-dev.9") {
		t.Errorf("missing target tag\n--- got ---\n%s", out)
	}
	if !strings.Contains(out, "3 commits") {
		t.Errorf("missing commit count\n--- got ---\n%s", out)
	}
	if !strings.Contains(out, "[j/k]") {
		t.Errorf("missing key footer\n--- got ---\n%s", out)
	}
}

func TestRenderCommitWalkStatic_carriesEveryCommit(t *testing.T) {
	out := RenderCommitWalkStatic(makeCommitInput(Options{NoColor: true}))
	for _, want := range []string{
		"v0.7.3-dev.5", "v0.7.3-dev.9", "3 commits",
		"feat(cli): per-version Highlights walk",
		"fix(selfupdate): pagination cap",
		"chore(deps): bump",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("static render missing %q\n--- got ---\n%s", want, out)
		}
	}
	// The whole point of the static render: nothing to press, so nothing
	// may advertise a key. A footer here would be an instruction the
	// caller has already moved past by the time it is read.
	if strings.Contains(out, "[j/k]") || strings.Contains(out, "[enter]") {
		t.Errorf("static render must carry no key footer\n--- got ---\n%s", out)
	}
}

// The static render is not height-bounded: the interactive walk caps its
// viewport at the terminal height, and a static block that inherited that
// cap would silently drop commits the operator is about to install.
func TestRenderCommitWalkStatic_ignoresTerminalHeight(t *testing.T) {
	in := makeCommitInput(Options{NoColor: true})
	in.Height = 3
	out := RenderCommitWalkStatic(in)
	if !strings.Contains(out, "chore(deps): bump") {
		t.Errorf("last commit dropped on a short terminal\n--- got ---\n%s", out)
	}
}

func TestRenderCommitWalkStatic_keepsTruncationNotice(t *testing.T) {
	in := makeCommitInput(Options{NoColor: true})
	in.Commits.TotalCommits = 400 // more than the compare API returned
	out := RenderCommitWalkStatic(in)
	if !strings.Contains(out, "400") {
		t.Errorf("static render should report the full commit count\n--- got ---\n%s", out)
	}
}

func TestCommitWalk_emptyRange(t *testing.T) {
	in := makeCommitInput(Options{NoColor: true})
	in.Commits = selfupdate.CommitRange{TotalCommits: 0}
	m := newCommitWalkModel(in)
	out := m.View().Content
	if !strings.Contains(out, "0 commits") {
		t.Errorf("empty range should show 0 commits\n--- got ---\n%s", out)
	}
	if !strings.Contains(out, "no commits") {
		t.Errorf("empty range should show 'no commits' message\n--- got ---\n%s", out)
	}
}

func TestCommitWalk_enterExits(t *testing.T) {
	m := newCommitWalkModel(makeCommitInput(Options{NoColor: true}))
	wm, cmd := updateCommitWalk(t, m, "enter")
	if wm.outcome != CommitWalkDone {
		t.Errorf("outcome = %v, want CommitWalkDone", wm.outcome)
	}
	if cmd == nil {
		t.Fatal("enter should produce tea.Quit cmd")
	}
}

func TestCommitWalk_qExits(t *testing.T) {
	m := newCommitWalkModel(makeCommitInput(Options{NoColor: true}))
	wm, cmd := updateCommitWalk(t, m, "q")
	if wm.outcome != CommitWalkQuit {
		t.Errorf("outcome = %v, want CommitWalkQuit", wm.outcome)
	}
	if cmd == nil {
		t.Fatal("q should produce tea.Quit cmd")
	}
}

func TestCommitWalk_ctrlcExits(t *testing.T) {
	m := newCommitWalkModel(makeCommitInput(Options{NoColor: true}))
	wm, cmd := updateCommitWalk(t, m, "ctrl+c")
	if wm.outcome != CommitWalkQuit {
		t.Errorf("outcome = %v, want CommitWalkQuit", wm.outcome)
	}
	if cmd == nil {
		t.Fatal("ctrl+c should produce tea.Quit cmd")
	}
}

func TestCommitWalk_jScrollsViewport(t *testing.T) {
	m := newCommitWalkModel(makeCommitInput(Options{NoColor: true}))
	// Force a tiny viewport so we can scroll predictably.
	m.viewport.SetHeight(1)
	m.viewport.SetContent(strings.Repeat("line\n", 10))
	before := m.viewport.YOffset()
	wm, _ := updateCommitWalk(t, m, "j")
	if wm.viewport.YOffset() <= before {
		t.Errorf("j did not scroll: yOffset before=%d after=%d", before, wm.viewport.YOffset())
	}
}

func TestCommitWalk_GjumpsToBottom(t *testing.T) {
	m := newCommitWalkModel(makeCommitInput(Options{NoColor: true}))
	m.viewport.SetHeight(2)
	m.viewport.SetContent(strings.Repeat("line\n", 30))
	wm, _ := updateCommitWalk(t, m, "G")
	if !wm.viewport.AtBottom() {
		t.Errorf("G should jump to bottom, AtBottom = false (yOffset=%d)", wm.viewport.YOffset())
	}
}

func TestCommitWalk_gJumpsToTop(t *testing.T) {
	m := newCommitWalkModel(makeCommitInput(Options{NoColor: true}))
	m.viewport.SetHeight(2)
	m.viewport.SetContent(strings.Repeat("line\n", 30))
	m.viewport.GotoBottom()
	wm, _ := updateCommitWalk(t, m, "g")
	if wm.viewport.YOffset() != 0 {
		t.Errorf("g should jump to top, yOffset = %d", wm.viewport.YOffset())
	}
}

func TestCommitWalk_resizeUpdatesViewportAndRerenders(t *testing.T) {
	m := newCommitWalkModel(makeCommitInput(Options{NoColor: true}))
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 60, Height: 10})
	wm := updated.(commitWalkModel)
	if wm.viewport.Width() != 60 {
		t.Errorf("viewport width after resize = %d, want 60", wm.viewport.Width())
	}
	if wm.width != 60 {
		t.Errorf("model width = %d, want 60", wm.width)
	}
}
