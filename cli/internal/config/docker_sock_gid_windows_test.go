//go:build windows

package config

import "testing"

// Reporting "unknown" here looked correct (a Windows process genuinely cannot
// stat a socket inside Docker Desktop's Linux VM) and was not: compose then
// emits no `group_add`, the non-root backend gets EACCES on every daemon call,
// and the only symptom is agent tools that cannot run. Pinned because that
// failure is silent at every layer above it.
func TestDetectDockerSockGIDNamesTheGroupTheBackendMustJoin(t *testing.T) {
	t.Parallel()

	gid, ok := DetectDockerSockGID("/var/run/docker.sock")

	if !ok {
		t.Fatal("DetectDockerSockGID reported unknown, so compose emits no group_add")
	}
	if gid != dockerDesktopSockGID {
		t.Fatalf("DetectDockerSockGID = %d, want %d", gid, dockerDesktopSockGID)
	}
}
