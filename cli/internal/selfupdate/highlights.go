package selfupdate

import "strings"

// Marker and separator constants. These are stable contract surface from
// .github/workflows/release-cut.yml and .github/workflows/release-finalize.yml --
// the workflow always emits the markers on their own line at column 0, so
// substring matching is sufficient (no Markdown parser required).
const (
	highlightsStartMarker = "<!-- HIGHLIGHTS_START -->"
	highlightsEndMarker   = "<!-- HIGHLIGHTS_END -->"

	// installSeparator separates the commit-based changelog from the install
	// instructions / SBOM / verification region in finalised release bodies.
	// Always at column 0 on its own line.
	installSeparator = "\n---\n"
)

// ExtractHighlights returns the styled-block content between the
// HIGHLIGHTS_START and HIGHLIGHTS_END markers in a release body, with the
// markers, the "## Highlights" header, and the AI-attribution blockquote
// stripped (the renderer adds its own styled header + dimmed attribution
// to avoid double-rendering).
//
// Returns (content, true) on success; ("", false) when either marker is
// missing or the body is malformed.
func ExtractHighlights(body string) (string, bool) {
	body = normaliseLineEndings(body)
	_, afterStart, ok := strings.Cut(body, highlightsStartMarker)
	if !ok {
		return "", false
	}
	content, _, ok := strings.Cut(afterStart, highlightsEndMarker)
	if !ok {
		return "", false
	}
	content = stripHighlightsHeader(content)
	content = stripAttribution(content)
	return strings.TrimSpace(content), true
}

// ExtractCommits returns the commit-based changelog region of the release
// body. When the Highlights block is present and well-formed, returns the
// content between HIGHLIGHTS_END and the first installSeparator. When the
// Highlights block is missing, returns the content from the start of the
// body up to the first installSeparator. When the start marker is present
// but the end marker is missing (malformed), skips past the orphan start
// marker to the first version heading. Returns the whole body when no
// installSeparator is present.
func ExtractCommits(body string) string {
	body = normaliseLineEndings(body)

	if before, afterStart, ok := strings.Cut(body, highlightsStartMarker); ok {
		if _, afterEnd, ok2 := strings.Cut(afterStart, highlightsEndMarker); ok2 {
			body = afterEnd
		} else {
			body = stripOrphanStartMarker(before, highlightsStartMarker+afterStart)
		}
	}

	if commits, _, ok := strings.Cut(body, installSeparator); ok {
		body = commits
	}
	return strings.TrimSpace(body)
}

// normaliseLineEndings converts CRLF and lone CR to LF so marker / separator
// searches work identically on Windows-generated bodies. No-op on strings
// that contain no '\r'.
func normaliseLineEndings(s string) string {
	if !strings.ContainsRune(s, '\r') {
		return s
	}
	s = strings.ReplaceAll(s, "\r\n", "\n")
	return strings.ReplaceAll(s, "\r", "\n")
}

// stripHighlightsHeader removes a leading "## Highlights" line if present.
// release-cut.yml always emits this header right after the start marker; the
// renderer adds its own styled header.
func stripHighlightsHeader(content string) string {
	trimmed := strings.TrimLeft(content, "\n")
	if !strings.HasPrefix(trimmed, "## Highlights") {
		return content
	}
	if _, rest, ok := strings.Cut(trimmed, "\n"); ok {
		return rest
	}
	return ""
}

// stripAttribution removes the AI-attribution blockquote line at the top of
// the highlights content. The renderer adds its own dimmed attribution.
func stripAttribution(content string) string {
	trimmed := strings.TrimLeft(content, "\n")
	if !strings.HasPrefix(trimmed, "> _AI-generated summary") {
		return content
	}
	if _, rest, ok := strings.Cut(trimmed, "\n"); ok {
		return rest
	}
	return ""
}

// stripOrphanStartMarker handles a malformed body with a start marker but
// no end marker. Drops the start marker plus everything up to the first
// release-please version heading ("\n## [") so the caller still sees the
// commit log instead of half-rendered highlights noise. before is the body
// content prior to the start marker; rest is from the start marker onward.
func stripOrphanStartMarker(before, rest string) string {
	if _, after, ok := strings.Cut(rest, "\n## ["); ok {
		return before + "## [" + after
	}
	// No version heading after the marker: drop the marker line only.
	if _, afterLine, ok := strings.Cut(rest, "\n"); ok {
		return before + afterLine
	}
	return before
}
