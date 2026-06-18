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
	tests := []struct {
		name         string
		body         string // either inline or "fixture:<name>"
		wantOK       bool
		wantContains []string
		wantOmits    []string
	}{
		{
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
			name:   "no_markers_pre_1555",
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
			body:   "<!-- HIGHLIGHTS_START -->\r\n## Highlights\r\n\r\n> _AI-generated summary (model: `example-provider/example-medium-001` via Mistral). Commit-based changelog below._\r\n\r\n### What's new\r\n\r\n- CRLF body should parse identically to LF.\r\n\r\n<!-- HIGHLIGHTS_END -->\r\n\r\n## [0.0.1] (2026-01-01)\r\n\r\n### Features\r\n* something\r\n",
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
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
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

func TestExtractCommits(t *testing.T) {
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
	for _, name := range []string{"with_highlights.md", "no_highlights.md", "dev_release.md", "truncated.md", "no_separator.md"} {
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
