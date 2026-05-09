// Package codeqlfixtures contains negative and positive fixtures for the
// CodeQL Models-as-Data sanitiser pack. The package is built as part of the
// cli module so go/path-injection extraction can resolve the
// internal/config import; nothing imports it from production code.
//
// The pack-validation workflow (.github/workflows/codeql-pack-validate.yml)
// runs CodeQL against this package and asserts:
//
//   - NegativePathInjection: go/path-injection MUST NOT fire (sanitised).
//   - PositivePathInjection: go/path-injection MUST fire (genuine leak).
//
// If either assertion changes, the pack is over- or under-modelling and
// the gate fails.
package codeqlfixtures

import (
	"os"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// NegativePathInjection is the sanitised idiom: SecurePath validates the
// input is absolute and returns filepath.Clean'd output. CodeQL must NOT
// flag this as go/path-injection when the synthorg-sanitisers extension
// pack is loaded.
func NegativePathInjection(userInput string) ([]byte, error) {
	safe, err := config.SecurePath(userInput)
	if err != nil {
		return nil, err
	}
	return os.ReadFile(safe)
}

// PositivePathInjection is the deliberate genuine leak: user input flows
// straight into os.ReadFile with no validation. CodeQL MUST fire
// go/path-injection here even with the extension pack loaded.
func PositivePathInjection(userInput string) ([]byte, error) {
	return os.ReadFile(userInput)
}
