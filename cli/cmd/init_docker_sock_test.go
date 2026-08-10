package cmd

import (
	"strings"
	"testing"
)

// The socket is mounted into a LINUX container on every host, so the Windows
// named pipe is never the right default: Docker binds it as an empty directory
// rather than refusing, which leaves every sandbox-backed agent tool dead with
// nothing in any log to say why. Pinned because the failure is silent, so a
// regression would only surface as agents that cannot run a command.
func TestDefaultDockerSockIsTheLinuxSocketOnEveryHost(t *testing.T) {
	t.Parallel()

	got := defaultDockerSock()

	if got != "/var/run/docker.sock" {
		t.Fatalf("defaultDockerSock() = %q, want the Linux socket path", got)
	}
	if strings.Contains(got, "pipe") {
		t.Fatalf("defaultDockerSock() = %q: a named pipe cannot reach a Linux container", got)
	}
}

// An operator running a Windows-container daemon can still name the pipe, so
// the validator must keep accepting it; only the DEFAULT changed.
func TestValidateDockerSockStillAcceptsAWindowsPipe(t *testing.T) {
	t.Parallel()

	if err := validateDockerSock("//./pipe/docker_engine"); err != nil {
		t.Fatalf("validateDockerSock(named pipe) = %v, want nil", err)
	}
}

// The value names a path in the DAEMON's namespace, which is Linux on every
// host. On Windows `filepath.IsAbs` wants a drive letter, so without the POSIX
// arm an operator there cannot set the one value that reaches a Linux
// container, whatever they know.
func TestValidateDockerSockAcceptsThePosixSocketOnEveryHost(t *testing.T) {
	t.Parallel()

	if err := validateDockerSock("/var/run/docker.sock"); err != nil {
		t.Fatalf("validateDockerSock(posix socket) = %v, want nil", err)
	}
}

func TestValidateDockerSockStillRejectsARelativePath(t *testing.T) {
	t.Parallel()

	for _, path := range []string{"docker.sock", "./docker.sock", "run/docker.sock"} {
		if err := validateDockerSock(path); err == nil {
			t.Fatalf("validateDockerSock(%q) = nil, want a rejection", path)
		}
	}
}
