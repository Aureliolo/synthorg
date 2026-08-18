package ui

import (
	"strings"
	"testing"
)

// hasANSI reports whether s contains ANSI escape sequences.
func hasANSI(s string) bool {
	return strings.Contains(s, "\x1b[")
}

func TestRenderHighlights_basic(t *testing.T) {
	body := strings.TrimSpace(`
### What you'll notice

- Update walks every release between installed and target.
- Press c to toggle between highlights and commit-based view.

### What's new

- Per-version Highlights view in synthorg update.

### Under the hood

- Bubbletea-based viewport for in-block scrolling.
`)
	opts := Options{NoColor: true}
	got := RenderHighlights(body, opts)

	for _, want := range []string{
		"What you'll notice",
		"What's new",
		"Under the hood",
		"Update walks every release between installed and target.",
		"Press c to toggle between highlights",
		"Bubbletea-based viewport",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("missing %q\n--- got ---\n%s", want, got)
		}
	}
	// Bullets should be converted away from raw "- ".
	if strings.Contains(got, "\n- ") {
		t.Errorf("raw `- ` bullets should be replaced\n--- got ---\n%s", got)
	}
	// Section headers should be stripped of `### ` prefix.
	if strings.Contains(got, "### ") {
		t.Errorf("`### ` heading prefix should be stripped\n--- got ---\n%s", got)
	}
	// NoColor mode means no ANSI escape codes.
	if hasANSI(got) {
		t.Errorf("NoColor=true output should not contain ANSI codes")
	}
}

func TestRenderHighlights_colorMode(t *testing.T) {
	body := "### What's new\n\n- Bullet."
	opts := Options{}
	got := RenderHighlights(body, opts)
	if !hasANSI(got) {
		t.Errorf("color mode should emit ANSI escape codes\n--- got ---\n%s", got)
	}
	if !strings.Contains(got, "What's new") {
		t.Errorf("output should contain heading text")
	}
}

func TestRenderHighlights_plainMode(t *testing.T) {
	body := "### What's new\n\n- A bullet point."
	opts := Options{Plain: true}
	got := RenderHighlights(body, opts)
	if hasANSI(got) {
		t.Errorf("Plain mode should not emit ANSI codes")
	}
	// Plain mode uses ASCII bullet character or just text indent.
	if !strings.Contains(got, "A bullet point") {
		t.Errorf("plain output missing bullet text\n--- got ---\n%s", got)
	}
}

func TestRenderHighlights_stripsMarkdownLinks(t *testing.T) {
	body := "### What's new\n\n- See [the docs](https://example.com/docs) for details."
	opts := Options{NoColor: true}
	got := RenderHighlights(body, opts)
	if strings.Contains(got, "](https://") {
		t.Errorf("Markdown link syntax should be stripped or rewritten\n--- got ---\n%s", got)
	}
	if !strings.Contains(got, "the docs") {
		t.Errorf("link label should be preserved\n--- got ---\n%s", got)
	}
}

func TestRenderCommits_basic(t *testing.T) {
	body := strings.TrimSpace(`
## [0.7.3](https://github.com/Aureliolo/synthorg/compare/v0.7.2...v0.7.3) (2026-04-25)


### Features

* **cli:** per-version Highlights walk ([#1564](https://github.com/Aureliolo/synthorg/issues/1564)) ([abc1234](https://github.com/Aureliolo/synthorg/commit/abc1234abc1234abc1234abc1234abc1234abc12))
* **selfupdate:** harden pagination cap ([#1573](https://github.com/Aureliolo/synthorg/issues/1573)) ([fed9876](https://github.com/Aureliolo/synthorg/commit/fed9876fed9876fed9876fed9876fed9876fed98))


### Bug Fixes

* **web:** repair locale fallback ([#1577](https://github.com/Aureliolo/synthorg/issues/1577)) ([cba8765](https://github.com/Aureliolo/synthorg/commit/cba8765cba8765cba8765cba8765cba8765cba87))
`)
	opts := Options{NoColor: true}
	got := RenderCommits(body, opts)

	for _, want := range []string{
		"Features",
		"Bug Fixes",
		"per-version Highlights walk",
		"harden pagination cap",
		"repair locale fallback",
		"#1564",
		"#1573",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("missing %q\n--- got ---\n%s", want, got)
		}
	}
	for _, omit := range []string{
		"## [0.7.3]", // version heading should be stripped
		"abc1234abc1234",
		"fed9876fed9876",
		"cba8765cba8765",
		"](https://github.com/Aureliolo/synthorg/commit/",
		"### Features", // raw markdown heading prefix stripped
	} {
		if strings.Contains(got, omit) {
			t.Errorf("output should not contain %q\n--- got ---\n%s", omit, got)
		}
	}
}

func TestRenderCommits_stripsBoldEmphasis(t *testing.T) {
	body := "### Features\n\n* **cli:** add toggle ([#1500](https://github.com/x/y/issues/1500)) ([abc1234](https://github.com/x/y/commit/abc1234abcdef0123456789abcdef0123456789ab))"
	opts := Options{NoColor: true}
	got := RenderCommits(body, opts)
	// Conventional-commit scope **cli:** should render readably -- either keep
	// it bold (color mode only) or strip the asterisks (NoColor / Plain).
	if strings.Contains(got, "**cli:**") {
		t.Errorf("raw markdown bold (`**...**`) should be stripped\n--- got ---\n%s", got)
	}
	if !strings.Contains(got, "cli") {
		t.Errorf("scope text should be preserved\n--- got ---\n%s", got)
	}
}

func TestRenderCommits_emptyBody(t *testing.T) {
	got := RenderCommits("", Options{NoColor: true})
	if strings.TrimSpace(got) != "" {
		t.Errorf("empty body should render empty, got %q", got)
	}
}

func TestRenderCommits_noPRReference(t *testing.T) {
	body := "### Maintenance\n\n* internal-only refactor without PR reference"
	opts := Options{NoColor: true}
	got := RenderCommits(body, opts)
	if !strings.Contains(got, "internal-only refactor without PR reference") {
		t.Errorf("missing bullet text\n--- got ---\n%s", got)
	}
}

func TestRenderFallbackNote_textPresent(t *testing.T) {
	for _, opts := range []Options{
		{},
		{NoColor: true},
		{Plain: true},
	} {
		got := RenderFallbackNote(opts)
		if !strings.Contains(got, "No AI highlights") {
			t.Errorf("opts %+v: fallback should mention 'No AI highlights', got %q", opts, got)
		}
	}
}

func TestRenderFallbackNote_plainNoANSI(t *testing.T) {
	got := RenderFallbackNote(Options{Plain: true})
	if hasANSI(got) {
		t.Errorf("Plain mode should not emit ANSI codes, got %q", got)
	}
}

// Trojan-Source code points, written as UTF-8 byte escapes. The literal
// characters are invisible in a diff, staticcheck rejects them in a string
// literal (ST1018), and a literal BOM is not legal Go source at all.
const (
	rloRune  = "\xe2\x80\xae" // U+202E RIGHT-TO-LEFT OVERRIDE
	pdfRune  = "\xe2\x80\xac" // U+202C POP DIRECTIONAL FORMATTING
	lriRune  = "\xe2\x81\xa6" // U+2066 LEFT-TO-RIGHT ISOLATE
	pdiRune  = "\xe2\x81\xa9" // U+2069 POP DIRECTIONAL ISOLATE
	zwspRune = "\xe2\x80\x8b" // U+200B ZERO WIDTH SPACE
	zwjRune  = "\xe2\x80\x8d" // U+200D ZERO WIDTH JOINER
	bomRune  = "\xef\xbb\xbf" // U+FEFF BYTE ORDER MARK
	nelRune  = "\xc2\x90"     // U+0090 DEVICE CONTROL STRING (C1)
)

func TestSanitizeUntrusted(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want string
	}{
		{"plain_text_unchanged", "hello world", "hello world"},
		{"strip_csi_color", "\x1b[31mWARNING\x1b[0m: bad", "WARNING: bad"},
		{"strip_csi_bold", "\x1b[1mbold\x1b[22m text", "bold text"},
		{"strip_cursor_move", "before\x1b[2Aafter", "beforeafter"},
		{"strip_clear_screen", "header\x1b[2Jbody", "headerbody"},
		{"strip_osc_hyperlink", "click \x1b]8;;https://evil.com\x07here\x1b]8;;\x07!", "click here!"},
		{"empty_unchanged", "", ""},
		{"no_escape_unchanged", "no escapes here \\x1b not literal", "no escapes here \\x1b not literal"},
		// The CSI/OSC regex passes all of these; only the control-character
		// sweep behind it catches them, and each one can overwrite or hide
		// what the operator is reading.
		{"strip_bare_cr", "real subject\rspoofed subject", "real subjectspoofed subject"},
		{"strip_backspace", "safe\x08\x08\x08\x08evil", "safeevil"},
		{"strip_bel", "ring\x07ring", "ringring"},
		{"strip_vertical_tab_and_formfeed", "a\x0bb\x0cc", "abc"},
		{"strip_bare_ris_reset", "before\x1bcafter", "beforecafter"},
		{"strip_c1_control", "a" + nelRune + "b", "ab"},
		{"keep_newline_and_tab", "line one\nline\ttwo", "line one\nline\ttwo"},
		// Trojan-Source shapes: not control characters, so no control
		// sweep catches them, and git permits them in a ref name.
		{"strip_bidi_override", "v1.0.0" + rloRune + "gnitset" + pdfRune, "v1.0.0gnitset"},
		{"strip_bidi_isolate", "v1" + lriRune + ".0" + pdiRune + ".0", "v1.0.0"},
		{"strip_zero_width", "v1." + zwspRune + "0." + zwjRune + "0" + bomRune, "v1.0.0"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := sanitizeUntrusted(tt.in); got != tt.want {
				t.Errorf("sanitizeUntrusted(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

// SanitizeUntrustedLine is for values rendered into a single row, so unlike
// its multi-line sibling it must also drop the newlines and tabs that would
// let a hostile value break out of that row.
func TestSanitizeUntrustedLine_dropsLayoutBreakingWhitespace(t *testing.T) {
	got := SanitizeUntrustedLine("v1.0.0\nfake release line\tpadded")
	want := "v1.0.0fake release linepadded"
	if got != want {
		t.Errorf("SanitizeUntrustedLine = %q, want %q", got, want)
	}
	if strings.ContainsAny(got, "\n\t") {
		t.Errorf("result still carries layout-breaking whitespace: %q", got)
	}
}

func TestRenderHighlights_stripsEmbeddedANSI(t *testing.T) {
	body := "### What's new\n\n- \x1b[31mFAKE WARNING: rm -rf /\x1b[0m bullet content."
	got := RenderHighlights(body, Options{NoColor: true})
	if strings.Contains(got, "\x1b[31m") || strings.Contains(got, "\x1b[0m") {
		t.Errorf("RenderHighlights leaked attacker-controlled ANSI escape\n--- got ---\n%q", got)
	}
	if !strings.Contains(got, "FAKE WARNING") {
		t.Errorf("text content should be preserved\n--- got ---\n%s", got)
	}
}

func TestRenderCommits_stripsEmbeddedANSI(t *testing.T) {
	body := "### Features\n\n* \x1b[32mfake-success-banner\x1b[0m: ([#1](https://x/y/issues/1))"
	got := RenderCommits(body, Options{NoColor: true})
	if strings.Contains(got, "\x1b[32m") || strings.Contains(got, "\x1b[0m") {
		t.Errorf("RenderCommits leaked attacker-controlled ANSI escape\n--- got ---\n%q", got)
	}
	if !strings.Contains(got, "fake-success-banner") {
		t.Errorf("text content should be preserved\n--- got ---\n%s", got)
	}
}
