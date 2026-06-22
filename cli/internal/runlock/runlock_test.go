package runlock

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"
)

// shortWaitOpts shrink the contention wait budget so a "second holder is
// blocked" assertion does not stall for the production 5s budget. They are
// call-scoped (no shared package state), so every test can run in parallel.
func shortWaitOpts() []Option {
	return []Option{
		WithWaitTimeout(150 * time.Millisecond),
		WithRetryInterval(20 * time.Millisecond),
	}
}

func TestAcquireRelease(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name    string
		prelock bool // hold the lock before the subject Acquire
		wantErr error
	}{
		{name: "free lock acquires", prelock: false, wantErr: nil},
		{name: "held lock returns ErrLocked", prelock: true, wantErr: ErrLocked},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			dir := t.TempDir()
			ctx := context.Background()

			if tc.prelock {
				held, err := Acquire(ctx, dir, shortWaitOpts()...)
				if err != nil {
					t.Fatalf("prelock Acquire: %v", err)
				}
				t.Cleanup(func() { _ = held.Release() })
			}

			lock, err := Acquire(ctx, dir, shortWaitOpts()...)
			if tc.wantErr != nil {
				if !errors.Is(err, tc.wantErr) {
					t.Fatalf("Acquire err = %v, want %v", err, tc.wantErr)
				}
				if lock != nil {
					t.Fatalf("Acquire returned a non-nil lock on error")
				}
				return
			}
			if err != nil {
				t.Fatalf("Acquire: %v", err)
			}
			t.Cleanup(func() { _ = lock.Release() })
		})
	}
}

func TestReleaseAllowsReacquire(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	ctx := context.Background()

	first, err := Acquire(ctx, dir, shortWaitOpts()...)
	if err != nil {
		t.Fatalf("first Acquire: %v", err)
	}
	if err := first.Release(); err != nil {
		t.Fatalf("Release: %v", err)
	}

	second, err := Acquire(ctx, dir, shortWaitOpts()...)
	if err != nil {
		t.Fatalf("re-Acquire after Release: %v", err)
	}
	t.Cleanup(func() { _ = second.Release() })
}

func TestCancelledContextIsNotErrLocked(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()

	// Hold the lock, then try to acquire it with an already-cancelled parent
	// context: the caller aborted, so the error must NOT be ErrLocked.
	held, err := Acquire(context.Background(), dir, shortWaitOpts()...)
	if err != nil {
		t.Fatalf("prelock Acquire: %v", err)
	}
	t.Cleanup(func() { _ = held.Release() })

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	lock, err := Acquire(ctx, dir, shortWaitOpts()...)
	if lock != nil {
		t.Fatalf("Acquire returned a non-nil lock on cancelled context")
	}
	if errors.Is(err, ErrLocked) {
		t.Fatalf("cancelled context surfaced ErrLocked, want a cancellation error: %v", err)
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Acquire err = %v, want context.Canceled", err)
	}
}

func TestReleaseIsNilAndDoubleSafe(t *testing.T) {
	t.Parallel()
	var nilLock *Lock
	if err := nilLock.Release(); err != nil {
		t.Fatalf("nil Release: %v", err)
	}

	dir := t.TempDir()
	lock, err := Acquire(context.Background(), dir, shortWaitOpts()...)
	if err != nil {
		t.Fatalf("Acquire: %v", err)
	}
	if err := lock.Release(); err != nil {
		t.Fatalf("first Release: %v", err)
	}
	if err := lock.Release(); err != nil {
		t.Fatalf("second Release should be a no-op: %v", err)
	}
}

func TestLockFileLivesInDataDir(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	lock, err := Acquire(context.Background(), dir, shortWaitOpts()...)
	if err != nil {
		t.Fatalf("Acquire: %v", err)
	}
	t.Cleanup(func() { _ = lock.Release() })

	if got := lock.fl.Path(); got != filepath.Join(dir, lockFileName) {
		t.Fatalf("lock path = %q, want %q", got, filepath.Join(dir, lockFileName))
	}
}
