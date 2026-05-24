// Package verify: DHI (Docker Hardened Images) verification.
//
// DHI images at dhi.io are signed with Docker's cosign key (ECDSA P-256).
// Attestations (SLSA provenance) are discoverable via OCI referrers on
// the platform-specific manifest digest at dhi.io.
//
// The embedded public key is pinned by SHA-256 fingerprint and validated
// at parse time. If Docker rotates the key, fingerprint verification
// fails and a CLI update ships the new key.
package verify

import (
	"context"
	"crypto/ecdsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"runtime"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/google/go-containerregistry/pkg/authn"
	"github.com/google/go-containerregistry/pkg/name"
	v1 "github.com/google/go-containerregistry/pkg/v1"
	"github.com/google/go-containerregistry/pkg/v1/remote"
)

// ── Key material ────────────────────────────────────────────────────

const (
	// DHIPublicKeyURL is Docker's cosign public key (reference).
	DHIPublicKeyURL = "https://registry.scout.docker.com/keyring/dhi/latest.pub"

	// DHIPublicKeyFingerprint is the SHA-256 of the embedded key.
	DHIPublicKeyFingerprint = "1d02bbccf149283ae6288d96264dcad3fb23ee1911d90324a48eab28e4cb8a5f"
)

// dhiRegistry is the DHI image registry. Set via Configure; defaults to
// "dhi.io". Overriding it invalidates the dhiPinnedIndexDigests lookup
// because digests are keyed on "dhi.io/postgres:..." etc., so consumers
// must skip verification when this differs from the default.
var dhiRegistry = "dhi.io"

// postgresImageTag / natsImageTag are the third-party image tags. Used
// both for pre-flight auth checks and for consumers (compose template)
// that need to reference the exact image without duplicating the string.
//
// Default values come from config.Default*ImageTag (the single source of
// truth that Renovate keeps current); Configure() overrides them with the
// operator-resolved State value at startup.
var (
	postgresImageTag = config.DefaultPostgresImageTag
	natsImageTag     = config.DefaultNATSImageTag
)

// dhiEmbeddedPublicKeyPEM is Docker's DHI cosign public key (ECDSA P-256).
// Pinned in the binary. Source: DHIPublicKeyURL
// Archived: https://github.com/docker-hardened-images/keyring
var dhiEmbeddedPublicKeyPEM = []byte(`-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEKdROmntRJFBrOJOQF5ww6gDBJqGm
Fxa4333s1KsL9ISjtmRzGNih9lNRsqfRVjgFgJIdL6EQ9dohdanvn7r2cg==
-----END PUBLIC KEY-----
`)

// dhiPinnedIndexDigests maps "registry/repo:tag" to the pinned multi-arch
// INDEX digest. At runtime, the CLI resolves this to the platform-specific
// manifest, then discovers attestations via the OCI referrers API.
//
// Both keys and values are derived from the canonical Default*Image{Tag,Digest}
// constants in cli/internal/config/state.go so a Renovate bump touches one
// file and one regex match per dependency. The literal "dhi.io" prefix is
// intentional: overriding dhiRegistry invalidates the lookup (verification
// is skipped for non-default registries; see the dhiRegistry doc comment).
var dhiPinnedIndexDigests = map[string]string{
	"dhi.io/postgres:" + config.DefaultPostgresImageTag: config.DefaultPostgresImageDigest,
	"dhi.io/nats:" + config.DefaultNATSImageTag:         config.DefaultNATSImageDigest,
}

// DHIPinnedIndexDigest returns the pinned index digest for a DHI image.
// Used by the cache to detect when Renovate bumps a digest (cache miss
// triggers re-verification).
func DHIPinnedIndexDigest(image string) (string, bool) {
	d, ok := dhiPinnedIndexDigests[image]
	return d, ok
}

// ── Public types ────────────────────────────────────────────────────

// DHIVerifyResult holds the outcome of verifying a single DHI image.
type DHIVerifyResult struct {
	Image         string
	SigOK         bool
	SLSAOK        bool
	Digest        string // resolved platform manifest digest
	AttDigest     string // discovered SLSA attestation digest
	SigDigest     string // discovered cosign signature digest
	RekorLogIndex int64  // Rekor transparency log index (-1 if not verified)
	SigErr        error
	SLSAErr       error
}

// ── Entry point ─────────────────────────────────────────────────────

// VerifyDHIImages verifies cosign signatures for DHI images using the
// embedded public key. Attestations are fetched from Docker Scout's
// registry (registry.scout.docker.com).
//
// Returns an error if any signature verification fails (hard stop).
func VerifyDHIImages(ctx context.Context, images []string) ([]DHIVerifyResult, error) {
	if len(images) == 0 {
		return nil, nil
	}

	// Pre-flight: verify Docker credentials for dhi.io are available.
	if err := checkDHIAuth(ctx); err != nil {
		return nil, err
	}

	pubKey, err := parseDHIPublicKey(dhiEmbeddedPublicKeyPEM)
	if err != nil {
		return nil, fmt.Errorf("parsing embedded DHI key: %w", err)
	}

	results := make([]DHIVerifyResult, 0, len(images))
	for _, img := range images {
		r := verifyOneDHI(ctx, img, pubKey)
		results = append(results, r)
		if !r.SigOK {
			return results, fmt.Errorf("DHI signature verification failed for %s: %w", img, r.SigErr)
		}
	}
	return results, nil
}

// ── Per-image verification ──────────────────────────────────────────

func verifyOneDHI(ctx context.Context, image string, pubKey *ecdsa.PublicKey) DHIVerifyResult {
	r := DHIVerifyResult{Image: image, RekorLogIndex: -1}

	// 1. Look up pinned index digest.
	indexDigest, ok := dhiPinnedIndexDigests[image]
	if !ok {
		r.SigErr = fmt.Errorf("no pinned digest for %s -- update the CLI", image)
		return r
	}

	// 2. Resolve index -> platform manifest.
	_, repo, _, err := parseDHIRef(image)
	if err != nil {
		r.SigErr = err
		return r
	}
	platformDigest, err := resolveIndexToPlatform(ctx, repo, indexDigest)
	if err != nil {
		r.SigErr = fmt.Errorf("resolving platform manifest: %w", err)
		return r
	}
	r.Digest = platformDigest

	// 3. Discover SLSA v1 provenance attestation via referrers API.
	attDigest, err := findSLSAv1Attestation(ctx, repo, platformDigest)
	if err != nil {
		r.SLSAErr = err
		r.SigErr = fmt.Errorf("discovering SLSA attestation: %w", err)
		return r
	}
	r.AttDigest = attDigest

	// 4. Fetch attestation and verify its subject + in-toto content.
	if err := verifyAttestationContent(ctx, repo, attDigest, platformDigest); err != nil {
		r.SLSAErr = err
		r.SigErr = fmt.Errorf("attestation verification: %w", err)
		return r
	}
	r.SLSAOK = true

	// 5. Find cosign signature on the attestation via referrers.
	sigDesc, err := findCosignSignatureOnAttestation(ctx, repo, attDigest)
	if err != nil {
		r.SigErr = fmt.Errorf("discovering cosign signature: %w", err)
		return r
	}
	r.SigDigest = sigDesc.Digest.String()

	// 6. Verify cosign signature with embedded DHI key + Rekor.
	rekorIdx, err := verifyCosignDHISignature(ctx, repo, sigDesc, attDigest, pubKey)
	if err != nil {
		r.SigErr = fmt.Errorf("signature verification: %w", err)
		return r
	}
	r.SigOK = true
	r.RekorLogIndex = rekorIdx

	return r
}

// ── Index resolution ───────────────────────────────────────────────

// resolveIndexToPlatform fetches the multi-arch index at indexDigest and
// returns the platform-specific manifest digest for linux/GOARCH.
func resolveIndexToPlatform(ctx context.Context, repo, indexDigest string) (string, error) {
	ref := fmt.Sprintf("%s/%s@%s", dhiRegistry, repo, indexDigest)
	parsed, err := name.NewDigest(ref)
	if err != nil {
		return "", fmt.Errorf("parsing index ref: %w", err)
	}

	idx, err := remote.Index(parsed, dhiRemoteOpts(ctx)...)
	if err != nil {
		return "", fmt.Errorf("fetching index: %w", err)
	}

	idxManifest, err := idx.IndexManifest()
	if err != nil {
		return "", fmt.Errorf("reading index manifest: %w", err)
	}

	arch := runtime.GOARCH
	for _, desc := range idxManifest.Manifests {
		if desc.Platform != nil && desc.Platform.OS == "linux" && desc.Platform.Architecture == arch {
			return desc.Digest.String(), nil
		}
	}
	return "", fmt.Errorf("no linux/%s manifest in index %s", arch, indexDigest[:16])
}

// ── Attestation discovery ──────────────────────────────────────────

// findSLSAv1Attestation queries OCI referrers on the platform manifest
// and returns the digest of the SLSA provenance v1 attestation.
func findSLSAv1Attestation(ctx context.Context, repo, platformDigest string) (string, error) {
	ref := fmt.Sprintf("%s/%s@%s", dhiRegistry, repo, platformDigest)
	parsed, err := name.NewDigest(ref)
	if err != nil {
		return "", fmt.Errorf("parsing digest ref: %w", err)
	}

	referrerIdx, err := remote.Referrers(parsed, dhiRemoteOpts(ctx)...)
	if err != nil {
		return "", fmt.Errorf("querying referrers: %w", err)
	}

	idxManifest, err := referrerIdx.IndexManifest()
	if err != nil {
		return "", fmt.Errorf("reading referrer index: %w", err)
	}

	for _, desc := range idxManifest.Manifests {
		pt := desc.Annotations["in-toto.io/predicate-type"]
		if pt == dhiSLSAv1PredicateType {
			return desc.Digest.String(), nil
		}
	}
	return "", fmt.Errorf("no SLSA v1 attestation found among %d referrers", len(idxManifest.Manifests))
}

// findCosignSignatureOnAttestation queries OCI referrers on the
// attestation digest and returns the descriptor of the cosign signature.
func findCosignSignatureOnAttestation(ctx context.Context, repo, attDigest string) (v1.Descriptor, error) {
	ref := fmt.Sprintf("%s/%s@%s", dhiRegistry, repo, attDigest)
	parsed, err := name.NewDigest(ref)
	if err != nil {
		return v1.Descriptor{}, fmt.Errorf("parsing attestation ref: %w", err)
	}

	referrerIdx, err := remote.Referrers(parsed, dhiRemoteOpts(ctx)...)
	if err != nil {
		return v1.Descriptor{}, fmt.Errorf("querying attestation referrers: %w", err)
	}

	idxManifest, err := referrerIdx.IndexManifest()
	if err != nil {
		return v1.Descriptor{}, fmt.Errorf("reading referrer index: %w", err)
	}

	for _, desc := range idxManifest.Manifests {
		if desc.ArtifactType == dhiCosignSigArtifactType {
			return desc, nil
		}
	}
	return v1.Descriptor{}, fmt.Errorf("no cosign signature found on attestation %s", attDigest[:16])
}

// ── Attestation content verification ───────────────────────────────

// verifyAttestationContent fetches the attestation image, checks that
// its subject matches the expected platform digest, and validates the
// in-toto statement layer has a SLSA v1 provenance predicate.
func verifyAttestationContent(ctx context.Context, repo, attDigest, expectedPlatformDigest string) error {
	ref := fmt.Sprintf("%s/%s@%s", dhiRegistry, repo, attDigest)
	parsed, err := name.NewDigest(ref)
	if err != nil {
		return fmt.Errorf("parsing ref: %w", err)
	}
	img, err := remote.Image(parsed, dhiRemoteOpts(ctx)...)
	if err != nil {
		return fmt.Errorf("fetching attestation: %w", err)
	}
	if err := verifyAttestationSubject(img, expectedPlatformDigest); err != nil {
		return err
	}
	stmtBytes, err := readAttestationStatement(img)
	if err != nil {
		return err
	}
	return verifyInTotoStatement(stmtBytes, expectedPlatformDigest)
}

// verifyAttestationSubject reads the attestation manifest and asserts
// its Subject digest matches the expected platform manifest digest.
func verifyAttestationSubject(img v1.Image, expectedPlatformDigest string) error {
	manifest, err := img.Manifest()
	if err != nil {
		return fmt.Errorf("reading manifest: %w", err)
	}
	if manifest.Subject == nil {
		return fmt.Errorf("attestation has no subject field")
	}
	if manifest.Subject.Digest.String() != expectedPlatformDigest {
		return fmt.Errorf("subject mismatch: got %s, want %s",
			manifest.Subject.Digest.String()[:16], expectedPlatformDigest[:16])
	}
	return nil
}

// readAttestationStatement reads the first attestation layer (capped at
// maxBundleBytes) and returns the raw in-toto statement bytes.
func readAttestationStatement(img v1.Image) ([]byte, error) {
	layers, err := img.Layers()
	if err != nil || len(layers) == 0 {
		return nil, fmt.Errorf("no layers in attestation")
	}
	reader, err := layers[0].Uncompressed()
	if err != nil {
		return nil, fmt.Errorf("reading layer: %w", err)
	}
	defer func() { _ = reader.Close() }()

	stmtBytes, err := io.ReadAll(io.LimitReader(reader, maxBundleBytes+1))
	if err != nil {
		return nil, fmt.Errorf("reading statement: %w", err)
	}
	if int64(len(stmtBytes)) > maxBundleBytes {
		return nil, fmt.Errorf("statement too large")
	}
	return stmtBytes, nil
}

// verifyInTotoStatement parses stmtBytes as an in-toto statement and
// asserts the statement type, predicate type, and subject digest match
// the SLSA v1 contract for expectedPlatformDigest.
func verifyInTotoStatement(stmtBytes []byte, expectedPlatformDigest string) error {
	var stmt inTotoStatement
	if err := json.Unmarshal(stmtBytes, &stmt); err != nil {
		return fmt.Errorf("parsing in-toto statement: %w", err)
	}
	if stmt.Type != dhiInTotoStatementType {
		return fmt.Errorf("unexpected statement type %q", stmt.Type)
	}
	if stmt.PredicateType != dhiSLSAv1PredicateType {
		return fmt.Errorf("unexpected predicate type %q, want %s", stmt.PredicateType, dhiSLSAv1PredicateType)
	}
	for _, subj := range stmt.Subject {
		for algo, hash := range subj.Digest {
			if fmt.Sprintf("%s:%s", algo, hash) == expectedPlatformDigest {
				return nil
			}
		}
	}
	return fmt.Errorf("platform digest %s not found in statement subjects", expectedPlatformDigest[:16])
}

// ── Cosign signature verification ──────────────────────────────────

// errSkipCosignLayer is returned by verifyCosignLayer when the caller
// should try the next layer (e.g. missing annotation, invalid signature,
// payload does not reference our attestation).
var errSkipCosignLayer = errors.New("skip cosign layer")

// verifyCosignDHISignature fetches the cosign signature image, extracts
// the simplesigning payload and ECDSA signature, and verifies it against
// the embedded DHI public key. Also verifies the Rekor transparency log
// entry for tamper-evident timestamping.
//
// Returns the Rekor log index on success.
func verifyCosignDHISignature(ctx context.Context, repo string, sigDesc v1.Descriptor, attDigest string, pubKey *ecdsa.PublicKey) (int64, error) {
	sigImg, sigManifest, err := fetchCosignSignatureImage(ctx, repo, sigDesc, attDigest)
	if err != nil {
		return -1, err
	}
	layers, err := sigImg.Layers()
	if err != nil || len(layers) == 0 {
		return -1, fmt.Errorf("no layers in signature image")
	}
	// Try each layer; first valid signature wins.
	for i := range sigManifest.Layers {
		logIndex, err := verifyCosignLayer(layers[i], sigManifest.Layers[i], attDigest, pubKey)
		if err == nil {
			return logIndex, nil
		}
		if !errors.Is(err, errSkipCosignLayer) {
			return -1, err
		}
	}
	return -1, fmt.Errorf("no valid cosign signature verified with DHI key")
}

// fetchCosignSignatureImage resolves the signature ref, fetches the
// image, and validates the manifest Subject equals attDigest before
// returning the layers manifest.
func fetchCosignSignatureImage(ctx context.Context, repo string, sigDesc v1.Descriptor, attDigest string) (v1.Image, *v1.Manifest, error) {
	ref := fmt.Sprintf("%s/%s@%s", dhiRegistry, repo, sigDesc.Digest.String())
	parsed, err := name.NewDigest(ref)
	if err != nil {
		return nil, nil, fmt.Errorf("parsing sig ref: %w", err)
	}
	sigImg, err := remote.Image(parsed, dhiRemoteOpts(ctx)...)
	if err != nil {
		return nil, nil, fmt.Errorf("fetching signature image: %w", err)
	}
	sigManifest, err := sigImg.Manifest()
	if err != nil {
		return nil, nil, fmt.Errorf("reading signature manifest: %w", err)
	}
	if sigManifest.Subject == nil {
		return nil, nil, fmt.Errorf("signature has no subject field")
	}
	if sigManifest.Subject.Digest.String() != attDigest {
		return nil, nil, fmt.Errorf("signature subject %s does not match attestation %s",
			sigManifest.Subject.Digest.String()[:16], attDigest[:16])
	}
	return sigImg, sigManifest, nil
}

// verifyCosignLayer validates one signature layer. Returns the Rekor
// log index on success. Returns errSkipCosignLayer if this layer should
// be skipped (the caller iterates to the next). Returns any other error
// to halt the search (Rekor verification failure is terminal).
func verifyCosignLayer(layer v1.Layer, desc v1.Descriptor, attDigest string, pubKey *ecdsa.PublicKey) (int64, error) {
	sigB64 := desc.Annotations["dev.cosignproject.cosign/signature"]
	if sigB64 == "" {
		return -1, errSkipCosignLayer
	}
	sigBytes, err := base64.StdEncoding.DecodeString(sigB64)
	if err != nil {
		return -1, errSkipCosignLayer
	}
	payload, err := readCosignPayload(layer)
	if err != nil {
		return -1, errSkipCosignLayer
	}
	payloadHash := sha256.Sum256(payload)
	if !ecdsa.VerifyASN1(pubKey, payloadHash[:], sigBytes) {
		return -1, errSkipCosignLayer
	}
	var ss simpleSigningPayload
	if err := json.Unmarshal(payload, &ss); err != nil {
		return -1, errSkipCosignLayer
	}
	if ss.Critical.Image.DockerManifestDigest != attDigest {
		return -1, errSkipCosignLayer
	}
	bundleJSON := desc.Annotations["dev.sigstore.cosign/bundle"]
	if bundleJSON == "" {
		// Signature is cryptographically valid but no Rekor bundle is
		// attached. Accept with index -1 (no transparency-log entry).
		// Trades auditability for compatibility with signatures that
		// predate Rekor or are signed offline.
		return -1, nil
	}
	logIndex, err := verifyRekorBundle(bundleJSON, payloadHash[:], pubKey)
	if err != nil {
		return -1, fmt.Errorf("rekor verification: %w", err)
	}
	return logIndex, nil
}

// readCosignPayload reads the layer content (capped at maxBundleBytes),
// closing the reader before returning. Reads up to maxBundleBytes+1 and
// rejects exact-cap+1 so an oversize payload surfaces as an explicit
// error instead of being silently truncated (mirrors the
// readAttestationStatement contract).
func readCosignPayload(layer v1.Layer) ([]byte, error) {
	reader, err := layer.Uncompressed()
	if err != nil {
		return nil, err
	}
	payload, err := io.ReadAll(io.LimitReader(reader, maxBundleBytes+1))
	_ = reader.Close()
	if err != nil {
		return nil, err
	}
	if int64(len(payload)) > maxBundleBytes {
		return nil, fmt.Errorf("cosign payload too large")
	}
	return payload, nil
}

// ── Rekor verification ─────────────────────────────────────────────

// verifyRekorBundle parses the cosign Rekor bundle annotation and
// verifies that the transparency log entry is consistent with the
// signature we just verified:
//   - The logged public key matches our embedded DHI key
//   - The logged data hash matches the simplesigning payload hash
//
// Note: SignedEntryTimestamp (SET) verification is not implemented.
// The SET provides tamper-evidence by committing the entry to a tree
// snapshot signed by Rekor's key. Full SET verification would require
// embedding Rekor's public key and validating the inclusion proof.
// The current checks confirm the entry references the correct key and
// payload, but do not cryptographically prove the entry was logged.
//
// Returns the Rekor log index for audit purposes.
func verifyRekorBundle(bundleJSON string, payloadHash []byte, pubKey *ecdsa.PublicKey) (int64, error) {
	var bundle rekorBundle
	if err := json.Unmarshal([]byte(bundleJSON), &bundle); err != nil {
		return -1, fmt.Errorf("parsing rekor bundle: %w", err)
	}

	if bundle.Payload.Body == "" {
		return -1, fmt.Errorf("rekor bundle has empty body")
	}

	// Decode the hashedrekord entry from the body.
	bodyBytes, err := base64.StdEncoding.DecodeString(bundle.Payload.Body)
	if err != nil {
		return -1, fmt.Errorf("decoding rekor body: %w", err)
	}

	var entry rekorHashedRekord
	if err := json.Unmarshal(bodyBytes, &entry); err != nil {
		return -1, fmt.Errorf("parsing hashedrekord: %w", err)
	}

	if entry.Kind != "hashedrekord" {
		return -1, fmt.Errorf("unexpected rekor entry kind %q", entry.Kind)
	}

	// Verify the logged data hash matches our payload hash.
	expectedHash := fmt.Sprintf("%x", payloadHash)
	if entry.Spec.Data.Hash.Value != expectedHash {
		return -1, fmt.Errorf("rekor data hash mismatch: logged %s, computed %s",
			entry.Spec.Data.Hash.Value[:16], expectedHash[:16])
	}

	// Verify the logged public key matches our embedded DHI key.
	loggedKeyPEM, err := base64.StdEncoding.DecodeString(entry.Spec.Signature.PublicKey.Content)
	if err != nil {
		return -1, fmt.Errorf("decoding logged public key: %w", err)
	}
	loggedKey, err := parseDHIPublicKey(loggedKeyPEM)
	if err != nil {
		return -1, fmt.Errorf("parsing logged public key: %w", err)
	}
	if !loggedKey.Equal(pubKey) {
		return -1, fmt.Errorf("rekor logged key does not match embedded DHI key")
	}

	return bundle.Payload.LogIndex, nil
}

// ── Types ──────────────────────────────────────────────────────────

// inTotoStatement is an in-toto Statement v0.1 with just the fields
// we need for verification.
type inTotoStatement struct {
	Type          string          `json:"_type"`
	PredicateType string          `json:"predicateType"`
	Subject       []inTotoSubject `json:"subject"`
}

type inTotoSubject struct {
	Name   string            `json:"name"`
	Digest map[string]string `json:"digest"`
}

// simpleSigningPayload is the cosign simplesigning format.
type simpleSigningPayload struct {
	Critical struct {
		Identity struct {
			DockerReference string `json:"docker-reference"`
		} `json:"identity"`
		Image struct {
			DockerManifestDigest string `json:"docker-manifest-digest"`
		} `json:"image"`
		Type string `json:"type"`
	} `json:"critical"`
}

// rekorBundle is the cosign Rekor bundle stored in layer annotations.
type rekorBundle struct {
	SignedEntryTimestamp string       `json:"SignedEntryTimestamp"`
	Payload              rekorPayload `json:"Payload"`
}

type rekorPayload struct {
	Body           string `json:"body"`
	IntegratedTime int64  `json:"integratedTime"`
	LogIndex       int64  `json:"logIndex"`
	LogID          string `json:"logID"`
}

// rekorHashedRekord is the transparency log entry for a hashedrekord.
type rekorHashedRekord struct {
	APIVersion string `json:"apiVersion"`
	Kind       string `json:"kind"`
	Spec       struct {
		Data struct {
			Hash struct {
				Algorithm string `json:"algorithm"`
				Value     string `json:"value"`
			} `json:"hash"`
		} `json:"data"`
		Signature struct {
			Content   string `json:"content"`
			PublicKey struct {
				Content string `json:"content"`
			} `json:"publicKey"`
		} `json:"signature"`
	} `json:"spec"`
}

const (
	// dhiSLSAv1PredicateType is the predicate type for SLSA provenance v1.
	dhiSLSAv1PredicateType = "https://slsa.dev/provenance/v1"

	// dhiInTotoStatementType is the in-toto Statement v0.1 type URI.
	dhiInTotoStatementType = "https://in-toto.io/Statement/v0.1"

	// dhiCosignSigArtifactType is the OCI artifact type for cosign
	// signatures on DHI attestations.
	dhiCosignSigArtifactType = "application/vnd.dev.cosign.artifact.sig.v1+json"
)

// ── Helpers ─────────────────────────────────────────────────────────

// checkDHIAuth verifies that Docker credentials for dhi.io are available.
// DHI images require authentication -- a free Docker Hub account is
// sufficient. Returns a clear error with instructions if not logged in.
func checkDHIAuth(ctx context.Context) error {
	ref, err := name.NewTag(dhiRegistry + "/nats:" + natsImageTag)
	if err != nil {
		return fmt.Errorf("internal: parsing test ref: %w", err)
	}

	_, err = remote.Head(ref, dhiRemoteOpts(ctx)...)
	if err != nil {
		if strings.Contains(err.Error(), "401") || strings.Contains(err.Error(), "UNAUTHORIZED") || strings.Contains(err.Error(), "Unauthorized") {
			return fmt.Errorf(
				"not logged in to dhi.io. SynthOrg uses Docker Hardened Images " +
					"which require a free Docker Hub account.\n\n" +
					"  Run: docker login dhi.io\n\n" +
					"Then retry 'synthorg start'")
		}
		return fmt.Errorf("cannot reach dhi.io: %w", err)
	}
	return nil
}

func dhiRemoteOpts(ctx context.Context) []remote.Option {
	return []remote.Option{
		remote.WithContext(ctx),
		remote.WithAuthFromKeychain(authn.DefaultKeychain),
	}
}

func parseDHIRef(image string) (registry, repo, tag string, err error) {
	parts := strings.SplitN(image, "/", 2)
	if len(parts) != 2 {
		return "", "", "", fmt.Errorf("invalid DHI ref %q", image)
	}
	repoTag := strings.SplitN(parts[1], ":", 2)
	if len(repoTag) != 2 {
		return "", "", "", fmt.Errorf("missing tag in %q", image)
	}
	return parts[0], repoTag[0], repoTag[1], nil
}

func parseDHIPublicKey(pemBytes []byte) (*ecdsa.PublicKey, error) {
	block, _ := pem.Decode(pemBytes)
	if block == nil {
		return nil, fmt.Errorf("no PEM block found")
	}
	pub, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parsing public key: %w", err)
	}
	ecKey, ok := pub.(*ecdsa.PublicKey)
	if !ok {
		return nil, fmt.Errorf("expected ECDSA key, got %T", pub)
	}

	// Validate the key's SHA-256 fingerprint against the pinned value
	// to detect accidental or malicious key substitution. The fingerprint
	// is computed over the full PEM encoding (including headers).
	actualFP := fmt.Sprintf("%x", sha256.Sum256(pemBytes))
	if actualFP != DHIPublicKeyFingerprint {
		return nil, fmt.Errorf("DHI key fingerprint mismatch: got %s, want %s", actualFP[:16], DHIPublicKeyFingerprint[:16])
	}

	return ecKey, nil
}
