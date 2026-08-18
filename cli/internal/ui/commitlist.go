package ui

import (
	"fmt"
	"strings"

	"charm.land/lipgloss/v2"
	"github.com/mattn/go-runewidth"

	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
)

// commitListStyle holds the visual choices for RenderCommitList. Picked once
// at the top of each render to keep style construction out of the hot loop.
type commitListStyle struct {
	sha      lipgloss.Style
	meta     lipgloss.Style
	muted    lipgloss.Style
	ellipsis string
}

func newCommitListStyle(opts Options) commitListStyle {
	plain := opts.NoColor || opts.Plain
	ellipsis := "…"
	if opts.Plain {
		ellipsis = "..."
	}
	sha := lipgloss.NewStyle()
	meta := lipgloss.NewStyle()
	muted := lipgloss.NewStyle()
	if !plain {
		sha = sha.Foreground(colorBrand)
		meta = meta.Foreground(colorMuted)
		muted = muted.Foreground(colorMuted)
	}
	return commitListStyle{
		sha:      sha,
		meta:     meta,
		muted:    muted,
		ellipsis: ellipsis,
	}
}

const (
	shaShortLen   = 7  // "abc1234", git default
	minSubjectCol = 12 // never collapse the subject column below this
)

// RenderCommitList formats a CommitRange as a multi-line styled listing.
// Each commit renders on a single line: "<sha7>  <subject>  (@author, date)".
// Long subjects are truncated to fit the supplied terminal width, with a
// footer line if TotalCommits exceeds len(Commits) (compare API caps at 250).
func RenderCommitList(r selfupdate.CommitRange, width int, opts Options) string {
	if len(r.Commits) == 0 {
		return newCommitListStyle(opts).muted.Render("(no commits in this range)")
	}
	if width < 40 {
		// Pathologically narrow terminal -- give each component the bare
		// minimum and let the terminal wrap if it must.
		width = 40
	}

	st := newCommitListStyle(opts)

	// Pre-compute the metadata suffix per commit so we can size the subject
	// column by what is left over.
	type prepared struct {
		shortSHA string
		subject  string
		suffix   string
	}
	rows := make([]prepared, len(r.Commits))
	for i, c := range r.Commits {
		// Scrub every operator-visible field so a hostile commit subject /
		// author / SHA cannot act on the terminal instead of printing in
		// it. See sanitizeUntrusted in changelog.go for the threat model.
		// Sanitize once per commit and reuse the cleaned values; passing
		// a still-dirty `c` into formatMeta would re-do the work and risk
		// drift if a future field is added without the helper coverage.
		clean := sanitizeCommit(c)
		rows[i] = prepared{
			shortSHA: shortSHA(clean.SHA),
			subject:  strings.TrimSpace(clean.Subject),
			suffix:   formatMeta(clean),
		}
	}

	var out strings.Builder
	for _, row := range rows {
		// Layout: "<sha>  <subject>  <suffix>"
		// Spacing: 2 spaces between sha and subject, 2 spaces between subject and suffix.
		fixed := runewidth.StringWidth(row.shortSHA) + 2 + 2 + runewidth.StringWidth(row.suffix)
		subjectBudget := max(width-fixed, minSubjectCol)
		subj := truncate(row.subject, subjectBudget, st.ellipsis)

		out.WriteString(st.sha.Render(row.shortSHA))
		out.WriteString("  ")
		out.WriteString(subj)
		out.WriteString("  ")
		out.WriteString(st.meta.Render(row.suffix))
		out.WriteByte('\n')
	}

	if r.TotalCommits > len(r.Commits) {
		footer := fmt.Sprintf("... showing first %d of %d commits", len(r.Commits), r.TotalCommits)
		out.WriteString(st.muted.Render(footer))
		out.WriteByte('\n')
	}

	return strings.TrimRight(out.String(), "\n")
}

// shortSHA returns the first shaShortLen characters of sha (or sha itself if
// shorter). Mirrors `git log --oneline` formatting.
func shortSHA(sha string) string {
	if len(sha) <= shaShortLen {
		return sha
	}
	return sha[:shaShortLen]
}

// formatMeta renders the metadata suffix shown after each subject:
//
//	(@author, YYYY-MM-DD)
//
// Empty author/date components are omitted from the suffix so very minimal
// commits still produce a sane line.
func formatMeta(c selfupdate.Commit) string {
	var parts []string
	if c.Author != "" {
		parts = append(parts, "@"+c.Author)
	}
	if c.Date != "" {
		parts = append(parts, c.Date)
	}
	if len(parts) == 0 {
		return ""
	}
	return "(" + strings.Join(parts, ", ") + ")"
}

// sanitizeCommit returns a copy of c with terminal-acting sequences scrubbed
// from every operator-visible field. Used by the commit-list renderer at the
// boundary between attacker-controllable git data and the operator's terminal.
func sanitizeCommit(c selfupdate.Commit) selfupdate.Commit {
	c.Author = SanitizeUntrustedLine(c.Author)
	c.Date = SanitizeUntrustedLine(c.Date)
	c.Subject = SanitizeUntrustedLine(c.Subject)
	c.SHA = SanitizeUntrustedLine(c.SHA)
	return c
}

// truncate cuts s to fit within the given visual width, appending the
// supplied ellipsis when truncation occurs. Width is measured in display
// columns (runewidth) rather than bytes so wide CJK / emoji chars don't
// blow past the budget.
func truncate(s string, width int, ellipsis string) string {
	if width <= 0 {
		return ""
	}
	if runewidth.StringWidth(s) <= width {
		return s
	}
	ellipsisWidth := runewidth.StringWidth(ellipsis)
	budget := width - ellipsisWidth
	if budget <= 0 {
		// Width is so tight that even the ellipsis doesn't fit; return as
		// many leading runes as we can.
		return runewidth.Truncate(s, width, "")
	}
	return runewidth.Truncate(s, budget, "") + ellipsis
}
