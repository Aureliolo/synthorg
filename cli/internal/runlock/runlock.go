// Package runlock provides a cross-platform advisory file lock that
// serialises Docker Compose lifecycle operations (start, stop, restart) for a
// single data directory.
//
// Without it, two concurrent invocations (for example a `synthorg update`
// restart racing a `synthorg start`) can both reach `compose up -d` against
// the same named volumes, producing duplicate containers, port-binding
// conflicts, or data corruption. Every command that runs `compose up -d` or
// `compose down` acquires this lock first and holds it across the whole
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

// ErrLocked is returned when another process holds the lifecycle lock and it
// could not be acquired within the wait budget. Callers surface it as a clear
// "another operation is in progress" message rather than proceeding into a
// split-brain compose-up.
var ErrLocked = errors.New(
	"another synthorg lifecycle operation (start/stop/update) is in progress",
)

// WaitTimeout bounds how long Acquire waits for a competing holder to release
// before returning ErrLocked. It is intentionally short: a lifecycle mutation
// that is genuinely mid-flight should make the second caller fail fast and
// retry, not block for a multi-minute health wait. It is a package variable so
// tests can shorten it.
var WaitTimeout = 5 * time.Second

// RetryInterval is the poll cadence while waiting for the lock.
var RetryInterval = 200 * time.Millisecond

// Lock is an acquired advisory lock. Call Release (typically via defer) to
// unlock. A nil *Lock releases to a no-op so callers can defer unconditionally.
type Lock struct {
	fl *flock.Flock
}

// Acquire takes the exclusive lifecycle lock under safeDir, waiting up to
// WaitTimeout for a competing holder before returning ErrLocked. safeDir must
// already exist; callers pass the validated state directory (the output of
// config.SecurePath), whose parent is created by `init`.
func Acquire(ctx context.Context, safeDir string) (*Lock, error) {
	fl := flock.New(filepath.Join(safeDir, lockFileName))

	lockCtx, cancel := context.WithTimeout(ctx, WaitTimeout)
	defer cancel()

	locked, err := fl.TryLockContext(lockCtx, RetryInterval)
	if err != nil {
		// A deadline/cancel means a competing holder never released within the
		// budget; surface the actionable ErrLocked. The parent ctx being
		// cancelled is treated the same way (the caller is shutting down).
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
			return nil, ErrLocked
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
		return fmt.Errorf("releasing lifecycle lock: %w", err)
	}
	return nil
}
