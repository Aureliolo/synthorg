// Package runlock provides a cross-platform advisory file lock that
// serialises Docker Compose lifecycle operations for a single data directory.
//
// Without it, two concurrent invocations (for example a `synthorg update`
// restart racing a `synthorg start`) can both reach `compose up -d` against
// the same named volumes, producing duplicate containers, port-binding
// conflicts, or data corruption. The lifecycle commands that mutate the stack
// (`start`, `stop`, `update`) and the teardown commands that stop it
// (`wipe`, `uninstall`) acquire this lock first and hold it across the whole
// operation, so at most one lifecycle mutation touches the stack at a time.
package runlock

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"time"

	"github.com/gofrs/flock"
)

// lockFileName is the advisory lock file created in the data directory. It is
// distinct from compose.yml / config.json so a corrupt lock file never harms
// real state and can be deleted safely.
const lockFileName = "synthorg.lock"

// defaultWaitTimeout bounds how long Acquire waits for a competing holder to
// release before returning ErrLocked. It is intentionally short: a lifecycle
// mutation that is genuinely mid-flight should make the second caller fail fast
// and retry, not block for a multi-minute health wait.
const defaultWaitTimeout = 5 * time.Second

// defaultRetryInterval is the poll cadence while waiting for the lock.
const defaultRetryInterval = 200 * time.Millisecond

// ErrLocked is returned when another process holds the lifecycle lock and it
// could not be acquired within the wait budget. Callers surface it as a clear
// "another operation is in progress" message rather than proceeding into a
// split-brain compose-up.
var ErrLocked = errors.New(
	"another synthorg lifecycle operation (start/stop/update/wipe) is in progress",
)

// config holds the per-acquire tunables. It is set from Option values so the
// timing knobs are call-scoped rather than mutable package globals (which would
// race under `go test -race` if any caller added t.Parallel()).
type config struct {
	waitTimeout   time.Duration
	retryInterval time.Duration
}

// Option customises an Acquire call. Production callers pass none and get the
// default wait budget; tests pass WithWaitTimeout / WithRetryInterval to keep
// the suite fast without mutating shared state.
type Option func(*config)

// WithWaitTimeout overrides how long Acquire waits before returning ErrLocked.
func WithWaitTimeout(d time.Duration) Option {
	return func(c *config) { c.waitTimeout = d }
}

// WithRetryInterval overrides the lock-acquisition poll cadence.
func WithRetryInterval(d time.Duration) Option {
	return func(c *config) { c.retryInterval = d }
}

// Lock is an acquired advisory lock. Call Release (typically via defer) to
// unlock. A nil *Lock releases to a no-op so callers can defer unconditionally.
type Lock struct {
	fl *flock.Flock
}

// Acquire takes the exclusive lifecycle lock under safeDir, waiting up to the
// configured wait timeout for a competing holder before returning ErrLocked.
// safeDir must already exist; callers pass the validated state directory (the
// output of config.SecurePath), whose parent is created by `init`.
func Acquire(ctx context.Context, safeDir string, opts ...Option) (*Lock, error) {
	cfg := config{waitTimeout: defaultWaitTimeout, retryInterval: defaultRetryInterval}
	for _, opt := range opts {
		opt(&cfg)
	}

	fl := flock.New(filepath.Join(safeDir, lockFileName))

	lockCtx, cancel := context.WithTimeout(ctx, cfg.waitTimeout)
	defer cancel()

	locked, err := fl.TryLockContext(lockCtx, cfg.retryInterval)
	if err != nil {
		// The lock-context deadline firing means a competing holder never
		// released within the budget; surface the actionable ErrLocked.
		if errors.Is(err, context.DeadlineExceeded) {
			return nil, ErrLocked
		}
		// A cancelled parent ctx (e.g. Ctrl+C) is an operator abort, not a lock
		// conflict; surfacing ErrLocked here would falsely claim another
		// operation is running.
		if errors.Is(err, context.Canceled) {
			return nil, fmt.Errorf("lifecycle lock acquisition cancelled: %w", err)
		}
		return nil, fmt.Errorf("acquiring lifecycle lock %s: %w", fl.Path(), err)
	}
	if !locked {
		return nil, ErrLocked
	}
	return &Lock{fl: fl}, nil
}

// Release unlocks the advisory lock. It is safe to call on a nil *Lock and safe
// to call more than once.
func (l *Lock) Release() error {
	if l == nil || l.fl == nil {
		return nil
	}
	fl := l.fl
	l.fl = nil
	if err := fl.Unlock(); err != nil {
		return fmt.Errorf("releasing lifecycle lock %s: %w", fl.Path(), err)
	}
	return nil
}
