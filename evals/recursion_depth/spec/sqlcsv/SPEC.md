# sqlcsv: a SQL query CLI over CSV files

Build a Python command-line tool that answers SQL queries against CSV files on
disk. It has no dependencies outside the Python standard library, and it must
not use `sqlite3` or any other engine: the point of the exercise is the engine.

This document is the whole specification. Every numbered requirement below is
checked by a test you will not see, run against the tree you deliver. Build what
the requirement says, not what would make a particular test pass.

## Invocation

```
python -m sqlcsv --data DIR "SELECT ..." [--format table|csv|json]
```

- `--data DIR` names a directory of CSV files. A table named `orders` in a query
  is the file `orders.csv` in that directory.
- The SQL statement is a single positional argument. `-` reads it from stdin.
- `--format` selects the output rendering and defaults to `table`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The query ran and its result was printed. |
| 2 | The input was not usable: a lexical or syntax error, a bad argument, an unreadable data directory. |
| 3 | The query was well formed but named something that does not exist: an unknown table or an unknown column. |

Every non-zero exit writes a diagnostic to stderr and prints nothing to stdout.

## Requirements

### CSV ingest and typing

- **R01** The first row of a CSV file is its header and names the columns. Data
  rows follow.
- **R02** A column whose values all parse as integers compares and sorts
  numerically, not lexically, so `9` orders after `10` is wrong.
- **R03** A column whose values parse as decimal numbers is read as a float, and
  arithmetic aggregates over it produce a float.
- **R04** An empty field is NULL, distinct from the empty string and from zero.
- **R05** A field quoted with double quotes may contain commas, newlines, and
  doubled double quotes (`""`) standing for one literal quote.

### Lexing

- **R06** Keywords are case-insensitive: `select`, `SELECT` and `SeLeCt` are the
  same token. Identifiers are case-sensitive.
- **R07** A string literal is written in single quotes. A doubled single quote
  (`''`) inside one stands for a literal apostrophe.
- **R08** Numeric literals may be integers (`42`) or decimals (`3.5`), and may
  carry a leading minus sign.
- **R09** An unterminated string literal is a lexical error: exit 2.

### Parsing

- **R10** `SELECT <expr-list> FROM <table>` parses, where the expression list is
  one or more column references separated by commas.
- **R11** `SELECT *` selects every column of the source, in the order the header
  declares them.
- **R12** An optional `WHERE <condition>` clause parses after the table.
- **R13** An optional `ORDER BY <column> [ASC|DESC]` clause parses, with one or
  more comma-separated sort keys.
- **R14** An optional `LIMIT <n>` clause parses, optionally followed by
  `OFFSET <n>`.

### Planning and semantic checks

- **R15** A query naming a table with no matching CSV file exits 3.
- **R16** A query naming a column no source provides exits 3.
- **R17** A syntactically malformed query exits 2, and is distinguished from a
  query that is well formed but names something absent, which exits 3.
- **R18** Mixing a bare column with an aggregate and no `GROUP BY` is rejected:
  exit 2.

### Execution

- **R19** Projection emits the named columns, in the order the SELECT list names
  them, not the order the header declares.
- **R20** `AS` renames a projected column, and the new name is what the output
  header shows.
- **R21** `DISTINCT` after `SELECT` removes duplicate result rows.
- **R22** `WHERE` supports `=` and `!=` (also spelled `<>`) against string and
  numeric literals.
- **R23** `WHERE` supports `<`, `<=`, `>` and `>=`, comparing numerically for
  numeric columns.
- **R24** `WHERE` supports `AND` and `OR`, with `AND` binding tighter than `OR`,
  and parentheses overriding that.
- **R25** `WHERE` supports `NOT`.
- **R26** `WHERE` supports `IS NULL` and `IS NOT NULL`.
- **R27** `ORDER BY` sorts ascending by default and descending on `DESC`, and a
  second key breaks ties in the first.
- **R28** `LIMIT` truncates the result after ordering; `OFFSET` skips that many
  rows first.
- **R29** `COUNT(*)` counts rows; `COUNT(col)` counts rows where `col` is not
  NULL.
- **R30** `SUM`, `AVG`, `MIN` and `MAX` aggregate a column, ignoring NULLs.
- **R31** `GROUP BY <column>` groups rows and evaluates each aggregate per
  group, emitting one row per group.
- **R32** `HAVING <condition>` filters groups after aggregation.
- **R33** `INNER JOIN <table> ON <a> = <b>` joins two sources on an equality,
  and a qualified `table.column` reference resolves in a joined query. A
  projected `table.column` is named by its bare column name in the output
  unless an `AS` says otherwise.
- **R34** `LEFT JOIN` keeps every left row, filling the right side with NULLs
  where nothing matched.

### Output

- **R35** `--format table` prints the column names on the first line, a line of
  `-` characters on the second, and one line per result row after that. Cells on
  a line are separated by ` | ` (space, pipe, space) and padded on the right
  with spaces to the width of the widest cell in their column, the header
  included. Trailing padding at the end of a line may be omitted.
- **R36** `--format csv` prints RFC 4180 CSV with a header row, quoting any
  value containing a comma, a quote or a newline.
- **R37** `--format json` prints a JSON array of objects, one per row, keyed by
  the output column names.
- **R38** A NULL renders as an empty field in `csv`, and as `null` in `json`.
- **R39** An empty result set still prints its header in `table` and `csv`, and
  prints `[]` in `json`.

### CLI surface

- **R40** `--help` prints usage and exits 0.
- **R41** Omitting `--data`, or pointing it at a directory that does not exist,
  exits 2.
- **R42** Passing `-` as the SQL argument reads the statement from stdin.

## Deliverable

A package importable as `sqlcsv` from the root of the tree you are given, with a
`__main__.py` so `python -m sqlcsv` works. Write your own tests for what you
build; they are part of the deliverable and are how you show the work is done.
