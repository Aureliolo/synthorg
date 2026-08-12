// Package privdrop resolves an image account and permanently becomes it.
//
// The sidecar needs CAP_NET_ADMIN to install its netfilter rules, and Docker
// cannot deliver a capability to a non-root container process: on execve the
// kernel computes the permitted set from the binary's file capabilities and
// the ambient set, both empty here, so a uid other than 0 receives nothing
// however much cap_add names. The bounding set that remains is a ceiling, not
// a grant, and no-new-privileges (correctly) rules out file capabilities as a
// way around it. The container therefore starts as uid 0 and gives that up
// here, before the relay accepts anything an attacker can reach.
package privdrop

import (
	"fmt"
	"os/user"
	"strconv"
)

// Account is a resolved uid/gid pair from the image's account database.
type Account struct {
	UID int
	GID int
}

// Lookup resolves username in the image's own account database.
//
// The ids are read rather than compiled in because the netfilter skip rule
// names the same account: a constant would silently disagree the moment
// either side moved, and the relay's own upstream dials would then be
// redirected back into it.
func Lookup(username string) (Account, error) {
	u, err := user.Lookup(username)
	if err != nil {
		return Account{}, fmt.Errorf("lookup %q: %w", username, err)
	}
	uid, err := strconv.Atoi(u.Uid)
	if err != nil {
		return Account{}, fmt.Errorf("uid %q of %q: %w", u.Uid, username, err)
	}
	gid, err := strconv.Atoi(u.Gid)
	if err != nil {
		return Account{}, fmt.Errorf("gid %q of %q: %w", u.Gid, username, err)
	}
	return Account{UID: uid, GID: gid}, nil
}

// Drop permanently gives up privilege, becoming a.
func Drop(a Account) error {
	if a.UID <= 0 || a.GID <= 0 {
		return fmt.Errorf(
			"refusing to drop to uid %d gid %d: not an unprivileged account",
			a.UID, a.GID,
		)
	}
	return platformDrop(a)
}
