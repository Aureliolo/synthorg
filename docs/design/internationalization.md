# Internationalization

SynthOrg's UI is **English-only**. Translation, localization, and right-to-left layout support are not currently planned.

## Decision

This was confirmed on 2026-04-30. The dashboard ships in International / British English (`colour`, `behaviour`, `organise`, `centred`, `analyse`). Strings are inline in components; there is no `t()` resolver, no message catalog, and no locale switcher.

## What this means in practice

- Component authors keep user-facing strings inline. Do not extract them into a constants module "for centralization." That has indirection cost without payoff while the product remains English-only.
- The narrow exception is when the same long error message is used three or more times verbatim. That is deduplication, not internationalization; keep it close to the consumers (e.g. a small `errors.ts` next to the components).
- Currency, date, and number formatting still flow through `Intl` and read the operator's settings (see [Regional Defaults in CLAUDE.md](https://github.com/Aureliolo/synthorg/blob/main/CLAUDE.md)). That is locale-aware *display* of numeric data, not translation of the surrounding UI chrome.
- Audit findings flagging "hardcoded user-facing strings should be centralized for i18n readiness" are no longer tracked. The codebase audit's agent definition has been updated accordingly.

## When to revisit

This decision is reversible. The trigger is a real, scoped requirement: a specific market, a specific customer engagement, or a regulatory translation mandate. If that arrives, open a new issue with the requirement in the body. The implementation path would then be a real translation framework (likely react-intl or i18next), not a string-constants module.
