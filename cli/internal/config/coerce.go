package config

import "fmt"

// rejectedValueLimit caps how much of an unrecognised on-disk value is
// echoed back. The value is arbitrary operator-supplied text that reaches
// stderr on every invocation and the 0600 doctor report, so a pathological
// config must not be able to flood either.
const rejectedValueLimit = 64

// Coercion records one persisted enum value that the current binary no
// longer recognises, together with the value substituted in its place.
type Coercion struct {
	// Field is the config.json key, matching the name used by
	// `synthorg config set` and by the validation error messages.
	Field string
	// Rejected is the value found on disk, truncated to
	// rejectedValueLimit.
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
	// An explicitly empty value is not "unrecognised", it is unset, and
	// telling an operator that "" is not a recognised value reads as a
	// bug in the CLI rather than a repair of their config.
	if c.Rejected == "" {
		return fmt.Sprintf(
			"%s: no value set, using %s instead (valid: %s)",
			c.Field, applied, c.Allowed,
		)
	}
	return fmt.Sprintf(
		"%s: %q is not a recognised value, using %s instead (valid: %s)",
		c.Field, c.Rejected, applied, c.Allowed,
	)
}

// enumField is one row of the coercion table: a persisted string field
// whose accepted values are a closed set AND whose blast radius is small
// enough that substituting a default is a repair rather than a silent
// change of behaviour. See nonCoercibleEnums for the fields deliberately
// excluded.
type enumField struct {
	// name is the config.json key.
	name string
	// accessor returns a pointer to the field on s, so one function
	// covers both the read and the write. Mirrors the shape
	// cmd/config_dispatch.go already uses for these same fields.
	accessor func(*State) *string
	// valid reports whether a value is in this release's allowlist.
	valid func(string) bool
	// fallback is the value substituted for an unrecognised one. Empty
	// means "leave the field unset", which is only correct where a
	// *OrDefault accessor resolves an empty value downstream.
	fallback func() string
	// emptyIsSafe marks a field whose empty value is already resolved
	// downstream, so an omitted value is left alone rather than repaired.
	//
	// This is a DIFFERENT axis from Validate's required/optional split
	// (checkEnumRequired vs checkEnumOptional in validate.go), and the two
	// deliberately disagree for one field. Validate treats log_level as
	// optional, but compose/generate.go interpolates State.LogLevel into
	// the compose file with no fallback, so an empty value would reach the
	// backend container as an empty SYNTHORG_LOG_LEVEL. It is therefore
	// NOT emptyIsSafe here. bus_backend looks like the same case but is
	// not: ParamsFromState already defaults it to "internal", so its row
	// only guards the callers that read State.BusBackend directly.
	emptyIsSafe bool
	// options lists the accepted values for the operator-facing message.
	options func() string
}

// emptyFallback is the fallback for a field whose empty value is resolved
// downstream: clearing the value restores the compiled-in default without
// writing a redundant explicit value into the operator's config.
func emptyFallback() string { return "" }

// nonCoercibleEnum names a closed-set field that must NOT be coerced, with
// the reason recorded next to it. Substituting a default here would not
// repair the install, it would silently point the running stack at
// different state than the operator configured.
type nonCoercibleEnum struct {
	name   string
	reason string
}

// nonCoercibleEnums are the closed-set fields deliberately excluded from
// enumFields. Defaulting a data-location field is not a recoverable
// substitution: `synthorg start` regenerates compose.yml from the loaded
// state, so a coerced persistence_backend would drop the postgres service,
// point the backend at an empty SQLite file, and bring up a stack that
// looks healthy while the operator's data sits in an orphaned volume. An
// empty database also re-arms the unauthenticated first-run admin claim.
//
// These keep failing Validate. The install stays repairable because the
// commands that exist to repair it (init, doctor, config) read through the
// lenient loaders instead of the strict one.
var nonCoercibleEnums = []nonCoercibleEnum{
	{
		name: "persistence_backend",
		reason: "selects which database the stack runs against; defaulting it " +
			"would silently start against an empty one",
	},
	{
		name: "memory_backend",
		reason: "selects where agent memory and its embeddings live; defaulting " +
			"it would silently start against an empty store",
	},
}

// enumFields is the coercion table. Every closed-set string field
// persisted in State must appear either here or in nonCoercibleEnums.
//
// A value dropped from an allowlist that appears in neither makes Validate
// reject the whole config, and since Load runs Validate, every command
// reading through it stops working: start, status, logs, update, backup,
// and doctor's own second read. init is the exception -- it carries
// secrets forward through LoadForReinit, which skips Validate -- but the
// operator still has to know that init is the way out, having just been
// told by five other commands that their config is invalid.
//
// TestEveryAllowlistIsClassified fails when an allowlist is added to
// neither list.
var enumFields = []enumField{
	{
		// Not emptyIsSafe despite Validate treating it as optional:
		// ParamsFromState defaults it, but a direct State.BusBackend read
		// would still see "".
		name:     "bus_backend",
		accessor: func(s *State) *string { return &s.BusBackend },
		valid:    IsValidBusBackend,
		fallback: func() string { return DefaultState().BusBackend },
		options:  BusBackendNames,
	},
	{
		name:        "channel",
		accessor:    func(s *State) *string { return &s.Channel },
		valid:       IsValidChannel,
		fallback:    emptyFallback,
		emptyIsSafe: true,
		options:     ChannelNames,
	},
	{
		// See emptyIsSafe's doc: compose interpolates this one raw.
		name:     "log_level",
		accessor: func(s *State) *string { return &s.LogLevel },
		valid:    IsValidLogLevel,
		fallback: func() string { return DefaultState().LogLevel },
		options:  LogLevelNames,
	},
	{
		name:        "color",
		accessor:    func(s *State) *string { return &s.Color },
		valid:       IsValidColorMode,
		fallback:    emptyFallback,
		emptyIsSafe: true,
		options:     ColorModeNames,
	},
	{
		name:        "output",
		accessor:    func(s *State) *string { return &s.Output },
		valid:       IsValidOutputMode,
		fallback:    emptyFallback,
		emptyIsSafe: true,
		options:     OutputModeNames,
	},
	{
		name:        "timestamps",
		accessor:    func(s *State) *string { return &s.Timestamps },
		valid:       IsValidTimestampMode,
		fallback:    emptyFallback,
		emptyIsSafe: true,
		options:     TimestampModeNames,
	},
	{
		name:        "hints",
		accessor:    func(s *State) *string { return &s.Hints },
		valid:       IsValidHintsMode,
		fallback:    emptyFallback,
		emptyIsSafe: true,
		options:     HintsModeNames,
	},
	{
		name:        "changelog_view",
		accessor:    func(s *State) *string { return &s.ChangelogView },
		valid:       IsValidChangelogView,
		fallback:    emptyFallback,
		emptyIsSafe: true,
		options:     ChangelogViewNames,
	},
	{
		// validateFineTuning rejects an unrecognised variant whether or
		// not fine-tuning is switched on, so a dropped variant is the same
		// brick as a dropped backend. Coercible because the variant only
		// selects which image a feature pulls, not where data lives.
		name:        "fine_tuning_variant",
		accessor:    func(s *State) *string { return &s.FineTuningVariant },
		valid:       isValidFineTuneVariant,
		fallback:    emptyFallback,
		emptyIsSafe: true,
		options:     FineTuneVariantNames,
	},
}

// truncateRejected bounds an arbitrary on-disk value before it is echoed
// to stderr or written into a diagnostic report.
func truncateRejected(value string) string {
	if len(value) <= rejectedValueLimit {
		return value
	}
	return value[:rejectedValueLimit] + "..."
}

// Coerce replaces every persisted enum value this binary does not
// recognise with the field's default, returning the updated State and one
// Coercion per replacement.
//
// This exists so that removing a value from an allowlist can never strand
// an install. Load runs Validate, so without coercion a config written by
// an older release fails to load and every command reading through Load
// refuses to run. The repair commands survive a non-coercible failure by a
// different route: init reads through LoadForReinit and doctor / config
// through LoadTolerant, all of which skip Validate entirely.
//
// Coercion is deliberately limited to fields whose default is a repair
// rather than a change of behaviour; see nonCoercibleEnums.
func Coerce(s State) (State, []Coercion) {
	var applied []Coercion
	for _, f := range enumFields {
		field := f.accessor(&s)
		value := *field
		if f.emptyIsSafe && value == "" {
			// Already "use the default": nothing was configured, so there
			// is nothing to report.
			continue
		}
		if f.valid(value) {
			continue
		}
		fallback := f.fallback()
		*field = fallback
		applied = append(applied, Coercion{
			Field:    f.name,
			Rejected: truncateRejected(value),
			Applied:  fallback,
			Allowed:  f.options(),
		})
	}
	return s, applied
}
