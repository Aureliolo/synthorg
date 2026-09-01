package config

import (
	"strings"
	"testing"
)

func TestAPIPrefix(t *testing.T) {
	tests := []struct {
		name  string
		env   string
		unset bool
		want  string
	}{
		{name: "unset", unset: true, want: DefaultAPIPrefix},
		{name: "empty", env: "", want: DefaultAPIPrefix},
		{name: "whitespace only", env: "   ", want: DefaultAPIPrefix},
		{name: "operator override", env: "/api/v2", want: "/api/v2"},
		{name: "missing leading slash", env: "api/v2", want: "/api/v2"},
		{name: "trailing slash", env: "/api/v2/", want: "/api/v2"},
		{name: "root serves no prefix", env: "/", want: ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if tt.unset {
				t.Setenv(EnvAPIPrefix, "")
			} else {
				t.Setenv(EnvAPIPrefix, tt.env)
			}
			if got := APIPrefix(); got != tt.want {
				t.Errorf("APIPrefix() = %q, want %q", got, tt.want)
			}
		})
	}
}

// TestAPIURL_FollowsTheOperatorsPrefix pins the reason this helper exists: the
// backend serves under whatever api.api_prefix says, so a CLI holding its own
// copy 404s on every call with nothing to explain why.
func TestAPIURL_FollowsTheOperatorsPrefix(t *testing.T) {
	t.Setenv(EnvAPIPrefix, "/custom")
	want := "http://localhost:3001/custom/healthz"
	if got := APIURL(3001, "/healthz"); got != want {
		t.Errorf("APIURL = %q, want %q", got, want)
	}
}

func TestAPIURL_DefaultsToTheRegisteredPrefix(t *testing.T) {
	t.Setenv(EnvAPIPrefix, "")
	want := "http://localhost:3001/api/v1/admin/backups"
	if got := APIURL(3001, "/admin/backups"); got != want {
		t.Errorf("APIURL = %q, want %q", got, want)
	}
}

// TestValidateAPIPrefix_RefusesAnythingButAPath covers the values that are
// not a misspelt path but a different kind of thing entirely. Each one is
// refused rather than reduced, because reducing it would apply a route the
// operator did not ask for while the backend serves the one they did.
func TestValidateAPIPrefix_RefusesAnythingButAPath(t *testing.T) {
	tests := []struct {
		name string
		env  string
	}{
		{name: "absolute URL", env: "http://example.invalid/api"},
		{name: "scheme-only", env: "https://"},
		{name: "userinfo", env: "//user@example.invalid/api"},
		{name: "protocol-relative authority", env: "//example.invalid/api"},
		{name: "query", env: "/api/v1?x=1"},
		{name: "fragment", env: "/api/v1#frag"},
		{name: "control character", env: "/api/v1\n/x"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Setenv(EnvAPIPrefix, tt.env)
			err := ValidateAPIPrefix()
			if err == nil {
				t.Fatalf("ValidateAPIPrefix() = nil, want an error for %q", tt.env)
			}
			if !strings.Contains(err.Error(), EnvAPIPrefix) {
				t.Errorf("error %q does not name %s", err, EnvAPIPrefix)
			}
		})
	}
}

func TestValidateAPIPrefix_AcceptsAPath(t *testing.T) {
	for _, env := range []string{"", "   ", "/", "/api/v1", "api/v2", "/api/v2/"} {
		t.Setenv(EnvAPIPrefix, env)
		if err := ValidateAPIPrefix(); err != nil {
			t.Errorf("ValidateAPIPrefix() = %v, want nil for %q", err, env)
		}
	}
}

// TestAPIURL_HostIsNotReachableFromTheEnvironment is the security property
// this package owns: whatever an operator exports, the request goes to their
// own machine. It is asserted on APIURL rather than on the validator because
// the validator is the loud path and this is the structural one.
func TestAPIURL_HostIsNotReachableFromTheEnvironment(t *testing.T) {
	for _, env := range []string{
		"http://example.invalid/api",
		"//example.invalid/api",
		"/api@example.invalid",
		"/../../..",
	} {
		t.Setenv(EnvAPIPrefix, env)
		got := APIURL(3001, "/healthz")
		if !strings.HasPrefix(got, "http://localhost:3001/") {
			t.Errorf("APIURL = %q for %q, want a localhost:3001 URL", got, env)
		}
	}
}
