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

// The VM's ownership is knowable for one path only. A named pipe has no Unix
// group, and a forwarded or rootless daemon's socket is owned by something a
// Windows process cannot see, so answering 0 for those would emit a group_add
// describing a group that owns nothing.
func TestDetectDockerSockGIDDoesNotAnswerForAnOtherSocket(t *testing.T) {
	t.Parallel()

	for _, sock := range []string{
		`//./pipe/docker_engine`,
		"/run/user/1000/docker.sock",
		"/var/run/docker.sock.bak",
	} {
		t.Run(sock, func(t *testing.T) {
			t.Parallel()

			if _, ok := DetectDockerSockGID(sock); ok {
				t.Fatalf("DetectDockerSockGID(%q) claimed to know the group", sock)
			}
		})
	}
}
