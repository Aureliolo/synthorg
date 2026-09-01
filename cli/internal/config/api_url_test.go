package config

import "testing"

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
