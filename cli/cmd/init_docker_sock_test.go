package cmd

import (
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
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
		t.Run(path, func(t *testing.T) {
			t.Parallel()

			if err := validateDockerSock(path); err == nil {
				t.Fatalf("validateDockerSock(%q) = nil, want a rejection", path)
			}
		})
	}
}

// The GID belongs to the socket. Both mutation paths must keep them in step,
// or compose renders a group_add describing a group that does not own the
// configured socket, and the backend gets EACCES on every daemon call with
// nothing above it saying why.
func TestConfigSetDockerSockRederivesTheGID(t *testing.T) {
	t.Parallel()

	state := config.State{DockerSock: "/tmp/old.sock", DockerSockGID: 4242}

	setter, ok := configSetters["docker_sock"]
	if !ok {
		t.Fatal("no setter registered for docker_sock")
	}
	if err := setter(&state, config.DefaultDockerSockPath); err != nil {
		t.Fatalf("setter returned %v, want nil", err)
	}

	if state.DockerSock != config.DefaultDockerSockPath {
		t.Fatalf("DockerSock = %q, want the new path", state.DockerSock)
	}
	want, _ := config.DetectDockerSockGID(config.DefaultDockerSockPath)
	if state.DockerSockGID == 4242 {
		t.Fatal("DockerSockGID carried over from the previous socket")
	}
	if state.DockerSockGID != want {
		t.Fatalf("DockerSockGID = %d, want %d", state.DockerSockGID, want)
	}
}

// Resetting is the third mutation site and the one most easily missed: it
// clears the path, so leaving the group behind describes nothing at all.
func TestConfigResetDockerSockAlsoClearsTheGID(t *testing.T) {
	t.Parallel()

	state := config.State{DockerSock: "/tmp/old.sock", DockerSockGID: 4242}
	defaults := config.DefaultState()

	resetter, ok := configResetters["docker_sock"]
	if !ok {
		t.Fatal("no resetter registered for docker_sock")
	}
	resetter(&state, defaults)

	if state.DockerSock != defaults.DockerSock {
		t.Fatalf("DockerSock = %q, want %q", state.DockerSock, defaults.DockerSock)
	}
	if state.DockerSockGID != defaults.DockerSockGID {
		t.Fatalf(
			"DockerSockGID = %d, want the default sentinel %d",
			state.DockerSockGID,
			defaults.DockerSockGID,
		)
	}
}
