package cmd

import (
	"encoding/json"
	"maps"
	"os"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// writeConfigWith persists a minimal valid config with the given overrides
// applied on top, and returns the directory holding it.
func writeConfigWith(t *testing.T, overrides map[string]any) string {
	t.Helper()
	dir := t.TempDir()
	body := map[string]any{
		"data_dir":            dir,
		"backend_port":        3001,
		"web_port":            3000,
		"persistence_backend": "sqlite",
		"memory_backend":      "sqlvector",
		"encrypt_secrets":     false,
	}
	maps.Copy(body, overrides)
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config.StatePath(dir), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return dir
}

// TestCoercionWarningIsAlwaysVisible pins the one thing that makes a
// silent substitution safe: the operator is told. A stack running on a
// value nobody chose, with the on-disk file still showing the original,
// is the failure mode -- so this warning is exempt from --quiet and
// --json exactly as the custom-registry trust warning is. A scripted
// pipeline that never saw it would deploy the substitution and record
// nothing.
func TestCoercionWarningIsAlwaysVisible(t *testing.T) {
	tests := []struct {
		name string
		opts func(*GlobalOpts)
	}{
		{"default output", func(*GlobalOpts) {}},
		{"--quiet", func(o *GlobalOpts) { o.Quiet = true }},
		{"--json", func(o *GlobalOpts) { o.JSON = true }},
		{"--plain", func(o *GlobalOpts) { o.Plain = true }},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			withDefaultTunables(t)
			c, errBuf := newTestCmd()
			dir := writeConfigWith(t, map[string]any{"channel": "nightly-that-was-removed"})
			opts := &GlobalOpts{Hints: "auto", DataDir: dir}
			tt.opts(opts)

			if err := applyTunables(c, opts); err != nil {
				t.Fatalf("applyTunables must not fail on a coercible value: %v", err)
			}

			out := errBuf.String()
			for _, want := range []string{
				config.StatePath(dir),       // which file to fix
				"channel",                   // which setting
				"nightly-that-was-removed",  // what it held
				"is not a recognised value", // why it was replaced
				"synthorg config set",       // how to choose deliberately
			} {
				if !strings.Contains(out, want) {
					t.Errorf("warning must mention %q\ngot: %s", want, out)
				}
			}
		})
	}
}

// TestNoCoercionWarningOnACleanConfig keeps the warning meaningful. One
// that fires on a healthy install trains the operator to ignore it, and
// the value of this one is entirely in being believed.
func TestNoCoercionWarningOnACleanConfig(t *testing.T) {
	withDefaultTunables(t)
	c, errBuf := newTestCmd()
	dir := writeConfigWith(t, map[string]any{"channel": "stable"})

	if err := applyTunables(c, &GlobalOpts{Hints: "auto", DataDir: dir}); err != nil {
		t.Fatalf("applyTunables: %v", err)
	}
	if strings.Contains(errBuf.String(), "could not use as written") {
		t.Errorf("coercion warning fired on a clean config: %q", errBuf.String())
	}
}

// TestNoCoercionWarningWhenNoConfigExists covers the first-run path.
// `synthorg init` runs against a directory with no config at all, and a
// warning there would announce a repair that never happened.
func TestNoCoercionWarningWhenNoConfigExists(t *testing.T) {
	withDefaultTunables(t)
	c, errBuf := newTestCmd()

	if err := applyTunables(c, &GlobalOpts{Hints: "auto", DataDir: t.TempDir()}); err != nil {
		t.Fatalf("applyTunables: %v", err)
	}
	if errBuf.Len() != 0 {
		t.Errorf("a fresh install must produce no warnings, got: %q", errBuf.String())
	}
}

// TestUnsetValueIsNotReportedAsUnrecognised covers the wording for the
// settings whose empty form is unusable (compose interpolates log_level
// verbatim, so an empty one would ship an empty env var). Calling an unset
// value "not recognised" would send the operator looking for a typo that
// is not there, or read as a bug in the CLI itself.
func TestUnsetValueIsNotReportedAsUnrecognised(t *testing.T) {
	withDefaultTunables(t)
	c, errBuf := newTestCmd()
	dir := writeConfigWith(t, map[string]any{"log_level": ""})

	if err := applyTunables(c, &GlobalOpts{Hints: "auto", DataDir: dir}); err != nil {
		t.Fatalf("applyTunables: %v", err)
	}

	out := errBuf.String()
	if !strings.Contains(out, "log_level: no value set") {
		t.Errorf("an unset value must read as unset\ngot: %s", out)
	}
	if strings.Contains(out, `"" is not a recognised value`) {
		t.Errorf("an unset value must not be reported as unrecognised\ngot: %s", out)
	}
}
