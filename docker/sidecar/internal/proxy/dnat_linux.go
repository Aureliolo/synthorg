//go:build linux

package proxy

import (
	"encoding/binary"
	"fmt"
	"net"
	"syscall"
	"unsafe"
)

const soOriginalDst = 80

// sockaddrInet4 matches the kernel struct sockaddr_in layout.
type sockaddrInet4 struct {
	Family uint16
	Port   [2]byte
	Addr   [4]byte
	Zero   [8]byte
}

func lookupOriginalDst(conn net.Conn) (string, uint16, error) {
	tcpConn, ok := conn.(*net.TCPConn)
	if !ok {
		return "", 0, fmt.Errorf("not a TCP connection")
	}

	rawConn, err := tcpConn.SyscallConn()
	if err != nil {
		return "", 0, fmt.Errorf("SyscallConn: %w", err)
	}

	var sa sockaddrInet4
	var sockErr error
	err = rawConn.Control(func(fd uintptr) {
		size := uint32(unsafe.Sizeof(sa))
		_, _, errno := syscall.Syscall6(
			syscall.SYS_GETSOCKOPT,
			fd,
			syscall.SOL_IP,
			soOriginalDst,
			uintptr(unsafe.Pointer(&sa)),
			uintptr(unsafe.Pointer(&size)),
			0,
		)
		if errno != 0 {
			sockErr = fmt.Errorf("getsockopt SO_ORIGINAL_DST: %w", errno)
		}
	})
	if err != nil {
		return "", 0, fmt.Errorf("rawConn.Control: %w", err)
	}
	if sockErr != nil {
		return "", 0, sockErr
	}

	ip := net.IPv4(sa.Addr[0], sa.Addr[1], sa.Addr[2], sa.Addr[3]).String()
	port := binary.BigEndian.Uint16(sa.Port[:])
	return ip, port, nil
}
