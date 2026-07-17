package verify

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/klauspost/compress/snappy"
	sigverify "github.com/sigstore/sigstore-go/pkg/verify"
)

// withGitHubAPIBase points the attestation API at base for the duration of the
// test, restoring the original on cleanup.
func withGitHubAPIBase(t *testing.T, base string) {
	t.Helper()
	orig := githubAPIBase
	t.Cleanup(func() { setGitHubAPIBase(orig) })
	setGitHubAPIBase(base)
}

// withValidateBundleURL swaps the bundle_url validator (so tests can accept a
// local httptest host), restoring the original on cleanup.
func withValidateBundleURL(t *testing.T, fn func(string) (*url.URL, error)) {
	t.Helper()
	orig := validateBundleURL
	t.Cleanup(func() { validateBundleURL = orig })
	validateBundleURL = fn
}

func TestVerifyProvenanceEmptyDigest(t *testing.T) {
	ref := ImageRef{Registry: "ghcr.io", Repository: "test/image", Tag: "1.0.0"}
	err := VerifyProvenance(context.Background(), ref, nil, sigverify.CertificateIdentity{})
	if err == nil {
		t.Fatal("expected error for empty digest")
	}
	if !strings.Contains(err.Error(), "digest not resolved") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestFetchGitHubAttestations(t *testing.T) {
	cases := []struct {
		name       string
		status     int
		body       string
		wantErrIs  error
		wantErrSub string
		wantCount  int
	}{
		{name: "not found", status: http.StatusNotFound, wantErrIs: ErrNoProvenanceAttestations},
		{name: "empty attestations", status: http.StatusOK, body: `{"attestations": []}`, wantErrIs: ErrNoProvenanceAttestations},
		{name: "server error", status: http.StatusInternalServerError, wantErrSub: "HTTP 500"},
		{name: "inline bundle", status: http.StatusOK, body: `{"attestations":[{"bundle":{"mediaType":"x"}}]}`, wantCount: 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if !strings.Contains(r.URL.Path, testDigest) {
					t.Errorf("expected digest in URL path, got: %s", r.URL.Path)
				}
				if tc.status != http.StatusOK {
					w.WriteHeader(tc.status)
					return
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()
			withGitHubAPIBase(t, srv.URL)

			atts, err := fetchGitHubAttestations(context.Background(), testDigest)
			switch {
			case tc.wantErrIs != nil:
				if !errors.Is(err, tc.wantErrIs) {
					t.Fatalf("expected error %v, got: %v", tc.wantErrIs, err)
				}
			case tc.wantErrSub != "":
				if err == nil || !strings.Contains(err.Error(), tc.wantErrSub) {
					t.Fatalf("expected error containing %q, got: %v", tc.wantErrSub, err)
				}
			default:
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				if len(atts) != tc.wantCount {
					t.Fatalf("expected %d attestations, got %d", tc.wantCount, len(atts))
				}
			}
		})
	}
}

func TestFetchSetsAPIVersionHeader(t *testing.T) {
	var gotVersion string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotVersion = r.Header.Get("X-GitHub-Api-Version")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"attestations":[{"bundle":{"mediaType":"x"}}]}`))
	}))
	defer srv.Close()
	withGitHubAPIBase(t, srv.URL)

	if _, err := fetchGitHubAttestations(context.Background(), testDigest); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if gotVersion != githubAPIVersion {
		t.Errorf("expected X-GitHub-Api-Version %q, got %q", githubAPIVersion, gotVersion)
	}
}

// TestParseAttestationsSkipsNullBundle covers GitHub's current response shape:
// the inline bundle is null and no bundle_url is present. The four-byte
// null must not be treated as a usable bundle.
func TestParseAttestationsSkipsNullBundle(t *testing.T) {
	_, err := parseAttestations([]byte(`{"attestations":[{"bundle":null}]}`), testDigest)
	if !errors.Is(err, ErrNoProvenanceAttestations) {
		t.Fatalf("expected ErrNoProvenanceAttestations, got: %v", err)
	}
}

// TestParseAttestationsAcceptsBundleURL confirms an entry carrying only a
// bundle_url (inline bundle null) is retained for later resolution.
func TestParseAttestationsAcceptsBundleURL(t *testing.T) {
	body := []byte(`{"attestations":[{"bundle":null,"bundle_url":"https://x.blob.core.windows.net/b"}]}`)
	atts, err := parseAttestations(body, testDigest)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(atts) != 1 || atts[0].BundleURL == "" {
		t.Fatalf("expected 1 attestation with a bundle_url, got %+v", atts)
	}
}

func TestVerifyProvenanceInvalidBundle(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"attestations": [{"bundle": {"invalid": "not a sigstore bundle"}}]}`))
	}))
	defer srv.Close()
	withGitHubAPIBase(t, srv.URL)

	ref := ImageRef{Registry: "ghcr.io", Repository: "test/image", Tag: "1.0.0", Digest: testDigest}
	err := VerifyProvenance(context.Background(), ref, nil, sigverify.CertificateIdentity{})
	if err == nil {
		t.Fatal("expected error for invalid bundle")
	}
	if !strings.Contains(err.Error(), "no valid SLSA provenance attestation") {
		t.Errorf("expected provenance attestation error, got: %v", err)
	}
}

func TestResolveBundleFromURL(t *testing.T) {
	want := `{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Deliberately a generic content-type: decoding must not depend on it.
		w.Header().Set("Content-Type", "application/octet-stream")
		_, _ = w.Write(snappy.Encode(nil, []byte(want)))
	}))
	defer srv.Close()
	withValidateBundleURL(t, url.Parse)

	att := githubAttestation{Bundle: json.RawMessage("null"), BundleURL: srv.URL}
	got, err := resolveBundleJSON(context.Background(), att)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if string(got) != want {
		t.Errorf("decoded bundle mismatch:\n got %s\nwant %s", got, want)
	}
}

func TestResolveBundlePrefersInline(t *testing.T) {
	att := githubAttestation{
		Bundle:    json.RawMessage(`{"mediaType":"inline"}`),
		BundleURL: "https://x.blob.core.windows.net/should-not-be-fetched",
	}
	got, err := resolveBundleJSON(context.Background(), att)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if string(got) != `{"mediaType":"inline"}` {
		t.Errorf("expected inline bundle, got %s", got)
	}
}

// snappyHeader builds a Snappy block whose declared decoded length is
// declaredLen (with a single trailing byte), for exercising the size guard
// without allocating the payload.
func snappyHeader(declaredLen uint64) []byte {
	buf := make([]byte, binary.MaxVarintLen64)
	n := binary.PutUvarint(buf, declaredLen)
	return append(buf[:n], 0x00)
}

func TestDecodeBundleBody(t *testing.T) {
	jsonBody := `{"mediaType":"x"}`
	cases := []struct {
		name       string
		in         []byte
		want       string
		wantErrSub string
	}{
		{name: "raw json passthrough", in: []byte(jsonBody), want: jsonBody},
		{name: "json array passthrough", in: []byte(`[1,2]`), want: `[1,2]`},
		{name: "leading whitespace json", in: []byte("  \n" + jsonBody), want: "  \n" + jsonBody},
		{name: "snappy compressed", in: snappy.Encode(nil, []byte(jsonBody)), want: jsonBody},
		{name: "oversized declared length rejected", in: snappyHeader(uint64(maxDecodedBundleBytes) + 1), wantErrSub: "too large"},
		{name: "not json and not snappy", in: []byte{0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80}, wantErrSub: "neither JSON nor"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := decodeBundleBody(tc.in)
			if tc.wantErrSub != "" {
				if err == nil || !strings.Contains(err.Error(), tc.wantErrSub) {
					t.Fatalf("expected error containing %q, got: %v", tc.wantErrSub, err)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if string(got) != tc.want {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestDefaultValidateBundleURL(t *testing.T) {
	cases := []struct {
		name    string
		url     string
		wantErr bool
	}{
		{"azure blob subdomain", "https://tmaproduction.blob.core.windows.net/x", false},
		{"http rejected", "http://tmaproduction.blob.core.windows.net/x", true},
		{"github domain rejected", "https://github.com/x", true},
		{"githubusercontent rejected", "https://raw.githubusercontent.com/x", true},
		{"bare apex rejected", "https://blob.core.windows.net/x", true},
		{"foreign host rejected", "https://evil.example.com/x", true},
		{"blob lookalike rejected", "https://blob.core.windows.net.evil.com/x", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := defaultValidateBundleURL(tc.url)
			if (err != nil) != tc.wantErr {
				t.Errorf("defaultValidateBundleURL(%q) err=%v, wantErr=%v", tc.url, err, tc.wantErr)
			}
		})
	}
}

// TestDefaultValidateBundleURLDoesNotLeakURL confirms a parse failure never
// echoes the raw URL (which may carry an access token) into the error.
func TestDefaultValidateBundleURLDoesNotLeakURL(t *testing.T) {
	secret := "https://%zz/path?sig=SECRETTOKEN"
	_, err := defaultValidateBundleURL(secret)
	if err == nil {
		t.Fatal("expected parse error")
	}
	if strings.Contains(err.Error(), "SECRETTOKEN") {
		t.Errorf("error leaked the raw URL/token: %v", err)
	}
}

// TestBundleFetchRejectsCrossHostRedirect confirms a redirect to a host outside
// the allowlist is revalidated and rejected mid-fetch (SSRF guard).
func TestBundleFetchRejectsCrossHostRedirect(t *testing.T) {
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("should never be read"))
	}))
	defer target.Close()
	redirector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusFound)
	}))
	defer redirector.Close()

	redirectorURL, err := url.Parse(redirector.URL)
	if err != nil {
		t.Fatalf("parsing redirector url: %v", err)
	}
	// Accept only the first host; a redirect to any other host must be rejected.
	withValidateBundleURL(t, func(raw string) (*url.URL, error) {
		u, perr := url.Parse(raw)
		if perr != nil {
			return nil, perr
		}
		if u.Host != redirectorURL.Host {
			return nil, fmt.Errorf("host %q not allowed", u.Host)
		}
		return u, nil
	})

	_, err = fetchBundleURL(context.Background(), redirector.URL)
	if err == nil {
		t.Fatal("expected cross-host redirect to be rejected")
	}
	if !strings.Contains(err.Error(), "not allowed") {
		t.Errorf("expected host-not-allowed error, got: %v", err)
	}
}

// TestVerifyProvenanceViaBundleURL exercises the full path: the API returns a
// null inline bundle plus a bundle_url, the bundle is fetched and
// Snappy-decompressed, and (being invalid Sigstore material) verification
// fails with the aggregate provenance error rather than the null-parse panic.
func TestVerifyProvenanceViaBundleURL(t *testing.T) {
	blob := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-snappy")
		_, _ = w.Write(snappy.Encode(nil, []byte(`{"invalid":"not a sigstore bundle"}`)))
	}))
	defer blob.Close()

	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprintf(w, `{"attestations":[{"bundle":null,"bundle_url":%q}]}`, blob.URL)
	}))
	defer api.Close()

	withGitHubAPIBase(t, api.URL)
	withValidateBundleURL(t, url.Parse)

	ref := ImageRef{Registry: "ghcr.io", Repository: "test/image", Tag: "1.0.0", Digest: testDigest}
	err := VerifyProvenance(context.Background(), ref, nil, sigverify.CertificateIdentity{})
	if err == nil {
		t.Fatal("expected error for invalid bundle fetched via bundle_url")
	}
	if !strings.Contains(err.Error(), "no valid SLSA provenance attestation") {
		t.Errorf("expected provenance attestation error, got: %v", err)
	}
}
