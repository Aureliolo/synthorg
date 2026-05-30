package cmd

import (
	"slices"
	"testing"
)

// TestValidateLogsInput pins the injection-safety gate for `synthorg
// logs`. The three input families each have their own validator:
//   - --tail: positive integer or the literal "all" (whitespace-trimmed)
//   - --since/--until: timeFilterPattern, anchored to a leading
//     alphanumeric so flag-shaped values (leading '-') are rejected
//   - service names: serviceNamePattern, restricting to
//     [a-zA-Z0-9_-]+ so shell metacharacters cannot reach the
//     `docker compose logs` argv
//
// validateLogsInput takes all inputs as parameters (no package
// globals), so the cases run in parallel.
func TestValidateLogsInput(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		tail     string
		since    string
		until    string
		services []string
		wantErr  bool
	}{
		// --- tail ---
		{name: "default tail accepted", tail: "100"},
		{name: "tail all accepted", tail: "all"},
		{name: "tail with surrounding whitespace trimmed", tail: " 50 "},
		{name: "tail zero rejected", tail: "0", wantErr: true},
		{name: "tail negative rejected", tail: "-5", wantErr: true},
		{name: "tail non-numeric rejected", tail: "abc", wantErr: true},
		{name: "tail empty rejected", tail: "", wantErr: true},
		{name: "tail fractional rejected", tail: "12.5", wantErr: true},

		// --- since / until accept ---
		{name: "since relative duration accepted", tail: "100", since: "1h"},
		{name: "since date accepted", tail: "100", since: "2024-01-01"},
		{name: "until rfc3339 timestamp accepted", tail: "100", until: "2024-01-01T00:00:00Z"},

		// --- since / until reject (flag-shaped + metacharacters) ---
		{name: "since flag-shaped rejected", tail: "100", since: "--evil", wantErr: true},
		{name: "since flag-with-value rejected", tail: "100", since: "--since=x", wantErr: true},
		{name: "since leading hyphen duration rejected", tail: "100", since: "-1h", wantErr: true},
		{name: "since with space rejected", tail: "100", since: "1 hour", wantErr: true},
		{name: "until flag-shaped rejected", tail: "100", until: "--rm", wantErr: true},

		// --- service names ---
		{name: "service plain accepted", tail: "100", services: []string{"backend"}},
		{name: "service with underscore accepted", tail: "100", services: []string{"web_1"}},
		{name: "service with hyphen accepted", tail: "100", services: []string{"synthorg-backend"}},
		{name: "service with space rejected", tail: "100", services: []string{"back end"}, wantErr: true},
		{name: "service with semicolon rejected", tail: "100", services: []string{"back;rm"}, wantErr: true},
		{name: "service with slash rejected", tail: "100", services: []string{"back/end"}, wantErr: true},
		{name: "service with command substitution rejected", tail: "100", services: []string{"back$(x)"}, wantErr: true},
		{name: "service with bang rejected", tail: "100", services: []string{"svc!"}, wantErr: true},
		{name: "second service invalid is rejected", tail: "100", services: []string{"backend", "bad;svc"}, wantErr: true},

		// Current-behaviour documentation: a flag-shaped service name
		// passes serviceNamePattern because '-' is a member of the
		// allowed class. It is NOT rejected here; the `--` separator
		// emitted by buildLogsArgs (asserted in TestBuildLogsArgs) is
		// what neutralises it for docker compose.
		{name: "flag-shaped service accepted by name gate", tail: "100", services: []string{"--evil"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			err := validateLogsInput(tt.tail, tt.since, tt.until, tt.services)
			if tt.wantErr && err == nil {
				t.Errorf("validateLogsInput(%q, %q, %q, %v) = nil, want error",
					tt.tail, tt.since, tt.until, tt.services)
			}
			if !tt.wantErr && err != nil {
				t.Errorf("validateLogsInput(%q, %q, %q, %v) = %v, want nil",
					tt.tail, tt.since, tt.until, tt.services, err)
			}
		})
	}
}

// TestBuildLogsArgs pins the full flag-to-args mapping for `docker
// compose logs`, including the load-bearing `--` separator that
// terminates flag parsing immediately before the user-supplied service
// list. buildLogsArgs reads the logFollow / logSince / logUntil /
// logTimestamps / logNoPrefix package globals, so cases mutate them in
// place under a single t.Cleanup and the test does NOT run in parallel.
func TestBuildLogsArgs(t *testing.T) {
	origFollow, origSince, origUntil := logFollow, logSince, logUntil
	origTimestamps, origNoPrefix := logTimestamps, logNoPrefix
	t.Cleanup(func() {
		logFollow, logSince, logUntil = origFollow, origSince, origUntil
		logTimestamps, logNoPrefix = origTimestamps, origNoPrefix
	})

	tests := []struct {
		name       string
		follow     bool
		since      string
		until      string
		timestamps bool
		noPrefix   bool
		tail       string
		services   []string
		wantArgs   []string
	}{
		{
			name:     "defaults",
			tail:     "100",
			wantArgs: []string{"logs", "--tail", "100", "--"},
		},
		{
			name:     "follow",
			follow:   true,
			tail:     "100",
			wantArgs: []string{"logs", "--tail", "100", "-f", "--"},
		},
		{
			name:     "since",
			since:    "1h",
			tail:     "100",
			wantArgs: []string{"logs", "--tail", "100", "--since", "1h", "--"},
		},
		{
			name:     "until",
			until:    "2024-01-01",
			tail:     "100",
			wantArgs: []string{"logs", "--tail", "100", "--until", "2024-01-01", "--"},
		},
		{
			name:       "timestamps",
			timestamps: true,
			tail:       "100",
			wantArgs:   []string{"logs", "--tail", "100", "--timestamps", "--"},
		},
		{
			name:     "no log prefix",
			noPrefix: true,
			tail:     "100",
			wantArgs: []string{"logs", "--tail", "100", "--no-log-prefix", "--"},
		},
		{
			name:     "tail all passthrough",
			tail:     "all",
			wantArgs: []string{"logs", "--tail", "all", "--"},
		},
		{
			name:     "single service selection",
			tail:     "50",
			services: []string{"backend"},
			wantArgs: []string{"logs", "--tail", "50", "--", "backend"},
		},
		{
			name:       "all flags with two services preserves order",
			follow:     true,
			since:      "2h",
			until:      "2024-02-02",
			timestamps: true,
			noPrefix:   true,
			tail:       "200",
			services:   []string{"backend", "web"},
			wantArgs: []string{
				"logs", "--tail", "200", "-f",
				"--since", "2h", "--until", "2024-02-02",
				"--timestamps", "--no-log-prefix",
				"--", "backend", "web",
			},
		},
		{
			name:     "flag-shaped service sits after the separator",
			tail:     "100",
			services: []string{"--evil"},
			wantArgs: []string{"logs", "--tail", "100", "--", "--evil"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Assign all five globals every iteration so no case inherits
			// leftover state from a prior one.
			logFollow, logSince, logUntil = tt.follow, tt.since, tt.until
			logTimestamps, logNoPrefix = tt.timestamps, tt.noPrefix

			got := buildLogsArgs(tt.tail, tt.services)
			if !slices.Equal(got, tt.wantArgs) {
				t.Fatalf("buildLogsArgs(%q, %v) = %v, want %v", tt.tail, tt.services, got, tt.wantArgs)
			}

			// Injection guard: the `--` separator must sit immediately
			// before the service list and every service token must come
			// after it, so a hyphen-leading service can never be parsed
			// as a flag by docker compose.
			sep := slices.Index(got, "--")
			if sep == -1 {
				t.Fatalf("buildLogsArgs() = %v, missing %q separator", got, "--")
			}
			if !slices.Equal(got[sep+1:], tt.services) {
				t.Errorf("tokens after %q separator = %v, want services %v", "--", got[sep+1:], tt.services)
			}
			for _, svc := range tt.services {
				if before := slices.Index(got[:sep], svc); before != -1 {
					t.Errorf("service %q appears at index %d before the %q separator (index %d)", svc, before, "--", sep)
				}
			}
		})
	}
}
