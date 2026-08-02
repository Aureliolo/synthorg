//go:build !linux

package proxy

import (
	"fmt"
	"net"
	"runtime"
)

func lookupOriginalDst(_ net.Conn) (string, uint16, error) {
	return "", 0, fmt.Errorf("SO_ORIGINAL_DST not supported on %s", runtime.GOOS)
}
