package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

// PinnedKeys returns the set of config keys physically present in the
// persisted state file, keyed by config key name.
//
// A key written to config.json PINS its value: readState starts from
// DefaultState and unmarshals the file over it, so a later change to a
// compiled-in default cannot move a key the file names, while an absent
// key follows it. Comparing a resolved value against DefaultState cannot
// tell those apart, because a pinned value is free to equal the default
// it is pinned against.
//
// State is flat and every readable config key is a top-level JSON tag of
// the same name, so the file's own object keys are the answer with no
// path mapping in between.
//
// An absent state file pins nothing and yields an empty set. A file that
// cannot be read or parsed yields an error wrapping ErrReading or
// ErrParsing, the same classification readState applies.
func PinnedKeys(dataDir string) (map[string]bool, error) {
	safeDir, err := SecurePath(dataDir)
	if err != nil {
		return nil, err
	}
	path := StatePath(safeDir)
	data, readErr := os.ReadFile(path) //nolint:gosec // G304: path is the state file under the SecurePath-cleaned data dir
	if readErr != nil {
		if errors.Is(readErr, os.ErrNotExist) {
			return map[string]bool{}, nil
		}
		return nil, fmt.Errorf("%w %s: %w", ErrReading, path, readErr)
	}
	var raw map[string]json.RawMessage
	if unmarshalErr := json.Unmarshal(data, &raw); unmarshalErr != nil {
		return nil, fmt.Errorf("%w %s: %w", ErrParsing, path, unmarshalErr)
	}
	pinned := make(map[string]bool, len(raw))
	for key := range raw {
		pinned[key] = true
	}
	return pinned, nil
}
