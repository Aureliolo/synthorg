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

// Logger mirrors docker/sidecar/internal/health/health.go's Logger
// interface. Variadic any kvs is the surface CodeQL's go/log-injection
// query analyses on the production caller; modelling it here proves the
// rule's behaviour against int/bool args end-to-end.
type Logger interface {
	Info(msg string, kvs ...any)
	Warn(msg string, kvs ...any)
}

// NegativeLogInjection is the sanitised idiom: every variadic arg is
// either a static label string, a non-string scalar (int / bool), or a
// constant. CodeQL must NOT flag this as go/log-injection because no
// user-controlled string flows into the log call.
func NegativeLogInjection(logger Logger, hostCount int, allowAll bool) {
	logger.Info("rules.updated", "count", hostCount, "allow_all", allowAll)
}

// PositiveLogInjection is the deliberate genuine leak: a user-controlled
// string flows straight into the log call without sanitisation. CodeQL
// MUST fire go/log-injection here.
func PositiveLogInjection(logger Logger, userInput string) {
	logger.Info("user said", "value", userInput)
}
