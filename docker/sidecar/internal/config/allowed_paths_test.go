package config

import "testing"

func TestParseAllowedPathsAcceptsHostPortPrefix(t *testing.T) {
	t.Parallel()

	rules, err := parseAllowedPaths(
		"host.docker.internal:3001=/api/v1/gateway/v1," +
			"host.docker.internal:3001=/api/v1/mcp-gateway",
	)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(rules) != 2 {
		t.Fatalf("got %d rules, want 2", len(rules))
	}
	for _, r := range rules {
		if r.Host != "host.docker.internal" || r.Port != 3001 {
			t.Errorf("unexpected destination %s:%d", r.Host, r.Port)
		}
	}
	if rules[0].Prefix != "/api/v1/gateway/v1" {
		t.Errorf("prefix = %q", rules[0].Prefix)
	}
	if rules[1].Prefix != "/api/v1/mcp-gateway" {
		t.Errorf("prefix = %q", rules[1].Prefix)
	}
}

// TestParseAllowedPathsRejectsMalformed pins the fail-loud choice: skipping a
// malformed entry would widen the destination back to every route it serves,
// which is the opposite of what configuring a narrowing asked for.
func TestParseAllowedPathsRejectsMalformed(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name string
		raw  string
	}{
		{name: "no_equals", raw: "host:3001/api/v1"},
		{name: "prefix_not_absolute", raw: "host:3001=api/v1"},
		{name: "empty_prefix", raw: "host:3001="},
		{name: "no_port", raw: "host=/api/v1"},
		{name: "bad_port", raw: "host:notaport=/api/v1"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if _, err := parseAllowedPaths(tc.raw); err == nil {
				t.Errorf("parseAllowedPaths(%q) must fail, got nil", tc.raw)
			}
		})
	}
}

func TestLoadRejectsMalformedAllowedPaths(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", "0123456789012345678901234567890123456789")
	t.Setenv("SIDECAR_ALLOWED_HOSTS", "host:3001")
	t.Setenv("SIDECAR_ALLOWED_PATHS", "host:3001")

	if _, err := Load(); err == nil {
		t.Error("Load must reject a malformed SIDECAR_ALLOWED_PATHS")
	}
}
