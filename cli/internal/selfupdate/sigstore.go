package selfupdate

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	protobundle "github.com/sigstore/protobuf-specs/gen/pb-go/bundle/v1"
	"github.com/sigstore/sigstore-go/pkg/bundle"
	"github.com/sigstore/sigstore-go/pkg/root"
	"github.com/sigstore/sigstore-go/pkg/tuf"
	"github.com/sigstore/sigstore-go/pkg/verify"
	"github.com/theupdateframework/go-tuf/v2/metadata/fetcher"

	ociverify "github.com/Aureliolo/synthorg/cli/internal/verify"
)

const (
	// expectedSANRegex matches the CLI release signing identity: the
	// workflow holding the signing step, never a caller that invokes it.
	// Tag refs only, because a release archive is only ever cut from a v*
	// tag. verify-cli.yml delegates to the signer and cannot appear on a
	// certificate.
	//
	// cli.yml is the retired signer, bounded to the versions it actually
	// signed so it cannot vouch for a release that does not exist yet. Its
	// signatures cannot be re-minted, and the path that still needs it is
	// real: a binary built from source reports version "dev", which the
	// updater always treats as out of date, so it verifies the current
	// stable release, and that release carries this name.
	//
	// The SAN is only half the identity. Repository binding lives in
	// verify.BuildReleaseIdentityPolicy, because a reusable workflow's SAN
	// is shared by every caller on GitHub.
	expectedSANRegex = `^https://github\.com/Aureliolo/synthorg/\.github/workflows/(?:reusable-release-cli\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?|cli\.yml@refs/tags/v0\.(?:[0-8]\.[0-9]+|9\.[0-3])(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?)$`
)

// tufFetchTimeout bounds the TUF metadata fetch for the trusted root. Set
// by Configure; defaults to 30s.
var tufFetchTimeout = 30 * time.Second

// verifySigstoreBundle verifies the Sigstore bundle for checksums.txt.
// It checks that:
// 1. The bundle signature is valid over the artifact digest
// 2. The signing certificate chains to Sigstore's trusted root
// 3. The signer identity matches the expected GitHub Actions workflow
// 4. The artifact digest matches the actual checksums data
// 5. If the bundle contains a DSSE envelope, the predicate type is SLSA provenance
func verifySigstoreBundle(checksumData, bundleData []byte) error {
	b, err := loadBundleFromJSON(bundleData)
	if err != nil {
		return fmt.Errorf("parsing sigstore bundle: %w", err)
	}

	// Use Sigstore's public good trusted root with a bounded HTTP timeout.
	opts := tuf.DefaultOptions()
	f := fetcher.NewDefaultFetcher()
	f.SetHTTPClient(&http.Client{Timeout: tufFetchTimeout})
	opts = opts.WithFetcher(f)
	trustedRoot, err := root.FetchTrustedRootWithOptions(opts)
	if err != nil {
		return fmt.Errorf("fetching sigstore trusted root: %w", err)
	}

	// Build verifier requiring SCTs, transparency log entries, and
	// integrated timestamps (required by sigstore-go v1.1+).
	sev, err := verify.NewVerifier(trustedRoot,
		verify.WithSignedCertificateTimestamps(1),
		verify.WithTransparencyLog(1),
		verify.WithIntegratedTimestamps(1),
	)
	if err != nil {
		return fmt.Errorf("creating sigstore verifier: %w", err)
	}

	// Build identity policy -- must match GitHub Actions OIDC from our repo.
	certID, err := ociverify.BuildReleaseIdentityPolicy(expectedSANRegex)
	if err != nil {
		return fmt.Errorf("creating certificate identity: %w", err)
	}

	// Compute the artifact digest for verification.
	digest := sha256.Sum256(checksumData)

	// Verify the bundle against the identity and artifact digest.
	_, err = sev.Verify(b, verify.NewPolicy(
		verify.WithArtifactDigest("sha256", digest[:]),
		verify.WithCertificateIdentity(certID),
	))
	if err != nil {
		return fmt.Errorf("bundle verification failed: %w", err)
	}

	// Verify SLSA provenance predicate type if bundle contains a DSSE envelope.
	if err := assertSLSAProvenance(b); err != nil {
		return fmt.Errorf("SLSA provenance check: %w", err)
	}

	return nil
}

// Constants for SLSA validation are imported from the verify package to avoid
// duplication. The slsaStatement struct is defined locally (trivial one-field
// struct for JSON unmarshalling -- not worth exporting from verify).

// slsaStatement is a minimal in-toto statement for predicate type extraction.
type slsaStatement struct {
	PredicateType string `json:"predicateType"`
}

// assertSLSAProvenance checks that the bundle contains a DSSE envelope with
// a SLSA provenance predicate. If the bundle does not contain a DSSE envelope
// (i.e. it's a plain message signature), this is a no-op -- SLSA provenance
// is additive assurance, not a hard requirement for older bundles.
func assertSLSAProvenance(b *bundle.Bundle) error {
	env := b.GetDsseEnvelope()
	if env == nil {
		// Bundle uses message signature, not DSSE -- no provenance to check.
		return nil
	}

	if env.PayloadType != ociverify.DSSEPayloadType {
		return fmt.Errorf("unexpected DSSE payload type %q, want %q", env.PayloadType, ociverify.DSSEPayloadType)
	}

	// The protobuf DSSE envelope stores Payload as raw bytes.
	var stmt slsaStatement
	if err := json.Unmarshal(env.Payload, &stmt); err != nil {
		return fmt.Errorf("parsing in-toto statement: %w", err)
	}

	if !strings.HasPrefix(stmt.PredicateType, ociverify.SLSAProvenancePredicatePrefix) {
		return fmt.Errorf("unexpected predicate type %q, want prefix %q", stmt.PredicateType, ociverify.SLSAProvenancePredicatePrefix)
	}

	return nil
}

// loadBundleFromJSON parses a Sigstore bundle from JSON bytes.
func loadBundleFromJSON(data []byte) (*bundle.Bundle, error) {
	b := &bundle.Bundle{Bundle: new(protobundle.Bundle)}
	if err := b.UnmarshalJSON(data); err != nil {
		return nil, err
	}
	return b, nil
}
