# British vocabulary

This directory drives the `Vocab = British` directive in `.vale.ini`.
Two files, both treated as case-sensitive regular expressions per line:

- `accept.txt` -- British spellings and project proper nouns Vale should
  accept. Entries are added to every active spelling exception list
  (so `colour` no longer trips `Vale.Spelling` / `Google.Spelling`).
  Vale also auto-generates a `Vale.Terms` rule from this file to enforce
  exact casing, but **`Vale.Terms = NO` in `.vale.ini` disables that
  rule** for this project: the shared `SynthOrg` vocabulary is
  intentionally lowercase to match the codespell convention
  (`ignore-words` in `.codespellrc`), and enabling `Vale.Terms` would
  flag every `SynthOrg` / `Pydantic` / `Litestar` in prose as the
  wrong case. If a downstream fork wants exact-case enforcement,
  remove the `Vale.Terms = NO` line from `.vale.ini` and rewrite the
  vocab entries with the desired casing.
- `reject.txt` -- American-only spellings whose British equivalent
  exists. Each entry auto-generates a `Vale.Avoid` violation so new
  prose typing `color` or `organize` is rejected at pre-push.

## Editing

- One regex per line. Patterns are case-sensitive by default; prefix
  with `(?i)` for case-insensitive matches (used heavily here so
  `colour` and `Colour` both match the same entry).
- Comments are not supported in `accept.txt` / `reject.txt`; group
  related entries with blank lines for readability.
- Adding a tech proper noun (new dependency, new product name) goes in
  `accept.txt`. A regression like seeing `Organize` in a PR diff goes
  in `reject.txt` if not already there.
- The CI gate is `vale` in `.pre-commit-config.yaml` (pre-push stage);
  test locally with `vale README.md` or `vale docs/`.
