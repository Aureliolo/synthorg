package verify

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/klauspost/compress/snappy"
	"github.com/sigstore/sigstore-go/pkg/verify"
)

const (
	// SLSAProvenancePredicatePrefix is the prefix for SLSA provenance predicates.
	// Exported so selfupdate can reuse the same constant.
	SLSAProvenancePredicatePrefix = "https://slsa.dev/provenance/"

	// DSSEPayloadType is the expected DSSE envelope payload type for in-toto statements.
	// Exported so selfupdate can reuse the same constant.
	DSSEPayloadType = "application/vnd.in-toto+json"

	// defaultGitHubAPIBase is the base URL for the GitHub REST API.
	defaultGitHubAPIBase = "https://api.github.com"

	// githubAttestationOwnerRepo is the GitHub owner/repo for attestation
	// lookups. This is the canonical GitHub repository path (case-sensitive
	// for the API), not derived from the image registry prefix.
	githubAttestationOwnerRepo = "Aureliolo/synthorg"

	// githubAPIVersion pins the REST API version. GitHub moved attestation
	// bundles out-of-line to bundle_url and let the inline bundle go null;
	// that reached unversioned callers through the default version. Pinning
	// keeps the response contract deterministic against future default
	// shifts (following bundle_url below is what restores verification).
	githubAPIVersion = "2022-11-28"

	// maxAttestationResponseBytes caps the GitHub attestation API response
	// to prevent memory exhaustion from malicious or oversized responses.
	maxAttestationResponseBytes = 5 << 20 // 5MB

	// maxBundleFetchBytes caps a bundle fetched from bundle_url. Attestation
	// bundles are ~10KB compressed; 5MB is generous while bounding memory.
	maxBundleFetchBytes = 5 << 20

	// maxDecodedBundleBytes caps the DECOMPRESSED bundle size. A Snappy block
	// header declares an output length that snappy.Decode allocates up front, so
	// this ceiling is checked before decoding to stop a decompression bomb (the
	// fetch cap only bounds the compressed input).
	maxDecodedBundleBytes = 16 << 20

	// maxBundleRedirects bounds redirect following when fetching bundle_url.
	maxBundleRedirects = 5
)

// attestationHTTPTimeout bounds individual HTTP requests to the GitHub API.
// Set by Configure; defaults to 30s.
var attestationHTTPTimeout = 30 * time.Second

// ErrNoProvenanceAttestations indicates that no SLSA provenance attestations
// were found for an image via the GitHub attestation API. This is distinct
// from a cryptographic verification failure.
var ErrNoProvenanceAttestations = errors.New("no SLSA provenance attestations found")

// githubAPIBase is the effective base URL for the GitHub REST API.
// Defaults to the production URL; tests override via setGitHubAPIBase.
var githubAPIBase = defaultGitHubAPIBase

// attestationHTTPClient is a dedicated client for GitHub attestation API
// requests, isolated from http.DefaultClient to avoid side effects from
// other packages modifying global state.
var attestationHTTPClient = &http.Client{}

// bundleFetchClient fetches the externalized bundle from bundle_url. Every
// redirect is revalidated against the host allowlist AND rejected if it changes
// host, so a redirect cannot hop to a different storage account even when the
// destination is itself an allowlisted Azure blob host.
var bundleFetchClient = &http.Client{
	CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) >= maxBundleRedirects {
			return fmt.Errorf("too many redirects fetching bundle_url")
		}
		if !strings.EqualFold(req.URL.Hostname(), via[0].URL.Hostname()) {
			return fmt.Errorf("cross-host redirect fetching bundle_url")
		}
		_, err := validateBundleURL(req.URL.String())
		return err
	},
}

// validateBundleURL is the effective bundle_url validator. Tests override it
// to accept a local test server; production uses defaultValidateBundleURL.
var validateBundleURL = defaultValidateBundleURL

// setGitHubAPIBase overrides the GitHub API base URL (for tests only).
func setGitHubAPIBase(base string) { githubAPIBase = base }

// VerifyProvenance fetches SLSA provenance attestations from the GitHub
// attestation API and verifies the Sigstore bundle against the public
// transparency log and expected identity.
//
// The image ref must have a resolved Digest.
func VerifyProvenance(ctx context.Context, ref ImageRef, sev *verify.Verifier, certID verify.CertificateIdentity) error {
	if ref.Digest == "" {
		return fmt.Errorf("image digest not resolved")
	}

	attestations, err := fetchGitHubAttestations(ctx, ref.Digest)
	if err != nil {
		return err
	}

	// Try each attestation -- first successful verification wins. The bundle
	// is resolved (inline, or fetched from bundle_url) only as each is tried.
	var errs []error
	for i, att := range attestations {
		bundleJSON, resolveErr := resolveBundleJSON(ctx, att)
		if resolveErr != nil {
			errs = append(errs, fmt.Errorf("attestation[%d]: %w", i, resolveErr))
			continue
		}
		if err := verifyProvenanceBundle(bundleJSON, ref.Digest, sev, certID); err != nil {
			errs = append(errs, fmt.Errorf("attestation[%d]: %w", i, err))
			continue
		}
		return nil
	}
	return fmt.Errorf("no valid SLSA provenance attestation for %s: %w", ref, errors.Join(errs...))
}

// githubAttestation represents a single attestation entry in the GitHub API
// response. GitHub externalized bundle storage: the inline bundle is now
// null and the Sigstore bundle lives at bundle_url (Snappy-compressed in
// blob storage). Older responses may still carry an inline bundle.
type githubAttestation struct {
	Bundle    json.RawMessage `json:"bundle"`
	BundleURL string          `json:"bundle_url"`
}

// githubAttestationResponse is the structure returned by the GitHub
// attestation API (GET /repos/OWNER/REPO/attestations/SUBJECT_DIGEST).
type githubAttestationResponse struct {
	Attestations []githubAttestation `json:"attestations"`
}

// fetchGitHubAttestations queries the GitHub attestation API for attestation
// entries associated with the given image digest.
func fetchGitHubAttestations(ctx context.Context, digest string) ([]githubAttestation, error) {
	body, err := fetchAttestationResponseBody(ctx, digest)
	if err != nil {
		return nil, err
	}
	return parseAttestations(body, digest)
}

// fetchAttestationResponseBody issues the API request, classifies the
// response status, and returns the body bytes capped at
// maxAttestationResponseBytes.
func fetchAttestationResponseBody(ctx context.Context, digest string) ([]byte, error) {
	apiURL := fmt.Sprintf("%s/repos/%s/attestations/%s", githubAPIBase, githubAttestationOwnerRepo, digest)
	reqCtx, cancel := context.WithTimeout(ctx, attestationHTTPTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, apiURL, nil)
	if err != nil {
		return nil, fmt.Errorf("creating attestation request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("X-GitHub-Api-Version", githubAPIVersion)
	resp, err := attestationHTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetching attestations from GitHub API: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	switch resp.StatusCode {
	case http.StatusOK:
	case http.StatusNotFound:
		return nil, fmt.Errorf("%w via GitHub API for digest %s", ErrNoProvenanceAttestations, digest)
	default:
		return nil, fmt.Errorf("GitHub attestation API returned HTTP %d for digest %s", resp.StatusCode, digest)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxAttestationResponseBytes+1))
	if err != nil {
		return nil, fmt.Errorf("reading attestation response: %w", err)
	}
	if int64(len(body)) > maxAttestationResponseBytes {
		return nil, fmt.Errorf("attestation response too large (>%d bytes)", maxAttestationResponseBytes)
	}
	return body, nil
}

// parseAttestations parses an attestation API response and returns every
// entry that carries a usable bundle -- either a non-null inline bundle or a
// bundle_url to fetch it from. digest is used only for error messages.
func parseAttestations(body []byte, digest string) ([]githubAttestation, error) {
	var apiResp githubAttestationResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("parsing attestation response: %w", err)
	}
	if len(apiResp.Attestations) == 0 {
		return nil, fmt.Errorf("%w via GitHub API for digest %s", ErrNoProvenanceAttestations, digest)
	}
	usable := make([]githubAttestation, 0, len(apiResp.Attestations))
	for _, a := range apiResp.Attestations {
		if isUsableInlineBundle(a.Bundle) || a.BundleURL != "" {
			usable = append(usable, a)
		}
	}
	if len(usable) == 0 {
		return nil, fmt.Errorf("%w (no bundle or bundle_url in response) for digest %s", ErrNoProvenanceAttestations, digest)
	}
	return usable, nil
}

// isUsableInlineBundle reports whether raw is a present, non-null inline
// bundle. A JSON null (the current API's inline value) is four bytes, so a
// length check alone would wrongly treat it as a bundle and feed null to the
// protojson parser.
func isUsableInlineBundle(raw json.RawMessage) bool {
	t := bytes.TrimSpace(raw)
	return len(t) > 0 && !bytes.Equal(t, []byte("null"))
}

// resolveBundleJSON returns the Sigstore bundle JSON for an attestation,
// preferring a still-present inline bundle and otherwise fetching and
// decompressing the externalized bundle_url.
func resolveBundleJSON(ctx context.Context, att githubAttestation) (json.RawMessage, error) {
	if isUsableInlineBundle(att.Bundle) {
		return att.Bundle, nil
	}
	if att.BundleURL == "" {
		return nil, fmt.Errorf("attestation has neither an inline bundle nor a bundle_url")
	}
	return fetchBundleURL(ctx, att.BundleURL)
}

// fetchBundleURL fetches, size-caps, and decompresses the bundle at a
// GitHub-provided bundle_url. The URL is host-allowlisted and HTTPS-only to
// keep a tampered URL from redirecting the fetch off GitHub's storage.
func fetchBundleURL(ctx context.Context, rawURL string) (json.RawMessage, error) {
	u, err := validateBundleURL(rawURL)
	if err != nil {
		return nil, err
	}
	reqCtx, cancel := context.WithTimeout(ctx, attestationHTTPTimeout)
	defer cancel()
	// Errors are reported by host only: a bundle_url can carry a scoped access
	// token in its query string, so the raw URL must never reach logs/stderr.
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, fmt.Errorf("creating bundle_url request for host %q: %w", u.Hostname(), redactURLError(err))
	}
	resp, err := bundleFetchClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetching bundle from host %q: %w", u.Hostname(), redactURLError(err))
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("bundle host %q returned HTTP %d", u.Hostname(), resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBundleFetchBytes+1))
	if err != nil {
		return nil, fmt.Errorf("reading bundle from host %q: %w", u.Hostname(), err)
	}
	if int64(len(body)) > maxBundleFetchBytes {
		return nil, fmt.Errorf("bundle from host %q too large (>%d bytes)", u.Hostname(), maxBundleFetchBytes)
	}
	return decodeBundleBody(body)
}

// decodeBundleBody returns the raw bundle JSON. A body that already begins with
// a JSON object/array is returned verbatim; otherwise it is treated as a Snappy
// block and decompressed. Sniffing the bytes rather than trusting the storage
// host's Content-Type (set at upload time, often a generic value) keeps this
// correct whatever header the host sends -- trusting the header would return a
// still-compressed payload as "JSON" and reintroduce the original parse bug.
// The declared decompressed length is bounded before allocation because
// snappy.Decode trusts the block's self-declared output length, an
// unbounded-allocation (decompression-bomb) vector on untrusted input.
func decodeBundleBody(body []byte) (json.RawMessage, error) {
	trimmed := bytes.TrimSpace(body)
	if len(trimmed) > 0 && (trimmed[0] == '{' || trimmed[0] == '[') {
		return body, nil
	}
	declaredLen, err := snappy.DecodedLen(body)
	if err != nil {
		return nil, fmt.Errorf("bundle is neither JSON nor a valid snappy block: %w", err)
	}
	if declaredLen > maxDecodedBundleBytes {
		return nil, fmt.Errorf("decompressed bundle too large (%d bytes, max %d)", declaredLen, maxDecodedBundleBytes)
	}
	decoded, err := snappy.Decode(make([]byte, 0, declaredLen), body)
	if err != nil {
		return nil, fmt.Errorf("decompressing snappy bundle: %w", err)
	}
	return decoded, nil
}

// redactURLError strips the URL from a *url.Error, returning its underlying
// cause. net/http wraps request failures in a *url.Error whose message embeds
// the full URL, which for a bundle_url can carry a scoped access token; the
// inner cause (timeout, connection refused, ...) is safe to surface.
func redactURLError(err error) error {
	var urlErr *url.Error
	for errors.As(err, &urlErr) {
		err = urlErr.Err
	}
	return err
}

// defaultValidateBundleURL confirms rawURL is an HTTPS URL on an allowed
// attestation-bundle storage host. The raw URL is never echoed in the error
// (it may carry a scoped access token in its query string).
func defaultValidateBundleURL(rawURL string) (*url.URL, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, fmt.Errorf("bundle_url is not a parseable URL")
	}
	if u.Scheme != "https" {
		return nil, fmt.Errorf("bundle_url must be https, got %q", u.Scheme)
	}
	if !isAllowedBundleURLHost(u.Hostname()) {
		return nil, fmt.Errorf("bundle_url host %q is not an allowed attestation-storage host", u.Hostname())
	}
	return u, nil
}

// isAllowedBundleURLHost reports whether host is GitHub's attestation-bundle
// storage host. GitHub serves externalized bundles from Azure Blob Storage
// (*.blob.core.windows.net). This allowlist is SSRF/DoS defence-in-depth, not
// the authenticity control -- verifyProvenanceBundle cryptographically verifies
// the Sigstore bundle regardless of origin. It matches the Azure blob suffix
// rather than a single pinned storage account so a GitHub storage-account
// rotation cannot silently break every verified pull.
func isAllowedBundleURLHost(host string) bool {
	return strings.HasSuffix(strings.ToLower(host), ".blob.core.windows.net")
}

// verifyProvenanceBundle parses and verifies a single Sigstore bundle from
// the GitHub attestation API against the expected identity and image digest.
// After cryptographic verification, it also checks that the in-toto statement
// has a SLSA provenance predicate type (not an SBOM or other attestation).
func verifyProvenanceBundle(bundleJSON json.RawMessage, digest string, sev *verify.Verifier, certID verify.CertificateIdentity) error {
	b, err := loadBundle(bundleJSON)
	if err != nil {
		return fmt.Errorf("parsing provenance bundle: %w", err)
	}

	digestAlgo, digestHex, err := parseDigest(digest)
	if err != nil {
		return err
	}

	// Verify the bundle cryptographically: check the signature, certificate
	// identity (must be our image-publishing workflow), and artifact digest.
	result, err := sev.Verify(b, verify.NewPolicy(
		verify.WithArtifactDigest(digestAlgo, digestHex),
		verify.WithCertificateIdentity(certID),
	))
	if err != nil {
		return fmt.Errorf("provenance bundle verification failed: %w", err)
	}

	// sigstore-go does not validate the in-toto predicate type -- we must
	// check it ourselves. Without this, an SBOM or other attestation signed
	// by the same workflow identity would incorrectly pass as SLSA provenance.
	if result == nil || result.Statement == nil {
		return fmt.Errorf("verification succeeded but returned no statement for predicate check")
	}
	pt := result.Statement.PredicateType
	if !strings.HasPrefix(pt, SLSAProvenancePredicatePrefix) {
		return fmt.Errorf("unexpected predicate type %q, want prefix %q", pt, SLSAProvenancePredicatePrefix)
	}

	return nil
}
