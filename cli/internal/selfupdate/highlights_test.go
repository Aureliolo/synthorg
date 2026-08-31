package selfupdate

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// loadFixture reads testdata/bodies/<name> and returns the file contents.
func loadFixture(t *testing.T, name string) string {
	t.Helper()
	path := filepath.Join("testdata", "bodies", name)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("loadFixture(%q): %v", name, err)
	}
	return string(data)
}

func TestExtractHighlights(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name         string
		body         string // either inline or "fixture:<name>"
		wantOK       bool
		wantContains []string
		wantOmits    []string
	}{
		{
			// The pre-tagline shape: no tagline, three sections. The CLI
			// walks every release in (installed, target], which reaches back
			// past the point either changed, so this is a live input rather
			// than a historical artefact.
			name:   "with_markers",
			body:   "fixture:with_highlights.md",
			wantOK: true,
			wantContains: []string{
				"### What you'll notice",
				"### What's new",
				"### Under the hood",
				"Update walks every release between installed and target",
				"Bubbletea-based viewport for in-block scrolling",
			},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## Highlights",
				"AI-generated summary (model:",
				"## [0.7.3]",
				"### Features",
				"## CLI Installation",
				"## Verification",
			},
		},
		{
			// The shape a fresh release carries: attribution first, then the
			// tagline, then two sections, no "Under the hood". Kept as a
			// fixture beside the pre-tagline one so a change to either shape
			// has something to fail.
			name:   "tagline_and_two_sections",
			body:   "fixture:tagline_two_section.md",
			wantOK: true,
			wantContains: []string{
				"_Nineteen new gates shipped",
				"### What you'll notice",
				"### What's new",
				"Release digests are built from commit bodies",
			},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## Highlights",
				"AI-generated summary (model:",
				"### Under the hood",
				"## [0.9.0]",
				"## CLI Installation",
			},
		},
		{
			name:   "no_markers_legacy_release",
			body:   "fixture:no_highlights.md",
			wantOK: false,
		},
		{
			name:   "dev_release_no_markers",
			body:   "fixture:dev_release.md",
			wantOK: false,
		},
		{
			name:   "truncated_no_end_marker",
			body:   "fixture:truncated.md",
			wantOK: false,
		},
		{
			name:   "no_separator_below",
			body:   "fixture:no_separator.md",
			wantOK: true,
			wantContains: []string{
				"### What's new",
				"Single-bullet release",
			},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## Highlights",
				"AI-generated summary",
				"## [0.0.1]",
			},
		},
		{
			name:   "empty_body",
			body:   "",
			wantOK: false,
		},
		{
			name:   "crlf_line_endings",
			body:   "<!-- HIGHLIGHTS_START -->\r\n## Highlights\r\n\r\n> _AI-generated summary (model: `example-provider/example-capable-001`). Commit-based changelog below._\r\n\r\n### What's new\r\n\r\n- CRLF body should parse identically to LF.\r\n\r\n<!-- HIGHLIGHTS_END -->\r\n\r\n## [0.0.1] (2026-01-01)\r\n\r\n### Features\r\n* something\r\n",
			wantOK: true,
			wantContains: []string{
				"### What's new",
				"CRLF body should parse identically",
			},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## Highlights",
				"AI-generated summary",
				"## [0.0.1]",
			},
		},
		{
			name:         "markers_only_empty_content",
			body:         "<!-- HIGHLIGHTS_START -->\n<!-- HIGHLIGHTS_END -->\n\n## [0.0.1]\n",
			wantOK:       true,
			wantContains: []string{},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## [0.0.1]",
			},
		},
		{
			// A tagline leads the block in this shape, so the attribution
			// blockquote sits somewhere in the middle rather than directly
			// under the header. Already-published release bodies are
			// immutable and the walk spans (installed, target], so this
			// order is a live input rather than a historical artefact. The
			// tagline is the hook and must survive; the attribution must
			// still go, since the walk has no room to explain what it names.
			name: "tagline_precedes_attribution",
			body: "<!-- HIGHLIGHTS_START -->\n## Highlights\n\n" +
				"_Nineteen new gates, because the last nineteen were not enough._\n\n" +
				"> _AI-generated summary (model: `example-capable-001` via Example). " +
				"Commit-based changelog below._\n\n" +
				"### What's new\n\n- A bullet.\n\n<!-- HIGHLIGHTS_END -->\n\n## [0.0.1]\n",
			wantOK: true,
			wantContains: []string{
				"_Nineteen new gates, because the last nineteen were not enough._",
				"### What's new",
				"- A bullet.",
			},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## Highlights",
				"AI-generated summary (model:",
				"## [0.0.1]",
			},
		},
		{
			// Attribution leads the block so a reader is told the summary is
			// AI-generated before reading the tagline.
			name: "attribution_precedes_tagline",
			body: "<!-- HIGHLIGHTS_START -->\n## Highlights\n\n" +
				"> _AI-generated summary (model: `example-capable-001` via Example). " +
				"Commit-based changelog below._\n\n" +
				"_Nineteen new gates, because the last nineteen were not enough._\n\n" +
				"### What's new\n\n- A bullet.\n\n<!-- HIGHLIGHTS_END -->\n\n## [0.0.1]\n",
			wantOK: true,
			wantContains: []string{
				"_Nineteen new gates, because the last nineteen were not enough._",
				"### What's new",
				"- A bullet.",
			},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## Highlights",
				"AI-generated summary (model:",
				"## [0.0.1]",
			},
		},
		{
			// stripAttribution scans every line rather than one position, so a
			// body carrying the blockquote twice (a re-run that appended
			// instead of replacing) must come back with neither copy. Matching
			// one and stopping would ship the second into the walk.
			name: "repeated_attribution_all_removed",
			body: "<!-- HIGHLIGHTS_START -->\n## Highlights\n\n" +
				"> _AI-generated summary (model: `example-capable-001` via Example)._\n\n" +
				"### What's new\n\n- A bullet.\n\n" +
				"> _AI-generated summary (model: `example-capable-001` via Example)._\n\n" +
				"<!-- HIGHLIGHTS_END -->\n\n## [0.0.1]\n",
			wantOK: true,
			wantContains: []string{
				"### What's new",
				"- A bullet.",
			},
			wantOmits: []string{
				"AI-generated summary",
			},
		},
		{
			// The blockquote is indented rather than at column 0. Matching on
			// the trimmed line is what covers it; anchoring on the raw prefix
			// would leave the line in place.
			name: "indented_attribution_removed",
			body: "<!-- HIGHLIGHTS_START -->\n## Highlights\n\n" +
				"   > _AI-generated summary (model: `example-capable-001`)._\n\n" +
				"### What's new\n\n- A bullet.\n\n<!-- HIGHLIGHTS_END -->\n",
			wantOK: true,
			wantContains: []string{
				"### What's new",
			},
			wantOmits: []string{
				"AI-generated summary",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			body := tt.body
			if name, found := strings.CutPrefix(body, "fixture:"); found {
				body = loadFixture(t, name)
			}

			got, ok := ExtractHighlights(body)
			if ok != tt.wantOK {
				t.Fatalf("ExtractHighlights ok = %v, want %v\n--- got content ---\n%s", ok, tt.wantOK, got)
			}
			for _, want := range tt.wantContains {
				if !strings.Contains(got, want) {
					t.Errorf("ExtractHighlights output missing %q\n--- got ---\n%s", want, got)
				}
			}
			for _, omit := range tt.wantOmits {
				if strings.Contains(got, omit) {
					t.Errorf("ExtractHighlights output should not contain %q\n--- got ---\n%s", omit, got)
				}
			}
		})
	}
}

// TestStripAttribution exercises the private function directly rather than
// through ExtractHighlights, whose own strings.TrimSpace call masks the gap-
// closing behaviour whenever the attribution happens to sit at the very
// start of the content (the shape a fresh release currently produces).
// stripAttribution's mid-block dedup only shows up when there is real,
// non-blank content on both sides of the removed line.
func TestStripAttribution(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		in   string
		want string
	}{
		{
			// Dropping the attribution line alone would leave the blank
			// lines either side of it back to back, costing the walk a row
			// of a viewport that only holds fourteen.
			name: "closes_the_gap_it_leaves_mid_block",
			in: "Line before.\n\n" +
				"> _AI-generated summary (model: `example-capable-001`)._\n\n" +
				"Line after.",
			want: "Line before.\n\nLine after.",
		},
		{
			name: "no_attribution_present_is_a_no_op",
			in:   "Line one.\n\nLine two.",
			want: "Line one.\n\nLine two.",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := stripAttribution(tt.in); got != tt.want {
				t.Errorf("stripAttribution(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

func TestExtractCommits(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name         string
		body         string
		wantContains []string
		wantOmits    []string
		wantEmpty    bool
	}{
		{
			name: "with_highlights_strips_block",
			body: "fixture:with_highlights.md",
			wantContains: []string{
				"### Features",
				"per-version Highlights on upgrade walk",
				"### Bug Fixes",
				"harden GitHub API pagination cap",
			},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## Highlights",
				"AI-generated summary",
				"## CLI Installation",
				"## Verification",
				"### CLI Checksums",
			},
		},
		{
			name: "no_highlights_returns_changelog_only",
			body: "fixture:no_highlights.md",
			wantContains: []string{
				"## [0.7.1]",
				"### Features",
				"persist currency on every cost row",
				"### Bug Fixes",
				"### Maintenance",
				"Lock file maintenance",
			},
			wantOmits: []string{
				"## CLI Installation",
				"## Verification",
				"### CLI Checksums",
				"sha256",
			},
		},
		{
			name: "dev_release_short_body",
			body: "fixture:dev_release.md",
			wantContains: []string{
				"Dev build #5",
				"5a4e672",
			},
			wantOmits: []string{
				"## CLI Installation",
				"docker pull",
			},
		},
		{
			name: "truncated_no_end_marker",
			body: "fixture:truncated.md",
			// When end marker is missing, ExtractCommits should still return
			// the commit-style content above the install separator, ignoring
			// the orphan start marker and any half-rendered Highlights
			// content above the changelog version heading.
			wantContains: []string{
				"### Features",
				"something useful",
			},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## Highlights",
				"## CLI Installation",
			},
		},
		{
			name: "no_separator_returns_remainder",
			body: "fixture:no_separator.md",
			wantContains: []string{
				"## [0.0.1]",
				"### Features",
				"tiny initial release",
			},
			wantOmits: []string{
				"<!-- HIGHLIGHTS_START -->",
				"<!-- HIGHLIGHTS_END -->",
				"## Highlights",
			},
		},
		{
			name:      "empty_body",
			body:      "",
			wantEmpty: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			body := tt.body
			if name, found := strings.CutPrefix(body, "fixture:"); found {
				body = loadFixture(t, name)
			}

			got := ExtractCommits(body)
			if tt.wantEmpty {
				if strings.TrimSpace(got) != "" {
					t.Errorf("ExtractCommits expected empty, got %q", got)
				}
				return
			}
			for _, want := range tt.wantContains {
				if !strings.Contains(got, want) {
					t.Errorf("ExtractCommits output missing %q\n--- got ---\n%s", want, got)
				}
			}
			for _, omit := range tt.wantOmits {
				if strings.Contains(got, omit) {
					t.Errorf("ExtractCommits output should not contain %q\n--- got ---\n%s", omit, got)
				}
			}
		})
	}
}

// FuzzExtractHighlights ensures arbitrary byte sequences never panic the
// parser. A malformed release body should always return cleanly with ok=false
// or an empty string -- never crash.
func FuzzExtractHighlights(f *testing.F) {
	f.Add("")
	f.Add("<!-- HIGHLIGHTS_START -->")
	f.Add("<!-- HIGHLIGHTS_START -->\n")
	f.Add("<!-- HIGHLIGHTS_END -->")
	f.Add("<!-- HIGHLIGHTS_START --><!-- HIGHLIGHTS_END -->")
	f.Add("\r\n\r\n<!-- HIGHLIGHTS_START -->\r\n")
	for _, name := range []string{"with_highlights.md", "tagline_two_section.md", "no_highlights.md", "dev_release.md", "truncated.md", "no_separator.md"} {
		data, err := os.ReadFile(filepath.Join("testdata", "bodies", name))
		if err == nil {
			f.Add(string(data))
		}
	}
	f.Fuzz(func(_ *testing.T, body string) {
		_, _ = ExtractHighlights(body)
		_ = ExtractCommits(body)
	})
}
