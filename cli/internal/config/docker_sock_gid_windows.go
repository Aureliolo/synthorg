//go:build windows

package config

// dockerDesktopSockGID is the group owning the engine socket inside Docker
// Desktop's Linux VM, which is where the path the backend is given resolves.
// It is root:root 0660 there, so a container process running as 65532 needs
// this as a supplementary group to open it at all.
const dockerDesktopSockGID = 0

// DetectDockerSockGID reports the group the backend must join to use the
// mounted Docker socket.
//
// A Windows process cannot stat that socket: it lives inside Docker Desktop's
// Linux VM, not on the Windows filesystem. Reporting "unknown" therefore
// looked correct and was not, because compose then emits no `group_add` and
// the non-root backend gets EACCES on every daemon call, which surfaces only
// as agent tools that cannot run. The VM's ownership is fixed, so it is named
// here instead of probed.
//
// Joining group 0 does not make the process root: the uid stays 65532,
// `cap_drop: ALL` and `no-new-privileges` still apply, and the access it
// unlocks is exactly the daemon access mounting the socket already granted.
func DetectDockerSockGID(_ string) (int, bool) {
	return dockerDesktopSockGID, true
}
