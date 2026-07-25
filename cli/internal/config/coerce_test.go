package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"
)

// TestCoerceEnumFields covers every row of the coercion table: an
// unrecognised persisted value falls back to the field's documented
// default, a recognised value is untouched, and an omitted value whose
// empty form is resolved downstream is left alone rather than reported.
func TestCoerceEnumFields(t *testing.T) {
	t.Parallel()

	defaults := DefaultState()

	tests := []struct {
		name string
		// field is the JSON key the coercion is expected to report.
		field string
		// apply writes the tested value onto a State.
		apply func(*State, string)
		// read pulls the tested value back off a State.
		read func(State) string
		// valid is a value the allowlist accepts; it must survive untouched.
		valid string
		// wantFallback is the value an unrecognised input coerces to.
		wantFallback string
		// emptyIsSafe marks a field where "" is resolved downstream, so an
		// omitted value is not a coercion.
		emptyIsSafe bool
	}{
		{
			// Validate treats bus_backend as optional, but ParamsFromState
			// is the only thing that defaults an empty value; a direct
			// State.BusBackend read would still see "". Hence not
			// emptyIsSafe.
			name:         "bus_backend",
			field:        "bus_backend",
			apply:        func(s *State, v string) { s.BusBackend = v },
			read:         func(s State) string { return s.BusBackend },
			valid:        "nats",
			wantFallback: defaults.BusBackend,
		},
		{
			name:         "channel",
			field:        "channel",
			apply:        func(s *State, v string) { s.Channel = v },
			read:         func(s State) string { return s.Channel },
			valid:        "dev",
			wantFallback: "",
			emptyIsSafe:  true,
		},
		{
			// compose interpolates State.LogLevel raw, so an empty value
			// would ship an empty SYNTHORG_LOG_LEVEL.
			name:         "log_level",
			field:        "log_level",
			apply:        func(s *State, v string) { s.LogLevel = v },
			read:         func(s State) string { return s.LogLevel },
			valid:        "debug",
			wantFallback: defaults.LogLevel,
		},
		{
			name:         "color",
			field:        "color",
			apply:        func(s *State, v string) { s.Color = v },
			read:         func(s State) string { return s.Color },
			valid:        "never",
			wantFallback: "",
			emptyIsSafe:  true,
		},
		{
			name:         "output",
			field:        "output",
			apply:        func(s *State, v string) { s.Output = v },
			read:         func(s State) string { return s.Output },
			valid:        "json",
			wantFallback: "",
			emptyIsSafe:  true,
		},
		{
			name:         "timestamps",
			field:        "timestamps",
			apply:        func(s *State, v string) { s.Timestamps = v },
			read:         func(s State) string { return s.Timestamps },
			valid:        "iso8601",
			wantFallback: "",
			emptyIsSafe:  true,
		},
		{
			name:         "hints",
			field:        "hints",
			apply:        func(s *State, v string) { s.Hints = v },
			read:         func(s State) string { return s.Hints },
			valid:        "always",
			wantFallback: "",
			emptyIsSafe:  true,
		},
		{
			name:         "changelog_view",
			field:        "changelog_view",
			apply:        func(s *State, v string) { s.ChangelogView = v },
			read:         func(s State) string { return s.ChangelogView },
			valid:        "commits",
			wantFallback: "",
			emptyIsSafe:  true,
		},
		{
			name:         "fine_tuning_variant",
			field:        "fine_tuning_variant",
			apply:        func(s *State, v string) { s.FineTuningVariant = v },
			read:         func(s State) string { return s.FineTuningVariant },
			valid:        FineTuneVariantCPU,
			wantFallback: "",
			emptyIsSafe:  true,
		},
	}

	const removed = "a-value-that-was-removed"

	for _, tt := range tests {
		t.Run(tt.name+"/unrecognised value coerces to the default", func(t *testing.T) {
			t.Parallel()
			s := DefaultState()
			tt.apply(&s, removed)
			s, coercions := Coerce(s)
			if tt.read(s) != tt.wantFallback {
				t.Errorf("%s = %q, want %q", tt.field, tt.read(s), tt.wantFallback)
			}
			if len(coercions) != 1 {
				t.Fatalf("coercions = %d, want exactly 1: %+v", len(coercions), coercions)
			}
			c := coercions[0]
			if c.Field != tt.field {
				t.Errorf("Field = %q, want %q", c.Field, tt.field)
			}
			if c.Rejected != removed {
				t.Errorf("Rejected = %q, want %q", c.Rejected, removed)
			}
			if c.Applied != tt.wantFallback {
				t.Errorf("Applied = %q, want %q", c.Applied, tt.wantFallback)
			}
			if c.Allowed == "" {
				t.Error("Allowed must list the accepted values so the operator can pick one")
			}
		})

		t.Run(tt.name+"/recognised value is untouched", func(t *testing.T) {
			t.Parallel()
			s := DefaultState()
			tt.apply(&s, tt.valid)
			s, coercions := Coerce(s)
			if tt.read(s) != tt.valid {
				t.Errorf("%s = %q, want %q (unchanged)", tt.field, tt.read(s), tt.valid)
			}
			if len(coercions) != 0 {
				t.Errorf("coercions = %+v, want none", coercions)
			}
		})

		t.Run(tt.name+"/empty value", func(t *testing.T) {
			t.Parallel()
			s := DefaultState()
			tt.apply(&s, "")
			s, coercions := Coerce(s)
			if tt.emptyIsSafe {
				if tt.read(s) != "" {
					t.Errorf("%s = %q, want %q (still unset)", tt.field, tt.read(s), "")
				}
				if len(coercions) != 0 {
					t.Errorf("coercions = %+v, want none", coercions)
				}
				return
			}
			// Not emptyIsSafe: an explicitly empty value would reach the
			// compose file verbatim, so it is repaired like any other
			// unusable value.
			if tt.read(s) != tt.wantFallback {
				t.Errorf("%s = %q, want %q", tt.field, tt.read(s), tt.wantFallback)
			}
			if len(coercions) != 1 {
				t.Fatalf("coercions = %+v, want exactly 1", coercions)
			}
		})
	}
}

// TestNonCoercibleEnumsAreNotCoerced pins the blast-radius split: a field
// that selects where data lives must keep failing the load rather than
// being defaulted. Coercing persistence_backend would make `start`
// regenerate compose without postgres and bring the stack up against an
// empty SQLite database, which also re-arms the unauthenticated first-run
// admin claim.
func TestNonCoercibleEnumsAreNotCoerced(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name  string
		apply func(*State, string)
		read  func(State) string
	}{
		{
			name:  "persistence_backend",
			apply: func(s *State, v string) { s.PersistenceBackend = v },
			read:  func(s State) string { return s.PersistenceBackend },
		},
		{
			name:  "memory_backend",
			apply: func(s *State, v string) { s.MemoryBackend = v },
			read:  func(s State) string { return s.MemoryBackend },
		},
	}

	const removed = "a-backend-that-was-removed"

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			s := DefaultState()
			tt.apply(&s, removed)
			s, coercions := Coerce(s)
			if tt.read(s) != removed {
				t.Errorf("%s = %q, want it left at %q", tt.name, tt.read(s), removed)
			}
			if len(coercions) != 0 {
				t.Errorf("coercions = %+v, want none: this field must not be defaulted", coercions)
			}
			if err := s.Validate(); err == nil {
				t.Error("Validate must still reject the unrecognised value")
			}
		})
	}
}

// allowlistDecl matches an enum allowlist declaration in state.go, at
// top level or inside a grouped `var (...)` block. The grouped form is
// the one worth allowing for explicitly: state.go already uses grouped
// blocks elsewhere, so anchoring to a line starting with `var ` would let
// the next allowlist land in one and slip past the whole check.
var allowlistDecl = regexp.MustCompile(`(?m)^\s*(?:var\s+)?(valid[A-Za-z]+)\s*=\s*map\[string\]bool`)

// stateSourcePath locates state.go relative to this test file rather than
// the caller's working directory.
func stateSourcePath(t *testing.T) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Join(filepath.Dir(thisFile), "state.go")
}

// TestEveryAllowlistIsClassified fails when an enum allowlist is added to
// state.go without deciding whether it is coercible.
//
// The allowlist set is DERIVED from the source, not restated here: a
// hand-maintained list could only ever prove two hand-maintained lists
// agree with each other, which is exactly the omission that let a removed
// memory_backend value take down every command. Adding a `var validFoos =
// map[string]bool` without a row in enumFields or an entry in
// nonCoercibleEnums fails here, on the unmapped name, forcing the author
// to make the coercible-or-not call deliberately.
func TestEveryAllowlistIsClassified(t *testing.T) {
	t.Parallel()

	// The config key each allowlist gates. Entries are required to be
	// total over what the source declares; the assertions below enforce
	// that, so this map cannot silently fall behind.
	allowlistToField := map[string]string{
		"validPersistenceBackends": "persistence_backend",
		"validMemoryBackends":      "memory_backend",
		"validBusBackends":         "bus_backend",
		"validChannels":            "channel",
		"validLogLevels":           "log_level",
		"validColorModes":          "color",
		"validOutputModes":         "output",
		"validTimestampModes":      "timestamps",
		"validHintsModes":          "hints",
		"validChangelogViews":      "changelog_view",
		"validFineTuneVariants":    "fine_tuning_variant",
	}

	source, err := os.ReadFile(stateSourcePath(t))
	if err != nil {
		t.Fatalf("read state.go: %v", err)
	}
	matches := allowlistDecl.FindAllStringSubmatch(string(source), -1)
	if len(matches) == 0 {
		t.Fatal("found no enum allowlists in state.go; the detector has drifted from the source")
	}

	coercible := make(map[string]bool, len(enumFields))
	for _, f := range enumFields {
		coercible[f.name] = true
	}
	nonCoercible := make(map[string]bool, len(nonCoercibleEnums))
	for _, e := range nonCoercibleEnums {
		nonCoercible[e.name] = true
		if strings.TrimSpace(e.reason) == "" {
			t.Errorf("nonCoercibleEnums[%q] must state why it is not coercible", e.name)
		}
	}

	declared := make(map[string]bool, len(matches))
	for _, m := range matches {
		varName := m[1]
		declared[varName] = true
		field, mapped := allowlistToField[varName]
		if !mapped {
			t.Errorf(
				"state.go declares allowlist %s with no entry in this test's "+
					"allowlistToField map. Add it, then classify the field as "+
					"coercible (a row in enumFields) or not (an entry in "+
					"nonCoercibleEnums with a reason). Leaving it unclassified "+
					"means a value removed from that allowlist would make every "+
					"command reading through Load refuse to run.",
				varName,
			)
			continue
		}
		switch {
		case coercible[field] && nonCoercible[field]:
			t.Errorf("%q is in BOTH enumFields and nonCoercibleEnums", field)
		case !coercible[field] && !nonCoercible[field]:
			t.Errorf(
				"allowlist %s gates %q, which is in neither enumFields nor "+
					"nonCoercibleEnums", varName, field,
			)
		}
	}

	for varName := range allowlistToField {
		if !declared[varName] {
			t.Errorf(
				"allowlistToField names %s, which state.go no longer declares; "+
					"remove the stale entry", varName,
			)
		}
	}
}

// TestCoercionRendering covers the operator-facing text, including the
// empty-value branch that eight of the table's rows use. doctor and the
// startup warning both render through String(), so a format change here
// changes every message an operator sees.
func TestCoercionRendering(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		in   Coercion
		want string
	}{
		{
			name: "unrecognised value names what was rejected",
			in:   Coercion{Field: "channel", Rejected: "nightly", Applied: "stable", Allowed: "dev, stable"},
			want: `channel: "nightly" is not a recognised value, using stable instead (valid: dev, stable)`,
		},
		{
			name: "empty applied renders as the built-in default",
			in:   Coercion{Field: "hints", Rejected: "sometimes", Applied: "", Allowed: "always, auto, never"},
			want: `hints: "sometimes" is not a recognised value, using the built-in default instead (valid: always, auto, never)`,
		},
		{
			// An unset value is not an unrecognised one, and saying so
			// would read as a CLI bug rather than a repair.
			name: "empty rejected reads as unset, not unrecognised",
			in:   Coercion{Field: "log_level", Rejected: "", Applied: "info", Allowed: "debug, error, info, warn"},
			want: "log_level: no value set, using info instead (valid: debug, error, info, warn)",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := tt.in.String(); got != tt.want {
				t.Errorf("String() =\n  %s\nwant\n  %s", got, tt.want)
			}
		})
	}
}

// TestCoerceBoundsTheRejectedValue keeps a pathological config from
// flooding stderr on every invocation and the 0600 doctor report.
func TestCoerceBoundsTheRejectedValue(t *testing.T) {
	t.Parallel()

	s := DefaultState()
	s.Channel = strings.Repeat("x", rejectedValueLimit*10)
	s, coercions := Coerce(s)
	if len(coercions) != 1 {
		t.Fatalf("coercions = %+v, want 1", coercions)
	}
	if len(coercions[0].Rejected) > rejectedValueLimit+len("...") {
		t.Errorf("Rejected not bounded: %d chars", len(coercions[0].Rejected))
	}
}

// FuzzCoerce pins the properties Coerce must hold for ANY persisted
// string, since config.json is hand-editable and an older release can
// write anything: it never panics, and it never leaves a coercible field
// holding a value its own allowlist rejects.
func FuzzCoerce(f *testing.F) {
	for _, seed := range []string{"", " ", "sqlite", "mem0", "MEM0", "nightly", "\x00", strings.Repeat("x", 500)} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, value string) {
		s := DefaultState()
		s.Channel = value
		s.LogLevel = value
		s.Hints = value
		s.BusBackend = value
		got, _ := Coerce(s)
		for _, field := range enumFields {
			resolved := *field.accessor(&got)
			if field.emptyIsSafe && resolved == "" {
				continue
			}
			if !field.valid(resolved) {
				t.Fatalf("Coerce left %s = %q, which its own allowlist rejects", field.name, resolved)
			}
		}
	})
}

// TestLoadCoercesStaleEnum is the end-to-end shape of the bug: a config
// holding a value that a later release removed must still load, so every
// command stays usable and `init` can repair it.
func TestLoadCoercesStaleEnum(t *testing.T) {
	t.Parallel()

	tmp := t.TempDir()
	raw, err := json.Marshal(map[string]any{
		"data_dir":            tmp,
		"backend_port":        3001,
		"web_port":            3000,
		"persistence_backend": "sqlite",
		"memory_backend":      "sqlvector",
		"channel":             "nightly-that-was-removed",
		"encrypt_secrets":     false,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(StatePath(tmp), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	loaded, err := Load(tmp)
	if err != nil {
		t.Fatalf("Load must not fail on a removed enum value, got %v", err)
	}
	if loaded.Channel != "" {
		t.Errorf("Channel = %q, want it cleared to the default", loaded.Channel)
	}
	if len(loaded.Coerced) != 1 || loaded.Coerced[0].Field != "channel" {
		t.Fatalf("Coerced = %+v, want one channel entry", loaded.Coerced)
	}
}

// TestLoadStillRejectsANonCoercibleEnum is the other half of the split:
// a data-location field keeps failing the strict loader, so `start` cannot
// bring the stack up against the wrong database.
func TestLoadStillRejectsANonCoercibleEnum(t *testing.T) {
	t.Parallel()

	tmp := t.TempDir()
	writeMem0Config(t, tmp)

	if _, err := Load(tmp); err == nil {
		t.Fatal("Load must reject a removed memory_backend value")
	}
}

// TestLoadTolerantSurvivesANonCoercibleEnum pins the recovery route: the
// commands that diagnose and repair a broken config must still read it,
// which is what keeps the install from being stranded even though the
// strict loader refuses.
func TestLoadTolerantSurvivesANonCoercibleEnum(t *testing.T) {
	t.Parallel()

	tmp := t.TempDir()
	writeMem0Config(t, tmp)

	state, advisory := LoadTolerant(tmp)
	if advisory == nil {
		t.Error("expected an advisory error naming the invalid value")
	}
	if state.MemoryBackend != "mem0" {
		t.Errorf("MemoryBackend = %q, want the on-disk value preserved for reporting", state.MemoryBackend)
	}
	if state.BackendPort != 3001 {
		t.Errorf("BackendPort = %d, want the rest of the config readable", state.BackendPort)
	}
}

// TestLoadTolerantReportsEveryProblemAtOnce covers the compound case a
// diagnostic exists for. A config can hold both an unusable data_dir and
// an unrelated invariant breach; reporting only the first sends the
// operator round the loop again for the second.
func TestLoadTolerantReportsEveryProblemAtOnce(t *testing.T) {
	t.Parallel()

	tmp := t.TempDir()
	raw, err := json.Marshal(map[string]any{
		"data_dir":            filepath.Join("relative", "synthorg"),
		"backend_port":        3001,
		"web_port":            3000,
		"nats_client_port":    999999,
		"persistence_backend": "sqlite",
		"memory_backend":      "sqlvector",
		"encrypt_secrets":     false,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(StatePath(tmp), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	state, advisory := LoadTolerant(tmp)
	if advisory == nil {
		t.Fatal("expected an advisory error")
	}
	for _, want := range []string{"data_dir", "nats_client_port"} {
		if !strings.Contains(advisory.Error(), want) {
			t.Errorf("advisory must report %s, got: %v", want, advisory)
		}
	}
	// The command still has to be able to run: an unusable persisted
	// data_dir falls back to the one the caller asked for.
	if state.DataDir != tmp {
		t.Errorf("DataDir = %q, want the caller's %q", state.DataDir, tmp)
	}
}

// writeMem0Config persists a config whose memory_backend holds the value
// dropped by the layered-memory work, alongside otherwise valid fields.
func writeMem0Config(t *testing.T, dir string) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{
		"data_dir":            dir,
		"backend_port":        3001,
		"web_port":            3000,
		"persistence_backend": "sqlite",
		"memory_backend":      "mem0",
		"encrypt_secrets":     false,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(StatePath(dir), raw, 0o600); err != nil {
		t.Fatal(err)
	}
}

// TestLoadReportsCoercionsAlongsideAValidationFailure covers the compound
// case: a config with both a coercible enum and an unrelated invariant
// breach must not hide the substitution behind the hard failure.
func TestLoadReportsCoercionsAlongsideAValidationFailure(t *testing.T) {
	t.Parallel()

	tmp := t.TempDir()
	raw, err := json.Marshal(map[string]any{
		"data_dir":            tmp,
		"backend_port":        3001,
		"web_port":            3000,
		"nats_client_port":    999999,
		"persistence_backend": "sqlite",
		"memory_backend":      "sqlvector",
		"hints":               "sometimes",
		"encrypt_secrets":     false,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(StatePath(tmp), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	_, err = Load(tmp)
	if err == nil {
		t.Fatal("expected the out-of-range port to fail the load")
	}
	if !strings.Contains(err.Error(), "hints") {
		t.Errorf("error must also report the coerced field, got: %v", err)
	}
}

// TestCoercionsAreNeverPersisted pins the json:"-" contract. A coercion is
// a fact about one load, not configuration: persisting it would put a
// growing audit trail into the operator's config file and round-trip it
// back out on the next read.
func TestCoercionsAreNeverPersisted(t *testing.T) {
	t.Parallel()

	tmp := t.TempDir()
	s := DefaultState()
	s.DataDir = tmp
	s.EncryptSecrets = false
	s.Coerced = []Coercion{{Field: "channel", Rejected: "nightly", Applied: "stable"}}

	if err := Save(s); err != nil {
		t.Fatalf("Save: %v", err)
	}
	body, err := os.ReadFile(StatePath(tmp))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(body), "nightly") || strings.Contains(string(body), "Coerced") {
		t.Errorf("coercions leaked into the persisted config:\n%s", body)
	}

	reloaded, err := Load(tmp)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(reloaded.Coerced) != 0 {
		t.Errorf("Coerced = %+v, want empty after a clean round-trip", reloaded.Coerced)
	}
}

// TestSaveHealsACoercedConfig covers the self-healing property: once any
// command persists state, the stale value is gone from disk and the
// warning stops on its own.
func TestSaveHealsACoercedConfig(t *testing.T) {
	t.Parallel()

	tmp := t.TempDir()
	raw, err := json.Marshal(map[string]any{
		"data_dir":            tmp,
		"backend_port":        3001,
		"web_port":            3000,
		"persistence_backend": "sqlite",
		"memory_backend":      "sqlvector",
		"hints":               "sometimes",
		"encrypt_secrets":     false,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(StatePath(tmp), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	loaded, err := Load(tmp)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if err := Save(loaded); err != nil {
		t.Fatalf("Save: %v", err)
	}

	healed, err := Load(tmp)
	if err != nil {
		t.Fatalf("Load after Save: %v", err)
	}
	if len(healed.Coerced) != 0 {
		t.Errorf("Coerced = %+v, want none once the coerced value is persisted", healed.Coerced)
	}
}
