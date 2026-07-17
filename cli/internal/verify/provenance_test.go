package verify

import (
	"context"
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

func TestVerifyProvenanceEmptyDigest(t *testing.T) {
	ref := ImageRef{
		Registry:   "ghcr.io",
		Repository: "test/image",
		Tag:        "1.0.0",
	}
	err := VerifyProvenance(context.Background(), ref, nil, sigverify.CertificateIdentity{})
	if err == nil {
		t.Fatal("expected error for empty digest")
	}
	if !strings.Contains(err.Error(), "digest not resolved") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestFetchGitHubAttestationsNotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	// Temporarily override the API base for testing.
	origBase := githubAPIBase
	defer func() { setGitHubAPIBase(origBase) }()
	setGitHubAPIBase(srv.URL)

	_, err := fetchGitHubAttestations(context.Background(), testDigest)
	if err == nil {
		t.Fatal("expected error for 404 response")
	}
	if !errors.Is(err, ErrNoProvenanceAttestations) {
		t.Errorf("expected ErrNoProvenanceAttestations, got: %v", err)
	}
}

func TestFetchGitHubAttestationsEmptyResponse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"attestations": []}`))
	}))
	defer srv.Close()

	origBase := githubAPIBase
	defer func() { setGitHubAPIBase(origBase) }()
	setGitHubAPIBase(srv.URL)

	_, err := fetchGitHubAttestations(context.Background(), testDigest)
	if err == nil {
		t.Fatal("expected error for empty attestations")
	}
	if !errors.Is(err, ErrNoProvenanceAttestations) {
		t.Errorf("expected ErrNoProvenanceAttestations, got: %v", err)
	}
}

func TestFetchGitHubAttestationsSuccess(t *testing.T) {
	bundle := json.RawMessage(`{"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}`)
	resp := githubAttestationResponse{
		Attestations: []githubAttestation{
			{Bundle: bundle},
		},
	}
	respJSON, err := json.Marshal(resp)
	if err != nil {
		t.Fatalf("marshaling response: %v", err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify the URL path contains the digest.
		if !strings.Contains(r.URL.Path, testDigest) {
			t.Errorf("expected digest in URL path, got: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(respJSON)
	}))
	defer srv.Close()

	origBase := githubAPIBase
	defer func() { setGitHubAPIBase(origBase) }()
	setGitHubAPIBase(srv.URL)

	bundles, err := fetchGitHubAttestations(context.Background(), testDigest)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(bundles) != 1 {
		t.Fatalf("expected 1 bundle, got %d", len(bundles))
	}
}

func TestFetchGitHubAttestationsServerError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	origBase := githubAPIBase
	defer func() { setGitHubAPIBase(origBase) }()
	setGitHubAPIBase(srv.URL)

	_, err := fetchGitHubAttestations(context.Background(), testDigest)
	if err == nil {
		t.Fatal("expected error for 500 response")
	}
	if !strings.Contains(err.Error(), fmt.Sprintf("HTTP %d", http.StatusInternalServerError)) {
		t.Errorf("expected HTTP status in error, got: %v", err)
	}
}

func TestVerifyProvenanceInvalidBundle(t *testing.T) {
	// Mock GitHub API returning an invalid bundle.
	resp := `{"attestations": [{"bundle": {"invalid": "not a sigstore bundle"}}]}`

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(resp))
	}))
	defer srv.Close()

	origBase := githubAPIBase
	defer func() { setGitHubAPIBase(origBase) }()
	setGitHubAPIBase(srv.URL)

	ref := ImageRef{
		Registry:   "ghcr.io",
		Repository: "test/image",
		Tag:        "1.0.0",
		Digest:     testDigest,
	}

	err := VerifyProvenance(context.Background(), ref, nil, sigverify.CertificateIdentity{})
	if err == nil {
		t.Fatal("expected error for invalid bundle")
	}
	if !strings.Contains(err.Error(), "no valid SLSA provenance attestation") {
		t.Errorf("expected provenance attestation error, got: %v", err)
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

func TestFetchSetsAPIVersionHeader(t *testing.T) {
	var gotVersion string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotVersion = r.Header.Get("X-GitHub-Api-Version")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"attestations":[{"bundle":{"mediaType":"x"}}]}`))
	}))
	defer srv.Close()

	origBase := githubAPIBase
	defer func() { setGitHubAPIBase(origBase) }()
	setGitHubAPIBase(srv.URL)

	if _, err := fetchGitHubAttestations(context.Background(), testDigest); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if gotVersion != githubAPIVersion {
		t.Errorf("expected X-GitHub-Api-Version %q, got %q", githubAPIVersion, gotVersion)
	}
}

func TestResolveBundleFromURL(t *testing.T) {
	want := `{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-snappy")
		_, _ = w.Write(snappy.Encode(nil, []byte(want)))
	}))
	defer srv.Close()

	orig := validateBundleURL
	defer func() { validateBundleURL = orig }()
	validateBundleURL = url.Parse

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

func TestDecodeBundleBodyRawWhenNotSnappy(t *testing.T) {
	raw := []byte(`{"mediaType":"x"}`)
	got, err := decodeBundleBody("application/json", raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if string(got) != string(raw) {
		t.Errorf("expected raw passthrough, got %s", got)
	}
}

func TestDefaultValidateBundleURL(t *testing.T) {
	cases := []struct {
		name    string
		url     string
		wantErr bool
	}{
		{"azure blob", "https://tmaproduction.blob.core.windows.net/x", false},
		{"github domain", "https://github.com/x", false},
		{"http rejected", "http://tmaproduction.blob.core.windows.net/x", true},
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

	origBase := githubAPIBase
	defer func() { setGitHubAPIBase(origBase) }()
	setGitHubAPIBase(api.URL)
	orig := validateBundleURL
	defer func() { validateBundleURL = orig }()
	validateBundleURL = url.Parse

	ref := ImageRef{Registry: "ghcr.io", Repository: "test/image", Tag: "1.0.0", Digest: testDigest}
	err := VerifyProvenance(context.Background(), ref, nil, sigverify.CertificateIdentity{})
	if err == nil {
		t.Fatal("expected error for invalid bundle fetched via bundle_url")
	}
	if !strings.Contains(err.Error(), "no valid SLSA provenance attestation") {
		t.Errorf("expected provenance attestation error, got: %v", err)
	}
}
