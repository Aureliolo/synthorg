package verify

import (
	"regexp"
	"testing"
)

func TestExpectedSANRegexMatchesValidRefs(t *testing.T) {
	re := regexp.MustCompile(ExpectedSANRegex)

	valid := []string{
		"https://github.com/Aureliolo/synthorg/.github/workflows/build-images.yml@refs/tags/v0.3.0",
		"https://github.com/Aureliolo/synthorg/.github/workflows/build-images.yml@refs/heads/main",
		"https://github.com/Aureliolo/synthorg/.github/workflows/build-images.yml@refs/tags/v0.3.0-rc.1",
		"https://github.com/Aureliolo/synthorg/.github/workflows/build-images.yml@refs/tags/v1.2.3+build.456",
		// Images published under the retired workflow name stay verifiable.
		"https://github.com/Aureliolo/synthorg/.github/workflows/docker.yml@refs/tags/v0.3.0",
		"https://github.com/Aureliolo/synthorg/.github/workflows/docker.yml@refs/heads/main",
	}
	for _, ref := range valid {
		if !re.MatchString(ref) {
			t.Errorf("SAN regex should match %q", ref)
		}
	}
}

func TestExpectedSANRegexRejectsInvalidRefs(t *testing.T) {
	re := regexp.MustCompile(ExpectedSANRegex)

	invalid := []string{
		"https://github.com/evil/synthorg/.github/workflows/build-images.yml@refs/tags/v0.3.0",
		"https://github.com/Aureliolo/other-repo/.github/workflows/build-images.yml@refs/tags/v0.3.0",
		"https://example.com/Aureliolo/synthorg/.github/workflows/build-images.yml@refs/tags/v0.3.0",
		"https://github.com/Aureliolo/synthorg/.github/workflows/verify-cli.yml@refs/tags/v1.0.0",
		"https://github.com/Aureliolo/synthorg/.github/workflows/cli.yml@refs/tags/v1.0.0",
		"https://github.com/Aureliolo/synthorg/.github/workflows/build-images.yml@refs/heads/feature/evil",
		"https://github.com/Aureliolo/synthorg/.github/workflows/docker.yml@refs/heads/feature/evil",
		// A workflow whose name merely contains the accepted one.
		"https://github.com/Aureliolo/synthorg/.github/workflows/evil-build-images.yml@refs/heads/main",
		"",
		"random-string",
	}
	for _, ref := range invalid {
		if re.MatchString(ref) {
			t.Errorf("SAN regex should NOT match %q", ref)
		}
	}
}

func TestImageNamesContainsExpectedServices(t *testing.T) {
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
	_, err := BuildIdentityPolicy()
	if err != nil {
		t.Fatalf("BuildIdentityPolicy() error: %v", err)
	}
}
