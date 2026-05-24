---
title: Fork setup
description: Configure CI on a fresh fork or clone. Environments, labels, branch protection, and the release-bot GitHub App.
---

# Fork setup

If you have just forked or cloned this repository, the CI workflows will not run cleanly until you create a small set of GitHub-side artifacts: environments, labels, branch protection on `main`, and a GitHub App for the release pipeline. Push any commit to your fork (or open a pull request) and the **CI Preflight** workflow opens a tracking issue in your fork listing exactly what is missing; this page is the long-form companion to that checklist.

The preflight is non-blocking on pull requests and feature branches; it only fails the job on push to `main`. So the path of least resistance is: push, read the tracking issue, work through this page, push again, watch the issue auto-close.

## 1. Sync labels

CI workflows reference a fixed set of automation labels (`automation:ci-health`, `automation:ci-preflight`, `type:ci`, `prio:low`, `prio:medium`, `prio:high`, and `autorelease: pending`). The source of truth is `.github/labels.yml`.

Bootstrap once: **Actions -> Sync Labels -> Run workflow** on `main`. The workflow reads `.github/labels.yml` and creates or updates each label via `gh label create --force`. It never deletes labels, so any repo-specific labels you add are safe.

After this, the `Missing labels` section of the preflight tracking issue should clear on the next preflight run.

## 2. Create the GitHub environments

CI uses seven GitHub environments for branch-policy gating and to scope secrets. The preflight job audits all of them unconditionally, so create every one even if your fork does not yet exercise the corresponding workflow; a missing environment will keep the preflight tracking issue open.

Create at **Settings -> Environments -> New environment**:

| Environment | Used by | Required to pass CI Preflight? |
|-------------|---------|---------------------|
| `release` | `release.yml`, `dev-release.yml`, `auto-rollover.yml`, `graduate.yml`, `finalize-release.yml` | Yes |
| `release-tags` | `cli.yml`, `docker.yml` (tag pushes) | Yes |
| `apko-lock` | `apko-lock.yml` (scheduled lockfile updates) | Yes |
| `cloudflare-preview` | `pages-preview.yml` | Yes |
| `image-push` | `docker.yml` image push paths | Yes |
| `github-pages` | `pages.yml` push to main | Yes |

For `release` and `release-tags`, configure a deployment branch policy of `main` (and `v*` for `release-tags`) so secrets only unlock for the intended refs. See [`docs/reference/github-environments.md`](../reference/github-environments.md) for the full branch-policy matrix.

Workflow consumers of each environment fall into two camps: required for any release activity (`release`, `release-tags`, `image-push`, `apko-lock`, `github-pages`), and optional capabilities you can leave un-credentialed if your fork does not need them (`cloudflare-preview` for PR docs previews, `apko-lock` if you skip scheduled Wolfi lock updates). The environment must still exist for the preflight to pass; the secrets inside can be empty until you actually use the workflow.

## 3. Create the release-bot GitHub App

The release pipeline (`release.yml`, `dev-release.yml`, `auto-rollover.yml`, `graduate.yml`, and `finalize-release.yml`) mints installation tokens from a GitHub App with the right repository permissions. Without it, every commit those workflows produce on `main` would be unsigned and rejected by branch protection. The App is the single piece of state that makes the release pipeline able to write to a protected `main`. The same App is also used by the weekly `apko-lock.yml` cron when it opens its lockfile-update PR (its credentials are duplicated into the `apko-lock` env under different secret names so each env carries its own copy).

Steps:

1. Go to **Settings -> Developer settings -> GitHub Apps -> New GitHub App** (or `https://github.com/settings/apps/new` for a personal-account App).
2. Name the App something memorable (e.g. `myorg-release-bot`). Disable the webhook (uncheck "Active").
3. Set **Repository permissions** to:
    - Contents: **Read & write**
    - Pull requests: **Read & write**
    - Metadata: **Read-only** (always required)
4. Save the App, then under **Install App** install it on your fork.
5. Generate a private key under **Private keys -> Generate private key** and save the PEM file.
6. Copy the App's **Client ID** (top of the App settings page).

In your repository, go to **Settings -> Environments -> release** and add two secrets:

- `RELEASE_BOT_APP_CLIENT_ID`: the Client ID from step 6.
- `RELEASE_BOT_APP_PRIVATE_KEY`: the entire PEM file contents, including the `-----BEGIN ... PRIVATE KEY-----` header and `-----END ... PRIVATE KEY-----` footer.

Then duplicate the SAME values into the `apko-lock` environment under different names so the weekly lockfile-update cron can mint a token of its own. Settings -> Environments -> apko-lock:

- `APKO_BOT_APP_CLIENT_ID`: same Client ID as `RELEASE_BOT_APP_CLIENT_ID` above.
- `APKO_BOT_APP_PRIVATE_KEY`: same PEM contents as `RELEASE_BOT_APP_PRIVATE_KEY` above.

The credentials are duplicated rather than shared because GitHub-environment secrets are env-scoped: a workflow running under `release` cannot read a secret defined only in `apko-lock`, and vice versa. Both names point at the same App, so a key rotation only needs to update both copies once.

If you do not need the release pipeline at all (you are running a research fork and never cut releases), skip this section. The release and apko-lock workflows are gated on `!github.event.repository.fork` and skip cleanly.

## 4. Populate the remaining environment secrets

| Environment | Secret | Source |
|-------------|--------|--------|
| `cloudflare-preview` | `CLOUDFLARE_API_TOKEN` | <https://dash.cloudflare.com/profile/api-tokens> (Pages-deploy-scoped) |
| `cloudflare-preview` | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard sidebar |

`image-push` needs no secrets; the workflow uses the auto-provided `${{ github.token }}` against your fork's GHCR namespace. The environment exists purely for branch-policy gating.

## 5. Branch protection on `main`

The preflight checks for three things on `main`:

- **Required signed commits**: needed because the release pipeline produces commits, and branch protection rejects unsigned ones.
- **Required status check `CI Pass`**: the gate job that aggregates lint, type-check, and tests.
- **Strict policy** ("Require branches to be up to date before merging"): prevents stale PRs from merging.

Configure at **Settings -> Branches -> Add rule** on `main`. The minimum to satisfy preflight is the three checkboxes above. Pull-request review counts and code-owner requirements are repository-policy decisions and not enforced by preflight.

## When preflight passes

Once every section above is checked off, the next preflight run finds the tracking issue by title, posts a `Preflight passed at <SHA>` comment, and closes it. If anything later regresses (a deleted environment, a missing label after manual cleanup), the next preflight run reopens or recreates the tracking issue with the updated diff. The issue body is regenerated from scratch on every run, so the checklist always reflects the current state.
