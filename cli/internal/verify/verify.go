package verify

import (
	"context"
	"fmt"
	"io"
	"regexp"
	"strings"

	protobundle "github.com/sigstore/protobuf-specs/gen/pb-go/bundle/v1"
	"github.com/sigstore/sigstore-go/pkg/bundle"
	"github.com/sigstore/sigstore-go/pkg/verify"
)

// digestPattern validates an OCI content digest (algorithm:hex).
var digestPattern = regexp.MustCompile(`^sha256:[a-f0-9]{64}$`)

// sigstoreBundle is a type alias for the sigstore-go bundle type.
type sigstoreBundle = bundle.Bundle

// newBundle creates a new empty Sigstore bundle for JSON unmarshalling.
func newBundle() *sigstoreBundle {
	return &bundle.Bundle{Bundle: new(protobundle.Bundle)}
}

// ImageRef identifies a container image with an optional resolved digest.
type ImageRef struct {
	Registry   string // e.g. "ghcr.io"
	Repository string // e.g. "aureliolo/synthorg-backend"
	Tag        string // e.g. "0.3.0" or "latest"
	Digest     string // e.g. "sha256:abc..." -- filled after resolution
}

// String returns the full image reference with tag.
// If Tag is empty, returns registry/repository without a trailing colon.
func (r ImageRef) String() string {
	if r.Tag == "" {
		return fmt.Sprintf("%s/%s", r.Registry, r.Repository)
	}
	return fmt.Sprintf("%s/%s:%s", r.Registry, r.Repository, r.Tag)
}

// DigestRef returns the full image reference pinned to its digest.
// Returns an error if Digest is empty.
func (r ImageRef) DigestRef() (string, error) {
	if r.Digest == "" {
		return "", fmt.Errorf("digest not resolved for %s", r)
	}
	return fmt.Sprintf("%s/%s@%s", r.Registry, r.Repository, r.Digest), nil
}

// Name returns the short image name suffix (e.g. "backend" from
// "aureliolo/synthorg-backend").
func (r ImageRef) Name() string {
	_, after, ok := strings.Cut(r.Repository, ImageRepoPrefix)
	if ok {
		return after
	}
	return r.Repository
}

// VerifyResult holds the outcome of verifying a single image.
// CosignVerified is always true when returned from VerifyImages because cosign
// failure returns an error -- the result is only constructed on success.
type VerifyResult struct {
	Ref                ImageRef
	ProvenanceVerified bool
}

// VerifyOptions configures the image verification behavior.
type VerifyOptions struct {
	Images   []ImageRef
	Output   io.Writer                       // user-visible progress output
	OnResult func(index int, r VerifyResult) // called after each image completes
}

// NewImageRef creates an ImageRef for a SynthOrg service image.
// name is the service name (e.g. "backend", "web", "sandbox").
func NewImageRef(name, tag string) ImageRef {
	return ImageRef{
		Registry:   RegistryHost,
		Repository: ImageRepoPrefix + name,
		Tag:        tag,
	}
}

// BuildImageRefs creates ImageRef values for the standard SynthOrg images.
// If sandbox is false, the sandbox and sidecar images are excluded: both are
// spawned by the backend through the Docker socket, which is mounted only when
// the sandbox is enabled.
// If fineTuning is true, the fine-tune image for the requested variant
// ("gpu" or "cpu") is included; empty variant defaults to "gpu".
//
// The refs returned here are the single source of truth for the verify /
// pull / pin / compose-rendering pipeline. The chosen fine-tune ref is
// propagated to the backend as SYNTHORG_FINE_TUNE_IMAGE via the compose
// template. Do NOT read SYNTHORG_FINE_TUNE_IMAGE from os.Getenv in the
// CLI; an operator-supplied value would bypass signature/provenance
// verification and split the verify/run trust chain for this feature.
func BuildImageRefs(tag string, sandbox bool, fineTuning bool, fineTuneVariant string) []ImageRef {
	refs := []ImageRef{
		NewImageRef("backend", tag),
		NewImageRef("web", tag),
	}
	if sandbox {
		refs = append(refs, NewImageRef("sandbox", tag), NewImageRef("sidecar", tag))
		if fineTuning {
			refs = append(refs, NewImageRef(FineTuneServiceName(fineTuneVariant), tag))
		}
	}
	return refs
}

// FineTuneServiceName returns the service/image suffix for the requested
// fine-tune variant. Accepts "gpu" or "cpu"; any other value (including the
// empty string) falls back to "gpu", matching the CLI default.
func FineTuneServiceName(variant string) string {
	if variant == "cpu" {
		return "fine-tune-cpu"
	}
	return "fine-tune-gpu"
}

// FormatImageRef returns the fully-qualified reference for a SynthOrg image.
// If digest is a valid sha256 OCI digest it is used (repo@digest); otherwise
// the ref falls back to repo:tag. Shared by compose template rendering and
// the CLI start flow so both pick up the same pin source of truth.
func FormatImageRef(name, tag, digest string) string {
	repo := RegistryHost + "/" + ImageRepoPrefix + name
	if IsValidDigest(digest) {
		return repo + "@" + digest
	}
	return repo + ":" + tag
}

// IsValidDigest reports whether d is a valid sha256 OCI digest.
func IsValidDigest(d string) bool {
	return digestPattern.MatchString(d)
}

// VerifyImages verifies cosign signatures and SLSA provenance for all images
// in opts. Returns verified results with resolved digests, or an error if
// any verification fails.
//
// A single Sigstore verifier and identity policy is built once and reused
// across all images to avoid redundant TUF root fetches.
//
// Callers are responsible for checking skip conditions (e.g. --skip-verify)
// before calling this function.
//
// Progress is printed to opts.Output during verification.
func VerifyImages(ctx context.Context, opts VerifyOptions) ([]VerifyResult, error) {
	if len(opts.Images) == 0 {
		return nil, nil
	}

	w := opts.Output
	if w == nil {
		w = io.Discard
	}

	// Build verifier and identity once -- reuse for all images.
	sev, err := BuildVerifier()
	if err != nil {
		return nil, fmt.Errorf("building sigstore verifier: %w", err)
	}
	certID, err := BuildIdentityPolicy()
	if err != nil {
		return nil, fmt.Errorf("building identity policy: %w", err)
	}

	results := make([]VerifyResult, 0, len(opts.Images))
	for i, img := range opts.Images {
		result, err := verifyOneImage(ctx, img, sev, certID, w)
		if err != nil {
			return nil, fmt.Errorf("verifying %s: %w", img, err)
		}
		results = append(results, result)
		if opts.OnResult != nil {
			opts.OnResult(i, result)
		}
	}
	return results, nil
}

// verifyOneImage resolves the digest and verifies cosign + SLSA for one image.
func verifyOneImage(ctx context.Context, ref ImageRef, sev *verify.Verifier, certID verify.CertificateIdentity, w io.Writer) (VerifyResult, error) {
	_, _ = fmt.Fprintf(w, "Verifying %s...\n", ref)

	// Step 1: Resolve tag to digest.
	digest, err := ResolveDigest(ctx, ref)
	if err != nil {
		return VerifyResult{}, fmt.Errorf("resolving digest: %w", err)
	}
	ref.Digest = digest
	_, _ = fmt.Fprintf(w, "  Resolved digest: %s\n", digest)

	// Step 2: Verify cosign signature.
	if err := VerifyCosignSignature(ctx, ref, sev, certID); err != nil {
		return VerifyResult{}, fmt.Errorf("cosign signature: %w", err)
	}
	_, _ = fmt.Fprintf(w, "  Cosign signature: verified\n")

	// Step 3: Verify SLSA provenance via GitHub attestation API.
	// Both missing attestations and cryptographic failures are hard errors.
	if err := VerifyProvenance(ctx, ref, sev, certID); err != nil {
		return VerifyResult{}, fmt.Errorf("SLSA provenance: %w", err)
	}
	_, _ = fmt.Fprintf(w, "  SLSA provenance:  verified\n")

	_, _ = fmt.Fprintf(w, "  %s: OK\n", ref.Name())

	return VerifyResult{
		Ref:                ref,
		ProvenanceVerified: true,
	}, nil
}
