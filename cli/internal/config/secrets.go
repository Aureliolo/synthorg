package config

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"strings"
)

// masterKeyBytes is the raw byte length of a Fernet master key (32 bytes,
// URL-safe base64 -> 44 characters). Matches the format validated by
// validateFernetKey and required by Python cryptography.fernet.Fernet.
const masterKeyBytes = 32

// GenerateMasterKey returns a fresh Fernet-compatible master key: 32 random
// bytes encoded as URL-safe base64 (44 characters). It is the single source
// of master-key material shared by `init` and `config set` / `config
// import`, so the key format stays consistent across every write path.
func GenerateMasterKey() (string, error) {
	b := make([]byte, masterKeyBytes)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("generating master key: %w", err)
	}
	return base64.URLEncoding.EncodeToString(b), nil
}

// EnsureMasterKey generates and assigns a master key on s when secret
// encryption is enabled but no key is set yet, mirroring what `init` does on
// save. Returns true when a key was generated. A no-op (returns false) when
// encryption is disabled or a key already exists, so callers can persist a
// pre-init `config set` of an unrelated key without tripping the
// master-key-required invariant.
func EnsureMasterKey(s *State) (bool, error) {
	if !s.EncryptSecrets || strings.TrimSpace(s.MasterKey) != "" {
		return false, nil
	}
	key, err := GenerateMasterKey()
	if err != nil {
		return false, err
	}
	s.MasterKey = key
	return true, nil
}
