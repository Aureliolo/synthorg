package config

import "fmt"

// Coercion records one persisted enum value that the current binary no
// longer recognises, together with the value substituted in its place.
type Coercion struct {
	// Field is the config.json key, matching the name used by
	// `synthorg config set` and by the validation error messages.
	Field string
	// Rejected is the value found on disk.
	Rejected string
	// Applied is the value now in effect. Empty means the field fell back
	// to "unset", which every *OrDefault accessor reads as the
	// compiled-in default.
	Applied string
	// Allowed lists the values this binary accepts, so the warning can
	// tell the operator what to pick instead of only what was wrong.
	Allowed string
}

// String renders a Coercion for a warning line or a doctor report.
func (c Coercion) String() string {
	applied := c.Applied
	if applied == "" {
		applied = "the built-in default"
	}
	return fmt.Sprintf(
		"%s: %q is not a recognised value, using %s instead (valid: %s)",
		c.Field, c.Rejected, applied, c.Allowed,
	)
}

// enumField is one row of the coercion table: a persisted string field
// whose accepted values are a closed set.
type enumField struct {
	// name is the config.json key.
	name string
	get  func(State) string
	set  func(*State, string)
	// valid reports whether a value is in this release's allowlist.
	valid func(string) bool
	// fallback is the value substituted for an unrecognised one. Empty
	// means "leave the field unset", which is only correct where a
	// *OrDefault accessor resolves an empty value downstream.
	fallback func() string
	// optional marks a field whose empty value is resolved downstream by a
	// *OrDefault accessor, so an omitted value is left alone rather than
	// repaired. Fields WITHOUT such an accessor are not optional here even
	// where Validate tolerates an empty value: log_level and bus_backend
	// are interpolated straight into the compose file, so an empty value
	// would reach the backend container as an empty env var. Coercing them
	// to the explicit default is what stops that.
	optional bool
	// options lists the accepted values for the operator-facing message.
	options func() string
}

// emptyFallback is the fallback for an optional field: clearing the value
// restores the compiled-in default without writing a redundant explicit
// value into the operator's config.
func emptyFallback() string { return "" }

// enumFields is the coercion table. EVERY closed-set string field
// persisted in State must appear here: a value dropped from an allowlist
// that has no row would make Validate reject the whole config, and since
// Load runs Validate that takes down every command including the ones
// meant to repair the install. TestCoerceCoversEveryEnum fails when an
// allowlist is added without a row.
var enumFields = []enumField{
	{
		name:     "persistence_backend",
		get:      func(s State) string { return s.PersistenceBackend },
		set:      func(s *State, v string) { s.PersistenceBackend = v },
		valid:    IsValidPersistenceBackend,
		fallback: func() string { return DefaultState().PersistenceBackend },
		options:  PersistenceBackendNames,
	},
	{
		name:     "memory_backend",
		get:      func(s State) string { return s.MemoryBackend },
		set:      func(s *State, v string) { s.MemoryBackend = v },
		valid:    IsValidMemoryBackend,
		fallback: func() string { return DefaultState().MemoryBackend },
		options:  MemoryBackendNames,
	},
	{
		name:     "bus_backend",
		get:      func(s State) string { return s.BusBackend },
		set:      func(s *State, v string) { s.BusBackend = v },
		valid:    IsValidBusBackend,
		fallback: func() string { return DefaultState().BusBackend },
		options:  BusBackendNames,
	},
	{
		name:     "channel",
		get:      func(s State) string { return s.Channel },
		set:      func(s *State, v string) { s.Channel = v },
		valid:    IsValidChannel,
		fallback: emptyFallback,
		optional: true,
		options:  ChannelNames,
	},
	{
		name:     "log_level",
		get:      func(s State) string { return s.LogLevel },
		set:      func(s *State, v string) { s.LogLevel = v },
		valid:    IsValidLogLevel,
		fallback: func() string { return DefaultState().LogLevel },
		options:  LogLevelNames,
	},
	{
		name:     "color",
		get:      func(s State) string { return s.Color },
		set:      func(s *State, v string) { s.Color = v },
		valid:    IsValidColorMode,
		fallback: emptyFallback,
		optional: true,
		options:  ColorModeNames,
	},
	{
		name:     "output",
		get:      func(s State) string { return s.Output },
		set:      func(s *State, v string) { s.Output = v },
		valid:    IsValidOutputMode,
		fallback: emptyFallback,
		optional: true,
		options:  OutputModeNames,
	},
	{
		name:     "timestamps",
		get:      func(s State) string { return s.Timestamps },
		set:      func(s *State, v string) { s.Timestamps = v },
		valid:    IsValidTimestampMode,
		fallback: emptyFallback,
		optional: true,
		options:  TimestampModeNames,
	},
	{
		name:     "hints",
		get:      func(s State) string { return s.Hints },
		set:      func(s *State, v string) { s.Hints = v },
		valid:    IsValidHintsMode,
		fallback: emptyFallback,
		optional: true,
		options:  HintsModeNames,
	},
	{
		name:     "changelog_view",
		get:      func(s State) string { return s.ChangelogView },
		set:      func(s *State, v string) { s.ChangelogView = v },
		valid:    IsValidChangelogView,
		fallback: emptyFallback,
		optional: true,
		options:  ChangelogViewNames,
	},
	{
		// validateFineTuning rejects an unrecognised variant whether or
		// not fine-tuning is switched on, so a dropped variant is the same
		// brick as a dropped backend and belongs in the same table.
		name:     "fine_tuning_variant",
		get:      func(s State) string { return s.FineTuningVariant },
		set:      func(s *State, v string) { s.FineTuningVariant = v },
		valid:    isValidFineTuneVariant,
		fallback: emptyFallback,
		optional: true,
		options:  FineTuneVariantNames,
	},
}

// Coerce replaces every persisted enum value this binary does not
// recognise with the field's default, returning the updated State and one
// Coercion per replacement.
//
// This exists so that removing a value from an allowlist can never strand
// an install. Load runs Validate, so without coercion a config written by
// an older release fails to load and EVERY command refuses to run,
// including `synthorg init` and `synthorg doctor` -- the two that exist to
// repair exactly this. Coercing keeps the install usable; the caller is
// responsible for warning, and the value on disk is left untouched until
// something persists State again.
func Coerce(s State) (State, []Coercion) {
	var applied []Coercion
	for _, f := range enumFields {
		value := f.get(s)
		if f.optional && value == "" {
			// Already "use the default": nothing was configured, so there
			// is nothing to report.
			continue
		}
		if f.valid(value) {
			continue
		}
		fallback := f.fallback()
		f.set(&s, fallback)
		applied = append(applied, Coercion{
			Field:    f.name,
			Rejected: value,
			Applied:  fallback,
			Allowed:  f.options(),
		})
	}
	return s, applied
}
