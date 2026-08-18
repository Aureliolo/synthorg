package ui

import (
	"bytes"
	"strings"
	"testing"
)

func TestLogo(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.Logo("v1.2.3")
	out := buf.String()
	// ANSI Shadow block-letter banner uses full-block characters.
	if !strings.Contains(out, "\u2588") {
		t.Error("Logo output missing expected block-letter content")
	}
	if !strings.Contains(out, "v1.2.3") {
		t.Error("Logo output missing version string")
	}
	// Verify version string is positioned after the logo art.
	if trimmed := strings.TrimRight(out, "\n"); !strings.HasSuffix(trimmed, "v1.2.3") {
		t.Errorf("version string should appear at the end of logo output, got %q", trimmed)
	}
}

func TestOutputMethods(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		call func(*UI)
		want []string
	}{
		{"Success", func(u *UI) { u.Success("all good") }, []string{IconSuccess, "all good"}},
		{"Step", func(u *UI) { u.Step("doing work") }, []string{IconInProgress, "doing work"}},
		{"Warn", func(u *UI) { u.Warn("careful") }, []string{IconWarning, "careful"}},
		{"Error", func(u *UI) { u.Error("bad thing") }, []string{IconError, "bad thing"}},
		{"KeyValue", func(u *UI) { u.KeyValue("Data dir", "/tmp/test") }, []string{"Data dir:", "/tmp/test"}},
		{"HintNextStep", func(u *UI) { u.HintNextStep("try this") }, []string{IconHint, "try this"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var buf bytes.Buffer
			u := NewUI(&buf)
			tc.call(u)
			out := buf.String()
			for _, s := range tc.want {
				if !strings.Contains(out, s) {
					t.Errorf("output missing %q: %s", s, out)
				}
			}
			if !strings.HasSuffix(out, "\n") {
				t.Errorf("output not newline-terminated: %q", out)
			}
		})
	}
}

func TestLink(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.Link("Dashboard", "http://localhost:3000")
	out := buf.String()
	if !strings.Contains(out, "Dashboard:") {
		t.Error("Link missing label")
	}
	if !strings.Contains(out, "http://localhost:3000") {
		t.Error("Link missing URL")
	}
}

func TestTable(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.Table(
		[]string{"NAME", "VALUE"},
		[][]string{{"foo", "bar"}, {"longer", "x"}},
	)
	out := buf.String()
	if !strings.Contains(out, "NAME") {
		t.Error("Table missing header")
	}
	if !strings.Contains(out, "foo") || !strings.Contains(out, "bar") {
		t.Error("Table missing row data")
	}
	if !strings.Contains(out, "───") {
		t.Error("Table missing separator")
	}
}

func TestTableEmpty(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.Table(nil, nil)
	if buf.Len() != 0 {
		t.Error("Table with nil headers should produce no output")
	}
}

func TestWriter(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	if u.Writer() != &buf {
		t.Error("Writer() should return the underlying writer")
	}
}

func TestBlank(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.Blank()
	if buf.String() != "\n" {
		t.Errorf("Blank should produce single newline, got %q", buf.String())
	}
}

func TestPlain(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.Plain("hello world")
	if !strings.Contains(buf.String(), "hello world") {
		t.Error("Plain missing message")
	}
}

func TestDivider(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.Divider()
	out := buf.String()
	if !strings.Contains(out, "\u2500") {
		t.Error("Divider missing horizontal line character")
	}
}

func TestInlineKV(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.InlineKV("Docker", "29.2.1", "Compose", "5.1.0")
	out := buf.String()
	if !strings.Contains(out, "Docker") || !strings.Contains(out, "29.2.1") {
		t.Error("InlineKV missing first pair")
	}
	if !strings.Contains(out, "Compose") || !strings.Contains(out, "5.1.0") {
		t.Error("InlineKV missing second pair")
	}
}

func TestIconAccessors(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	if !strings.Contains(u.SuccessIcon(), IconSuccess) {
		t.Error("SuccessIcon missing checkmark")
	}
	if !strings.Contains(u.ErrorIcon(), IconError) {
		t.Error("ErrorIcon missing cross")
	}
	if !strings.Contains(u.WarnIcon(), IconWarning) {
		t.Error("WarnIcon missing exclamation")
	}
}

func TestIsTTY(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	// A bytes.Buffer is not a TTY.
	if u.IsTTY() {
		t.Error("bytes.Buffer should not be detected as TTY")
	}
}

func TestBox(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.Box("Test Box", []string{"line one", "line two"})
	out := buf.String()
	if !strings.Contains(out, "Test Box") {
		t.Error("Box missing title")
	}
	if !strings.Contains(out, "line one") {
		t.Error("Box missing first line")
	}
	if !strings.Contains(out, "line two") {
		t.Error("Box missing second line")
	}
	// Check box-drawing characters.
	if !strings.Contains(out, "\u256d") { // rounded top-left corner
		t.Error("Box missing top-left corner")
	}
	if !strings.Contains(out, "\u2570") { // rounded bottom-left corner
		t.Error("Box missing bottom-left corner")
	}
	if !strings.Contains(out, "\u2502") { // vertical line
		t.Error("Box missing vertical line")
	}
}

func TestBoxEmpty(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	u.Box("Empty", nil)
	if buf.Len() != 0 {
		t.Error("Box with no lines should produce no output")
	}
}

func TestSpinnerNonTTY(t *testing.T) {
	t.Parallel()
	// On a non-TTY writer (bytes.Buffer), the spinner should print a
	// static step line immediately and Stop/Success should work without
	// animation.
	var buf bytes.Buffer
	u := NewUI(&buf)
	s := u.StartSpinner("loading...")
	s.Success("done!")
	out := buf.String()
	if !strings.Contains(out, "loading...") {
		t.Error("Spinner should print step message on non-TTY")
	}
	if !strings.Contains(out, "done!") {
		t.Error("Spinner.Success should print final message")
	}
}

func TestSpinnerDoubleStop(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	s := u.StartSpinner("work")
	s.Stop()
	s.Stop() // should not panic
}

func TestStripControl(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name  string
		input string
		want  string
	}{
		{"plain text", "hello", "hello"},
		{"bell char", "hello\x07world", "helloworld"},
		{"backspace", "hello\x08world", "helloworld"},
		{"carriage return", "hello\rworld", "helloworld"},
		{"ESC byte", "hello\x1b[2Jworld", "hello[2Jworld"},
		{"null byte", "hello\x00world", "helloworld"},
		{"preserves tab", "hello\tworld", "hello\tworld"},
		{"preserves newline", "hello\nworld", "hello\nworld"},
		{"multiple controls", "\x01\x02\x03ok", "ok"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := stripControl(tt.input)
			if got != tt.want {
				t.Errorf("stripControl(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestStripControlStrict(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name  string
		input string
		want  string
	}{
		{"plain text", "hello", "hello"},
		{"strips tab", "hello\tworld", "helloworld"},
		{"strips newline", "hello\nworld", "helloworld"},
		{"strips ESC", "hello\x1b[32mworld", "hello[32mworld"},
		{"strips all controls", "\x00\x01\t\n\x1bok", "ok"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := stripControlStrict(tt.input)
			if got != tt.want {
				t.Errorf("stripControlStrict(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

// TestSpinnerConcurrentStop exercises the Spinner's concurrency safety by
// calling Stop from multiple goroutines concurrently. The sync.Once in
// waitAndClear should prevent any panics from double-closing the done channel.
// Note: full TTY spinner animation testing requires a pseudo-terminal which
// is not available in unit tests; CI uses the race detector to catch races.
func TestSpinnerConcurrentStop(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	s := u.StartSpinner("concurrent work")

	done := make(chan struct{})
	for range 5 {
		go func() {
			s.Stop()
			done <- struct{}{}
		}()
	}
	for range 5 {
		<-done
	}
}

func TestLiveBoxNonTTY(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	lb := u.NewLiveBox("Pull Images", []string{"backend", "web", "sandbox"})

	// Non-TTY: NewLiveBox prints a step line with the title.
	out := buf.String()
	if !strings.Contains(out, "Pull Images") {
		t.Error("NewLiveBox should print title on non-TTY")
	}

	// Updating lines should print status lines.
	lb.UpdateLine(0, IconSuccess)
	out = buf.String()
	if !strings.Contains(out, "backend") {
		t.Error("UpdateLine should print label on non-TTY")
	}

	lb.UpdateLine(1, IconError)
	out = buf.String()
	if !strings.Contains(out, "web") {
		t.Error("UpdateLine error should print label on non-TTY")
	}

	lb.UpdateLine(2, IconSuccess)
	lb.Finish()
}

func TestLiveBoxFinishIdempotent(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	lb := u.NewLiveBox("Test", []string{"a"})
	lb.UpdateLine(0, IconSuccess)
	lb.Finish()
	lb.Finish() // should not panic
}

func TestLiveBoxOutOfBounds(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	lb := u.NewLiveBox("Test", []string{"a"})
	lb.UpdateLine(-1, IconSuccess) // should not panic
	lb.UpdateLine(5, IconSuccess)  // should not panic
	lb.UpdateLine(0, IconSuccess)
	lb.Finish()
}

func TestLiveBoxBuildLines(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	lb := u.NewLiveBox("Test", []string{"svc1", "svc2"})

	lb.mu.Lock()
	// Before any updates, all lines should show spinner frame.
	lines := lb.buildLines(0)
	lb.mu.Unlock()

	if len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %d", len(lines))
	}
	if !strings.Contains(lines[0], "svc1") {
		t.Error("line 0 missing label svc1")
	}
	if !strings.Contains(lines[0], spinnerFrames[0]) {
		t.Error("line 0 should contain spinner frame")
	}

	// After marking finished, line should show status.
	lb.UpdateLine(0, IconSuccess)
	lb.mu.Lock()
	lines = lb.buildLines(0)
	lb.mu.Unlock()
	if !strings.Contains(lines[0], IconSuccess) {
		t.Error("finished line should show success icon")
	}
	if !strings.Contains(lines[1], spinnerFrames[0]) {
		t.Error("unfinished line should still show spinner")
	}

	lb.UpdateLine(1, IconSuccess)
	lb.Finish()
}

func TestInlineKVOddArgs(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)
	// Odd number of args: last key should be dropped silently.
	u.InlineKV("Docker", "29.2.1", "Orphan")
	out := buf.String()
	if !strings.Contains(out, "Docker") || !strings.Contains(out, "29.2.1") {
		t.Error("InlineKV should render complete pairs")
	}
	if strings.Contains(out, "Orphan") {
		t.Error("InlineKV should drop unpaired trailing key")
	}
}

// --- Output mode tests ---

func TestQuietModeSuppresses(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{Quiet: true})

	u.Logo("v1.0.0")
	u.Step("working")
	u.Blank()
	u.Section("Header")
	u.KeyValue("key", "val")
	u.Divider()
	u.InlineKV("a", "b")
	u.HintNextStep("do this")
	u.HintTip(t.Name() + " try that")
	u.HintGuidance("guidance")
	u.HintError("error hint")
	u.Link("label", "http://example.com")
	u.Box("Title", []string{"line"})
	u.Table([]string{"H"}, [][]string{{"r"}})

	if buf.Len() != 0 {
		t.Errorf("quiet mode should suppress all non-essential output, got: %q", buf.String())
	}

	// Success and Warn are suppressed in quiet mode (errors only).
	u.Success("ok")
	if buf.Len() != 0 {
		t.Errorf("quiet mode should suppress Success, got: %q", buf.String())
	}

	u.Warn("caution")
	if buf.Len() != 0 {
		t.Errorf("quiet mode should suppress Warn, got: %q", buf.String())
	}

	// Error and Plain should still print.
	u.Error("fail")
	if !strings.Contains(buf.String(), "fail") {
		t.Error("quiet mode should still print Error")
	}
	buf.Reset()

	u.Plain("raw")
	if !strings.Contains(buf.String(), "raw") {
		t.Error("quiet mode should still print Plain")
	}
}

func TestPlainModeASCIIIcons(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{Plain: true})

	u.Success("done")
	u.Error("oops")
	u.Warn("hmm")
	u.Step("loading")

	out := buf.String()
	if !strings.Contains(out, PlainIconSuccess) {
		t.Errorf("plain mode should use %q for success, got: %s", PlainIconSuccess, out)
	}
	if !strings.Contains(out, PlainIconError) {
		t.Errorf("plain mode should use %q for error, got: %s", PlainIconError, out)
	}
	if !strings.Contains(out, PlainIconWarning) {
		t.Errorf("plain mode should use %q for warning, got: %s", PlainIconWarning, out)
	}
	if !strings.Contains(out, PlainIconInProgress) {
		t.Errorf("plain mode should use %q for step, got: %s", PlainIconInProgress, out)
	}

	// Should NOT contain Unicode icons.
	for _, icon := range []string{IconSuccess, IconError, IconInProgress, IconWarning} {
		if strings.Contains(out, icon) {
			t.Errorf("plain mode should not contain Unicode icon %q", icon)
		}
	}
}

func TestPlainModeBox(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{Plain: true})
	u.Box("Title", []string{"content"})
	out := buf.String()

	// Should use ASCII box chars.
	if !strings.Contains(out, "+") {
		t.Error("plain box should use + for corners")
	}
	if !strings.Contains(out, "|") {
		t.Error("plain box should use | for vertical borders")
	}
	// Should NOT contain Unicode box-drawing.
	for _, ch := range []string{"\u250c", "\u2510", "\u2514", "\u2518", "\u2502"} {
		if strings.Contains(out, ch) {
			t.Errorf("plain box should not contain Unicode box char %q", ch)
		}
	}
}

func TestPlainModeDivider(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{Plain: true})
	u.Divider()
	out := buf.String()

	if !strings.Contains(out, "----") {
		t.Error("plain divider should use dashes")
	}
	if strings.Contains(out, "\u2500") {
		t.Error("plain divider should not contain Unicode horizontal line")
	}
}

func TestPlainModeTable(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{Plain: true})
	u.Table([]string{"NAME", "VALUE"}, [][]string{{"foo", "bar"}})
	out := buf.String()

	if !strings.Contains(out, "NAME") || !strings.Contains(out, "foo") {
		t.Error("plain table should contain data")
	}
	if strings.Contains(out, "\u2500") {
		t.Error("plain table should not contain Unicode separator")
	}
}

func TestPlainModeLogo(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{Plain: true})
	u.Logo("v1.0.0")
	out := buf.String()

	if !strings.Contains(out, "SynthOrg") {
		t.Error("plain logo should contain 'SynthOrg'")
	}
	if !strings.Contains(out, "v1.0.0") {
		t.Error("plain logo should contain version")
	}
	// Should NOT contain Unicode box-drawing from the fancy logo.
	if strings.Contains(out, "\u2554") {
		t.Error("plain logo should not contain Unicode logo art")
	}
}

func TestPlainModeIconAccessors(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{Plain: true})

	if u.SuccessIcon() != PlainIconSuccess {
		t.Errorf("plain SuccessIcon = %q, want %q", u.SuccessIcon(), PlainIconSuccess)
	}
	if u.ErrorIcon() != PlainIconError {
		t.Errorf("plain ErrorIcon = %q, want %q", u.ErrorIcon(), PlainIconError)
	}
	if u.WarnIcon() != PlainIconWarning {
		t.Errorf("plain WarnIcon = %q, want %q", u.WarnIcon(), PlainIconWarning)
	}
}

func TestHintCategories(t *testing.T) {
	t.Parallel()

	t.Run("auto mode", func(t *testing.T) {
		t.Parallel()
		var buf bytes.Buffer
		u := NewUIWithOptions(&buf, Options{Hints: "auto"})

		u.HintError("error hint")
		if !strings.Contains(buf.String(), "error hint") {
			t.Error("auto mode should show HintError")
		}
		buf.Reset()

		u.HintNextStep("next step")
		if !strings.Contains(buf.String(), "next step") {
			t.Error("auto mode should show HintNextStep")
		}
		buf.Reset()

		tip1 := t.Name() + " try this"
		u.HintTip(tip1)
		if !strings.Contains(buf.String(), tip1) {
			t.Error("auto mode should show HintTip first time")
		}
		buf.Reset()

		u.HintTip(tip1) // same message again
		if buf.Len() != 0 {
			t.Error("auto mode should suppress duplicate HintTip")
		}

		tip2 := t.Name() + " different tip"
		u.HintTip(tip2) // different message
		if !strings.Contains(buf.String(), tip2) {
			t.Error("auto mode should show new HintTip")
		}
		buf.Reset()

		u.HintGuidance("guidance")
		if buf.Len() != 0 {
			t.Error("auto mode should suppress HintGuidance")
		}
	})

	t.Run("always mode", func(t *testing.T) {
		t.Parallel()
		var buf bytes.Buffer
		u := NewUIWithOptions(&buf, Options{Hints: "always"})

		u.HintGuidance("guidance")
		if !strings.Contains(buf.String(), "guidance") {
			t.Error("always mode should show HintGuidance")
		}
	})

	t.Run("never mode", func(t *testing.T) {
		t.Parallel()
		var buf bytes.Buffer
		u := NewUIWithOptions(&buf, Options{Hints: "never"})

		u.HintError("error")
		u.HintNextStep("next")
		neverTip := t.Name() + " tip-never"
		neverGuide := t.Name() + " guide-never"
		u.HintTip(neverTip)
		u.HintGuidance(neverGuide)
		// HintError and HintNextStep always show unless quiet.
		// HintTip and HintGuidance are suppressed in never mode.
		out := buf.String()
		if !strings.Contains(out, "error") {
			t.Error("never mode should still show HintError")
		}
		if !strings.Contains(out, "next") {
			t.Error("never mode should still show HintNextStep")
		}
		if strings.Contains(out, neverTip) {
			t.Error("never mode should suppress HintTip")
		}
		if strings.Contains(out, neverGuide) {
			t.Error("never mode should suppress HintGuidance")
		}
	})
}

// StepAlways is the counterpart of WarnAlways: it exists for the step whose
// whole value is surviving the flag an unattended run sets, so a test that
// only proved it prints normally would prove nothing.
func TestStepAlways_survivesQuietButNotJSON(t *testing.T) {
	t.Parallel()

	var quiet bytes.Buffer
	NewUIWithOptions(&quiet, Options{Quiet: true}).StepAlways("installed without confirmation")
	if !strings.Contains(quiet.String(), "installed without confirmation") {
		t.Errorf("StepAlways must survive --quiet, got %q", quiet.String())
	}

	var jsonMode bytes.Buffer
	NewUIWithOptions(&jsonMode, Options{JSON: true}).StepAlways("installed without confirmation")
	if jsonMode.Len() != 0 {
		t.Errorf("StepAlways must stay out of JSON output, got %q", jsonMode.String())
	}
}

func TestSpinnerQuietMode(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{Quiet: true})
	s := u.StartSpinner("loading")
	if buf.Len() != 0 {
		t.Error("quiet spinner should produce no output on start")
	}
	s.Success("done")
	if buf.Len() != 0 {
		t.Errorf("quiet spinner Success should be suppressed (errors only), got: %q", buf.String())
	}
}

func TestSpinnerPlainMode(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{Plain: true})
	s := u.StartSpinner("loading")
	out := buf.String()
	if !strings.Contains(out, "loading") {
		t.Error("plain spinner should print step message")
	}
	if !strings.Contains(out, PlainIconInProgress) {
		t.Errorf("plain spinner should use %q icon", PlainIconInProgress)
	}
	s.Success("done")
}

func TestJSONOutput(t *testing.T) {
	t.Parallel()
	var buf bytes.Buffer
	u := NewUI(&buf)

	data := map[string]string{"version": "1.0.0", "commit": "abc123"}
	if err := u.JSONOutput(data); err != nil {
		t.Fatalf("JSONOutput error: %v", err)
	}
	out := buf.String()
	if !strings.Contains(out, `"version": "1.0.0"`) {
		t.Errorf("JSONOutput missing expected field, got: %s", out)
	}
	if !strings.Contains(out, `"commit": "abc123"`) {
		t.Errorf("JSONOutput missing expected field, got: %s", out)
	}
}
