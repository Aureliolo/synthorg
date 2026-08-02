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
	expected := map[string]bool{"backend": false, "web": false, "sandbox": false, "openhands": false, "sidecar": false, "fine-tune-gpu": false, "fine-tune-cpu": false}
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

func TestBuildReleaseIdentityPolicyDoesNotError(t *testing.T) {
	t.Parallel()
	_, err := BuildReleaseIdentityPolicy()
	if err != nil {
		t.Fatalf("BuildReleaseIdentityPolicy() error: %v", err)
	}
}

func TestExpectedReleaseSANRegex(t *testing.T) {
	t.Parallel()
	re := regexp.MustCompile(ExpectedReleaseSANRegex)

	tests := []struct {
		name string
		san  string
		want bool
	}{
		// Read off a live published release bundle.
		{"reusable_signer_dev_tag", wfPrefix + "reusable-release-cli.yml@refs/tags/v0.9.4-dev.85", true},
		{"reusable_signer_release_tag", wfPrefix + "reusable-release-cli.yml@refs/tags/v1.2.3", true},
		{"reusable_signer_prerelease", wfPrefix + "reusable-release-cli.yml@refs/tags/v1.2.3-rc.1", true},
		{"reusable_signer_build_metadata", wfPrefix + "reusable-release-cli.yml@refs/tags/v1.2.3+build.4", true},

		// The retired signer stays accepted only for the versions it
		// actually signed, so it cannot vouch for a future release.
		{"retired_signer_last_version", wfPrefix + "cli.yml@refs/tags/v0.9.3", true},
		{"retired_signer_older_minor", wfPrefix + "cli.yml@refs/tags/v0.8.9", true},
		{"retired_signer_prerelease", wfPrefix + "cli.yml@refs/tags/v0.9.1-rc.1", true},
		{"retired_signer_beyond_its_history", wfPrefix + "cli.yml@refs/tags/v0.9.4", false},
		// The bound is a character class, not a numeric comparison, so the
		// two neighbours that decide whether it holds are a trailing extra
		// digit and a two-digit minor.
		{"retired_signer_trailing_digit", wfPrefix + "cli.yml@refs/tags/v0.9.30", false},
		{"retired_signer_two_digit_minor", wfPrefix + "cli.yml@refs/tags/v0.10.0", false},
		{"retired_signer_future_major", wfPrefix + "cli.yml@refs/tags/v1.2.3", false},

		// A release archive is only ever cut from a tag.
		{"reusable_signer_main", wfPrefix + "reusable-release-cli.yml@refs/heads/main", false},
		{"retired_signer_main", wfPrefix + "cli.yml@refs/heads/main", false},
		{"pull_ref", wfPrefix + "reusable-release-cli.yml@refs/pull/1/merge", false},

		// verify-cli.yml delegates to the signer and runs no signing step,
		// so no certificate can ever carry it.
		{"caller_verify_cli", wfPrefix + "verify-cli.yml@refs/tags/v1.2.3", false},

		// An image signer must not vouch for a CLI archive.
		{"image_signer", wfPrefix + "reusable-publish-image.yml@refs/tags/v1.2.3", false},
		{"image_caller", wfPrefix + "build-images.yml@refs/tags/v1.2.3", false},
		{"retired_image_signer", wfPrefix + "docker.yml@refs/tags/v1.2.3", false},

		{"foreign_owner", "https://github.com/evil/synthorg/.github/workflows/reusable-release-cli.yml@refs/tags/v1.2.3", false},
		{"prefix_hijack", wfPrefix + "evil-reusable-release-cli.yml@refs/tags/v1.2.3", false},
		{"suffix_hijack", wfPrefix + "reusable-release-cli-evil.yml@refs/tags/v1.2.3", false},
		{"empty", "", false},
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

func TestNeitherPinAdmitsTheOthersSigner(t *testing.T) {
	t.Parallel()
	// The two anchors now sit in one file, so a pattern edited into the
	// wrong constant would compile and pass its own table. Each artefact
	// class must reject every workflow the other admits: an image signer
	// vouching for a release archive is the failure the split prevents.
	image := regexp.MustCompile(ExpectedSANRegex)
	release := regexp.MustCompile(ExpectedReleaseSANRegex)

	imageSANs := []string{
		wfPrefix + "reusable-publish-image.yml@refs/heads/main",
		wfPrefix + "reusable-publish-image-loaded.yml@refs/heads/main",
		wfPrefix + "docker.yml@refs/heads/main",
	}
	releaseSANs := []string{
		wfPrefix + "reusable-release-cli.yml@refs/tags/v1.2.3",
		wfPrefix + "cli.yml@refs/tags/v0.9.3",
	}
	for _, san := range imageSANs {
		if !image.MatchString(san) {
			t.Errorf("image pin rejects its own signer %q", san)
		}
		if release.MatchString(san) {
			t.Errorf("release pin admits image signer %q", san)
		}
	}
	for _, san := range releaseSANs {
		if !release.MatchString(san) {
			t.Errorf("release pin rejects its own signer %q", san)
		}
		if image.MatchString(san) {
			t.Errorf("image pin admits release signer %q", san)
		}
	}
}
