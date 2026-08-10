//go:build linux

package privdrop

import (
	"fmt"
	"os"
	"syscall"
)

func drop(a Account) error {
	// Supplementary groups outlive a uid change, so they go first; setgid
	// must precede setuid, which is the call that gives up the power to do
	// either. Go issues all three across every thread, so no goroutine is
	// left behind on the old credentials.
	if err := syscall.Setgroups([]int{a.GID}); err != nil {
		return fmt.Errorf("setgroups: %w", err)
	}
	if err := syscall.Setgid(a.GID); err != nil {
		return fmt.Errorf("setgid %d: %w", a.GID, err)
	}
	if err := syscall.Setuid(a.UID); err != nil {
		return fmt.Errorf("setuid %d: %w", a.UID, err)
	}

	// Read the credentials back rather than infer them from three nil
	// returns: everything after this point assumes the process can no longer
	// touch the netfilter tables it just wrote.
	if euid := os.Geteuid(); euid != a.UID {
		return fmt.Errorf("euid is %d after setuid(%d)", euid, a.UID)
	}
	if egid := os.Getegid(); egid != a.GID {
		return fmt.Errorf("egid is %d after setgid(%d)", egid, a.GID)
	}
	return nil
}
