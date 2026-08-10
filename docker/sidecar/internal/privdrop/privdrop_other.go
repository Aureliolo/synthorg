//go:build !linux

package privdrop

import (
	"fmt"
	"runtime"
)

func drop(_ Account) error {
	return fmt.Errorf("privilege drop not supported on %s", runtime.GOOS)
}
