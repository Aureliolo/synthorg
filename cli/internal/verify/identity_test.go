package verify

import (
	"regexp"
	"testing"

	"github.com/sigstore/sigstore-go/pkg/fulcio/certificate"
)

// wfPrefix is the workflow-path prefix every legitimate signing identity
// from this repository shares.
const wfPrefix = "https://github.com/Aureliolo/synthorg/.github/workflows/"

func TestExpectedSANRegex(t *testing.T) {
	t.Parallel()
	re := regexp.MustCompile(ExpectedSANRegex)

	tests := []struct {
		name string
		san  string
		want bool
	}{
		// Read off live published images. Every one carries heads/main,
		// including release-tagged images, because retagging re-points a
		// tag at an already-signed digest without signing again.
		{"reusable_publish_image_main", wfPrefix + "reusable-publish-image.yml@refs/heads/main", true},
		{"reusable_publish_image_loaded_main", wfPrefix + "reusable-publish-image-loaded.yml@refs/heads/main", true},
		{"retired_docker_main", wfPrefix + "docker.yml@refs/heads/main", true},

		// Publish jobs are gated to main, so no image can legitimately
		// carry a tag ref; accepting one would only ever help a forger.
		{"reusable_publish_image_tag", wfPrefix + "reusable-publish-image.yml@refs/tags/v1.2.3", false},
		{"retired_docker_tag", wfPrefix + "docker.yml@refs/tags/v0.9.3", false},

		// Callers grant scopes and delegate; no certificate carries them.
		{"caller_build_images", wfPrefix + "build-images.yml@refs/heads/main", false},
		{"caller_verify_cli", wfPrefix + "verify-cli.yml@refs/heads/main", false},

		// The release-archive signer must not vouch for an image.
		{"release_archive_signer", wfPrefix + "reusable-release-cli.yml@refs/heads/main", false},
		{"retired_release_archive_signer", wfPrefix + "cli.yml@refs/tags/v0.9.3", false},

		// Base layers are outside ImageNames(), so their signer must not
		// vouch for a service image.
		{"apko_base_signer", wfPrefix + "reusable-publish-apko-base.yml@refs/heads/main", false},

		// A workflow whose name merely contains an accepted one.
		{"prefix_hijack", wfPrefix + "evil-reusable-publish-image.yml@refs/heads/main", false},
		{"suffix_hijack", wfPrefix + "reusable-publish-image-evil.yml@refs/heads/main", false},
		{"loaded_suffix_hijack", wfPrefix + "reusable-publish-image-loaded-evil.yml@refs/heads/main", false},

		{"foreign_owner", "https://github.com/evil/synthorg/.github/workflows/reusable-publish-image.yml@refs/heads/main", false},
		{"foreign_repo", "https://github.com/Aureliolo/other-repo/.github/workflows/reusable-publish-image.yml@refs/heads/main", false},
		{"foreign_host", "https://example.com/Aureliolo/synthorg/.github/workflows/reusable-publish-image.yml@refs/heads/main", false},
		{"userinfo_trick", "https://github.com@evil.com/Aureliolo/synthorg/.github/workflows/reusable-publish-image.yml@refs/heads/main", false},
		{"feature_branch", wfPrefix + "reusable-publish-image.yml@refs/heads/feature/evil", false},
		{"pull_ref", wfPrefix + "reusable-publish-image.yml@refs/pull/1/merge", false},
		{"empty", "", false},
		{"garbage", "random-string", false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if got := re.MatchString(tc.san); got != tc.want {
				t.Errorf("MatchString(%q) = %v, want %v", tc.san, got, tc.want)
			}
		})
	}
}

// TestBuildIdentityPolicyBindsRepository covers what the SAN cannot express.
// A public reusable workflow may be called from any repository on GitHub, and
// the caller's build mints a certificate carrying this repository's workflow
// path as its SAN. Only the repository-binding extensions separate a build we
// ran from one that merely used our workflow as its recipe, so a SAN-only
// policy would accept an artefact signed by a stranger.
func TestBuildIdentityPolicyBindsRepository(t *testing.T) {
	t.Parallel()
	certID, err := BuildIdentityPolicy()
	if err != nil {
		t.Fatalf("BuildIdentityPolicy() error: %v", err)
	}
	trustedSAN := wfPrefix + "reusable-publish-image.yml@refs/heads/main"

	tests := []struct {
		name    string
		ext     certificate.Extensions
		wantErr bool
	}{
		{
			name: "this_repository",
			ext: certificate.Extensions{
				Issuer:                     ExpectedIssuer,
				SourceRepositoryURI:        ExpectedSourceRepositoryURI,
				SourceRepositoryIdentifier: ExpectedSourceRepositoryID,
				RunnerEnvironment:          ExpectedRunnerEnvironment,
			},
		},
		{
			name: "foreign_caller_identical_san",
			ext: certificate.Extensions{
				Issuer:                     ExpectedIssuer,
				SourceRepositoryURI:        "https://github.com/attacker/repo",
				SourceRepositoryIdentifier: "999999999",
				RunnerEnvironment:          ExpectedRunnerEnvironment,
			},
			wantErr: true,
		},
		{
			// The numeric id is pinned so a rename or transfer that frees
			// the URI cannot be used to reclaim the identity.
			name: "matching_uri_wrong_repository_id",
			ext: certificate.Extensions{
				Issuer:                     ExpectedIssuer,
				SourceRepositoryURI:        ExpectedSourceRepositoryURI,
				SourceRepositoryIdentifier: "999999999",
				RunnerEnvironment:          ExpectedRunnerEnvironment,
			},
			wantErr: true,
		},
		{
			name: "self_hosted_runner",
			ext: certificate.Extensions{
				Issuer:                     ExpectedIssuer,
				SourceRepositoryURI:        ExpectedSourceRepositoryURI,
				SourceRepositoryIdentifier: ExpectedSourceRepositoryID,
				RunnerEnvironment:          "self-hosted",
			},
			wantErr: true,
		},
		{
			name: "wrong_issuer",
			ext: certificate.Extensions{
				Issuer:                     "https://token.actions.example.com",
				SourceRepositoryURI:        ExpectedSourceRepositoryURI,
				SourceRepositoryIdentifier: ExpectedSourceRepositoryID,
				RunnerEnvironment:          ExpectedRunnerEnvironment,
			},
			wantErr: true,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			summary := certificate.Summary{
				CertificateIssuer:      tc.ext.Issuer,
				SubjectAlternativeName: trustedSAN,
				Extensions:             tc.ext,
			}
			err := certID.Verify(summary)
			if tc.wantErr && err == nil {
				t.Error("Verify() = nil, want rejection")
			}
			if !tc.wantErr && err != nil {
				t.Errorf("Verify() = %v, want nil", err)
			}
		})
	}
}

func TestImageNamesContainsExpectedServices(t *testing.T) {
	t.Parallel()
	expected := map[string]bool{"backend": false, "web": false, "sandbox": false, "sidecar": false, "fine-tune-gpu": false, "fine-tune-cpu": false}
	for _, name := range ImageNames() {
		if _, ok := expected[name]; !ok {
			t.Errorf("unexpected image name %q", name)
		}
		expected[name] = true
	}
	for name, found := range expected {
		if !found {
			t.Errorf("missing expected image name %q", name)
		}
	}
}

func TestBuildIdentityPolicyDoesNotError(t *testing.T) {
	t.Parallel()
	_, err := BuildIdentityPolicy()
	if err != nil {
		t.Fatalf("BuildIdentityPolicy() error: %v", err)
	}
}

func TestBuildReleaseIdentityPolicyRejectsUnanchoredRegex(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name     string
		sanRegex string
		wantErr  bool
	}{
		{
			"repository_anchored",
			`^https://github\.com/Aureliolo/synthorg/\.github/workflows/(?:x\.yml@refs/heads/main)$`,
			false,
		},
		// Each of these satisfies the extension binding on its own, so
		// only the pattern check separates them from the accepted form.
		{
			"foreign_repository",
			`^https://github\.com/evil/synthorg/\.github/workflows/(?:x\.yml@refs/heads/main)$`,
			true,
		},
		{
			"unanchored_start",
			`https://github\.com/Aureliolo/synthorg/\.github/workflows/(?:x\.yml@refs/heads/main)$`,
			true,
		},
		{
			"unanchored_end",
			`^https://github\.com/Aureliolo/synthorg/\.github/workflows/(?:x\.yml@refs/heads/main)`,
			true,
		},
		{"match_everything", `.*`, true},
		{"empty", "", true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			_, err := BuildReleaseIdentityPolicy(tc.sanRegex)
			if (err != nil) != tc.wantErr {
				t.Errorf("BuildReleaseIdentityPolicy(%q) error = %v, wantErr %v",
					tc.sanRegex, err, tc.wantErr)
			}
		})
	}
}
