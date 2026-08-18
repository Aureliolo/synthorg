package ui

import (
	"regexp"
	"strings"
	"unicode/utf8"

	"charm.land/lipgloss/v2"
)

// changelogStyle is a small palette built from the package-level color
// constants. Captured per-call so the same renderer respects opts.NoColor /
// opts.Plain consistently.
type changelogStyle struct {
	highlightHeader lipgloss.Style // ### What's new (sky blue, bold)
	commitHeader    lipgloss.Style // ### Features (indigo, bold)
	muted           lipgloss.Style // dim attribution / fallback note
	bullet          string         // "•" or "-"
	indent          string         // "  "
}

func newChangelogStyle(opts Options) changelogStyle {
	plain := opts.NoColor || opts.Plain
	bullet := "•"
	if opts.Plain {
		bullet = "-"
	}

	highlightHeader := lipgloss.NewStyle()
	commitHeader := lipgloss.NewStyle()
	muted := lipgloss.NewStyle()
	if !plain {
		highlightHeader = highlightHeader.Foreground(colorLabel).Bold(true)
		commitHeader = commitHeader.Foreground(colorBrand).Bold(true)
		muted = muted.Foreground(colorMuted)
	}
	return changelogStyle{
		highlightHeader: highlightHeader,
		commitHeader:    commitHeader,
		muted:           muted,
		bullet:          bullet,
		indent:          "  ",
	}
}

// markdownLinkRe matches `[label](url)` and is used to flatten links to plain
// text. Captures the label.
var markdownLinkRe = regexp.MustCompile(`\[([^\]]+)\]\(([^)]+)\)`)

// commitHashLinkRe matches the trailing commit-hash link Release Please emits:
// "([abc1234](https://github.com/.../commit/abc1234...))". Tightened to
// match exactly 7 (short) or 40 (full) hex chars -- the only forms git emits
// -- so the regex engine has no extra backtracking surface on malformed input.
var commitHashLinkRe = regexp.MustCompile(`\s*\(\[[0-9a-f]{7}(?:[0-9a-f]{33})?\]\([^)]+\)\)`)

// boldEmphasisRe matches `**text**` and captures the inner text. Used to
// strip Markdown bold from commit subjects in NoColor / Plain mode.
var boldEmphasisRe = regexp.MustCompile(`\*\*([^*]+)\*\*`)

// ansiEscapeRe matches CSI ("\x1b[...m" and friends) and OSC ("\x1b]...\x07")
// terminal escape sequences. Release bodies and commit messages are
// attacker-controllable surfaces; lipgloss does NOT strip embedded escapes
// from input strings, so a malicious tag or body could otherwise spoof
// terminal output.
var ansiEscapeRe = regexp.MustCompile(`\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)`)

// releaseHeadingRe matches the Release Please version-heading shape, e.g.
// "## [0.7.3](https://...) (2026-04-25)" or "## [0.7.3] (2026-04-25)".
// Only this exact shape is dropped from commit-view rendering -- arbitrary
// H2 sections in the release body (e.g. "## Migration notes") are kept.
var releaseHeadingRe = regexp.MustCompile(`^##\s+\[[^\]]+\](?:\([^)]+\))?(?:\s+\([^)]+\))?\s*$`)

// stripEscapes removes the CSI / OSC sequences ansiEscapeRe matches. It is
// only ever half the job: see sanitizeUntrusted.
func stripEscapes(s string) string {
	if !strings.ContainsRune(s, '\x1b') {
		return s
	}
	return ansiEscapeRe.ReplaceAllString(s, "")
}

// spoofingRanges is the isSpoofingRune vocabulary as inclusive codepoint
// spans, ascending and non-overlapping, which is what lets the lookup stop
// at the first span starting above r rather than reading the whole table.
var spoofingRanges = [...][2]rune{
	{0x00AD, 0x00AD},   // SOFT HYPHEN
	{0x061C, 0x061C},   // ARABIC LETTER MARK
	{0x180E, 0x180E},   // MONGOLIAN VOWEL SEPARATOR
	{0x200B, 0x200F},   // ZWSP, ZWNJ, ZWJ, LRM, RLM
	{0x202A, 0x202E},   // LRE, RLE, PDF, LRO, RLO
	{0x2060, 0x2064},   // WORD JOINER, invisible operators
	{0x2066, 0x2069},   // LRI, RLI, FSI, PDI
	{0xFEFF, 0xFEFF},   // zero-width no-break space / BOM
	{0xFFF9, 0xFFFB},   // interlinear annotation
	{0xE0000, 0xE007F}, // tag block
}

// isSpoofingRune reports whether r can visually reorder or hide neighbouring
// text without being a control character: the bidirectional overrides, marks
// and isolates behind Trojan-Source spoofing, the zero-width joiners and the
// invisible format runes, the interlinear annotation controls, and the tag
// block, which carries a whole hidden string past a reader one codepoint at
// a time. Git restricts none of them in a ref name, so they survive into the
// version string an operator reads immediately before consenting to an
// install. None of them carries meaning in a version label, a commit subject
// or a release body, so dropping the class outright costs nothing legible.
func isSpoofingRune(r rune) bool {
	// Every span sits above ASCII, so the printable majority of a release
	// body is answered without reaching the table at all.
	if r < spoofingRanges[0][0] {
		return false
	}
	for _, span := range spoofingRanges {
		if r < span[0] {
			return false
		}
		if r <= span[1] {
			return true
		}
	}
	return false
}

// isControlRune reports whether r acts on a terminal rather than printing in
// it. keepLayout spares the tab and newline a multi-line block is laid out
// with; a value that must occupy a single line keeps neither, so a hostile
// string cannot break out of the row it is rendered into.
func isControlRune(r rune, keepLayout bool) bool {
	if keepLayout && (r == '\t' || r == '\n') {
		return false
	}
	return r < 0x20 || r == 0x7F || (r >= 0x80 && r <= 0x9F)
}

// scrubDrops is the whole removal predicate: the control characters and the
// spoofing runes, behind an ASCII-printable fast exit because that is what
// almost every byte of a release body is.
func scrubDrops(r rune, keepLayout bool) bool {
	if r >= 0x20 && r < 0x7F {
		return false
	}
	return isControlRune(r, keepLayout) || isSpoofingRune(r)
}

// scrubDropsASCII is scrubDrops for a byte already known to be ASCII, which
// narrows the question to the C0 controls and DEL: no spoofing rune is
// encoded in one byte, so the table lookup cannot apply. Splitting it out
// keeps the single-byte case, the one that decides almost every byte of a
// release body, down to two comparisons.
func scrubDropsASCII(c byte, keepLayout bool) bool {
	if c >= 0x20 && c != 0x7F {
		return false
	}
	return !keepLayout || (c != '\t' && c != '\n')
}

// scrubIndex returns the index of the first rune scrubUntrusted has to drop
// or replace, or -1 when s is already clean.
//
// The scan is byte-oriented and decodes only at or above utf8.RuneSelf. That
// is the whole performance story here: a release body is almost entirely
// printable ASCII, the walk scrubs one on every "synthorg update", and
// decoding every rune to ask a per-class predicate about it measured 2.15ns
// per byte against the 0.01ns the escape strip costs for the same string.
// Settling the ASCII majority in two comparisons keeps the sweep off the
// render path's critical cost.
func scrubIndex(s string, keepLayout bool) int {
	for i := 0; i < len(s); {
		c := s[i]
		// Printable ASCII first and on its own: it is almost every byte of
		// a release body, and putting any other test ahead of it puts that
		// test on every byte too.
		if c >= 0x20 && c < 0x7F {
			i++
			continue
		}
		if c < utf8.RuneSelf {
			if scrubDropsASCII(c, keepLayout) {
				return i
			}
			i++
			continue
		}
		r, size := utf8.DecodeRuneInString(s[i:])
		// RuneError covers two cases that both have to stop the scan: a
		// byte that is not valid UTF-8, which must not reach a terminal as
		// though it were text, and a genuine U+FFFD, which the rebuild
		// simply writes back.
		if r == utf8.RuneError || isControlRune(r, keepLayout) || isSpoofingRune(r) {
			return i
		}
		i += size
	}
	return -1
}

// scrubUntrusted removes every control and spoofing rune in a single pass.
//
// One pass rather than one per class: each class costs a rune decode and an
// indirect call per rune, and layering two of them behind the escape strip
// measured 76% slower on the release-body render than the escape strip
// alone. A clean body is returned as itself, so the common case neither
// copies nor allocates.
func scrubUntrusted(s string, keepLayout bool) string {
	cut := scrubIndex(s, keepLayout)
	if cut < 0 {
		return s
	}
	var b strings.Builder
	b.Grow(len(s))
	b.WriteString(s[:cut])
	for _, r := range s[cut:] {
		if scrubDrops(r, keepLayout) {
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

// sanitizeUntrusted scrubs remote-sourced multi-line text (a release body)
// of everything that can act on a terminal rather than print in it, keeping
// the newlines and tabs the layout needs. Escape sequences go first so their
// printable payload (the "[0;31m" of a CSI) leaves with them instead of
// surviving as text.
//
// The escape regex alone is not enough: it matches CSI and OSC, leaving bare
// CR, backspace, BEL and the non-CSI introducers (DCS, APC, and the two-byte
// RIS full-terminal-reset) free to overwrite or hide what the operator is
// reading. Git constrains none of those in a commit subject or an author
// name, and the static renderers write their output raw, where it lands in
// log files and captured CI output that somebody later cats back to a real
// terminal.
func sanitizeUntrusted(s string) string {
	return scrubUntrusted(stripEscapes(s), true)
}

// SanitizeUntrustedLine is sanitizeUntrusted for a value that must occupy a
// single line: a tag name, a version label, a commit subject. It drops the
// newlines and tabs the multi-line form keeps, so a hostile value cannot
// break out of the row it is rendered into.
//
// Exported because the same remote values are printed by the surrounding
// command (the release index above the changelog), not only by the
// renderers in this package.
func SanitizeUntrustedLine(s string) string {
	return scrubUntrusted(stripEscapes(s), false)
}

// RenderHighlights formats the styled-block content of a release Highlights
// section. body is expected to be the output of selfupdate.ExtractHighlights,
// already stripped of markers / "## Highlights" / attribution. The renderer
// also strips any embedded ANSI escape sequences from the input so a hostile
// release body cannot inject terminal styling / cursor moves into the walk.
func RenderHighlights(body string, opts Options) string {
	st := newChangelogStyle(opts)
	body = sanitizeUntrusted(strings.ReplaceAll(body, "\r\n", "\n"))
	lines := strings.Split(body, "\n")
	var out strings.Builder
	for _, line := range lines {
		out.WriteString(formatHighlightLine(line, st))
		out.WriteByte('\n')
	}
	return strings.TrimRight(out.String(), "\n")
}

// formatHighlightLine handles a single line in the highlights body. Returns
// the styled line (no trailing newline).
func formatHighlightLine(line string, st changelogStyle) string {
	trimmed := strings.TrimSpace(line)
	if trimmed == "" {
		return ""
	}
	if rest, ok := strings.CutPrefix(trimmed, "### "); ok {
		return st.highlightHeader.Render(strings.TrimSpace(rest))
	}
	if rest, ok := bulletPayload(trimmed); ok {
		return st.indent + st.bullet + " " + flattenInline(rest)
	}
	// Anything else: attribution blockquote (`> _...`) or stray text.
	if rest, ok := strings.CutPrefix(trimmed, ">"); ok {
		blockquote := strings.TrimSpace(strings.Trim(rest, "_"))
		return st.muted.Render(st.indent + blockquote)
	}
	return st.indent + flattenInline(trimmed)
}

// RenderCommits formats the commit-based changelog of a release. body is
// expected to be the output of selfupdate.ExtractCommits. ANSI escape
// sequences embedded in the input are stripped before rendering -- see
// sanitizeUntrusted for the threat model.
func RenderCommits(body string, opts Options) string {
	st := newChangelogStyle(opts)
	body = sanitizeUntrusted(strings.ReplaceAll(body, "\r\n", "\n"))
	lines := strings.Split(body, "\n")
	var out strings.Builder
	for _, line := range lines {
		rendered, keep := formatCommitLine(line, st)
		if !keep {
			continue
		}
		out.WriteString(rendered)
		out.WriteByte('\n')
	}
	return strings.TrimRight(out.String(), "\n")
}

// formatCommitLine returns the styled line and a keep flag. Lines like the
// release-please version heading ("## [0.7.3]...") are dropped because the
// walk renders its own version separator above the block.
func formatCommitLine(line string, st changelogStyle) (string, bool) {
	trimmed := strings.TrimSpace(line)
	if trimmed == "" {
		return "", false
	}
	// Drop release-please version heading. Other H2 sections (e.g.
	// "## Migration notes") survive so they render in the commit view.
	if releaseHeadingRe.MatchString(trimmed) {
		return "", false
	}
	if rest, ok := strings.CutPrefix(trimmed, "### "); ok {
		return st.commitHeader.Render(strings.TrimSpace(rest)), true
	}
	if rest, ok := bulletPayload(trimmed); ok {
		return st.indent + st.bullet + " " + flattenCommitInline(rest), true
	}
	return st.indent + flattenCommitInline(trimmed), true
}

// bulletPayload reports whether line starts with a Markdown bullet ("- " or
// "* ") and returns the payload after the bullet marker.
func bulletPayload(line string) (string, bool) {
	if rest, ok := strings.CutPrefix(line, "- "); ok {
		return rest, true
	}
	if rest, ok := strings.CutPrefix(line, "* "); ok {
		return rest, true
	}
	return "", false
}

// flattenInline rewrites Markdown links `[label](url)` to "label" and strips
// `**bold**` markers. Used for highlight bullets where we want a clean
// single-line read.
func flattenInline(s string) string {
	s = markdownLinkRe.ReplaceAllString(s, "$1")
	s = boldEmphasisRe.ReplaceAllString(s, "$1")
	return s
}

// flattenCommitInline rewrites a release-please commit line. Specifically:
//   - Drops the trailing `([sha7](url))` commit-hash link (noise in the walk).
//   - Rewrites issue/PR links `[#1234](url)` to "#1234".
//   - Drops the leading `**scope:**` Markdown bold markers (we keep the scope
//     text but render it readably without asterisks; lipgloss can't apply
//     mid-line bold here without parsing the full Markdown stream).
func flattenCommitInline(s string) string {
	s = commitHashLinkRe.ReplaceAllString(s, "")
	s = markdownLinkRe.ReplaceAllString(s, "$1")
	s = boldEmphasisRe.ReplaceAllString(s, "$1")
	return strings.TrimSpace(s)
}

// RenderFallbackNote returns the dimmed status line shown for versions that
// have no Highlights block (releases predating the AI-highlights feature, or
// when the operator has opted out of highlights).
func RenderFallbackNote(opts Options) string {
	st := newChangelogStyle(opts)
	const text = "No AI highlights -- showing commit log"
	return st.muted.Render(text)
}
