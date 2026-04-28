package selfupdate

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Test fixture SHAs are valid hex (0-9, a-f) so isLikelyCommitSHA accepts
// them and the resolver short-circuits the tag-lookup branch. Mnemonic
// stems are kept readable by living in the leading bytes; the trailing
// padding fills out to 40 chars so projectListCommit / shaMatches see
// realistic full-length SHAs.
const (
	headSHA            = "aaaaaaa1111111111111111111111111111111aa"
	middleSHA          = "bbbbbbb2222222222222222222222222222222bb"
	baseSHA            = "ccccccc3333333333333333333333333333333cc"
	olderSHA           = "ddddddd4444444444444444444444444444444dd"
	shortBaseFullSHA   = "ba5eabc1234567890abcdef0000000000000000a" // matches "ba5eabc" prefix
	shortBasePrefix    = "ba5eabc"
	page2HeadSHA       = "9a9e2f1257aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	page2BaseSHA       = "ba5e02f21bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	neverFoundSHA      = "cafef00d000000000000000000000000000000ee"
	annotatedRefSHA    = "7a90b1ec00000000000000000000000000000000"
	annotatedTargetSHA = "a44074ed7a96e1000000000000000000000000bb"
	lightweightTagSHA  = "7a90e501ed5ba000000000000000000000000000"
	deadbeefSHA        = "deadbeefcafebabe1234567890abcdef00000000"
)

// fakeCommitsServer wires the list-commits endpoint and the two tag-resolution
// endpoints on a single httptest.Server so each test can drive the walk
// through one fixture set without standing up three separate servers.
type fakeCommitsServer struct {
	srv          *httptest.Server
	commitsByPg  map[int][]listCommitJSON
	tagRefs      map[string]gitRefJSON // tag -> /git/ref/tags/<tag> response
	tagObjects   map[string]gitTagJSON // sha -> /git/tags/<sha> response
	commitsCalls int
	commitsURIs  []string
	tagRefURIs   []string
	tagObjURIs   []string
}

func newFakeCommitsServer(t *testing.T) *fakeCommitsServer {
	t.Helper()
	f := &fakeCommitsServer{
		commitsByPg: map[int][]listCommitJSON{},
		tagRefs:     map[string]gitRefJSON{},
		tagObjects:  map[string]gitTagJSON{},
	}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case strings.Contains(r.URL.Path, "/git/ref/tags/"):
			f.tagRefURIs = append(f.tagRefURIs, r.RequestURI)
			tag := strings.TrimPrefix(r.URL.Path, "/git/ref/tags/")
			ref, ok := f.tagRefs[tag]
			if !ok {
				http.NotFound(w, r)
				return
			}
			_ = json.NewEncoder(w).Encode(ref)
		case strings.Contains(r.URL.Path, "/git/tags/"):
			f.tagObjURIs = append(f.tagObjURIs, r.RequestURI)
			sha := strings.TrimPrefix(r.URL.Path, "/git/tags/")
			obj, ok := f.tagObjects[sha]
			if !ok {
				http.NotFound(w, r)
				return
			}
			_ = json.NewEncoder(w).Encode(obj)
		case strings.HasSuffix(r.URL.Path, "/commits"):
			f.commitsCalls++
			f.commitsURIs = append(f.commitsURIs, r.RequestURI)
			page := 1
			if p := r.URL.Query().Get("page"); p != "" {
				_, _ = fmt.Sscanf(p, "%d", &page)
			}
			entries, ok := f.commitsByPg[page]
			if !ok {
				entries = []listCommitJSON{}
			}
			_ = json.NewEncoder(w).Encode(entries)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeCommitsServer) call(ctx context.Context, base, head string) (CommitRange, error) {
	commitsURL := f.srv.URL + "/commits"
	tagRefURL := f.srv.URL + "/git/ref/tags/{tag}"
	tagObjURL := f.srv.URL + "/git/tags/{sha}"
	return commitsBetweenFromURL(ctx, commitsURL, tagRefURL, tagObjURL, base, head)
}

// commitFixture is a brevity helper for building list-commits entries
// in-line. Tests that care about subject parsing (multiline, blank-line
// stripping) build the entry by hand instead.
func commitFixture(sha, subject, author, date string) listCommitJSON {
	return listCommitJSON{
		SHA:     sha,
		HTMLURL: "https://github.com/Aureliolo/synthorg/commit/" + sha,
		Commit: compareCommitInner{
			Message: subject,
			Author:  compareCommitAuthor{Name: author, Date: date},
		},
	}
}

func TestCommitsBetween_walksBackToBaseSHA(t *testing.T) {
	f := newFakeCommitsServer(t)
	// Single page returned by /commits; walk stops at the base SHA.
	f.commitsByPg[1] = []listCommitJSON{
		commitFixture(headSHA, "feat: head commit", "Daisy", "2026-04-25T12:00:00Z"),
		commitFixture(middleSHA, "fix: middle commit", "Bob", "2026-04-24T11:00:00Z"),
		commitFixture(baseSHA, "chore: base commit -- should NOT be in result", "Carol", "2026-04-23T10:00:00Z"),
		commitFixture(olderSHA, "older commit -- never reached", "Dan", "2026-04-22T09:00:00Z"),
	}

	got, err := f.call(context.Background(), baseSHA, "v0.7.5")
	if err != nil {
		t.Fatalf("CommitsBetween: %v", err)
	}
	if len(got.Commits) != 2 {
		t.Fatalf("len(Commits) = %d, want 2 (head + middle, base excluded)", len(got.Commits))
	}
	if got.Commits[0].SHA != headSHA {
		t.Errorf("Commits[0].SHA = %q, want head SHA", got.Commits[0].SHA)
	}
	if got.Commits[0].Subject != "feat: head commit" {
		t.Errorf("Commits[0].Subject = %q", got.Commits[0].Subject)
	}
	if got.Commits[0].Author != "Daisy" {
		t.Errorf("Commits[0].Author = %q, want Daisy", got.Commits[0].Author)
	}
	if got.Commits[0].Date != "2026-04-25" {
		t.Errorf("Commits[0].Date = %q, want 2026-04-25 (YYYY-MM-DD)", got.Commits[0].Date)
	}
	if got.TotalCommits != 2 {
		t.Errorf("TotalCommits = %d, want 2 (no truncation hint)", got.TotalCommits)
	}
	if f.commitsCalls != 1 {
		t.Errorf("commitsCalls = %d, want 1 (single page sufficient)", f.commitsCalls)
	}
}

func TestCommitsBetween_acceptsShortSHABase(t *testing.T) {
	// Embedded build SHAs are 40-char full SHAs in production, but the
	// resolver also accepts >= minSHAPrefixLen-char prefixes for forward
	// compatibility with abbreviated forms.
	f := newFakeCommitsServer(t)
	f.commitsByPg[1] = []listCommitJSON{
		commitFixture(headSHA, "head", "x", "2026-04-25T00:00:00Z"),
		commitFixture(shortBaseFullSHA, "this is base", "x", "2026-04-24T00:00:00Z"),
	}
	got, err := f.call(context.Background(), shortBasePrefix, "v0.7.5")
	if err != nil {
		t.Fatalf("CommitsBetween: %v", err)
	}
	if len(got.Commits) != 1 {
		t.Errorf("len(Commits) = %d, want 1 (short SHA prefix should match)", len(got.Commits))
	}
}

func TestCommitsBetween_paginatesUntilBase(t *testing.T) {
	// Base lives on page 2. The walk must follow pagination and combine the
	// per-page results in order before stopping at base.
	f := newFakeCommitsServer(t)
	f.commitsByPg[1] = make([]listCommitJSON, commitsPerPage)
	for i := range f.commitsByPg[1] {
		sha := fmt.Sprintf("a1%038x", i)
		f.commitsByPg[1][i] = commitFixture(sha, fmt.Sprintf("p1 commit %d", i), "x", "2026-04-25T00:00:00Z")
	}
	f.commitsByPg[2] = []listCommitJSON{
		commitFixture(page2HeadSHA, "p2 head", "x", "2026-04-24T00:00:00Z"),
		commitFixture(page2BaseSHA, "base on p2", "x", "2026-04-23T00:00:00Z"),
	}

	got, err := f.call(context.Background(), page2BaseSHA, "v0.7.5")
	if err != nil {
		t.Fatalf("CommitsBetween: %v", err)
	}
	if want := commitsPerPage + 1; len(got.Commits) != want {
		t.Errorf("len(Commits) = %d, want %d (page 1 + 1 from page 2)", len(got.Commits), want)
	}
	if f.commitsCalls != 2 {
		t.Errorf("commitsCalls = %d, want 2 (paginated)", f.commitsCalls)
	}
}

func TestCommitsBetween_truncationFooterWhenBaseNotReached(t *testing.T) {
	// Every page is a full 100-entry block and base is never encountered.
	// The walk should hit the maxCommitPages cap and surface a TotalCommits
	// value greater than len(Commits) so the renderer shows "showing N
	// (truncated)".
	f := newFakeCommitsServer(t)
	for page := 1; page <= maxCommitPages; page++ {
		entries := make([]listCommitJSON, commitsPerPage)
		for i := range entries {
			sha := fmt.Sprintf("%02x%038x", page, i)
			entries[i] = commitFixture(sha, fmt.Sprintf("p%d c%d", page, i), "x", "2026-04-25T00:00:00Z")
		}
		f.commitsByPg[page] = entries
	}

	got, err := f.call(context.Background(), neverFoundSHA, "v0.7.5")
	if err != nil {
		t.Fatalf("CommitsBetween: %v", err)
	}
	if want := maxCommitPages * commitsPerPage; len(got.Commits) != want {
		t.Errorf("len(Commits) = %d, want %d", len(got.Commits), want)
	}
	if got.TotalCommits <= len(got.Commits) {
		t.Errorf("TotalCommits = %d, want > %d so the UI renders the truncation footer",
			got.TotalCommits, len(got.Commits))
	}
	if f.commitsCalls != maxCommitPages {
		t.Errorf("commitsCalls = %d, want %d", f.commitsCalls, maxCommitPages)
	}
}

func TestCommitsBetween_emptyHeadStream(t *testing.T) {
	// /commits returns an empty array on the first page (head ref returns
	// no history -- e.g. an unborn ref). The walk returns an empty range
	// with no error AND no truncation hint so the caller can show its own
	// "range looks empty" message rather than a misleading "showing 0
	// (truncated)" footer.
	f := newFakeCommitsServer(t)
	f.commitsByPg[1] = []listCommitJSON{}

	got, err := f.call(context.Background(), deadbeefSHA, "v0.7.5")
	if err != nil {
		t.Fatalf("CommitsBetween: %v", err)
	}
	if len(got.Commits) != 0 {
		t.Errorf("len(Commits) = %d, want 0", len(got.Commits))
	}
	if got.TotalCommits != 0 {
		t.Errorf("TotalCommits = %d, want 0", got.TotalCommits)
	}
}

func TestCommitsBetween_resolvesLightweightTagBase(t *testing.T) {
	// Base is a tag name, not a SHA. The resolver should look it up via
	// /git/ref/tags/<tag>, see object.type == "commit", and use the
	// returned SHA directly without dereferencing.
	f := newFakeCommitsServer(t)
	f.tagRefs["v0.7.4"] = gitRefJSON{
		Object: struct {
			SHA  string `json:"sha"`
			Type string `json:"type"`
		}{SHA: lightweightTagSHA, Type: "commit"},
	}
	f.commitsByPg[1] = []listCommitJSON{
		commitFixture(headSHA, "head", "x", "2026-04-25T00:00:00Z"),
		commitFixture(lightweightTagSHA, "this is the tag target", "x", "2026-04-24T00:00:00Z"),
	}

	got, err := f.call(context.Background(), "v0.7.4", "v0.7.5")
	if err != nil {
		t.Fatalf("CommitsBetween: %v", err)
	}
	if len(got.Commits) != 1 {
		t.Errorf("len(Commits) = %d, want 1", len(got.Commits))
	}
	if len(f.tagRefURIs) != 1 {
		t.Errorf("tagRefURIs = %v, want exactly one /git/ref/tags lookup", f.tagRefURIs)
	}
	if len(f.tagObjURIs) != 0 {
		t.Errorf("tagObjURIs = %v, want zero (lightweight tag should not dereference)", f.tagObjURIs)
	}
}

func TestCommitsBetween_dereferencesAnnotatedTagBase(t *testing.T) {
	// Annotated tag: /git/ref/tags returns object.type "tag"; the resolver
	// must follow up with /git/tags/<sha> to recover the wrapped commit.
	f := newFakeCommitsServer(t)
	f.tagRefs["v0.7.4"] = gitRefJSON{
		Object: struct {
			SHA  string `json:"sha"`
			Type string `json:"type"`
		}{SHA: annotatedRefSHA, Type: "tag"},
	}
	f.tagObjects[annotatedRefSHA] = gitTagJSON{
		Object: struct {
			SHA  string `json:"sha"`
			Type string `json:"type"`
		}{SHA: annotatedTargetSHA, Type: "commit"},
	}
	f.commitsByPg[1] = []listCommitJSON{
		commitFixture(headSHA, "head", "x", "2026-04-25T00:00:00Z"),
		commitFixture(annotatedTargetSHA, "annotated tag target", "x", "2026-04-24T00:00:00Z"),
	}

	got, err := f.call(context.Background(), "v0.7.4", "v0.7.5")
	if err != nil {
		t.Fatalf("CommitsBetween: %v", err)
	}
	if len(got.Commits) != 1 {
		t.Errorf("len(Commits) = %d, want 1", len(got.Commits))
	}
	if len(f.tagRefURIs) != 1 || len(f.tagObjURIs) != 1 {
		t.Errorf("tagRefURIs=%v tagObjURIs=%v, want one of each", f.tagRefURIs, f.tagObjURIs)
	}
}

func TestCommitsBetween_rateLimited(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer srv.Close()
	_, err := commitsBetweenFromURL(
		context.Background(),
		srv.URL+"/commits",
		srv.URL+"/git/ref/tags/{tag}",
		srv.URL+"/git/tags/{sha}",
		deadbeefSHA,
		"v0.7.5",
	)
	if err == nil {
		t.Fatal("expected error")
	}
	if !strings.Contains(err.Error(), "rate-limited") {
		t.Errorf("error = %v, want rate-limited message", err)
	}
}

func TestCommitsBetween_notFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()
	_, err := commitsBetweenFromURL(
		context.Background(),
		srv.URL+"/commits",
		srv.URL+"/git/ref/tags/{tag}",
		srv.URL+"/git/tags/{sha}",
		deadbeefSHA,
		"v0.7.5",
	)
	if err == nil {
		t.Fatal("expected error for 404")
	}
}

func TestCommitsBetween_tagResolutionPathEscapesMetacharacters(t *testing.T) {
	// A tag name carrying URL metacharacters must be percent-escaped so it
	// cannot break out of the path segment of the /git/ref/tags/<tag>
	// lookup. Hits the resolver directly because passing through to the
	// commits endpoint would obscure which URL we are testing.
	tests := []struct {
		name    string
		tag     string
		wantSeg string
	}{
		{"slash_in_tag", "v0.7.4/evil", "/git/ref/tags/v0.7.4%2Fevil"},
		{"hash_in_tag", "v0.7.4#anchor", "/git/ref/tags/v0.7.4%23anchor"},
		{"question_in_tag", "v0.7.4?evil=1", "/git/ref/tags/v0.7.4%3Fevil=1"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var seenURI string
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				seenURI = r.RequestURI
				http.NotFound(w, r) // bail early -- we only care about the URI shape
			}))
			defer srv.Close()
			_, _ = commitsBetweenFromURL(
				context.Background(),
				srv.URL+"/commits",
				srv.URL+"/git/ref/tags/{tag}",
				srv.URL+"/git/tags/{sha}",
				tt.tag,
				"v0.7.5",
			)
			if seenURI != tt.wantSeg {
				t.Errorf("seenURI = %q, want %q", seenURI, tt.wantSeg)
			}
		})
	}
}

func TestCommitsBetween_commitsURIShape(t *testing.T) {
	// Per-page commits requests must carry sha=<head>, per_page=100,
	// page=N. Tests against the URI rather than a structural Query().Get
	// check so a future regression that rebuilds the URL by string-concat
	// is caught too.
	f := newFakeCommitsServer(t)
	f.commitsByPg[1] = []listCommitJSON{
		commitFixture(shortBaseFullSHA, "base", "x", "2026-04-24T00:00:00Z"),
	}
	_, _ = f.call(context.Background(), shortBasePrefix, "v0.7.5")
	if len(f.commitsURIs) == 0 {
		t.Fatalf("commitsURIs = %v, want at least one", f.commitsURIs)
	}
	got := f.commitsURIs[0]
	wantParams := []string{
		"sha=v0.7.5",
		fmt.Sprintf("per_page=%d", commitsPerPage),
		"page=1",
	}
	for _, want := range wantParams {
		if !strings.Contains(got, want) {
			t.Errorf("commits URI %q missing query param %q", got, want)
		}
	}
}

func TestCommitsBetween_subjectSkipsLeadingBlankLines(t *testing.T) {
	f := newFakeCommitsServer(t)
	f.commitsByPg[1] = []listCommitJSON{
		{
			SHA:     deadbeefSHA,
			HTMLURL: "https://github.com/Aureliolo/synthorg/commit/" + deadbeefSHA,
			Commit: compareCommitInner{
				Message: "\n\nsubject line\n\nbody",
				Author:  compareCommitAuthor{Name: "x", Date: "2026-04-25T00:00:00Z"},
			},
		},
		commitFixture(shortBaseFullSHA, "base", "x", "2026-04-24T00:00:00Z"),
	}
	got, err := f.call(context.Background(), shortBasePrefix, "v0.7.5")
	if err != nil {
		t.Fatalf("CommitsBetween: %v", err)
	}
	if want := "subject line"; got.Commits[0].Subject != want {
		t.Errorf("Subject = %q, want %q", got.Commits[0].Subject, want)
	}
}

func TestCommitsBetween_invalidDateGracefulFallback(t *testing.T) {
	f := newFakeCommitsServer(t)
	f.commitsByPg[1] = []listCommitJSON{
		commitFixture(deadbeefSHA, "subject", "x", "not-a-date"),
		commitFixture(shortBaseFullSHA, "base", "x", "2026-04-24T00:00:00Z"),
	}
	got, err := f.call(context.Background(), shortBasePrefix, "v0.7.5")
	if err != nil {
		t.Fatalf("CommitsBetween: %v", err)
	}
	if got.Commits[0].Date != "not-a-date" {
		t.Errorf("Date = %q, want raw fallback when unparseable", got.Commits[0].Date)
	}
}

func TestCommitsBetween_emptyBaseRejected(t *testing.T) {
	f := newFakeCommitsServer(t)
	_, err := f.call(context.Background(), "", "v0.7.5")
	if err == nil {
		t.Fatal("expected error for empty base ref")
	}
	if !strings.Contains(err.Error(), "empty base ref") {
		t.Errorf("error = %v, want explicit empty-base message", err)
	}
}

func TestCommitsBetween_emptyHeadRejected(t *testing.T) {
	f := newFakeCommitsServer(t)
	_, err := f.call(context.Background(), deadbeefSHA, "")
	if err == nil {
		t.Fatal("expected error for empty head ref")
	}
	if !strings.Contains(err.Error(), "empty head ref") {
		t.Errorf("error = %v, want explicit empty-head message", err)
	}
}
