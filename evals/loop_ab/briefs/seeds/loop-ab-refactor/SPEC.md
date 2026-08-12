# Task: split the report builder into three modules

`report/build.py` does three jobs at once: it parses raw rows, it renders text,
and it assembles the document. Split the first two out, leaving `build.py` as
the assembler that calls them.

**Behaviour must not change.** Every existing result stays byte-for-byte the
same, including the errors.

## `report/parse.py` (new)

`parse_row(raw: str) -> tuple[str, int]`
:   The parsing that `build.py` does today, moved here unchanged and made
    public under this name. It returns `(name, amount)` with the name stripped
    of surrounding spaces, and raises `ValueError` for a row with no `:`, an
    empty name, or an amount that is not an integer.

## `report/render.py` (new)

`render_line(name: str, amount: int) -> str`
:   One fixed-width line: the name padded right to `NAME_WIDTH`, then the
    amount padded left to `AMOUNT_WIDTH` with thousands separators.

`render_header(title: str) -> str`
:   The title, a newline, then `SEPARATOR` repeated to the title's length.

`NAME_WIDTH`, `AMOUNT_WIDTH` and `SEPARATOR` move here too, keeping their
current values.

## `report/build.py` (what remains)

Keeps `build_report(rows, *, title)` and nothing else of substance: it imports
from the two new modules rather than defining the work itself. It must be at
most 25 lines long, blank lines and comments included.

`build_report` stays importable from both `report` and `report.build`.
