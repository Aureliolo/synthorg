# module-kind: declarative
"""Providers namespace setting definitions."""

from synthorg.providers.routing.strategies import STRATEGY_MAP
from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="routing_strategy",
        type=SettingType.ENUM,
        default="cost_aware",
        description="Model routing strategy",
        group="Routing",
        enum_values=tuple(sorted(STRATEGY_MAP.keys())),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="retry_max_attempts",
        type=SettingType.INTEGER,
        default="3",
        description="Maximum retry attempts for transient provider errors",
        group="Resilience",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10,
    )
)

# Serviceability window and verdict boundaries. Mirrors the defaults in
# synthorg.providers.serviceability; kept literal here to avoid a
# definitions -> providers import cycle (parity asserted by
# test_serviceability_settings).
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="serviceability_window_seconds",
        type=SettingType.FLOAT,
        default="900.0",
        description=(
            "Trailing window the per-(provider, model) serviceability verdict"
            " is computed over. Deliberately far shorter than the 24-hour"
            " health window: a model that started returning 503 an hour ago"
            " still has a low daily error rate, which is how an unusable"
            " model reads healthy. Read live per request."
        ),
        group="Serviceability",
        level=SettingLevel.ADVANCED,
        min_value=60,
        max_value=86400,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="serviceability_degraded_error_rate_percent",
        type=SettingType.FLOAT,
        default="10.0",
        description=(
            "Failure rate within the serviceability window at or above which"
            " a (provider, model) pair reads degraded."
        ),
        group="Serviceability",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="serviceability_down_error_rate_percent",
        type=SettingType.FLOAT,
        default="50.0",
        description=(
            "Failure rate within the serviceability window at or above which"
            " a (provider, model) pair reads down. A down pair is skipped by"
            " candidate selection and marks the agents bound to it"
            " unavailable, so this is the boundary that actually moves work."
        ),
        group="Serviceability",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="serviceability_min_calls_for_verdict",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Calls required inside the window before a verdict is anything"
            " but unknown. Below it the window withholds judgement, so a"
            " single failure cannot take a pair (and every agent bound to it)"
            " out of service."
        ),
        group="Serviceability",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=1000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="prompt_caching_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Place cache_control breakpoints on the stable prompt prefix so a"
            " caching-capable provider reuses it across turns, cutting"
            " input-token cost and latency. Gated per model on prompt-caching"
            " capability, so non-caching models are unaffected. Resolved live"
            " per run, so a change applies without a restart."
        ),
        group="Resilience",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="configs",
        type=SettingType.JSON,
        default=None,
        description=(
            "LLM provider configurations (JSON object keyed by name). Marked"
            " sensitive so the settings UI masks its value: the blob embeds"
            " provider credentials. Manage providers through the dedicated"
            " Providers page (which redacts secrets per field) rather than"
            " editing this raw JSON setting directly."
        ),
        group="General",
        sensitive=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="discovery_allowlist",
        type=SettingType.JSON,
        default=None,
        description=(
            "Trusted host:port pairs for provider discovery SSRF bypass (JSON)"
        ),
        group="Discovery",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="capability_overrides",
        type=SettingType.JSON,
        default=None,
        description=(
            "Operator / LLM overrides of the per-model capability rung (JSON"
            " envelope). The effective capability of each configured model is"
            " published evidence over the deterministic heuristic"
            " classification, with these overrides on top of both. Manage"
            " through the Model Capability page rather than editing this raw"
            " JSON directly."
        ),
        group="Routing",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="capability_sources",
        type=SettingType.JSON,
        default=None,
        description=(
            "Which published sources contribute capability evidence, and"
            " where each is fetched from (JSON envelope). Unset means every"
            " shipped source is enabled on its default feed: the grading"
            " this feeds exists because the size-and-price proxy it replaces"
            " was wrong, so it is not opt-in. An entry may switch a source"
            " off or point it at a different URL, which is checked against"
            " the network allowlist before anything fetches it. Manage"
            " through the Model Capability page rather than editing this raw"
            " JSON directly."
        ),
        group="Routing",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="capability_source_refresh_interval_days",
        type=SettingType.INTEGER,
        default="7",
        description=(
            "How long a source's evidence may go without a refresh attempt"
            " before one is made. Published leaderboards move daily at most,"
            " so re-fetching more often costs bandwidth and buys nothing."
            " The clock runs from the last ATTEMPT rather than the last"
            " success, so a feed that is failing retries on this cadence"
            " instead of on every request. An operator can always refresh a"
            " source immediately from the Model Capability page, which"
            " ignores this interval."
        ),
        group="Routing",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=365,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="capability_evidence_expert_percentile",
        type=SettingType.FLOAT,
        default="0.75",
        description=(
            "Where the expert rung starts, as a model's standing among the"
            " models its own evidence source measured. A standing rather than"
            " a score because sources publish on different scales: one"
            " reports pass rates and another normalised ratings, so a shared"
            " numeric cutoff would grade the whole of one below the whole of"
            " the other. Raise it to reserve expert for a narrower band."
        ),
        group="Routing",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=1,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="capability_evidence_capable_percentile",
        type=SettingType.FLOAT,
        default="0.35",
        description=(
            "Where the capable rung starts, on the same standing scale as the"
            " expert boundary. Must sit below it; a value at or above the"
            " expert boundary is rejected because it would leave no model"
            " able to be graded capable."
        ),
        group="Routing",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=1,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="capability_evidence_max_age_days",
        type=SettingType.INTEGER,
        default="730",
        description=(
            "How long a source's evidence keeps counting after the last time"
            " that source was successfully read. Published leaderboards do"
            " not date their individual measurements, so this ages a row from"
            " when the source last told us, not from when the benchmark was"
            " run. A row past this age neither grades its model nor counts"
            " towards the group its peers are ranked against, which is what"
            " retires the evidence of a feed that has quietly stopped"
            " answering."
        ),
        group="Routing",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=3650,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="capability_classifier_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the optional LLM capability recommender runs on when"
            " 'Recommend by LLM' is used. A model reference"
            " (`{provider, model_id}`) so it resolves against the provider it"
            " was selected on. Unset by default: the recommend action asks the"
            " operator to pick a model the first time it is used."
        ),
        group="Routing",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="capability_classifier_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Enable the optional LLM-assisted capability recommender. Off by"
            " default: capability assignment uses the deterministic heuristic"
            " classifier unless an operator opts in and picks a classifier"
            " model."
        ),
        group="Routing",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="health_probe_interval_seconds",
        type=SettingType.INTEGER,
        default="300",
        description=(
            "How often the background prober pings providers that expose a"
            " base URL, to notice one going away and to keep a verdict"
            " current for a provider nothing else is calling. A provider"
            " already checked within the interval is skipped, so this also"
            " bounds how stale an idle provider's reading can be. Applies"
            " live: the running prober picks up a change at its next cycle."
        ),
        group="Health",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=86400,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="agent_profile_min_calls",
        type=SettingType.INTEGER,
        default="20",
        description=(
            "How many of an agent's own calls a comparison needs before it"
            " reports rates rather than 'not enough yet'. A success rate over"
            " four calls is not a measurement, and rendering it beside one"
            " over four hundred invites a decision the data cannot support."
            " Read live, so lowering it while a roster is new shows the"
            " numbers on the next read."
        ),
        group="Routing",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="failover_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Let a system feature whose bound connection is failing be served"
            " by the alternate an operator declared for it. Off by default,"
            " and enabling it is a governed write, because it widens what may"
            " answer a bound request: the same model id through two"
            " connections is two different calls, billed and rate-limited"
            " separately. Applies only to system features, which have no"
            " employee to mark out; an agent whose pair is unserviceable"
            " becomes unavailable and its work is reassigned instead. Read"
            " live per dispatch, so switching it off takes the next call."
        ),
        group="Failover",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="failover_routes",
        type=SettingType.JSON,
        default=None,
        description=(
            "Which alternate serves which declared pair, as a JSON object"
            " keyed `provider/model_id` with a `{provider, model_id}`"
            " alternate. Both halves are the operator's: resolution is an"
            " exact-key lookup and nothing is sorted, ranked or scanned, so no"
            " arrangement of the provider registry can produce a fallback"
            " nobody chose. A pair with no entry has no alternate, which reads"
            " the same as the feature being off. Adding a route is a governed"
            " write; removing one narrows and is not. Manage through the"
            " Providers page rather than editing this raw JSON directly."
        ),
        group="Failover",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="failover_event_retention_days",
        type=SettingType.INTEGER,
        default="90",
        description=(
            "How long a recorded failover engagement is kept. The row answers"
            " which connection served a request an operator is looking back"
            " at, so it needs to outlive the cost and usage windows it will be"
            " read alongside."
        ),
        group="Failover",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=3650,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="ollama_default_port",
        type=SettingType.INTEGER,
        default="11434",
        description=(
            "Default port used to detect a self-hosted Ollama provider when"
            " its ``litellm_provider`` field is not set explicitly."
            " The health prober treats a base URL bound to this port as"
            " an Ollama endpoint and pings the root URL (which returns a"
            " liveness string) instead of ``/models``."
        ),
        group="Ollama",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=65535,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="cassette_mode",
        type=SettingType.ENUM,
        default="off",
        enum_values=("off", "record", "replay"),
        description=(
            "Deterministic recorded-LLM cassette mode. 'off' is inert;"
            " 'record' wraps every provider, delegates to the real driver"
            " and persists each response keyed by request; 'replay' serves"
            " recorded responses with zero real LLM calls and never"
            " constructs a real driver. Baked in at process startup."
        ),
        group="Cassette",
        level=SettingLevel.ADVANCED,
        compose_set=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="model_refresh_mode",
        type=SettingType.ENUM,
        default="reconcile_recommend",
        # Mirrors synthorg.providers.management.refresh_config.RefreshMode;
        # kept literal here to avoid a definitions -> providers import cycle
        # (parity asserted by test_model_refresh_settings).
        enum_values=("off", "manual_only", "detect_only", "reconcile_recommend"),
        description=(
            "Periodic model-refresh/reconcile mode. 'reconcile_recommend'"
            " (the default) periodically probes providers, persists refreshed"
            " metadata, flags removed models stale, and feeds in-family"
            " upgrade recommendations for review (auto-apply stays gated by"
            " model_refresh_auto_apply_within_family). 'detect_only' probes"
            " and flags stale models without emitting recommendations;"
            " 'manual_only' runs only on the explicit refresh endpoint; 'off'"
            " schedules nothing. Re-read live each cycle, so changes apply"
            " without a restart."
        ),
        group="Model Refresh",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="model_refresh_interval_seconds",
        type=SettingType.FLOAT,
        default="21600.0",
        description=(
            "Cadence in seconds between automatic model-refresh cycles when"
            " the mode schedules a loop (default 21600 = 6 hours). Floored at"
            " the scheduler minimum. Re-read by the scheduler each tick (like"
            " the mode), so a change applies on the next cycle without a"
            " restart."
        ),
        group="Model Refresh",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=604800.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="model_refresh_auto_apply_within_family",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "When true, strictly in-family upgrade recommendations (same"
            " family, newer generation, no capability regression) are"
            " auto-applied by reassigning pinned agents instead of being"
            " parked for human approval. Cross-family upgrades always wait"
            " for approval. Off by default."
        ),
        group="Model Refresh",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="tool_call_feedback_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "When true, repeated runtime tool-call failures per (provider,"
            " model) are tracked at the provider boundary and a model that"
            " crosses the failure threshold is downgraded"
            " (tool_calls_verified=False) so the matcher stops assigning it"
            " to tool-requiring agents. A genuine tool-call success clears"
            " the signal. Turning it off only stops future tracking; any"
            " accumulator rows and existing tool_calls_verified=False"
            " downgrades from earlier observations persist, so already-"
            "downgraded models stay excluded until re-enabled manually."
        ),
        group="Tool-Call Feedback",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="tool_call_failure_threshold",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Time-decayed failure score at which a (provider, model) is"
            " downgraded as unable to call tools. Each non-retryable"
            " tool-call failure adds 1 to the score; the score decays"
            " exponentially so an old blip fades. Higher tolerates more"
            " transient failures before downgrading."
        ),
        group="Tool-Call Feedback",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=20,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="tool_call_failure_decay_half_life_seconds",
        type=SettingType.INTEGER,
        default="3600",
        description=(
            "Half-life in seconds for the tool-call failure score: a single"
            " failure's weight halves every half-life, so a short provider"
            " outage cannot accumulate into a permanent downgrade. Longer"
            " remembers failures for longer."
        ),
        group="Tool-Call Feedback",
        level=SettingLevel.ADVANCED,
        min_value=60,
        max_value=86400,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="gateway_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Enable the OpenAI-compatible LLM gateway: an in-process HTTP"
            " surface that fronts the provider registry so an embedded coding"
            " harness (OpenHands) can route its LLM calls through SynthOrg's"
            " cost attribution, Explicit Provider Binding, hard token budget"
            " and secret-redacted logging. On by default, because"
            " tools.openhands_enabled is: a wired loop whose every call 503s is"
            " not a capability. The route carries no ambient authority; it"
            " authenticates with a per-run signed bearer and rejects anything"
            " else. Re-enabling after an explicit disable reopens the egress"
            " path, so that transition takes the deliberate"
            " confirm+reason+actor guardrail. Re-read live per request, so"
            " toggling takes effect on the next call without a restart."
        ),
        group="Gateway",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="gateway_token_ttl_seconds",
        type=SettingType.INTEGER,
        default="172800",
        description=(
            "Lifetime in seconds of a per-run gateway bearer token (default 2"
            " days). Must exceed tools.openhands_max_runtime_seconds so a long"
            " OpenHands run is force-ended by the wall-clock cap before its"
            " bearer expires, never left to fail auth mid-run. A run that"
            " outlives its token re-mints on resume, so this also bounds how"
            " long a leaked token stays usable. Re-read live when a run token is"
            " minted."
        ),
        group="Gateway",
        level=SettingLevel.ADVANCED,
        min_value=60,
        max_value=604800,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="gateway_base_url",
        type=SettingType.STRING,
        default="",
        description=(
            "OpenAI-compatible base URL the in-sandbox harness uses to reach"
            " the LLM gateway: the app address reachable through the sandbox"
            " sidecar egress allowlist, including the mounted gateway route, so"
            " the client resolves .../v1/chat/completions. Both compose"
            " deployments set it to"
            " http://host.docker.internal:<published-port>/api/v1/gateway/v1"
            " and give the loop container a matching host-gateway alias, so a"
            " standard install needs no hand configuration. The registered"
            " default is empty, which leaves the OpenHands execution loop"
            " unavailable (it fails loud only if selected) on any deployment"
            " that publishes no such address. Re-read live."
        ),
        group="Gateway",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="cassette_path",
        type=SettingType.STRING,
        default=None,
        description=(
            "Filesystem path to the cassette document. Required whenever"
            " providers.cassette_mode is not 'off'. Baked in at process"
            " startup."
        ),
        group="Cassette",
        level=SettingLevel.ADVANCED,
        compose_set=True,
    )
)
