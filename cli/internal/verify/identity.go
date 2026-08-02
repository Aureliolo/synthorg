// Package verify provides container image signature and SLSA provenance
// verification using sigstore-go and go-containerregistry, and holds the
// signing identities the CLI trusts for both artefact classes it verifies:
// the images it runs and the release archives it installs.
package verify

import (
	"fmt"
	"net/http"
	"time"

	"github.com/sigstore/sigstore-go/pkg/fulcio/certificate"
	"github.com/sigstore/sigstore-go/pkg/root"
	"github.com/sigstore/sigstore-go/pkg/tuf"
	"github.com/sigstore/sigstore-go/pkg/verify"
	"github.com/theupdateframework/go-tuf/v2/metadata/fetcher"
)

const (
	// ExpectedIssuer is the OIDC issuer for GitHub Actions keyless signing.
	ExpectedIssuer = "https://token.actions.githubusercontent.com"

	// ExpectedSourceRepositoryURI, ExpectedSourceRepositoryID and
	// ExpectedRunnerEnvironment bind a certificate to this repository.
	//
	// The SAN alone cannot. Keyless signing derives it from
	// job_workflow_ref, which for a workflow_call job names the reusable
	// workflow itself, so it is identical for every caller on GitHub. This
	// repository is public, so anyone may call our reusable workflows from
	// their own and obtain a certificate whose SAN matches ExpectedSANRegex
	// exactly, with their own code in the build. These extensions are what
	// separates a build this repository ran from one that merely used its
	// workflow as a recipe.
	//
	// The numeric identifier is pinned alongside the URI because it survives
	// a rename or transfer, either of which would otherwise free the URI for
	// someone else to claim.
	ExpectedSourceRepositoryURI = "https://github.com/Aureliolo/synthorg"
	ExpectedSourceRepositoryID  = "1168268477"
	ExpectedRunnerEnvironment   = "github-hosted"

	// ExpectedSANRegex matches the image-publishing signing identity: the
	// workflow holding the signing step, never a caller that invokes it.
	// docs/security.md maps each reusable workflow to what it signs.
	//
	// Only refs/heads/main is accepted. Every image publish job is gated to
	// main, and retagging re-points a tag at an already-signed digest
	// without signing again, so a release-tagged image carries the main-ref
	// identity of the build it was cut from. No published image carries a
	// tag ref.
	//
	// docker.yml is the retired signer. Its signatures cannot be re-minted
	// and a pinned image_tag still installs images carrying them, so
	// dropping the name would make those images permanently unverifiable.
	//
	// reusable-publish-apko-base.yml is absent deliberately: it signs the
	// apko base layers, which ImageNames() does not list, so the CLI never
	// resolves or verifies one.
	//
	// This identity is cryptographically bound to the default registry
	// + repo prefix: signatures produced by Aureliolo/synthorg's publishing
	// workflow carry this SAN and cover images pushed to ghcr.io under
	// aureliolo/synthorg-*. Overriding RegistryHost/ImageRepoPrefix makes
	// verification impossible (no matching SAN), which is why custom
	// registry deployments run with signature verification disabled.
	ExpectedSANRegex = `^https://github\.com/Aureliolo/synthorg/\.github/workflows/(?:reusable-publish-image-loaded\.yml@refs/heads/main|reusable-publish-image\.yml@refs/heads/main|docker\.yml@refs/heads/main)$`

	// ExpectedReleaseSANRegex matches the CLI release signing identity, on
	// the same terms as ExpectedSANRegex and for the same reasons. Tag refs
	// only, because a release archive is only ever cut from a v* tag;
	// verify-cli.yml delegates to the signer and cannot appear on a
	// certificate.
	//
	// cli.yml is the retired signer, bounded to the versions it actually
	// signed so it cannot vouch for a release that does not exist yet. Its
	// signatures cannot be re-minted, and the path that still needs it is
	// real: a binary built from source reports version "dev", which the
	// updater always treats as out of date, so it verifies the current
	// stable release, and that release carries this name.
	//
	// It lives here, beside the pattern for the other artefact class, rather
	// than in the package that consumes it. Both are trust anchors of the
	// same kind, and a constructor that took one as an argument would be a
	// constructor whose caller decides what the binary trusts.
	ExpectedReleaseSANRegex = `^https://github\.com/Aureliolo/synthorg/\.github/workflows/(?:reusable-release-cli\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?|cli\.yml@refs/tags/v0\.(?:[0-8]\.[0-9]+|9\.[0-3])(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?)$`
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
	return []string{"backend", "web", "sandbox", "sidecar", "openhands", "fine-tune-gpu", "fine-tune-cpu"}
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
// signatures produced by this repository's publishing workflows.
//
// The policy pairs the SAN regex with repository-binding extensions;
// CompareExtensions ignores an empty expected field, so every field set here
// is an additional requirement rather than a replacement for the SAN.
func BuildIdentityPolicy() (verify.CertificateIdentity, error) {
	return buildIdentityPolicyFor(ExpectedSANRegex)
}

// buildIdentityPolicyFor builds a repository-bound identity around sanRegex.
// Shared with the self-update path so both consumers of this repository's
// keyless signatures enforce the same binding.
func buildIdentityPolicyFor(sanRegex string) (verify.CertificateIdentity, error) {
	san, err := verify.NewSANMatcher("", sanRegex)
	if err != nil {
		return verify.CertificateIdentity{}, fmt.Errorf("creating SAN matcher: %w", err)
	}
	issuer, err := verify.NewIssuerMatcher(ExpectedIssuer, "")
	if err != nil {
		return verify.CertificateIdentity{}, fmt.Errorf("creating issuer matcher: %w", err)
	}
	certID, err := verify.NewCertificateIdentity(san, issuer, certificate.Extensions{
		SourceRepositoryURI:        ExpectedSourceRepositoryURI,
		SourceRepositoryIdentifier: ExpectedSourceRepositoryID,
		RunnerEnvironment:          ExpectedRunnerEnvironment,
	})
	if err != nil {
		return verify.CertificateIdentity{}, fmt.Errorf("creating certificate identity: %w", err)
	}
	return certID, nil
}

// BuildReleaseIdentityPolicy creates the identity policy for release archives
// signed by this repository, for the self-update path.
//
// It takes no argument on purpose. A caller-supplied SAN regex cannot be
// validated into safety by inspecting parts of it: a pattern carrying a
// second top-level alternative naming an unapproved workflow still opens with
// the repository prefix and still closes with the anchor, so a prefix-and-
// suffix check passes it, and the repository extensions do not help when both
// alternatives name this repository. Compiling the pattern in leaves nothing
// for a later caller to decide.
func BuildReleaseIdentityPolicy() (verify.CertificateIdentity, error) {
	return buildIdentityPolicyFor(ExpectedReleaseSANRegex)
}
