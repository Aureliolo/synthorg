package config

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// TestCoerceEnumFields covers every row of the coercion table: an
// unrecognised persisted value falls back to the field's documented
// default, a recognised value is untouched, and an omitted optional value
// is left omitted rather than reported as a coercion.
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
		// optional marks a field where "" means "use the compiled-in
		// default", so an omitted value is not a coercion.
		optional bool
	}{
		{
			name:         "persistence_backend",
			field:        "persistence_backend",
			apply:        func(s *State, v string) { s.PersistenceBackend = v },
			read:         func(s State) string { return s.PersistenceBackend },
			valid:        "postgres",
			wantFallback: defaults.PersistenceBackend,
		},
		{
			// The value that bit in practice: "mem0" was a valid backend
			// until the layered-memory rework dropped it.
			name:         "memory_backend",
			field:        "memory_backend",
			apply:        func(s *State, v string) { s.MemoryBackend = v },
			read:         func(s State) string { return s.MemoryBackend },
			valid:        "composite",
			wantFallback: defaults.MemoryBackend,
		},
		{
			// Not optional here even though Validate tolerates an empty
			// value: bus_backend is interpolated into the compose file
			// verbatim, so "" would ship an empty SYNTHORG_BUS_BACKEND.
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
			optional:     true,
		},
		{
			// Same shape as bus_backend: an empty value would reach the
			// container as an empty SYNTHORG_LOG_LEVEL.
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
			optional:     true,
		},
		{
			name:         "output",
			field:        "output",
			apply:        func(s *State, v string) { s.Output = v },
			read:         func(s State) string { return s.Output },
			valid:        "json",
			wantFallback: "",
			optional:     true,
		},
		{
			name:         "timestamps",
			field:        "timestamps",
			apply:        func(s *State, v string) { s.Timestamps = v },
			read:         func(s State) string { return s.Timestamps },
			valid:        "iso8601",
			wantFallback: "",
			optional:     true,
		},
		{
			name:         "hints",
			field:        "hints",
			apply:        func(s *State, v string) { s.Hints = v },
			read:         func(s State) string { return s.Hints },
			valid:        "always",
			wantFallback: "",
			optional:     true,
		},
		{
			name:         "changelog_view",
			field:        "changelog_view",
			apply:        func(s *State, v string) { s.ChangelogView = v },
			read:         func(s State) string { return s.ChangelogView },
			valid:        "commits",
			wantFallback: "",
			optional:     true,
		},
		{
			name:         "fine_tuning_variant",
			field:        "fine_tuning_variant",
			apply:        func(s *State, v string) { s.FineTuningVariant = v },
			read:         func(s State) string { return s.FineTuningVariant },
			valid:        FineTuneVariantCPU,
			wantFallback: "",
			optional:     true,
		},
	}

	const removed = "a-backend-that-was-removed"

	for _, tt := range tests {
		t.Run(tt.name+"/unrecognised value coerces to the default", func(t *testing.T) {
			t.Parallel()
			s := DefaultState()
			tt.apply(&s, removed)
			got, coercions := Coerce(s)
			if tt.read(got) != tt.wantFallback {
				t.Errorf("%s = %q, want %q", tt.field, tt.read(got), tt.wantFallback)
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
			got, coercions := Coerce(s)
			if tt.read(got) != tt.valid {
				t.Errorf("%s = %q, want %q (unchanged)", tt.field, tt.read(got), tt.valid)
			}
			if len(coercions) != 0 {
				t.Errorf("coercions = %+v, want none", coercions)
			}
		})

		if !tt.optional {
			continue
		}
		t.Run(tt.name+"/omitted optional value is not a coercion", func(t *testing.T) {
			t.Parallel()
			s := DefaultState()
			tt.apply(&s, "")
			got, coercions := Coerce(s)
			if tt.read(got) != "" {
				t.Errorf("%s = %q, want %q (still omitted)", tt.field, tt.read(got), "")
			}
			if len(coercions) != 0 {
				t.Errorf("coercions = %+v, want none", coercions)
			}
		})
	}
}

// TestCoerceCoversEveryEnum fails when an enum allowlist is added to
// state.go without a matching coercion row. Without this the next removed
// enum value would brick the CLI exactly the way memory_backend did,
// because nothing else forces the two lists to move together.
func TestCoerceCoversEveryEnum(t *testing.T) {
	t.Parallel()

	// Every allowlist that gates a persisted State field, keyed by the JSON
	// key of the field it gates. IsValidBool / IsValidImageTag and the
	// registry format checks are deliberately absent: they are format rules
	// over an open set, not enums with a removable member.
	gated := map[string]func(string) bool{
		"persistence_backend": IsValidPersistenceBackend,
		"memory_backend":      IsValidMemoryBackend,
		"bus_backend":         IsValidBusBackend,
		"channel":             IsValidChannel,
		"log_level":           IsValidLogLevel,
		"color":               IsValidColorMode,
		"output":              IsValidOutputMode,
		"timestamps":          IsValidTimestampMode,
		"hints":               IsValidHintsMode,
		"changelog_view":      IsValidChangelogView,
		"fine_tuning_variant": isValidFineTuneVariant,
	}

	covered := make(map[string]bool, len(enumFields))
	for _, f := range enumFields {
		covered[f.name] = true
	}

	for name := range gated {
		if !covered[name] {
			t.Errorf(
				"enum %q has an allowlist in state.go but no row in enumFields; "+
					"a value removed from that allowlist would make every command "+
					"refuse to load the config",
				name,
			)
		}
	}
	for name := range covered {
		if _, ok := gated[name]; !ok {
			t.Errorf("enumFields row %q has no matching allowlist", name)
		}
	}
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
		"memory_backend":      "mem0",
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
	if loaded.MemoryBackend != DefaultState().MemoryBackend {
		t.Errorf("MemoryBackend = %q, want %q", loaded.MemoryBackend, DefaultState().MemoryBackend)
	}
	if len(loaded.Coerced) != 1 || loaded.Coerced[0].Field != "memory_backend" {
		t.Fatalf("Coerced = %+v, want one memory_backend entry", loaded.Coerced)
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
	s.Coerced = []Coercion{{Field: "memory_backend", Rejected: "mem0", Applied: "sqlvector"}}

	if err := Save(s); err != nil {
		t.Fatalf("Save: %v", err)
	}
	body, err := os.ReadFile(StatePath(tmp))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(body), "mem0") || strings.Contains(string(body), "Coerced") {
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
		"memory_backend":      "mem0",
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
