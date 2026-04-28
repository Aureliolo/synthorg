package selfupdate

import (
	"context"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Commit is the projected, UI-friendly view of a GitHub commit returned
// by CommitsBetween.
type Commit struct {
	SHA     string // full 40-char SHA
	Subject string // first line of the commit message
	Author  string // author name
	Date    string // YYYY-MM-DD (best-effort; raw value when unparseable)
	URL     string // html_url to the commit on github.com
}

// CommitRange is the result of a comparison between two refs. TotalCommits
// may exceed len(Commits) when the walk hit its page cap before reaching
// base; the caller can render a "showing N (truncated)" footer.
type CommitRange struct {
	Commits      []Commit
	TotalCommits int
}

const (
	// commitsPerPage is the page size used by the list-commits walk. GitHub
	// allows up to 100 entries per page, but each commit object inlines the
	// full PGP signature plus the signed payload (which duplicates the
	// commit message) plus 20+ author/committer URL fields, averaging ~15
	// KiB per commit on this repo. At per_page=100 a single page reliably
	// blows past the 1 MiB API cap. per_page=25 lands a typical page at
	// ~400 KiB -- comfortable headroom for outlier-heavy release commits.
	commitsPerPage = 25

	// maxCommitPages bounds how far back the walk will read. 20 pages * 25
	// entries = up to 500 commits, which comfortably covers the largest
	// dev-channel rollover we have observed in practice (~50-200 commits
	// per dev-to-stable jump). Beyond that, the truncation footer signals
	// to the user that the listing is incomplete.
	maxCommitPages = 20

	// commitSHALen is the canonical length of a git commit SHA. The base
	// ref must be exactly this many hex chars to be treated as a SHA;
	// anything shorter (or any non-hex char) is routed through tag
	// resolution. Restricting to the full length closes a corner case
	// flagged by external review: a hex-only tag of >= 7 chars (e.g.
	// "abcdef1") would otherwise bypass the tag-ref endpoint and the
	// list-commits walk would miss the actual commit the tag points at.
	// Real callers always pass the full 40-char SHA stamped in by
	// GoReleaser, so the tightening is purely defensive.
	commitSHALen = 40
)

// listCommitJSON mirrors the subset of the GitHub list-commits payload we
// need. The endpoint returns commit metadata only -- no inline `files[]`
// patch content -- which is what makes the response size bounded enough to
// fit comfortably under the API cap.
type listCommitJSON struct {
	SHA     string             `json:"sha"`
	HTMLURL string             `json:"html_url"`
	Commit  compareCommitInner `json:"commit"`
}

type compareCommitInner struct {
	Message string              `json:"message"`
	Author  compareCommitAuthor `json:"author"`
}

type compareCommitAuthor struct {
	Name string `json:"name"`
	Date string `json:"date"` // RFC 3339
}

// gitRefJSON is the minimal projection of GitHub's `/git/ref/tags/<tag>`
// response used to resolve a tag name to its commit SHA before walking the
// list-commits stream.
type gitRefJSON struct {
	Object struct {
		SHA  string `json:"sha"`
		Type string `json:"type"`
	} `json:"object"`
}

// gitTagJSON is the projection of `/git/tags/<sha>` used to dereference
// annotated tags (object.type == "tag") to their target commit SHA.
type gitTagJSON struct {
	Object struct {
		SHA  string `json:"sha"`
		Type string `json:"type"`
	} `json:"object"`
}

// CommitsBetween fetches the commits in (base, head]. base may be a 40-char
// commit SHA, a short SHA (>= 7 hex chars), or a tag name. head must be a
// ref the list-commits endpoint accepts via ?sha= (tag, branch, or SHA).
//
// The implementation paginates `/repos/{owner}/{repo}/commits?sha=<head>`
// in reverse chronological order from head and stops the moment a commit
// whose SHA matches base is encountered. Unlike the compare endpoint --
// which inlines a `files[]` array with full patch content for every changed
// file and routinely produces multi-MB responses -- list-commits returns
// commit metadata only and stays well under the 1 MiB API cap even for
// the maxCommitPages * commitsPerPage = 500-commit ceiling.
func CommitsBetween(ctx context.Context, base, head string) (CommitRange, error) {
	return commitsBetweenFromURL(ctx, listCommitsBaseURL(), tagRefBaseURL(), tagObjectBaseURL(), base, head)
}

// listCommitsBaseURL returns the list-commits endpoint. Kept as a function
// so tests can inject an httptest URL via commitsBetweenFromURL.
func listCommitsBaseURL() string {
	return "https://api.github.com/repos/" + repoSlug + "/commits"
}

// tagRefBaseURL returns the tag-ref resolution endpoint template. Tests
// inject an httptest URL via commitsBetweenFromURL.
func tagRefBaseURL() string {
	return "https://api.github.com/repos/" + repoSlug + "/git/ref/tags/{tag}"
}

// tagObjectBaseURL returns the tag-object dereference endpoint template,
// used to follow annotated-tag indirection (the `/git/ref/tags/<tag>`
// response for an annotated tag points at a tag object whose own SHA
// must be looked up here to recover the wrapped commit SHA).
func tagObjectBaseURL() string {
	return "https://api.github.com/repos/" + repoSlug + "/git/tags/{sha}"
}

// commitsBetweenFromURL is the testable core of CommitsBetween. The three
// URL templates carry the seam tests use to swap in httptest endpoints.
// commitsBaseURL is appended with `?sha=&per_page=&page=` query params;
// tagRefURL and tagObjectURL contain `{tag}` and `{sha}` placeholders that
// are URL-path-escaped before substitution so a tag containing path
// metacharacters cannot rewrite the request to a different endpoint.
func commitsBetweenFromURL(ctx context.Context, commitsBaseURL, tagRefURL, tagObjectURL, base, head string) (CommitRange, error) {
	if base == "" {
		return CommitRange{}, fmt.Errorf("comparing %s...%s: empty base ref", base, head)
	}
	if head == "" {
		return CommitRange{}, fmt.Errorf("comparing %s...%s: empty head ref", base, head)
	}

	// Resolve tag-form base to a SHA up front so the walk loop can do a
	// straight SHA-prefix match per entry. SHA-form bases skip the lookup.
	baseSHA, err := resolveBaseToSHA(ctx, tagRefURL, tagObjectURL, base)
	if err != nil {
		return CommitRange{}, fmt.Errorf("comparing %s...%s: %w", base, head, err)
	}

	collected, foundBase, hitCap, err := walkCommitsToBase(ctx, commitsBaseURL, baseSHA, head)
	if err != nil {
		return CommitRange{}, fmt.Errorf("comparing %s...%s: %w", base, head, err)
	}
	if foundBase {
		return CommitRange{Commits: collected, TotalCommits: len(collected)}, nil
	}

	// Walked without finding base. The "(truncated)" footer is only
	// honest when we actually hit the page cap (hitCap=true). If the
	// commit stream ran out naturally (empty or short final page),
	// we have the complete reachable history from head and there is
	// nothing more to show; bumping TotalCommits in that case would
	// lie to the UI about there being more commits than we render.
	total := len(collected)
	if hitCap {
		total++
	}
	return CommitRange{
		Commits:      collected,
		TotalCommits: total,
	}, nil
}

// walkCommitsToBase paginates the list-commits endpoint backwards from head
// and returns the commits encountered up to (but not including) the entry
// matching baseSHA. The three terminal flags distinguish the outcomes:
//   - foundBase = true: we encountered baseSHA in the stream (the happy path).
//   - hitCap = true: the loop ran out of pages without finding baseSHA AND
//     the last fetched page was full (commitsPerPage entries), implying more
//     history exists upstream that we did not fetch. The caller surfaces
//     this as a "(truncated)" footer.
//   - both false: the stream ended naturally (empty page or short final
//     page) without finding baseSHA. We have the complete reachable
//     history from head; nothing more to show. The caller MUST NOT render
//     a truncation footer in this case.
func walkCommitsToBase(ctx context.Context, commitsBaseURL, baseSHA, head string) (collected []Commit, foundBase, hitCap bool, err error) {
	for page := 1; page <= maxCommitPages; page++ {
		pageURL, err := buildCommitsPageURL(commitsBaseURL, head, page)
		if err != nil {
			return nil, false, false, err
		}
		entries, err := fetchJSON[[]listCommitJSON](ctx, pageURL)
		if err != nil {
			return nil, false, false, err
		}
		if len(entries) == 0 {
			return collected, false, false, nil
		}
		for _, c := range entries {
			if shaMatches(c.SHA, baseSHA) {
				return collected, true, false, nil
			}
			collected = append(collected, projectListCommit(c))
		}
		if len(entries) < commitsPerPage {
			return collected, false, false, nil
		}
	}
	// Exhausted maxCommitPages with full pages all the way; more history
	// exists upstream that we did not fetch.
	return collected, false, true, nil
}

// resolveBaseToSHA returns the full commit SHA for base. SHA-shaped inputs
// pass through unchanged. Tag-shaped inputs are resolved through the
// `/git/ref/tags/<tag>` endpoint, with one extra hop for annotated tags.
func resolveBaseToSHA(ctx context.Context, tagRefURL, tagObjectURL, base string) (string, error) {
	if isLikelyCommitSHA(base) {
		return base, nil
	}

	// Tags from the dev-release pipeline are written as `vX.Y.Z[-dev.N]`;
	// the GitHub API expects the leading `v` stripped from the URL path
	// only when the tag is itself stored without it -- the project always
	// stores tags with the `v` prefix, so we substitute base verbatim.
	refURL := strings.ReplaceAll(tagRefURL, "{tag}", url.PathEscape(base))
	ref, err := fetchJSON[gitRefJSON](ctx, refURL)
	if err != nil {
		return "", fmt.Errorf("resolving tag %q: %w", base, err)
	}
	if ref.Object.SHA == "" {
		return "", fmt.Errorf("resolving tag %q: empty object sha in response", base)
	}
	if ref.Object.Type == "commit" {
		return ref.Object.SHA, nil
	}
	if ref.Object.Type != "tag" {
		return "", fmt.Errorf("resolving tag %q: unexpected object type %q", base, ref.Object.Type)
	}

	// Annotated tag -- one extra dereference to reach the wrapped commit.
	// Annotated tags chain at most one level on GitHub (a tag object always
	// points at a commit object, never at another tag object), so we
	// deliberately do not loop: a second-level "tag" type would indicate a
	// malformed or manipulated response and is rejected by the type check
	// below rather than chased further.
	tagURL := strings.ReplaceAll(tagObjectURL, "{sha}", url.PathEscape(ref.Object.SHA))
	tag, err := fetchJSON[gitTagJSON](ctx, tagURL)
	if err != nil {
		return "", fmt.Errorf("dereferencing annotated tag %q: %w", base, err)
	}
	if tag.Object.SHA == "" {
		return "", fmt.Errorf("dereferencing annotated tag %q: empty target sha", base)
	}
	if tag.Object.Type != "commit" {
		return "", fmt.Errorf(
			"dereferencing annotated tag %q: expected commit, got %q",
			base, tag.Object.Type)
	}
	return tag.Object.SHA, nil
}

// buildCommitsPageURL appends ?sha=&per_page=&page= to the list-commits
// base URL. head is URL-query-escaped so a tag containing metacharacters
// cannot break out of the query parameter.
func buildCommitsPageURL(baseURL, head string, page int) (string, error) {
	parsed, err := url.Parse(baseURL)
	if err != nil {
		return "", fmt.Errorf("parsing commits URL: %w", err)
	}
	q := parsed.Query()
	q.Set("sha", head)
	q.Set("per_page", strconv.Itoa(commitsPerPage))
	q.Set("page", strconv.Itoa(page))
	parsed.RawQuery = q.Encode()
	return parsed.String(), nil
}

// shaMatches reports whether the full 40-char SHA returned by GitHub
// matches the requested ref. ref must be exactly commitSHALen hex chars
// (the canonical git commit SHA length); resolveBaseToSHA guarantees this
// by either pass-through (for SHA-form base inputs that pass
// isLikelyCommitSHA) or by tag dereferencing. Comparison is
// case-insensitive because tag-resolved SHAs and embedded build-time
// SHAs have historically arrived in different cases.
func shaMatches(full, ref string) bool {
	if len(ref) != commitSHALen || len(full) != commitSHALen {
		return false
	}
	return strings.EqualFold(full, ref)
}

// projectListCommit narrows a list-commits entry to the UI-facing Commit
// shape, dropping fields the renderer does not consume.
func projectListCommit(c listCommitJSON) Commit {
	return Commit{
		SHA:     c.SHA,
		Subject: firstLine(c.Commit.Message),
		Author:  c.Commit.Author.Name,
		Date:    formatCommitDate(c.Commit.Author.Date),
		URL:     c.HTMLURL,
	}
}

// isLikelyCommitSHA reports whether s has the canonical shape of a git
// commit SHA: exactly commitSHALen hex chars. Anything shorter (or any
// non-hex character) falls through to tag resolution. The strict length
// requirement closes a hex-named-tag bypass: a tag like "abcdef1" would
// otherwise match the prefix-length test and the list-commits walk would
// search for the wrong commit. Real callers (the embedded build-time
// SHA from GoReleaser and tag-dereferenced SHAs from /git/ref/tags) are
// always full 40-char SHAs, so the tightening costs nothing in practice.
func isLikelyCommitSHA(s string) bool {
	if len(s) != commitSHALen {
		return false
	}
	for _, r := range s {
		switch {
		case r >= '0' && r <= '9':
		case r >= 'a' && r <= 'f':
		case r >= 'A' && r <= 'F':
		default:
			return false
		}
	}
	return true
}

// firstLine returns the first non-empty line of a commit message (the
// subject). Skips leading blank lines so messages produced with a blank
// header still surface a meaningful subject. Returns the trimmed input
// when every line is blank.
func firstLine(msg string) string {
	for line := range strings.SplitSeq(msg, "\n") {
		if trimmed := strings.TrimSpace(line); trimmed != "" {
			return trimmed
		}
	}
	return strings.TrimSpace(msg)
}

// formatCommitDate parses an RFC 3339 timestamp from the GitHub commits API
// and returns YYYY-MM-DD. On parse failure, returns the raw input so the UI
// can still display something meaningful.
func formatCommitDate(raw string) string {
	if raw == "" {
		return ""
	}
	if t, err := time.Parse(time.RFC3339, raw); err == nil {
		return t.UTC().Format("2006-01-02")
	}
	return raw
}
