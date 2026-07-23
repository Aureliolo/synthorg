# Task: text helpers

Create `textkit.py` in this directory exposing two functions.

## `slugify(value: str) -> str`

Lower-case the input, replace every run of non-alphanumeric characters with a
single hyphen, and strip leading and trailing hyphens.

- `slugify("Hello World")` returns `"hello-world"`
- `slugify("  Multiple   Spaces  ")` returns `"multiple-spaces"`
- `slugify("A!!!B")` returns `"a-b"`
- `slugify("")` returns `""`

## `truncate(value: str, limit: int) -> str`

Return `value` unchanged when it is at most `limit` characters. Otherwise cut it
to `limit` characters total, with the final character replaced so the result
ends in a single ellipsis character `…`.

- `truncate("hello", 10)` returns `"hello"`
- `truncate("hello world", 8)` returns `"hello w…"`
- `truncate("abc", 3)` returns `"abc"`

Raise `ValueError` when `limit` is less than 1.
