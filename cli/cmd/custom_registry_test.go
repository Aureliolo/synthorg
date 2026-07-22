package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
	"github.com/Aureliolo/synthorg/cli/internal/verify"
	"github.com/spf13/cobra"
)

// newTestCmd returns a minimal *cobra.Command with OutOrStdout/ErrOrStderr
// redirected to buffers so applyTunables's trust-transfer warning can be
// captured and asserted against.
func newTestCmd() (*cobra.Command, *bytes.Buffer) {
	c := &cobra.Command{Use: "test"}
	errBuf := &bytes.Buffer{}
	c.SetOut(&bytes.Buffer{})
	c.SetErr(errBuf)
	return c, errBuf
}

// withDefaultTunables registers a t.Cleanup that restores verify,
// selfupdate, and health package-level state to their compiled-in
// defaults at test end. applyTunables() mutates those vars, and without
// this reset later tests in the same process observe the bleed-through
// and fail non-deterministically (e.g. patchComposeImageRefs expects
// the default ghcr.io registry after a test that set a custom one).
func withDefaultTunables(t *testing.T) {
	t.Helper()
	d := config.DefaultTunables()
	t.Cleanup(func() {
		verify.Configure(
			d.RegistryHost, d.ImageRepoPrefix,
			d.DHIRegistry, d.PostgresImageTag, d.NATSImageTag,
			d.TUFFetchTimeout, d.AttestationHTTPTimeout,
		)
		selfupdate.Configure(
			d.MaxAPIResponseBytes, d.MaxBinaryBytes, d.MaxArchiveEntryBytes,
			d.SelfUpdateHTTPTimeout, d.SelfUpdateAPITimeout, d.TUFFetchTimeout,
		)
		health.Configure(d.HealthCheckTimeout)
	})
}

func TestApplyTunables_DefaultRegistryKeepsVerification(t *testing.T) {
	withDefaultTunables(t)
	c, errBuf := newTestCmd()
	opts := &GlobalOpts{Hints: "auto", DataDir: t.TempDir()}

	if err := applyTunables(c, opts); err != nil {
		t.Fatalf("applyTunables: %v", err)
	}
	if opts.SkipVerify {
		t.Error("SkipVerify = true on default registry; want false")
	}
	if opts.Tunables.CustomRegistry {
		t.Error("Tunables.CustomRegistry = true on default registry; want false")
	}
	if strings.Contains(errBuf.String(), "DISABLED") {
		t.Errorf("trust-transfer warning fired on default registry: %q", errBuf.String())
	}
}

func TestApplyTunables_CustomRegistryDisablesVerification(t *testing.T) {
	t.Setenv("SYNTHORG_REGISTRY_HOST", "my.registry.example")

	withDefaultTunables(t)
	c, errBuf := newTestCmd()
	opts := &GlobalOpts{Hints: "auto", DataDir: t.TempDir()}

	if err := applyTunables(c, opts); err != nil {
		t.Fatalf("applyTunables: %v", err)
	}
	if !opts.SkipVerify {
		t.Error("SkipVerify = false on custom registry; want true")
	}
	if !opts.Tunables.CustomRegistry {
		t.Error("Tunables.CustomRegistry = false on custom registry; want true")
	}
	if !strings.Contains(errBuf.String(), "DISABLED") {
		t.Errorf("trust-transfer warning missing; stderr = %q", errBuf.String())
	}
}

func TestApplyTunables_CustomRegistryWarningIgnoresQuiet(t *testing.T) {
	t.Setenv("SYNTHORG_DHI_REGISTRY", "private.docker.example")

	withDefaultTunables(t)
	c, errBuf := newTestCmd()
	opts := &GlobalOpts{Hints: "auto", Quiet: true, DataDir: t.TempDir()}

	if err := applyTunables(c, opts); err != nil {
		t.Fatalf("applyTunables: %v", err)
	}
	if !opts.SkipVerify {
		t.Error("SkipVerify = false on custom registry; want true even when --quiet")
	}
	// Safety-critical warning MUST fire even under --quiet so that
	// scripted pipelines cannot silently disable verification.
	if !strings.Contains(errBuf.String(), "DISABLED") {
		t.Errorf("--quiet must not suppress trust-transfer warning; stderr = %q", errBuf.String())
	}
}

func TestApplyTunables_CustomRegistryWarningIgnoresJSON(t *testing.T) {
	t.Setenv("SYNTHORG_REGISTRY_HOST", "my.registry.example")

	withDefaultTunables(t)
	c, errBuf := newTestCmd()
	opts := &GlobalOpts{Hints: "auto", JSON: true, DataDir: t.TempDir()}

	if err := applyTunables(c, opts); err != nil {
		t.Fatalf("applyTunables: %v", err)
	}
	if !opts.SkipVerify {
		t.Error("SkipVerify = false on custom registry; want true")
	}
	if !strings.Contains(errBuf.String(), "DISABLED") {
		t.Errorf("--json must not suppress trust-transfer warning; stderr = %q", errBuf.String())
	}
}

// TestApplyTunables_CorruptConfigFailsFast guards the contract that a
// malformed config.json on disk is a hard error from applyTunables --
// not a silent fallback to zero-value state. Silent fallback would drop
// persisted overrides and CustomRegistry detection.
func TestApplyTunables_CorruptConfigFailsFast(t *testing.T) {
	withDefaultTunables(t)
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "config.json"), []byte("{not valid json"), 0o600); err != nil {
		t.Fatalf("writing corrupt config: %v", err)
	}

	c, _ := newTestCmd()
	opts := &GlobalOpts{Hints: "auto", DataDir: dir}

	err := applyTunables(c, opts)
	if err == nil {
		t.Fatal("applyTunables: want error for corrupt config.json, got nil")
	}
	if !strings.Contains(err.Error(), "loading config") {
		t.Errorf("error %q should mention 'loading config'", err.Error())
	}
}
