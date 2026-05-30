package cmd

import (
	"context"
	"testing"
)

// --- update flag validation ---

func TestValidateUpdateFlags(t *testing.T) {
	t.Run("cli-only and images-only mutually exclusive", func(t *testing.T) {
		old1, old2 := updateCLIOnly, updateImagesOnly
		defer func() { updateCLIOnly, updateImagesOnly = old1, old2 }()
		updateCLIOnly = true
		updateImagesOnly = true
		if err := validateUpdateFlags(); err == nil {
			t.Error("expected error for --cli-only + --images-only")
		}
	})

	t.Run("check and dry-run mutually exclusive", func(t *testing.T) {
		old1, old2 := updateCheck, updateDryRun
		defer func() { updateCheck, updateDryRun = old1, old2 }()
		updateCheck = true
		updateDryRun = true
		if err := validateUpdateFlags(); err == nil {
			t.Error("expected error for --check + --dry-run")
		}
	})

	t.Run("invalid timeout", func(t *testing.T) {
		old := updateTimeout
		defer func() { updateTimeout = old }()
		updateTimeout = "notaduration"
		if err := validateUpdateFlags(); err == nil {
			t.Error("expected error for invalid timeout")
		}
	})

	t.Run("valid flags", func(t *testing.T) {
		old1, old2, old3 := updateCLIOnly, updateImagesOnly, updateTimeout
		defer func() {
			updateCLIOnly, updateImagesOnly, updateTimeout = old1, old2, old3
		}()
		updateCLIOnly = true
		updateImagesOnly = false
		updateTimeout = "90s"
		if err := validateUpdateFlags(); err != nil {
			t.Errorf("unexpected error: %v", err)
		}
	})
}

// --- cleanup flag validation ---

func TestValidateCleanupFlags(t *testing.T) {
	t.Run("negative keep", func(t *testing.T) {
		old := cleanupKeep
		defer func() { cleanupKeep = old }()
		cleanupKeep = -1
		if err := validateCleanupFlags(); err == nil {
			t.Error("expected error for negative --keep")
		}
	})

	t.Run("valid keep", func(t *testing.T) {
		old := cleanupKeep
		defer func() { cleanupKeep = old }()
		cleanupKeep = 3
		if err := validateCleanupFlags(); err != nil {
			t.Errorf("unexpected error: %v", err)
		}
	})

	t.Run("zero keep is valid boundary", func(t *testing.T) {
		old := cleanupKeep
		defer func() { cleanupKeep = old }()
		cleanupKeep = 0
		if err := validateCleanupFlags(); err != nil {
			t.Errorf("unexpected error for --keep 0: %v", err)
		}
	})

	t.Run("large keep is valid", func(t *testing.T) {
		old := cleanupKeep
		defer func() { cleanupKeep = old }()
		cleanupKeep = 100
		if err := validateCleanupFlags(); err != nil {
			t.Errorf("unexpected error for --keep 100: %v", err)
		}
	})

	// validateCleanupFlags only checks cleanupKeep; it does not reference
	// cleanupAll. This guards that contract: --all does not change the
	// keep-validation verdict (the real --all/--keep interaction lives in
	// the docker-dependent collectCleanupCandidates and is out of scope
	// for unit tests). cleanupAll is saved/restored even though unused.
	t.Run("cleanupAll does not affect keep validation", func(t *testing.T) {
		oldKeep, oldAll := cleanupKeep, cleanupAll
		defer func() { cleanupKeep, cleanupAll = oldKeep, oldAll }()
		cleanupAll = true

		for _, keep := range []int{0, 5} {
			cleanupKeep = keep
			if err := validateCleanupFlags(); err != nil {
				t.Errorf("unexpected error for --all --keep %d: %v", keep, err)
			}
		}
		cleanupKeep = -1
		if err := validateCleanupFlags(); err == nil {
			t.Error("expected error for --all --keep -1 (negative keep must still fail)")
		}
	})
}

// --- doctor flag validation ---

func TestValidateDoctorFlags(t *testing.T) {
	t.Run("empty checks", func(t *testing.T) {
		old := doctorChecks
		defer func() { doctorChecks = old }()
		doctorChecks = ""
		if err := validateDoctorFlags(); err != nil {
			t.Errorf("unexpected error: %v", err)
		}
	})

	t.Run("valid checks", func(t *testing.T) {
		old := doctorChecks
		defer func() { doctorChecks = old }()
		doctorChecks = "health,containers,images"
		if err := validateDoctorFlags(); err != nil {
			t.Errorf("unexpected error: %v", err)
		}
	})

	t.Run("invalid check name", func(t *testing.T) {
		old := doctorChecks
		defer func() { doctorChecks = old }()
		doctorChecks = "health,nonexistent"
		if err := validateDoctorFlags(); err == nil {
			t.Error("expected error for invalid check name")
		}
	})

	t.Run("all keyword", func(t *testing.T) {
		old := doctorChecks
		defer func() { doctorChecks = old }()
		doctorChecks = "all"
		if err := validateDoctorFlags(); err != nil {
			t.Errorf("unexpected error: %v", err)
		}
	})
}

func TestDoctorCheckEnabled(t *testing.T) {
	t.Run("no filter returns true", func(t *testing.T) {
		old := doctorChecks
		defer func() { doctorChecks = old }()
		doctorChecks = ""
		if !doctorCheckEnabled("health") {
			t.Error("expected true with empty filter")
		}
	})

	t.Run("matching filter returns true", func(t *testing.T) {
		old := doctorChecks
		defer func() { doctorChecks = old }()
		doctorChecks = "health,containers"
		if !doctorCheckEnabled("health") {
			t.Error("expected true for matching check")
		}
	})

	t.Run("non-matching filter returns false", func(t *testing.T) {
		old := doctorChecks
		defer func() { doctorChecks = old }()
		doctorChecks = "health,containers"
		if doctorCheckEnabled("disk") {
			t.Error("expected false for non-matching check")
		}
	})

	t.Run("all keyword enables everything", func(t *testing.T) {
		old := doctorChecks
		defer func() { doctorChecks = old }()
		doctorChecks = "all"
		if !doctorCheckEnabled("disk") {
			t.Error("expected true for 'all' keyword")
		}
	})
}

// --- backup list flag validation ---

func TestValidateBackupListFlags(t *testing.T) {
	t.Run("invalid sort", func(t *testing.T) {
		old1, old2 := backupListSort, backupListLimit
		defer func() { backupListSort, backupListLimit = old1, old2 }()
		backupListSort = "invalid"
		backupListLimit = 0
		if err := validateBackupListFlags(); err == nil {
			t.Error("expected error for invalid sort")
		}
	})

	t.Run("negative limit", func(t *testing.T) {
		old1, old2 := backupListSort, backupListLimit
		defer func() { backupListSort, backupListLimit = old1, old2 }()
		backupListSort = "newest"
		backupListLimit = -1
		if err := validateBackupListFlags(); err == nil {
			t.Error("expected error for negative limit")
		}
	})

	t.Run("valid flags", func(t *testing.T) {
		old1, old2 := backupListSort, backupListLimit
		defer func() { backupListSort, backupListLimit = old1, old2 }()
		for _, sort := range []string{"newest", "oldest", "size"} {
			backupListSort = sort
			backupListLimit = 5
			if err := validateBackupListFlags(); err != nil {
				t.Errorf("unexpected error for sort=%q: %v", sort, err)
			}
		}
	})
}

// --- backup sorting ---

func TestSortBackups(t *testing.T) {
	backups := []backupInfo{
		{Timestamp: "2026-03-01", SizeBytes: 100},
		{Timestamp: "2026-03-03", SizeBytes: 50},
		{Timestamp: "2026-03-02", SizeBytes: 200},
	}

	t.Run("newest", func(t *testing.T) {
		b := make([]backupInfo, len(backups))
		copy(b, backups)
		sortBackups(b, "newest")
		if b[0].Timestamp != "2026-03-03" {
			t.Errorf("expected newest first, got %s", b[0].Timestamp)
		}
	})

	t.Run("oldest", func(t *testing.T) {
		b := make([]backupInfo, len(backups))
		copy(b, backups)
		sortBackups(b, "oldest")
		if b[0].Timestamp != "2026-03-01" {
			t.Errorf("expected oldest first, got %s", b[0].Timestamp)
		}
	})

	t.Run("size", func(t *testing.T) {
		b := make([]backupInfo, len(backups))
		copy(b, backups)
		sortBackups(b, "size")
		if b[0].SizeBytes != 200 {
			t.Errorf("expected largest first, got %d", b[0].SizeBytes)
		}
	})
}

// --- confirmUpdateWithDefault auto-accept ---

func TestConfirmUpdateWithDefault_AutoAccept(t *testing.T) {
	// When autoAccept is true and prompting IS enabled (no --yes),
	// the function should return (true, nil) without prompting.
	ctx := SetGlobalOpts(context.Background(), &GlobalOpts{
		Yes:   false,
		Hints: "auto",
	})

	ok, err := confirmUpdateWithDefault(ctx, "test?", false, true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !ok {
		t.Error("expected true with autoAccept=true")
	}
}

func TestConfirmUpdateWithDefault_YesFlagOverride(t *testing.T) {
	// When --yes is set, the function returns the defaultVal regardless
	// of autoAccept (--yes has higher precedence).
	ctx := SetGlobalOpts(context.Background(), &GlobalOpts{
		Yes:   true,
		Hints: "auto",
	})

	ok, err := confirmUpdateWithDefault(ctx, "test?", false, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ok {
		t.Error("expected false with --yes and defaultVal=false")
	}
}

// --- errorHint ---

func TestErrorHint(t *testing.T) {
	tests := []struct {
		name     string
		errMsg   string
		wantHint bool
	}{
		{"connection refused", "connection refused", true},
		{"backend unreachable", "backend unreachable: dial tcp", true},
		{"compose not found", "compose.yml not found in /data", true},
		{"loading config", "loading config: open /data/config.json", true},
		{"permission denied", "permission denied", true},
		{"image verification non-transport", "image verification failed", false},
		{"interactive terminal", "requires an interactive terminal", true},
		{"docker not available", "Docker not available", true},
		{"docker not found", "docker: not found", true},
		{"generic error", "something went wrong", false},
		{"docker running but error", "docker compose returned exit code 1", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			hint := errorHint(&testError{msg: tt.errMsg})
			if tt.wantHint && hint == "" {
				t.Errorf("expected hint for %q, got empty", tt.errMsg)
			}
			if !tt.wantHint && hint != "" {
				t.Errorf("expected no hint for %q, got %q", tt.errMsg, hint)
			}
		})
	}
}

type testError struct{ msg string }

func (e *testError) Error() string { return e.msg }

// --- boolToYesNo ---

func TestBoolToYesNo(t *testing.T) {
	if boolToYesNo(true) != "yes" {
		t.Error("expected yes for true")
	}
	if boolToYesNo(false) != "no" {
		t.Error("expected no for false")
	}
}
