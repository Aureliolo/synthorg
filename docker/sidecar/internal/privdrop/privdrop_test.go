package privdrop_test

import (
	"os/user"
	"strconv"
	"testing"

	"github.com/Aureliolo/synthorg/sidecar/internal/privdrop"
)

func TestLookupReadsTheAccountDatabase(t *testing.T) {
	me, err := user.Current()
	if err != nil {
		t.Fatalf("user.Current: %v", err)
	}
	want, err := strconv.Atoi(me.Uid)
	if err != nil {
		t.Skip("accounts are not uid-based on this platform")
	}

	got, err := privdrop.Lookup(me.Username)
	if err != nil {
		t.Fatalf("Lookup(%q): %v", me.Username, err)
	}
	if got.UID != want {
		t.Errorf("uid = %d, want %d", got.UID, want)
	}
}

func TestLookupFailsOnAnAbsentAccount(t *testing.T) {
	// The image declares the serving account; if it ever stops doing so the
	// sidecar has to refuse to start rather than keep the privilege it was
	// about to give up.
	if _, err := privdrop.Lookup("no-such-account-in-any-image"); err == nil {
		t.Fatal("expected an absent account to fail lookup")
	}
}

func TestDropRefusesRoot(t *testing.T) {
	// Dropping to uid 0 is a no-op that reads like a success, which would
	// leave the relay serving with the capability it installed rules with.
	if err := privdrop.Drop(privdrop.Account{UID: 0, GID: 0}); err == nil {
		t.Fatal("expected dropping to uid 0 to fail")
	}
}

func TestDropRefusesRootGroup(t *testing.T) {
	if err := privdrop.Drop(privdrop.Account{UID: 10002, GID: 0}); err == nil {
		t.Fatal("expected dropping to gid 0 to fail")
	}
}
