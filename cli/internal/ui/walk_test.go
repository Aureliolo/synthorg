package ui

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"

	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
)

// makeWalkInput builds a WalkBatchInput for tests with N synthetic releases.
// Every release has both a highlights block AND a commit-based changelog so
// the toggle exercises both paths. Pass tagsWithoutHighlights to make
// specific releases fall back to the commits-only path.
func makeWalkInput(t *testing.T, count int, withoutHighlights map[int]bool, isFinal bool, initialView changelogView) WalkBatchInput {
	t.Helper()
	versions := make([]selfupdate.Release, count)
	for i := range count {
		tag := "v0.7." + string(rune('0'+i))
		var body string
		if withoutHighlights[i] {
			body = "## [" + tag + "]\n\n### Features\n* basic feature\n\n---\n\n## CLI Installation\n"
		} else {
			body = "<!-- HIGHLIGHTS_START -->\n## Highlights\n\n> _AI-generated summary (model: example) Commit-based changelog below._\n\n### What's new\n\n- Bullet for " + tag + "\n\n<!-- HIGHLIGHTS_END -->\n\n## [" + tag + "]\n\n### Features\n* commit for " + tag + " ([#1](https://github.com/Aureliolo/synthorg/issues/1)) ([abc1234](https://github.com/Aureliolo/synthorg/commit/abc1234abc1234abc1234))\n\n---\n\n## CLI Installation\n"
		}
		versions[i] = selfupdate.Release{
			TagName: tag,
			Body:    body,
		}
	}
	return WalkBatchInput{
		Versions:     versions,
		InitialView:  initialView,
		IsFinalBatch: isFinal,
		Width:        100,
		Height:       30,
		Options:      Options{NoColor: true},
	}
}

// keyMsg builds a tea.KeyPressMsg that matches a string Keystroke. Bubbletea
// matches KeyPressMsg by Key.String(); for plain rune presses we set Code
// and Text to the rune. For named keys we match what tea.KeyPressMsg.String
// produces.
func keyMsg(key string) tea.KeyPressMsg {
	switch key {
	case "enter":
		return tea.KeyPressMsg{Code: tea.KeyEnter, Text: ""}
	case "ctrl+c":
		return tea.KeyPressMsg{Code: 'c', Mod: tea.ModCtrl}
	case "down":
		return tea.KeyPressMsg{Code: tea.KeyDown}
	case "up":
		return tea.KeyPressMsg{Code: tea.KeyUp}
	case "pgdown":
		return tea.KeyPressMsg{Code: tea.KeyPgDown}
	case "pgup":
		return tea.KeyPressMsg{Code: tea.KeyPgUp}
	}
	// Single-character keys: c, j, k, q, n, g, G.
	r := []rune(key)
	if len(r) != 1 {
		panic("keyMsg: unsupported key: " + key)
	}
	return tea.KeyPressMsg{Code: r[0], Text: key}
}

// updateAndCast applies a key message to a walkModel and returns the
// resulting walkModel. Wraps the tea.Model assertion noise.
func updateAndCast(t *testing.T, m walkModel, key string) walkModel {
	t.Helper()
	updated, _ := m.Update(keyMsg(key))
	wm, ok := updated.(walkModel)
	if !ok {
		t.Fatalf("expected walkModel, got %T", updated)
	}
	return wm
}

func TestWalk_initialViewMatchesInput(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, viewCommits)
	m := newWalkModel(in)
	if m.view != viewCommits {
		t.Errorf("InitialView=commits should set m.view=commits, got %q", m.view)
	}
}

func TestWalk_initialViewDefaultsToHighlights(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, "")
	m := newWalkModel(in)
	if m.view != viewHighlights {
		t.Errorf("empty InitialView should default to highlights, got %q", m.view)
	}
}

func TestWalk_cTogglesView(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, viewHighlights)
	m := newWalkModel(in)
	m = updateAndCast(t, m, "c")
	if m.view != viewCommits {
		t.Errorf("after c: view = %q, want commits", m.view)
	}
	m = updateAndCast(t, m, "c")
	if m.view != viewHighlights {
		t.Errorf("after second c: view = %q, want highlights", m.view)
	}
}

func TestRenderWalkStatic_carriesEveryRelease(t *testing.T) {
	// Height 30 fits one batch interactively; the static render must ignore
	// both the batch size and the viewport cap and emit all five.
	out := RenderWalkStatic(makeWalkInput(t, 5, nil, true, viewHighlights))
	for i := range 5 {
		tag := "v0.7." + string(rune('0'+i))
		if !strings.Contains(out, tag) {
			t.Errorf("static render missing %s\n--- got ---\n%s", tag, out)
		}
		if !strings.Contains(out, "Bullet for "+tag) {
			t.Errorf("static render missing highlights for %s\n--- got ---\n%s", tag, out)
		}
		if !strings.Contains(out, "["+string(rune('1'+i))+"/5]") {
			t.Errorf("static render missing position marker for %s\n--- got ---\n%s", tag, out)
		}
	}
	if strings.Contains(out, "[j/k]") || strings.Contains(out, "[enter]") {
		t.Errorf("static render must carry no key footer\n--- got ---\n%s", out)
	}
}

func TestRenderWalkStatic_honoursCommitsView(t *testing.T) {
	out := RenderWalkStatic(makeWalkInput(t, 1, nil, true, viewCommits))
	if !strings.Contains(out, "commit for v0.7.0") {
		t.Errorf("commits view should render the commit log\n--- got ---\n%s", out)
	}
	if strings.Contains(out, "Bullet for v0.7.0") {
		t.Errorf("commits view should not render highlights\n--- got ---\n%s", out)
	}
}

// A release predating AI highlights has only a commit log to show, whatever
// view was asked for, and says so -- same rule the interactive walk applies
// in loadCurrentContent.
func TestRenderWalkStatic_fallsBackForReleaseWithoutHighlights(t *testing.T) {
	out := RenderWalkStatic(makeWalkInput(t, 1, map[int]bool{0: true}, true, viewHighlights))
	if !strings.Contains(out, "No AI highlights") {
		t.Errorf("missing fallback note\n--- got ---\n%s", out)
	}
	if !strings.Contains(out, "basic feature") {
		t.Errorf("missing commit log\n--- got ---\n%s", out)
	}
}

// A tag name is remote and git permits bidi overrides in a ref, so the
// version string identifying what is about to be installed can be made to
// read as something else. It must never reach the terminal as authored.
func TestRenderWalkStatic_sanitisesTagName(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, viewHighlights)
	in.Versions[0].TagName = "v9.9.9" + rloRune + "0.0.1v"
	out := RenderWalkStatic(in)
	if strings.ContainsRune(out, 0x202E) {
		t.Errorf("bidi override survived into the header\n--- got ---\n%q", out)
	}
	if !strings.Contains(out, "v9.9.90.0.1v") {
		t.Errorf("scrubbed tag should still render its own characters\n--- got ---\n%q", out)
	}
}

func TestRenderWalkStatic_emptyInput(t *testing.T) {
	in := makeWalkInput(t, 0, nil, true, viewHighlights)
	if out := RenderWalkStatic(in); out != "" {
		t.Errorf("no versions should render nothing, got %q", out)
	}
}

func TestWalk_cIgnoredOnNonToggleable(t *testing.T) {
	in := makeWalkInput(t, 1, map[int]bool{0: true}, true, viewHighlights)
	m := newWalkModel(in)
	m = updateAndCast(t, m, "c")
	// view doesn't actually matter when not toggleable -- loadCurrentContent
	// forces commits anyway -- but flashMsg must be set so the user knows.
	if m.flashMsg == "" {
		t.Errorf("c on non-toggleable should set flashMsg")
	}
}

func TestWalk_enterAdvances(t *testing.T) {
	in := makeWalkInput(t, 3, nil, true, viewHighlights)
	m := newWalkModel(in)
	m = updateAndCast(t, m, "enter")
	if m.idx != 1 {
		t.Errorf("after enter on idx=0: idx = %d, want 1", m.idx)
	}
	m = updateAndCast(t, m, "enter")
	if m.idx != 2 {
		t.Errorf("after enter on idx=1: idx = %d, want 2", m.idx)
	}
}

func TestWalk_enterOnLastInFinalBatchExitsDone(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, viewHighlights)
	m := newWalkModel(in)
	updated, cmd := m.Update(keyMsg("enter"))
	wm := updated.(walkModel)
	if wm.result.Outcome != WalkOutcomeDone {
		t.Errorf("Outcome = %v, want WalkOutcomeDone", wm.result.Outcome)
	}
	if cmd == nil {
		t.Fatal("enter on last-in-final-batch should produce tea.Quit cmd")
	}
}

func TestWalk_enterOnLastInNonFinalBatchEmitsNextBatch(t *testing.T) {
	in := makeWalkInput(t, 1, nil, false, viewHighlights)
	m := newWalkModel(in)
	updated, cmd := m.Update(keyMsg("enter"))
	wm := updated.(walkModel)
	if wm.result.Outcome != WalkOutcomeNextBatch {
		t.Errorf("Outcome = %v, want WalkOutcomeNextBatch", wm.result.Outcome)
	}
	if cmd == nil {
		t.Fatal("enter on last-in-non-final-batch should produce tea.Quit cmd")
	}
}

func TestWalk_nOnLastInNonFinalBatchEmitsNextBatch(t *testing.T) {
	in := makeWalkInput(t, 1, nil, false, viewHighlights)
	m := newWalkModel(in)
	updated, cmd := m.Update(keyMsg("n"))
	wm := updated.(walkModel)
	if wm.result.Outcome != WalkOutcomeNextBatch {
		t.Errorf("Outcome = %v, want WalkOutcomeNextBatch", wm.result.Outcome)
	}
	if cmd == nil {
		t.Fatal("n on last-in-non-final-batch should produce tea.Quit cmd")
	}
}

func TestWalk_nOnFinalBatchIsNoOp(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, viewHighlights)
	m := newWalkModel(in)
	updated, cmd := m.Update(keyMsg("n"))
	wm := updated.(walkModel)
	if wm.result.Outcome == WalkOutcomeNextBatch {
		t.Errorf("n on final batch should NOT emit NextBatch")
	}
	if cmd != nil {
		t.Errorf("n on final batch should not quit")
	}
}

func TestWalk_qQuitsImmediately(t *testing.T) {
	in := makeWalkInput(t, 3, nil, true, viewHighlights)
	m := newWalkModel(in)
	m = updateAndCast(t, m, "enter") // idx=1
	updated, cmd := m.Update(keyMsg("q"))
	wm := updated.(walkModel)
	if wm.result.Outcome != WalkOutcomeQuit {
		t.Errorf("Outcome = %v, want WalkOutcomeQuit", wm.result.Outcome)
	}
	if cmd == nil {
		t.Fatal("q should produce tea.Quit cmd")
	}
}

func TestWalk_ctrlcQuits(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, viewHighlights)
	m := newWalkModel(in)
	updated, cmd := m.Update(keyMsg("ctrl+c"))
	wm := updated.(walkModel)
	if wm.result.Outcome != WalkOutcomeQuit {
		t.Errorf("Outcome = %v, want WalkOutcomeQuit", wm.result.Outcome)
	}
	if cmd == nil {
		t.Fatal("ctrl+c should produce tea.Quit cmd")
	}
}

func TestWalk_jScrollsViewport(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, viewHighlights)
	// Force tall content so j actually has somewhere to scroll.
	in.Height = 5
	m := newWalkModel(in)
	// Shrink the viewport so even short content is scrollable.
	m.viewport.SetHeight(1)
	m.loadCurrentContent()
	before := m.viewport.YOffset()
	m = updateAndCast(t, m, "j")
	if m.viewport.YOffset() == before {
		// Scrolling may not happen if content is shorter than the viewport;
		// assert that AtBottom() is true so the call was at least exercised.
		if !m.viewport.AtBottom() {
			t.Errorf("j did not scroll and viewport not AtBottom: yOffset=%d", m.viewport.YOffset())
		}
	}
}

func TestWalk_resizeUpdatesViewport(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, viewHighlights)
	m := newWalkModel(in)
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	wm := updated.(walkModel)
	if wm.width != 120 {
		t.Errorf("width after resize = %d, want 120", wm.width)
	}
	if wm.viewport.Width() != 120 {
		t.Errorf("viewport width = %d, want 120", wm.viewport.Width())
	}
}

func TestWalk_toggleStatePropagatesAcrossVersions(t *testing.T) {
	in := makeWalkInput(t, 3, nil, true, viewHighlights)
	m := newWalkModel(in)
	m = updateAndCast(t, m, "c") // flip to commits at idx=0
	if m.view != viewCommits {
		t.Fatalf("view should be commits after c, got %q", m.view)
	}
	m = updateAndCast(t, m, "enter") // advance to idx=1; view should still be commits
	if m.view != viewCommits {
		t.Errorf("view after advance = %q, want commits (session-level toggle)", m.view)
	}
}

func TestWalk_finalViewReportedInResult(t *testing.T) {
	in := makeWalkInput(t, 1, nil, true, viewHighlights)
	m := newWalkModel(in)
	m = updateAndCast(t, m, "c") // flip to commits
	updated, _ := m.Update(keyMsg("q"))
	wm := updated.(walkModel)
	if wm.result.FinalView != viewCommits {
		t.Errorf("FinalView = %q, want commits", wm.result.FinalView)
	}
}

func TestWalk_viewIncludesHeaderAndFooter(t *testing.T) {
	in := makeWalkInput(t, 2, nil, false, viewHighlights)
	m := newWalkModel(in)
	out := m.View().Content
	if !strings.Contains(out, "v0.7.0") {
		t.Errorf("view should contain version tag\n--- got ---\n%s", out)
	}
	if !strings.Contains(out, "[c]") {
		t.Errorf("view should contain key footer\n--- got ---\n%s", out)
	}
	if !strings.Contains(out, "[1/2]") {
		t.Errorf("view should contain position indicator\n--- got ---\n%s", out)
	}
	// Non-final batch on last item should show "[enter/n] next batch" --
	// both keys advance, so the footer must surface both to stay
	// consistent with handleKey.
	m = updateAndCast(t, m, "enter") // idx=1, last item
	out = m.View().Content
	if !strings.Contains(out, "[enter/n] next batch") {
		t.Errorf("view on last-in-non-final-batch should mention [enter/n] next batch\n--- got ---\n%s", out)
	}
}

func TestWalk_viewShowsFallbackForNonToggleable(t *testing.T) {
	in := makeWalkInput(t, 1, map[int]bool{0: true}, true, viewHighlights)
	m := newWalkModel(in)
	out := m.View().Content
	if !strings.Contains(out, "No AI highlights") {
		t.Errorf("non-toggleable version should show fallback note\n--- got ---\n%s", out)
	}
	if !strings.Contains(out, "[c] disabled") {
		t.Errorf("footer should show [c] disabled for non-toggleable\n--- got ---\n%s", out)
	}
}

func TestWalk_emptyVersionsHandled(t *testing.T) {
	in := WalkBatchInput{Versions: nil, IsFinalBatch: true, Width: 80, Height: 24, Options: Options{}}
	m := newWalkModel(in)
	if got := m.View().Content; got != "" {
		t.Errorf("empty Versions should render empty view, got %q", got)
	}
}
