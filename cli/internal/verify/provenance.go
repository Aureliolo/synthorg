package verify

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

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

	// maxAttestationResponseBytes caps the GitHub attestation API response
	// to prevent memory exhaustion from malicious or oversized responses.
	maxAttestationResponseBytes = 5 << 20 // 5MB
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

	bundles, err := fetchGitHubAttestations(ctx, ref.Digest)
	if err != nil {
		return err
	}

	// Try each attestation bundle -- first successful verification wins.
	var errs []error
	for i, bundleJSON := range bundles {
		if err := verifyProvenanceBundle(bundleJSON, ref.Digest, sev, certID); err != nil {
			errs = append(errs, fmt.Errorf("attestation[%d]: %w", i, err))
			continue
		}
		return nil
	}
	return fmt.Errorf("no valid SLSA provenance attestation for %s: %w", ref, errors.Join(errs...))
}

// githubAttestation represents a single attestation entry in the GitHub API response.
type githubAttestation struct {
	Bundle json.RawMessage `json:"bundle"`
}

// githubAttestationResponse is the structure returned by the GitHub
// attestation API (GET /repos/OWNER/REPO/attestations/SUBJECT_DIGEST).
type githubAttestationResponse struct {
	Attestations []githubAttestation `json:"attestations"`
}

// fetchGitHubAttestations queries the GitHub attestation API for Sigstore
// bundles associated with the given image digest.
func fetchGitHubAttestations(ctx context.Context, digest string) ([]json.RawMessage, error) {
	body, err := fetchAttestationResponseBody(ctx, digest)
	if err != nil {
		return nil, err
	}
	return parseAttestationBundles(body, digest)
}

// fetchAttestationResponseBody issues the API request, classifies the
// response status, and returns the body bytes capped at
// maxAttestationResponseBytes.
func fetchAttestationResponseBody(ctx context.Context, digest string) ([]byte, error) {
	url := fmt.Sprintf("%s/repos/%s/attestations/%s", githubAPIBase, githubAttestationOwnerRepo, digest)
	reqCtx, cancel := context.WithTimeout(ctx, attestationHTTPTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("creating attestation request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
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

// parseAttestationBundles parses an attestation API response and returns
// every non-empty bundle. digest is used only for error messages.
func parseAttestationBundles(body []byte, digest string) ([]json.RawMessage, error) {
	var apiResp githubAttestationResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("parsing attestation response: %w", err)
	}
	if len(apiResp.Attestations) == 0 {
		return nil, fmt.Errorf("%w via GitHub API for digest %s", ErrNoProvenanceAttestations, digest)
	}
	bundles := make([]json.RawMessage, 0, len(apiResp.Attestations))
	for _, a := range apiResp.Attestations {
		if len(a.Bundle) > 0 {
			bundles = append(bundles, a.Bundle)
		}
	}
	if len(bundles) == 0 {
		return nil, fmt.Errorf("%w (no bundles in response) for digest %s", ErrNoProvenanceAttestations, digest)
	}
	return bundles, nil
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
	// identity (must be our docker.yml workflow), and artifact digest.
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
