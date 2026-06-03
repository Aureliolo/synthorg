//go:build !windows

package diagnostics

import "syscall"

// diskUsage returns total and free bytes for the filesystem containing path.
func diskUsage(path string) (total, free uint64, err error) {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		return 0, 0, err
	}
	total = stat.Blocks * uint64(stat.Bsize) //nolint:gosec // G115: Bsize is the statfs block size, always non-negative
	free = stat.Bavail * uint64(stat.Bsize)  //nolint:gosec // G115: Bsize is the statfs block size, always non-negative
	return total, free, nil
}
