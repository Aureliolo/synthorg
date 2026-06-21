package ui

import (
	"bytes"
	"strings"
	"testing"
	"time"

	"charm.land/lipgloss/v2"
)

func TestFormatElapsed(t *testing.T) {
	t.Parallel()

	cases := []struct {
		d    time.Duration
		want string
	}{
		{0, "0m00s"},
		{42 * time.Second, "0m42s"},
		{90 * time.Second, "1m30s"},
		{6*time.Minute + 12*time.Second, "6m12s"},
		{-5 * time.Second, "0m00s"},
	}
	for _, c := range cases {
		if got := formatElapsed(c.d); got != c.want {
			t.Errorf("formatElapsed(%v) = %q, want %q", c.d, got, c.want)
		}
	}
}

func TestTruncateToWidth(t *testing.T) {
	t.Parallel()

	cases := []struct {
		s    string
		w    int
		want string
	}{
		{"abcdef", 10, "abcdef"},
		{"abcdef", 6, "abcdef"},
		{"abcdef", 3, "abc"},
		{"abcdef", 0, "abcdef"}, // non-positive width: no truncation
		{"abcdef", -1, "abcdef"},
	}
	for _, c := range cases {
		if got := truncateToWidth(c.s, c.w); got != c.want {
			t.Errorf("truncateToWidth(%q, %d) = %q, want %q", c.s, c.w, got, c.want)
		}
	}
}

// TestProgressStatusRendersProgressAndElapsed verifies a progress box line
// shows the spinner, the in-progress text, and a deterministic elapsed time,
// and never overflows the reserved status width.
func TestProgressStatusRendersProgressAndElapsed(t *testing.T) {
	t.Parallel()

	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{NoColor: true})
	lb := u.NewLiveBoxWithProgress("Pull Images", []string{"fine-tune-gpu", "backend"})

	// Deterministic elapsed: pin the clock 90s after each line's start.
	base := time.Unix(1_700_000_000, 0)
	for i := range lb.lines {
		lb.lines[i].started = base
	}
	lb.now = func() time.Time { return base.Add(90 * time.Second) }

	lb.lines[0].progress = "downloading 1.9 GB, 4/9 layers"

	lines := lb.buildLines(0)

	if !strings.Contains(lines[0], "downloading 1.9 GB, 4/9 layers") {
		t.Errorf("line 0 missing progress text: %q", lines[0])
	}
	if !strings.Contains(lines[0], "1m30s") {
		t.Errorf("line 0 missing elapsed time: %q", lines[0])
	}
	// A line with no progress text still shows the elapsed time.
	if !strings.Contains(lines[1], "1m30s") {
		t.Errorf("line 1 missing elapsed time: %q", lines[1])
	}
	// Content must never exceed the box inner width.
	for i, l := range lines {
		if w := lipgloss.Width(l); w > lb.innerW {
			t.Errorf("line %d width %d exceeds innerW %d: %q", i, w, lb.innerW, l)
		}
	}
}

// TestProgressStatusTruncatesLongProgress ensures an over-long live status is
// trimmed to the reserved width so the box border stays aligned.
func TestProgressStatusTruncatesLongProgress(t *testing.T) {
	t.Parallel()

	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{NoColor: true})
	lb := u.NewLiveBoxWithProgress("Pull Images", []string{"svc"})
	lb.lines[0].progress = strings.Repeat("x", 500)

	lines := lb.buildLines(0)
	if w := lipgloss.Width(lines[0]); w > lb.innerW {
		t.Errorf("oversized progress not truncated: width %d > innerW %d", w, lb.innerW)
	}
}

// TestUpdateProgressIgnoresFinishedAndOutOfRange confirms UpdateProgress is a
// no-op for finished and out-of-range lines.
func TestUpdateProgressIgnoresFinishedAndOutOfRange(t *testing.T) {
	t.Parallel()

	var buf bytes.Buffer
	u := NewUIWithOptions(&buf, Options{NoColor: true})
	lb := u.NewLiveBoxWithProgress("Pull Images", []string{"a"})

	lb.UpdateProgress(5, "ignored")  // out of range
	lb.UpdateProgress(-1, "ignored") // out of range
	if lb.lines[0].progress != "" {
		t.Errorf("out-of-range update leaked: %q", lb.lines[0].progress)
	}

	lb.lines[0].finished = true
	lb.UpdateProgress(0, "ignored")
	if lb.lines[0].progress != "" {
		t.Errorf("update on finished line was applied: %q", lb.lines[0].progress)
	}
}

// TestErrorRemainingMarksUnfinishedLines verifies ErrorRemaining flips every
// unfinished line to the error icon and, on a non-TTY writer, prints an error
// status line for each.
func TestErrorRemainingMarksUnfinishedLines(t *testing.T) {
	t.Parallel()

	var buf bytes.Buffer
	// Non-TTY (bytes.Buffer): finishes print plain status lines.
	u := NewUIWithOptions(&buf, Options{NoColor: true})
	lb := u.NewLiveBox("Verify", []string{"postgres", "nats"})

	lb.UpdateLine(0, IconSuccess) // one line already done
	buf.Reset()

	lb.ErrorRemaining()

	if !lb.lines[1].finished || lb.lines[1].status != IconError {
		t.Errorf("unfinished line not marked errored: %+v", lb.lines[1])
	}
	if lb.lines[0].status != IconSuccess {
		t.Errorf("already-finished line was overwritten: %+v", lb.lines[0])
	}
	// The newly errored line prints an error status line (label "nats").
	if !strings.Contains(buf.String(), "nats") {
		t.Errorf("ErrorRemaining did not print the errored line: %q", buf.String())
	}
}
