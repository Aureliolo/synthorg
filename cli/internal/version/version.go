// Package version holds build-time version information injected via ldflags.
package version

// RepoURL is the canonical GitHub repository URL.
const RepoURL = "https://github.com/Aureliolo/synthorg"

// Version, Commit, and Date are set by GoReleaser at build time via ldflags.
var (
	Version = "dev"
	Commit  = "none"
	Date    = "unknown"
)
