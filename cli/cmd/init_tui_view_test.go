package cmd

import (
	"strings"
	"testing"

	"charm.land/lipgloss/v2"
)

func TestContentBoxWidth(t *testing.T) {
	short := []string{"abc", "defg"}
	wide := []string{"abc", strings.Repeat("x", 80)}

	cases := []struct {
		name          string
		content       []string
		terminalWidth int
		want          int
	}{
		{"shorter than floor stays at floor", short, 0, boxW},
		{"wider than floor grows", wide, 0, 80},
		{"clamped to terminal width minus border overhead", wide, 60, 60 - boxBorderOverhead},
		{
			"narrow terminal shrinks below boxW floor",
			wide, boxW + boxBorderOverhead - 2, boxW + boxBorderOverhead - 2 - boxBorderOverhead,
		},
		{"unset terminal width skips clamp", wide, 0, 80},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := contentBoxWidth(tc.content, tc.terminalWidth)
			if got != tc.want {
				t.Errorf("contentBoxWidth(%v, %d) = %d, want %d",
					tc.content, tc.terminalWidth, got, tc.want)
			}
		})
	}
}

// TestRenderBox_truncatesOverflowingLines is the regression guard for the
// Gemini review on PR #1626: when contentBoxWidth's terminal-width clamp
// fires, content lines that exceed the clamped w MUST be truncated before
// brow renders them; otherwise the right border overflows past the
// top/bottom borders and the box appears broken (the original bug this
// helper exists to prevent).
func TestRenderBox_truncatesOverflowingLines(t *testing.T) {
	w := 20
	// 40-char line, far wider than w. ansi.Truncate is grapheme-aware so
	// the produced row should be visually exactly w + boxBorderOverhead
	// columns wide -- same as boxTop / boxBottom.
	long := strings.Repeat("x", 40)
	out := renderBox("T", []string{long}, w)
	if len(out) != 3 {
		t.Fatalf("expected 3 rows (top, content, bottom), got %d", len(out))
	}
	topW := lipgloss.Width(out[0])
	rowW := lipgloss.Width(out[1])
	bottomW := lipgloss.Width(out[2])
	if rowW != topW || rowW != bottomW {
		t.Errorf("row widths misaligned: top=%d row=%d bottom=%d (w=%d)",
			topW, rowW, bottomW, w)
	}
}

// TestRenderBox_preservesLinesShorterThanW guards the no-op path: short
// content must not be padded *into* a truncation, and styled lines must
// keep their ANSI escapes intact.
func TestRenderBox_preservesLinesShorterThanW(t *testing.T) {
	styled := sBrand.Render("hi")
	out := renderBox("T", []string{styled}, 40)
	if !strings.Contains(out[1], "hi") {
		t.Errorf("styled content was lost in renderBox: %q", out[1])
	}
}
