package scaffold

import (
	"strings"
	"testing"
)

func TestNewParamsValid(t *testing.T) {
	t.Parallel()
	cases := []struct {
		raw       string
		domain    string
		className string
		slug      string
	}{
		{"ping", "ping", "Ping", "ping"},
		{"agent_health", "agent_health", "AgentHealth", "agent health"},
		{"a1_b2_c3", "a1_b2_c3", "A1B2C3", "a1 b2 c3"},
		{"ping ", "ping", "Ping", "ping"}, // trim whitespace
	}
	for _, c := range cases {
		t.Run(c.raw, func(t *testing.T) {
			t.Parallel()
			p, err := NewParams(c.raw)
			if err != nil {
				t.Fatalf("NewParams(%q) returned error: %v", c.raw, err)
			}
			if string(p.Domain) != c.domain {
				t.Errorf("Domain = %q, want %q", p.Domain, c.domain)
			}
			if p.ClassName != c.className {
				t.Errorf("ClassName = %q, want %q", p.ClassName, c.className)
			}
			if p.ModuleDocSlug != c.slug {
				t.Errorf("ModuleDocSlug = %q, want %q", p.ModuleDocSlug, c.slug)
			}
		})
	}
}

func TestNewParamsInvalid(t *testing.T) {
	t.Parallel()
	cases := []struct {
		raw       string
		wantInErr string
	}{
		{"", "required"},
		{"   ", "required"},
		{"Ping", "snake_case"},      // PascalCase
		{"ping_", "snake_case"},     // trailing underscore
		{"_ping", "snake_case"},     // leading underscore
		{"ping-pong", "snake_case"}, // hyphen
		{"ping pong", "snake_case"}, // space
		{"1ping", "snake_case"},     // leading digit
		{"core", "reserved"},        // existing package
		{"persistence", "reserved"}, // existing package
		{"typing", "reserved"},      // stdlib shadow
		{"class", "reserved"},       // python keyword
	}
	for _, c := range cases {
		t.Run(c.raw, func(t *testing.T) {
			t.Parallel()
			_, err := NewParams(c.raw)
			if err == nil {
				t.Fatalf("NewParams(%q) returned no error; want one containing %q", c.raw, c.wantInErr)
			}
			if !strings.Contains(err.Error(), c.wantInErr) {
				t.Errorf("NewParams(%q) error = %v, want substring %q", c.raw, err, c.wantInErr)
			}
		})
	}
}

func TestPascalCase(t *testing.T) {
	t.Parallel()
	cases := map[string]string{
		"":              "",
		"ping":          "Ping",
		"agent_health":  "AgentHealth",
		"a_b_c":         "ABC",
		"a1_b2":         "A1B2",
		"already_snake": "AlreadySnake",
	}
	for input, want := range cases {
		t.Run(input, func(t *testing.T) {
			t.Parallel()
			got := pascalCase(input)
			if got != want {
				t.Errorf("pascalCase(%q) = %q, want %q", input, got, want)
			}
		})
	}
}
