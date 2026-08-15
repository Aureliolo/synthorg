# module-kind: code
"""Read the persisted ``providers.configs`` blob into a provider map.

Separate from :mod:`synthorg.config.provider_schema`, which owns the shape
a provider config has. This owns what a persisted blob *means*, which is a
different question with a different failure posture: the schema refuses
anything malformed, while a reader facing an operator's live deployment has
to decide what a malformed part costs.

It costs that part. A blob is a map of independent connections, each with
its own credentials and endpoint, so one entry the current schema will not
accept says nothing about the others. Validating the envelope as a single
model would tie every entry's fate to the worst one: one retired key on
one provider would drop an operator's entire provider set, and because an
empty result is indistinguishable from an unconfigured deployment, the
system would report a first-run empty company while the configuration sits
intact in the database.

So the result carries a status. ``UNREADABLE`` is not ``OK`` with nothing
in it, and the caller that decides whether this is a first run reads the
difference rather than inferring it from a count.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.budget.retired_degradation import strip_retired_degradation_settings
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import describe_without_input

PROVIDERS_CONFIG_SCHEMA_VERSION: Final[int] = 1

_DEGRADATION_FIELD: Final[str] = "degradation"
_PROVIDERS_FIELD: Final[str] = "providers"


class ProviderConfigsStatus(StrEnum):
    """How much of a persisted provider blob could be read.

    Attributes:
        OK: Every entry validated. The map is the operator's own.
        PARTIAL: Some entries validated. The map holds those; the rest are
            reported, and the deployment runs on what survived.
        UNREADABLE: Nothing usable could be read, so the map is the
            caller's fallback and means nothing about what the operator
            configured. Distinct from an empty ``OK``, which is a genuinely
            unconfigured deployment.
    """

    OK = "ok"
    PARTIAL = "partial"
    UNREADABLE = "unreadable"


class RejectedProviderConfig(BaseModel):
    """One provider entry the current schema will not accept.

    Attributes:
        name: The provider name the entry was keyed by.
        reason: Why it was rejected, redacted: a provider entry carries
            credentials, and a validation error quotes its input.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: str = Field(description="Provider name the rejected entry was keyed by")
    reason: str = Field(description="Redacted description of the rejection")


class CoercedProviderSetting(BaseModel):
    """One retired setting stripped from an entry as it was read.

    Attributes:
        name: The provider whose entry carried it.
        setting: The retired setting that was removed or replaced.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: str = Field(description="Provider whose entry carried the retired setting")
    setting: str = Field(description="Retired setting removed from the entry")


class ProviderConfigsRead(BaseModel):
    """The outcome of reading a persisted ``providers.configs`` blob.

    Attributes:
        status: How much of the blob could be read.
        providers: The usable provider map. The caller's fallback when the
            status is ``UNREADABLE``.
        rejected: Every entry that could not be read, with its reason.
        coerced: Every retired setting stripped while reading. Reported
            rather than logged, because the reader runs on every provider
            read and a stale setting would otherwise log forever; the
            caller that reports it does so once.
        detail: Why the envelope itself was unusable, when it was. Kept
            apart from *rejected*, which names entries: a blob whose
            version stamp is unknown has no entries to blame, and giving
            one a placeholder name would put a provider that does not
            exist in front of an operator.
        reason: Which part of the envelope was unusable, as a stable slug.
            Carried beside the prose *detail* because an alert keys on a
            slug and cannot key on a sentence. Internal to the read: the
            operator-facing diagnostics deliberately carry the prose only.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: ProviderConfigsStatus = Field(description="How much could be read")
    providers: dict[str, ProviderConfig] = Field(description="The usable provider map")
    rejected: tuple[RejectedProviderConfig, ...] = Field(
        default=(),
        description="Entries the current schema will not accept",
    )
    coerced: tuple[CoercedProviderSetting, ...] = Field(
        default=(),
        description="Retired settings stripped while reading",
    )
    detail: str | None = Field(
        default=None,
        description="Why the envelope itself was unusable, when it was",
    )
    reason: str | None = Field(
        default=None,
        description="Which part of the envelope was unusable, as a stable slug",
    )


class ProviderConfigDiagnostics(BaseModel):
    """What the last read of the persisted blob made of it, kept for asking.

    The notification raised when a config cannot be read is a moment: it
    needs a sink configured to reach anyone, and it is gone by the time an
    operator looks. This is the durable answer to the same question, so a
    deployment whose providers were rejected can be told apart from one
    that has never been configured by anything that can make a request,
    rather than only by whoever was reading the log at boot.

    Deliberately does not carry the provider map. The map is available
    from the registry, and every entry in it holds credentials this has no
    reason to duplicate.

    Attributes:
        status: What the read made of the blob.
        rejected: Every entry that could not be read, with its redacted
            reason.
        coerced: Every retired setting stripped while reading.
        detail: Why the envelope itself was unusable, when it was.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: ProviderConfigsStatus = Field(description="What the read made of the blob")
    rejected: tuple[RejectedProviderConfig, ...] = Field(
        default=(),
        description="Entries the current schema will not accept",
    )
    coerced: tuple[CoercedProviderSetting, ...] = Field(
        default=(),
        description="Retired settings stripped while reading",
    )
    detail: str | None = Field(
        default=None,
        description="Why the envelope itself was unusable, when it was",
    )

    @classmethod
    def of(cls, read: ProviderConfigsRead) -> Self:
        """Summarise *read* for later reporting.

        Returns:
            The diagnostics, without the provider map.
        """
        return cls(
            status=read.status,
            rejected=read.rejected,
            coerced=read.coerced,
            detail=read.detail,
        )


class _ProvidersConfigShell(BaseModel):
    """The envelope around the provider map, without judging its values.

    Read when the whole-envelope validation fails, so the version stamp and
    the container shape can be judged before any single entry is allowed to
    speak for the rest.

    ``providers`` is deliberately typed as a plain mapping of ``object``
    rather than a mapping of dicts. Requiring each value to be dict-shaped
    here would put every entry back inside one validation: a single entry
    that is a string, a ``null`` from a partial write, or a blank key would
    fail the shell and take the whole blob down with it, which is the
    failure this module exists to prevent, one layer up from where it was
    found. An entry that is not a usable mapping is rejected by name in the
    per-entry pass instead.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    schema_version: int = Field(description="Schema version of the persisted blob")
    providers: dict[str, object] = Field(
        default_factory=dict,
        description="Unvalidated provider entries keyed by provider name",
    )


class ProvidersConfigEnvelope(BaseModel):
    """Versioned wrapper for the persisted ``providers.configs`` blob.

    The ``providers.configs`` setting stores the full provider dict as a
    JSON value. Wrapping it in a versioned envelope lets the reader reject
    a blob written by an incompatible schema (or a corrupt write) rather
    than silently mis-parsing it. The ``providers`` map is keyed by
    provider name; values are full ``ProviderConfig`` models, so a
    round-trip is lossless.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    schema_version: int = Field(
        description="Schema version of the persisted provider-config blob",
    )
    providers: dict[NotBlankStr, ProviderConfig] = Field(
        default_factory=dict,
        description="Provider configurations keyed by provider name",
    )


def read_provider_configs(
    raw: object,
    fallback: dict[str, ProviderConfig],
) -> ProviderConfigsRead:
    """Read *raw* into a provider map, reporting whatever it could not read.

    Retired settings are stripped before anything is validated, not after a
    failure: the model refuses them by raising, and that raise carries a
    WARNING the operator can do nothing about, on a read that happens on
    every provider lookup. Cleaning first means a blob whose only problem
    is a retired setting reads exactly like a healthy one, once.

    The cleaned envelope is then validated whole, so a healthy blob (the
    overwhelming case) pays nothing for the per-entry path. Only a genuine
    failure re-reads it entry by entry, which is where one bad entry is
    confined to itself.

    Args:
        raw: The JSON-decoded ``providers.configs`` value.
        fallback: Provider map returned verbatim when nothing usable can be
            read. Never merged with what was read: it describes the code
            defaults, not the deployment.

    Returns:
        The read outcome, whose ``status`` distinguishes an unreadable blob
        from a deployment with no providers configured.
    """
    if not isinstance(raw, dict):
        return _unreadable(
            fallback,
            reason="expected_dict",
            detail=f"envelope is {type(raw).__name__}, not a mapping",
        )
    cleaned, coerced = _strip_retired_settings(raw)
    try:
        envelope = ProvidersConfigEnvelope.model_validate(cleaned)
    except ValidationError:
        return _read_each_entry(cleaned, fallback, coerced)
    if envelope.schema_version != PROVIDERS_CONFIG_SCHEMA_VERSION:
        return _unreadable(
            fallback,
            reason="unknown_schema_version",
            detail=(
                f"blob is version {envelope.schema_version}, this build reads"
                f" version {PROVIDERS_CONFIG_SCHEMA_VERSION}"
            ),
        )
    return ProviderConfigsRead(
        status=ProviderConfigsStatus.OK,
        providers=dict(envelope.providers),
        coerced=coerced,
    )


def _read_each_entry(
    raw: dict[str, object],
    fallback: dict[str, ProviderConfig],
    coerced: tuple[CoercedProviderSetting, ...],
) -> ProviderConfigsRead:
    """Read every entry on its own, keeping the ones that validate.

    Returns:
        The read outcome. ``UNREADABLE`` when the envelope around the
        entries is itself unusable, or when no entry survived.
    """
    try:
        shell = _ProvidersConfigShell.model_validate(raw)
    except ValidationError as exc:
        return _unreadable(
            fallback,
            reason="invalid_envelope",
            detail=describe_without_input(exc),
        )
    if shell.schema_version != PROVIDERS_CONFIG_SCHEMA_VERSION:
        return _unreadable(
            fallback,
            reason="unknown_schema_version",
            detail=(
                f"blob is version {shell.schema_version}, this build reads"
                f" version {PROVIDERS_CONFIG_SCHEMA_VERSION}"
            ),
        )

    providers: dict[str, ProviderConfig] = {}
    rejected: list[RejectedProviderConfig] = []
    for name, entry in shell.providers.items():
        if not name.strip():
            rejected.append(
                RejectedProviderConfig(
                    name=name,
                    reason="provider name is blank, so nothing can be bound to it",
                )
            )
            continue
        try:
            providers[name] = ProviderConfig.model_validate(entry)
        except ValidationError as exc:
            rejected.append(
                RejectedProviderConfig(
                    name=name,
                    reason=describe_without_input(exc),
                )
            )
    if not providers and rejected:
        return ProviderConfigsRead(
            status=ProviderConfigsStatus.UNREADABLE,
            providers=fallback,
            rejected=tuple(rejected),
            coerced=coerced,
            reason="no_entry_validated",
        )
    return ProviderConfigsRead(
        status=(
            ProviderConfigsStatus.PARTIAL if rejected else ProviderConfigsStatus.OK
        ),
        providers=providers,
        rejected=tuple(rejected),
        coerced=coerced,
    )


def _strip_retired_settings(
    raw: dict[str, object],
) -> tuple[dict[str, object], tuple[CoercedProviderSetting, ...]]:
    """Return *raw* with every entry's retired degradation settings removed.

    Returns:
        The blob to validate and what was stripped from it. *raw* itself
        when nothing was, so an ordinary read allocates nothing extra.
    """
    entries = raw.get(_PROVIDERS_FIELD)
    if not isinstance(entries, Mapping):
        return raw, ()
    coerced: list[CoercedProviderSetting] = []
    cleaned_entries: dict[object, object] = {}
    for name, entry in entries.items():
        cleaned_entries[name] = entry
        if not isinstance(entry, Mapping):
            continue
        degradation = entry.get(_DEGRADATION_FIELD)
        if not isinstance(degradation, Mapping):
            continue
        cleaned, stripped = strip_retired_degradation_settings(degradation)
        if not stripped:
            continue
        coerced.extend(
            CoercedProviderSetting(name=str(name), setting=setting)
            for setting in stripped
        )
        cleaned_entries[name] = {**entry, _DEGRADATION_FIELD: cleaned}
    if not coerced:
        return raw, ()
    return {**raw, _PROVIDERS_FIELD: cleaned_entries}, tuple(coerced)


def _unreadable(
    fallback: dict[str, ProviderConfig],
    *,
    reason: str,
    detail: str,
) -> ProviderConfigsRead:
    """Return the unreadable outcome, reporting why rather than logging it.

    Reported for the same reason *coerced* is: this read runs on every
    provider lookup, so a log here fires once per lookup for a condition
    that changes only when an operator edits the blob, and a single stale
    blob would fill the log for the life of the process. The reason and
    the detail travel to the caller instead, and the one caller that
    reloads the registry says it once per reload.

    Returns:
        An ``UNREADABLE`` outcome carrying *fallback*.
    """
    return ProviderConfigsRead(
        status=ProviderConfigsStatus.UNREADABLE,
        providers=fallback,
        reason=reason,
        detail=detail,
    )
