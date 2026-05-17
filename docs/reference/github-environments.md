# GitHub Deployment Environments

SynthOrg uses GitHub deployment environments to gate workflow jobs that carry
elevated permissions or access sensitive secrets. Each environment has a
branch allowlist (deployment branch policy) so the job only runs when the
workflow was triggered from an expected ref.

Policies cannot be declared in workflow YAML; they live on the GitHub
environment itself. Apply them via `scripts/configure_environments.sh`.

## Current environments

| Environment | Branch policy | Triggered by |
|---|---|---|
| `github-pages` | `main` | `pages.yml` push to main |
| `release` | `main` | `release.yml` + `dev-release.yml` + `auto-rollover.yml` + `graduate.yml` + `cla.yml:cla-sign` (main-scoped), `finalize-release.yml:publish` (workflow_run resolves `github.ref` to main; carries `statuses: write` so the publish job can post a `finalize-release` commit status against `workflow_run.head_sha`). Holds `RELEASE_BOT_APP_CLIENT_ID` + `RELEASE_BOT_APP_PRIVATE_KEY`. |
| `release-tags` | `v*` | `cli.yml:cli-release` + `docker.yml:update-release` (v* tag pushes). Structural ref gate only; no privileged secrets. |
| `image-push` | `main`, `v*` | `docker.yml` `*-publish` jobs (4 apko base pushes + 5 app image pushes) on main and v* refs |
| `apko-lock` | `main` | `apko-lock.yml` schedule + workflow_dispatch. Holds `APKO_BOT_APP_CLIENT_ID` + `APKO_BOT_APP_PRIVATE_KEY`: a copy of the `synthorg-repo-bot` App credentials (`Contents: Read and write` + `Pull requests: Read and write` + `Metadata: Read`, scoped to this repo only), used by the apko-lock workflow to mint an installation token for the lockfile-update PR. `GITHUB_TOKEN` cannot create the PR because the repo-level setting `can_approve_pull_request_reviews: false` blocks it. The same App credentials live under `RELEASE_BOT_APP_*` in the `release` env (env-scoped, not shared across envs). Both copies point at the same App; the dedicated `apko-lock` env keeps weekly-cron auth and release-pipeline auth in separate boxes even though they share an identity. |
| `cloudflare-preview` | _none_ (see below) | `pages-preview.yml` pull_request events |
| `lighthouse` | _none_ (see below) | `lighthouse.yml` pull_request events. Holds `LHCI_GITHUB_APP_TOKEN`. |

The release path is intentionally split into two environments. GitHub's
deployment branch policies only match ref *names*; they do NOT verify
that a tag's commit is reachable from an allowed branch. Admitting `v*`
on the secret-bearing environment would let any `v`-prefixed tag
(including one forged on an unmerged feature branch) unlock the App
credentials. Keeping `release` main-only and routing tag-only jobs
through `release-tags` preserves the structural ref gate without
exposing the App.

## Why `cloudflare-preview` and `lighthouse` have no branch policy

GitHub's deployment branch policies match against `github.ref` using fnmatch,
but only for refs under `refs/heads/*` (branches) and `refs/tags/*` (tags).
For `pull_request` event workflows, `github.ref` is `refs/pull/<N>/merge`,
which cannot be matched by any branch-type policy.

`cloudflare-preview` only runs on `pull_request` events, so a branch policy
would either:

- block every PR preview (if set to `main`), or
- admit everything (if set to `*`), providing no real protection.

`lighthouse` is the same case: `lighthouse.yml`'s dashboard + site
jobs run only on `pull_request`, hold `LHCI_GITHUB_APP_TOKEN`, and a
`main` policy would block every web/site PR (the `Lighthouse Pass`
aggregate is a REQUIRED check, so a permanently-pending sub-job would
wedge merges). It therefore carries no branch policy either.

The workflow-level gate is the actual control:

- `pages-preview.yml:deploy-preview` / `cleanup-preview`: gated on
  `same_repo == 'true'` so fork PRs cannot access Cloudflare secrets.
- `lighthouse.yml:lighthouse-dashboard` / `lighthouse-site`: gated on
  `github.event.pull_request.head.repo.full_name == github.repository`
  (or `workflow_dispatch`) so a fork PR cannot reach
  `LHCI_GITHUB_APP_TOKEN`; the `lighthouse-pass` aggregator treats the
  resulting skipped sub-jobs as a pass.

If GitHub ever extends deployment branch policies to cover PR refs, revisit
this entry in `scripts/configure_environments.sh`.

## Applying policies

Run once after merging SUP-1, and whenever a new environment is added:

```bash
# Preview the API calls (safe, default)
bash scripts/configure_environments.sh

# Apply
bash scripts/configure_environments.sh --apply
```

The script is a reconciler: on `--apply` the final state of each environment's
branch policies exactly matches the `ENV_CONFIG` table inside the script.
Missing policies are `POST`-ed (with `HTTP 422` already-exists treated as a
no-op), and any extra policy not in the desired set is `DELETE`-d. Requires a
`gh` CLI authenticated with the `repo` scope (classic PAT/OAuth) or
`administration:write` (fine-grained PATs/GitHub Apps); see the
[deployments API docs](https://docs.github.com/rest/deployments/environments).

## Verifying policies

```bash
gh api repos/Aureliolo/synthorg/environments/apko-lock \
  --jq '.deployment_branch_policy, .name'
gh api repos/Aureliolo/synthorg/environments/apko-lock/deployment-branch-policies \
  --jq '.branch_policies[].name'
```

Expected output for the reconciled environments (`github-pages`, `apko-lock`,
`release`, `release-tags`, `image-push`):

- `deployment_branch_policy`: `{"protected_branches": false, "custom_branch_policies": true}`
- `branch_policies` for `github-pages`, `apko-lock`, `release`: `["main"]`
- `branch_policies` for `release-tags`: `["v*"]`
- `branch_policies` for `image-push`: `["main", "v*"]`

`cloudflare-preview` and `lighthouse` are intentionally excluded from the
`custom_branch_policies` expectation; see the rationale above.

## Required secrets

Secrets gated by deployment environments are only available to jobs whose
`github.ref` matches that environment's branch policy. Any job referencing
`secrets.<NAME>` in its `env:` or step inputs must run under the
environment that scopes the secret.

### `RELEASE_BOT_APP_*`

The release pipeline is authenticated by a dedicated GitHub App,
`synthorg-repo-bot`. Its credentials live in the `release`
deployment environment as two secrets:

- `RELEASE_BOT_APP_CLIENT_ID`: the App's Client ID as shown on the
  App's settings page (format `Iv23...`). This is what
  `actions/create-github-app-token@v3.1+` expects via its `client-id`
  input; the older `app-id` input accepted the numeric App ID and
  was deprecated in v3.1.
- `RELEASE_BOT_APP_PRIVATE_KEY`: the full `.pem` contents verbatim,
  including the opening and closing marker lines. Both markers must
  be present and spelled exactly as emitted by the GitHub App page:
  - Opening line: `-----BEGIN RSA PRIVATE KEY-----`
  - Closing line: `-----END RSA PRIVATE KEY-----`
  Paste the file contents exactly as downloaded; GitHub's secret
  store accepts multi-line values but silently strips trailing
  whitespace, so do not hand-edit the `.pem`.

**Why an App token.** Two constraints rule out the alternatives:

1. `main` enforces `required_signatures`, so any API commit that
   lands there MUST verify as `{verified: true, reason: "valid"}`.
   Only `GITHUB_TOKEN` and App installation tokens produce
   GitHub-signed API commits; PAT-authored API commits are
   unsigned and get rejected at the branch-protection gate.
2. A tag or main-commit push must fire downstream workflows
   (Docker, CLI, Dev Release). GitHub's anti-recursion rule
   suppresses those events when the triggering push was authored
   by `GITHUB_TOKEN`. App installation tokens are exempt.

App tokens are the only credential that satisfies both at once.

**Purpose**. Every release workflow mints a fresh short-lived App
installation token (valid ≤1 hour) via the
`release-runner-setup` composite action, which wraps
`actions/create-github-app-token@v3.1.1`. Consumers:

- `release.yml`: `release-please-action` token input, so the RP
  tag push on release-PR merge triggers Docker + CLI builds. The
  BSL Change Date Contents API commit keeps `GITHUB_TOKEN` (lands
  on the RP PR branch, not `main`; no recursion concern). One
  side-effect of the App-token PR creation: GitHub's anti-recursion
  rule blocks `pull_request` workflows for events created by the
  workflow's own installation token, so `ci.yml` does not auto-fire
  on the release PR. The required `CI Pass` check is therefore
  satisfied by a direct commit-status post: the job's final step
  resolves the release PR's current head SHA and issues
  `gh api -X POST repos/.../statuses/<head_sha>` with
  `context=CI Pass`, `state=success` under `GITHUB_TOKEN` (which
  carries `statuses: write`). Statuses are unconditionally part of
  a PR's `statusCheckRollup` and satisfy `required_status_checks`
  evaluation, so the merge gate resolves without ci.yml ever
  firing. (The earlier mechanism, a `gh workflow run ci.yml`
  workflow_dispatch nudge, produced a check_run on the SHA but
  the dispatched run's `check_suite.pull_requests` was empty, so
  the check did not appear in the rollup; the merge UI stayed
  stuck on `CI Pass: Expected, waiting`. See PR #1615 for the
  full root cause.) `ci.yml`'s `is_release_please` skip remains
  in place as defense-in-depth so a future change in
  release-please's identity does not accidentally run a full CI
  suite on a release PR. The `branch-protection-audit` job inside
  `ci.yml` keeps a `github.ref == 'refs/heads/main'` gate so any
  ad-hoc dispatch from a non-main ref skips cleanly instead of
  hitting the `release` environment's branch allowlist.
- `dev-release.yml`: tag creation for dev pre-releases via
  `gh api`.
- `auto-rollover.yml`: empty `Release-As:` commit via the Git
  Data API (`POST /git/commits` + `PATCH /git/refs/heads/main`).
- `graduate.yml`: user-triggered signed empty commit with a
  `Release-As:` trailer for target versions that skip the normal
  patch cadence.
- `cla.yml:cla-sign`: signed commit on the `cla-signatures`
  branch via the Git Data API (`POST /git/blobs` + `POST
  /git/trees` + `POST /git/commits` + `PATCH /git/refs/heads/
  cla-signatures`). The signed commit lets the `cla-signatures`
  branch participate in the default ruleset's required-signatures
  rule without a per-branch carve-out. Read-only signature
  verification (`cla-check`) lives in the same workflow but uses
  `secrets.GITHUB_TOKEN` directly -- no App-token mint required.

**App configuration**. Ship the App with the minimum privilege set:

- Owner: `Aureliolo` (personal account).
- Install scope: `Aureliolo/synthorg` only. Single-repo install
  bounds the blast radius to the intended target.
- Repository permissions:
  - `Contents: Read and write`
  - `Pull requests: Read and write`
  - `Metadata: Read`
- Subscribe to **no** events; this App has no webhook
  endpoint and does not need to receive events.

**Provisioning checklist** (follow once at setup):

1. Settings -> Developer settings -> GitHub Apps -> New GitHub App.
2. Configure permissions + install scope as above.
3. Generate a private key; download the `.pem`.
4. Install the App on `Aureliolo/synthorg`.
5. Copy the App's Client ID (`Iv23...` format) from the same page.
6. Repo Settings -> Environments -> `release` -> Environment
   secrets. Add `RELEASE_BOT_APP_CLIENT_ID` (the Iv23... Client ID
   from the App's settings page) and `RELEASE_BOT_APP_PRIVATE_KEY`
   (full PEM contents).
7. Confirm the action allowlist includes
   `actions/create-github-app-token@*` (SHA-pinned in-workflow)
   and `actions/ai-inference@*` (used by the release-notes
   Highlights step in `release.yml`).

**No rotation schedule**. Installation tokens are ephemeral:
minted per workflow run and valid for at most one hour, then
discarded. The only long-lived secret is the App private key,
rotated only if the key file is compromised. Private-key rotation
is a two-step: generate a new key in the App settings, replace
`RELEASE_BOT_APP_PRIVATE_KEY` in the `release` environment,
delete the old key.

**Access control**. The `release` environment's branch policy
(`main` only) is the structural gate. A workflow triggered from
any other ref cannot read `RELEASE_BOT_APP_*` even if it
declares them in its YAML. Tag-only release jobs
(`cli.yml:cli-release`, `docker.yml:update-release`) run under
the separate `release-tags` environment, which carries no
privileged secrets, so a forged `v*` tag on unmerged code
cannot reach the App credentials.

## Testing the `apko-lock` gate

Trigger the workflow from a non-main ref via `workflow_dispatch` API (pushing
a commit to a feature branch is not enough; the workflow is not configured
for `push` events):

```bash
# Trigger from main -- should succeed
gh workflow run apko-lock.yml --ref main

# Trigger from a feature branch -- should be blocked at the environment gate.
# GitHub will show the job in "Waiting" state; the run log cites the branch
# policy violation.
gh workflow run apko-lock.yml --ref chore/some-branch
```
