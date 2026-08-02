// Package verify provides container image signature and SLSA provenance
// verification using sigstore-go and go-containerregistry.
package verify

import (
	"fmt"
	"net/http"
	"time"

	"github.com/sigstore/sigstore-go/pkg/root"
	"github.com/sigstore/sigstore-go/pkg/tuf"
	"github.com/sigstore/sigstore-go/pkg/verify"
	"github.com/theupdateframework/go-tuf/v2/metadata/fetcher"
)

const (
	// ExpectedIssuer is the OIDC issuer for GitHub Actions keyless signing.
	ExpectedIssuer = "https://token.actions.githubusercontent.com"

	// ExpectedSANRegex matches the image-publishing signing identity from
	// the SynthOrg repo on version tags or the main branch. Only accepts
	// signatures from a workflow that actually signs -- not from arbitrary
	// workflows or feature branches.
	//
	// Keyless signing derives the SAN from job_workflow_ref, which for a
	// workflow_call job is the reusable workflow's own path rather than the
	// caller's. The signing steps live in reusable-publish-image.yml
	// (backend, sandbox, sidecar, fine-tune) and its loaded-image sibling
	// (web); build-images.yml only grants scopes and passes inputs, and can
	// never appear on a certificate. Retagging re-points a tag at an
	// already-signed digest without signing again, so a release-tagged
	// image carries the heads/main identity of the build it was cut from.
	//
	// reusable-publish-apko-base.yml is absent deliberately: it signs the
	// apko base layers, which ImageNames() does not list, so the CLI never
	// verifies one.
	//
	// docker.yml signed every image through v0.9.3 and stays accepted
	// because a published signature cannot be re-minted: dropping the name
	// would leave the stable channel unable to verify the images it pins.
	// Accepting a retired name costs nothing: the ref alternation admits
	// only heads/main and version tags, so minting a certificate under the
	// old path would require restoring that file on the default branch,
	// which already implies write access.
	//
	// This identity is cryptographically bound to the default registry
	// + repo prefix: signatures produced by Aureliolo/synthorg's publishing
	// workflow carry this SAN and cover images pushed to ghcr.io under
	// aureliolo/synthorg-*. Overriding RegistryHost/ImageRepoPrefix makes
	// verification impossible (no matching SAN), which is why custom
	// registry deployments run with signature verification disabled.
	ExpectedSANRegex = `^https://github\.com/Aureliolo/synthorg/\.github/workflows/(reusable-publish-image-loaded|reusable-publish-image|docker)\.yml@refs/(tags/v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.\-]+)?(\+[0-9A-Za-z.\-]+)?|heads/main)$`
)

// Tunable registry + timeout values. Populated by Configure at program
// start; read without locking because Configure is called exactly once
// in root.go's PersistentPreRunE before any goroutine consumes them.
var (
	// RegistryHost is the container registry hosting SynthOrg images.
	RegistryHost = "ghcr.io"

	// ImageRepoPrefix is the repository prefix for all SynthOrg images.
	ImageRepoPrefix = "aureliolo/synthorg-"

	// TUFFetchTimeout bounds the TUF metadata fetch for the trusted root.
	TUFFetchTimeout = 30 * time.Second
)

// ImageNames returns the canonical set of SynthOrg service image suffixes.
// Returns a new slice each call to prevent callers from mutating the list.
//
// Fine-tune ships in two mutually exclusive variants (gpu, cpu); both are
// listed because the signer signs both and local-image discovery must
// recognize either suffix.
func ImageNames() []string {
	return []string{"backend", "web", "sandbox", "sidecar", "fine-tune-gpu", "fine-tune-cpu"}
}

// BuildVerifier creates a Sigstore verifier using the public good trusted
// root. The verifier requires SCTs, transparency log entries, and integrated
// timestamps (sigstore-go v1.1+ requirements).
func BuildVerifier() (*verify.Verifier, error) {
	opts := tuf.DefaultOptions()
	f := fetcher.NewDefaultFetcher()
	f.SetHTTPClient(&http.Client{Timeout: TUFFetchTimeout})
	opts = opts.WithFetcher(f)

	trustedRoot, err := root.FetchTrustedRootWithOptions(opts)
	if err != nil {
		return nil, fmt.Errorf("fetching sigstore trusted root: %w", err)
	}

	sev, err := verify.NewVerifier(trustedRoot,
		verify.WithSignedCertificateTimestamps(1),
		verify.WithTransparencyLog(1),
		verify.WithIntegratedTimestamps(1),
	)
	if err != nil {
		return nil, fmt.Errorf("creating sigstore verifier: %w", err)
	}
	return sev, nil
}

// BuildIdentityPolicy creates a certificate identity policy for verifying
// container image signatures from the SynthOrg repository's CI workflows.
func BuildIdentityPolicy() (verify.CertificateIdentity, error) {
	certID, err := verify.NewShortCertificateIdentity(
		ExpectedIssuer, "",
		"", ExpectedSANRegex,
	)
	if err != nil {
		return verify.CertificateIdentity{}, fmt.Errorf("creating certificate identity: %w", err)
	}
	return certID, nil
}
