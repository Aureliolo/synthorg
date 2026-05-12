"""Sibling module of ``scripts/check_schema_drift.py``: diff layer.

Compares the normalised dataclasses produced by ``_schema_drift_parser``
across the two backends. Emits canonical drift keys (without trailing
reason) suitable for comparison against baseline entries.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

if __package__ in {None, ""}:
    from _schema_drift_models import (  # type: ignore[import-not-found]
        BASELINE_MIGRATION_NAME,
        MIGRATION_FILENAME_RE,
        SIDE_POSTGRES_ONLY,
        SIDE_SQLITE_ONLY,
        TYPE_FAMILIES,
        NormalizedIndex,
        NormalizedTable,
        bool_yn,
        yn,
    )
else:
    from ._schema_drift_models import (
        BASELINE_MIGRATION_NAME,
        MIGRATION_FILENAME_RE,
        SIDE_POSTGRES_ONLY,
        SIDE_SQLITE_ONLY,
        TYPE_FAMILIES,
        NormalizedIndex,
        NormalizedTable,
        bool_yn,
        yn,
    )


def diff_schemas(
    sqlite_tables: dict[str, NormalizedTable],
    sqlite_indexes: dict[str, NormalizedIndex],
    postgres_tables: dict[str, NormalizedTable],
    postgres_indexes: dict[str, NormalizedIndex],
) -> list[str]:
    """Compare two normalised schemas and return canonical drift keys.

    Diff passes:

    1. Tables: symmetric difference of table-name sets.
    2. Per shared table: column existence + type + nullability + PK +
       UNIQUE.
    3. Indexes: symmetric difference of index-name sets, plus per
       shared-name index attribute (columns, unique, where, using).

    Each finding is a colon-separated key suitable for direct
    inclusion in the baseline file (with a trailing reason field
    appended by the operator).
    """
    sqlite_table_names = set(sqlite_tables)
    postgres_table_names = set(postgres_tables)
    findings: list[str] = []
    findings.extend(
        f"table:{name}:{SIDE_SQLITE_ONLY}"
        for name in sorted(sqlite_table_names - postgres_table_names)
    )
    findings.extend(
        f"table:{name}:{SIDE_POSTGRES_ONLY}"
        for name in sorted(postgres_table_names - sqlite_table_names)
    )
    for table_name in sorted(sqlite_table_names & postgres_table_names):
        s_tab = sqlite_tables[table_name]
        p_tab = postgres_tables[table_name]
        findings.extend(_diff_columns(s_tab, p_tab))
        findings.extend(_diff_pk(s_tab, p_tab))
        findings.extend(_diff_uniques(s_tab, p_tab))
    findings.extend(_diff_indexes(sqlite_indexes, postgres_indexes))
    return findings


def _diff_columns(
    sqlite_table: NormalizedTable,
    postgres_table: NormalizedTable,
) -> list[str]:
    """Return per-column drift findings for one shared table."""
    sqlite_cols = sqlite_table.columns
    postgres_cols = postgres_table.columns
    sqlite_names = set(sqlite_cols)
    postgres_names = set(postgres_cols)
    table = sqlite_table.name
    findings: list[str] = []
    findings.extend(
        f"column:{table}:{missing}:{sqlite_cols[missing].raw_type}:_"
        for missing in sorted(sqlite_names - postgres_names)
    )
    findings.extend(
        f"column:{table}:{missing}:_:{postgres_cols[missing].raw_type}"
        for missing in sorted(postgres_names - sqlite_names)
    )
    for col_name in sorted(sqlite_names & postgres_names):
        s_col = sqlite_cols[col_name]
        p_col = postgres_cols[col_name]
        if not _types_equivalent(s_col.canonical_type, p_col.canonical_type):
            findings.append(
                f"column:{table}:{col_name}:{s_col.raw_type}:{p_col.raw_type}"
            )
        if s_col.nullable != p_col.nullable:
            findings.append(
                f"nullable:{table}:{col_name}:"
                f"{yn(nullable=s_col.nullable)}:{yn(nullable=p_col.nullable)}"
            )
    return findings


def _diff_pk(
    sqlite_table: NormalizedTable,
    postgres_table: NormalizedTable,
) -> list[str]:
    """Return PRIMARY KEY drift findings for one shared table."""
    if sqlite_table.primary_key == postgres_table.primary_key:
        return []
    s_cols = ",".join(sqlite_table.primary_key) if sqlite_table.primary_key else "_"
    p_cols = ",".join(postgres_table.primary_key) if postgres_table.primary_key else "_"
    return [f"pk:{sqlite_table.name}:{s_cols}:{p_cols}"]


def _diff_uniques(
    sqlite_table: NormalizedTable,
    postgres_table: NormalizedTable,
) -> list[str]:
    """Return one-sided UNIQUE constraint drift findings for one shared table."""
    table = sqlite_table.name
    findings: list[str] = []
    findings.extend(
        f"unique:{table}:{','.join(cols)}:{SIDE_SQLITE_ONLY}"
        for cols in sorted(sqlite_table.uniques - postgres_table.uniques)
    )
    findings.extend(
        f"unique:{table}:{','.join(cols)}:{SIDE_POSTGRES_ONLY}"
        for cols in sorted(postgres_table.uniques - sqlite_table.uniques)
    )
    return findings


def _diff_indexes(
    sqlite_indexes: dict[str, NormalizedIndex],
    postgres_indexes: dict[str, NormalizedIndex],
) -> list[str]:
    """Return index drift findings (missing + per-attribute)."""
    sqlite_names = set(sqlite_indexes)
    postgres_names = set(postgres_indexes)
    findings: list[str] = []
    findings.extend(
        f"index:{name}:{SIDE_SQLITE_ONLY}"
        for name in sorted(sqlite_names - postgres_names)
    )
    findings.extend(
        f"index:{name}:{SIDE_POSTGRES_ONLY}"
        for name in sorted(postgres_names - sqlite_names)
    )
    for name in sorted(sqlite_names & postgres_names):
        findings.extend(_diff_index_attrs(sqlite_indexes[name], postgres_indexes[name]))
    return findings


def _diff_index_attrs(
    s_idx: NormalizedIndex,
    p_idx: NormalizedIndex,
) -> list[str]:
    """Return per-attribute drift for a shared-name index."""
    findings: list[str] = []
    if s_idx.columns != p_idx.columns:
        findings.append(
            f"index_columns:{s_idx.name}:"
            f"{','.join(s_idx.columns) or '_'}:"
            f"{','.join(p_idx.columns) or '_'}"
        )
    if s_idx.unique != p_idx.unique:
        findings.append(
            f"index_attr:{s_idx.name}:unique:"
            f"{bool_yn(value=s_idx.unique)}:{bool_yn(value=p_idx.unique)}"
        )
    if (s_idx.where or "") != (p_idx.where or ""):
        findings.append(
            f"index_attr:{s_idx.name}:where:{s_idx.where or '_'}:{p_idx.where or '_'}"
        )
    if (s_idx.using or "BTREE") != (p_idx.using or "BTREE"):
        findings.append(
            f"index_attr:{s_idx.name}:using:"
            f"{s_idx.using or 'BTREE'}:{p_idx.using or 'BTREE'}"
        )
    return findings


def _types_equivalent(a: Any, b: Any) -> bool:
    """Return True iff *a* and *b* live in the same default-equivalence family."""
    if a == b:
        return True
    return any(a in family and b in family for family in TYPE_FAMILIES)


def diff_migrations(
    sqlite_dir: Path,
    postgres_dir: Path,
) -> list[str]:
    """Compare the two ``revisions/`` directories for filename-suffix parity.

    Filenames match the pattern ``<14-digit-timestamp>_<suffix>.sql``;
    we strip the timestamp (which is picked at authoring time and may
    legitimately differ across the two backends) and take the
    symmetric difference of suffix sets.

    The post-squash ``00000000000000_baseline.sql`` is always ignored.
    """
    sqlite_suffixes = _collect_suffixes(sqlite_dir)
    postgres_suffixes = _collect_suffixes(postgres_dir)
    findings: list[str] = []
    findings.extend(
        f"migration:{name}:{SIDE_SQLITE_ONLY}"
        for name in sorted(sqlite_suffixes - postgres_suffixes)
    )
    findings.extend(
        f"migration:{name}:{SIDE_POSTGRES_ONLY}"
        for name in sorted(postgres_suffixes - sqlite_suffixes)
    )
    return findings


def _collect_suffixes(revisions_dir: Path) -> set[str]:
    """Return the set of migration suffixes under *revisions_dir*."""
    if not revisions_dir.is_dir():
        return set()
    suffixes: set[str] = set()
    for entry in revisions_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".sql":
            continue
        if entry.name == BASELINE_MIGRATION_NAME:
            continue
        match = MIGRATION_FILENAME_RE.match(entry.name)
        if not match:
            continue
        suffixes.add(match.group("suffix"))
    return suffixes
