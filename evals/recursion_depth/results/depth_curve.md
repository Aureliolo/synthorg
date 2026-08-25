# Recursion-depth sweep

Does verification at every merge hold off aggregation collapse as
recursive decomposition deepens?

- Measured against commit `d67bdae46526e7826c61b08561da5bd9ff89c35f`
- Generated 2026-08-25T14:46:06.171051+00:00
- Manifest `sha256:f721d6c1725dc7c290bb0e246595455c6922c48c824170bcdb7b4b804d6c9694`
- Spec `sqlcsv`, 42 requirements
- Executor `example-provider/example-capable-001`, reviewer `example-provider/example-expert-001` (cross_family)
- Total spend: 0.0000 across 210951956 tokens

## Survival by depth reached

The primary curve. Binned on the depth each leaf actually sat at, not
on the cap its run was allowed: sweeping the cap does not sweep depth.

| Depth | Arm | Satisfied | Required | Fraction | Runs | Sessions | Tokens | Spend |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | gated | 0 | 42 | 0.000 | 1 | 14 | 6677603 | 0.0000 |
| 1 | ungated | 0 | 42 | 0.000 | 1 | 14 | 10143655 | 0.0000 |
| 2 | gated | 36 | 42 | 0.857 | 1 | 85 | 43833804 | 0.0000 |
| 2 | ungated | 33 | 42 | 0.786 | 1 | 79 | 34813789 | 0.0000 |
| 3 | gated | 36 | 42 | 0.857 | 1 | 135 | 66734755 | 0.0000 |
| 3 | ungated | 35 | 42 | 0.833 | 1 | 155 | 48748350 | 0.0000 |

## Survival by depth cap

The manipulated variable, for comparison with the histogram below.

| Depth | Arm | Satisfied | Required | Fraction | Runs | Sessions | Tokens | Spend |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | gated | 0 | 42 | 0.000 | 1 | 14 | 6677603 | 0.0000 |
| 1 | ungated | 0 | 42 | 0.000 | 1 | 14 | 10143655 | 0.0000 |
| 2 | gated | 36 | 42 | 0.857 | 1 | 85 | 43833804 | 0.0000 |
| 2 | ungated | 33 | 42 | 0.786 | 1 | 79 | 34813789 | 0.0000 |
| 3 | gated | 36 | 42 | 0.857 | 1 | 135 | 66734755 | 0.0000 |
| 3 | ungated | 35 | 42 | 0.833 | 1 | 155 | 48748350 | 0.0000 |

## How deep the runs went

| Cap and depth reached | Runs |
|---|---:|
| cap=1 gated reached=1 | 1 |
| cap=1 ungated reached=1 | 1 |
| cap=2 gated reached=2 | 1 |
| cap=2 ungated reached=2 | 1 |
| cap=3 gated reached=3 | 1 |
| cap=3 ungated reached=3 | 1 |

## What each arm spent, and what it bought

| Arm | Merges | Sessions | Tokens | Spend | Parked escalations | Contract amendments |
|---|---:|---:|---:|---:|---:|---:|
| gated | 27 | 104 | 58582975 | 0.0000 | 7 | 25 |
| ungated | 24 | 144 | 46635257 | 0.0000 | 0 | 8 |

## Who judged whom

The gate is the treatment, so a reviewer that came up on the executor's
own binding would bias the result toward the null while every
sweep-level field still read correctly. Every pairing that actually ran
is listed, with the families the decorrelation claim rests on.

| Arm | Assembled by | Judged by | Merges |
|---|---|---|---:|
| gated | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | 27 |
| ungated | example-provider/example-capable-001 (example-family-a) | none | 24 |

## Every merge

Both parties per merge, which is the grain the independence claim is
made at. The same rows are in `depth_curve.json` under each cell's
`units`.

| Cell | Depth | Assembly | Assembled by | Judged by | Verdict | Parked | Amendments | Delivered |
|---|---:|---|---|---|---|---|---:|---|
| d1-gated-r0 | 0 | Assemble: A SQL query CLI over CSV files | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no |
| d1-ungated-r0 | 0 | Assemble: A SQL query CLI over CSV files | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d2-gated-r0 | 1 | Assemble: Implement CSV ingest and type inference | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no |
| d2-gated-r0 | 1 | Assemble: Implement SQL lexer and parser | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no |
| d2-gated-r0 | 1 | Assemble: Implement query executor | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no |
| d2-gated-r0 | 1 | Assemble: Implement output formatters | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve_with_notes | no | 4 | yes |
| d2-gated-r0 | 1 | Assemble: Implement CLI interface | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no |
| d2-gated-r0 | 1 | Assemble: Write comprehensive test suite | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no |
| d2-gated-r0 | 0 | Assemble: A SQL query CLI over CSV files | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | escalate | yes | 1 | no |
| d2-ungated-r0 | 1 | Assemble: CSV ingest with type inference | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d2-ungated-r0 | 1 | Assemble: SQL lexer and parser | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d2-ungated-r0 | 1 | Assemble: Query execution engine | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d2-ungated-r0 | 1 | Assemble: Output formatters | example-provider/example-capable-001 (example-family-a) | none | none | no | 3 | yes |
| d2-ungated-r0 | 1 | Assemble: CLI argument handling | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d2-ungated-r0 | 1 | Assemble: End-to-end integration tests | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d2-ungated-r0 | 0 | Assemble: A SQL query CLI over CSV files | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-gated-r0 | 2 | Assemble: Implement csv_loader.py core module | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | escalate | yes | 0 | yes |
| d3-gated-r0 | 1 | Assemble: Build CSV ingest module with type inference | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve | no | 3 | yes |
| d3-gated-r0 | 2 | Assemble: Implement SQL lexer with tokenisation and tests | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve | no | 4 | yes |
| d3-gated-r0 | 1 | Assemble: Build SQL lexer | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve | no | 0 | no |
| d3-gated-r0 | 2 | Assemble: Implement parser core for SELECT/FROM/WHERE | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve_with_notes | no | 0 | yes |
| d3-gated-r0 | 2 | Assemble: Implement parser extensions for ORDER BY/LIMIT/GROUP BY/JOIN | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve_with_notes | no | 6 | yes |
| d3-gated-r0 | 1 | Assemble: Build SQL parser | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no |
| d3-gated-r0 | 2 | Assemble: Core executor with table/column validation | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | escalate | yes | 0 | no |
| d3-gated-r0 | 2 | Assemble: WHERE clause evaluation engine | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | escalate | yes | 0 | no |
| d3-gated-r0 | 2 | Assemble: DISTINCT, ORDER BY, LIMIT, OFFSET | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve_with_notes | no | 0 | no |
| d3-gated-r0 | 2 | Assemble: Aggregate functions with GROUP BY and HAVING | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | escalate | yes | 0 | no |
| d3-gated-r0 | 2 | Assemble: INNER JOIN and LEFT JOIN support | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | escalate | yes | 0 | no |
| d3-gated-r0 | 1 | Assemble: Build query executor | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | reject | no | 0 | no |
| d3-gated-r0 | 2 | Assemble: Implement table output formatter | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve_with_notes | no | 0 | yes |
| d3-gated-r0 | 2 | Assemble: Implement CSV output formatter | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve | no | 3 | yes |
| d3-gated-r0 | 2 | Assemble: Implement JSON output formatter | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve | no | 0 | no |
| d3-gated-r0 | 2 | Assemble: Implement CLI entry point and argument parsing | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | escalate | yes | 4 | no |
| d3-gated-r0 | 1 | Assemble: Build CLI and output formatters | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve_with_notes | no | 0 | no |
| d3-gated-r0 | 0 | Assemble: A SQL query CLI over CSV files | example-provider/example-capable-001 (example-family-a) | example-provider/example-expert-001 (example-family-b) | approve_with_notes | no | 0 | no |
| d3-ungated-r0 | 2 | Assemble: CSV parser with RFC 4180 compliance and NULL handling | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 1 | Assemble: CSV reader with type inference | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 2 | Assemble: Implement SQL lexer with tests | example-provider/example-capable-001 (example-family-a) | none | none | no | 1 | yes |
| d3-ungated-r0 | 1 | Assemble: SQL lexer | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 2 | Assemble: Implement SQL parser with tests | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 1 | Assemble: SQL parser | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 2 | Assemble: Implement projection, DISTINCT and WHERE | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 2 | Assemble: Implement aggregates, GROUP BY and HAVING | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 1 | Assemble: Query executor | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 2 | Assemble: Implement table format with tests | example-provider/example-capable-001 (example-family-a) | none | none | no | 3 | yes |
| d3-ungated-r0 | 2 | Assemble: Implement CSV format with tests | example-provider/example-capable-001 (example-family-a) | none | none | no | 1 | yes |
| d3-ungated-r0 | 2 | Assemble: Implement JSON format with tests | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | yes |
| d3-ungated-r0 | 1 | Assemble: Output formatters | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 2 | Assemble: CLI module with argument parsing and exit codes | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 1 | Assemble: CLI entry point | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |
| d3-ungated-r0 | 0 | Assemble: A SQL query CLI over CSV files | example-provider/example-capable-001 (example-family-a) | none | none | no | 0 | no |

## Caveats

- Unit sizing is the planner's own: the size signal reads the declaration a planner made, so this measures gated recursion UNDER PLANNER-DECLARED SIZING and cannot separate 'recursion fails' from 'the planner sized badly'. Separating them needs an agent that has read the code deciding its own split, which no published system has.
- The oracle is held out: it never enters a workspace and is named in no brief, so a delivery cannot be built to it.
- 143 planner claim(s) named no requirement this specification defines and were dropped before scoring. A handful is one planner inventing a requirement; a large share means the criterion template and the id pattern have drifted apart, which deflates both halves of the survival ratio and reads on the chart like a gate that does not help.
- The token column was rebuilt from the recorder's per-call log. This recording predates the per-cell cost ledger, so concurrent leaf sessions swapped a process-wide sink and some journalled zero while others absorbed their records. The repair attributes each call from the log, which is written per call and cannot be scrambled by that swap; plan units keep their journalled figures, being the one kind of session that never ran concurrently. Session and attempt counts were never affected.
