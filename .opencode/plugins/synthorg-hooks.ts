/**
 * SynthOrg hooks plugin for OpenCode.
 *
 * Mirrors the Claude Code hooks defined in .claude/settings.json and
 * .claude/settings.local.json by calling the same shell scripts.
 *
 * Committed Claude Code hooks (from .claude/settings.json):
 *   PreToolUse (Bash): scripts/check_no_repush_after_failure.sh
 *   PreToolUse (Bash): scripts/check_no_cmd_pager_pipe.sh
 *   PreToolUse (Bash): scripts/check_push_rebased.sh
 *   PreToolUse (Bash): scripts/check_push_throttle.sh
 *   PreToolUse (Bash): scripts/check_ci_before_push.sh
 *   PreToolUse (Bash): scripts/check_no_throttle_override_creation.sh
 *   PreToolUse (Bash): scripts/check_no_baseline_update.sh
 *   PreToolUse (Bash): scripts/check_bash_no_write.sh
 *   PreToolUse (Bash): scripts/check_git_c_cwd.sh
 *   PreToolUse (Bash): scripts/check_no_pr_create.sh
 *   PreToolUse (Bash): scripts/check_no_git_no_verify.sh
 *   PreToolUse (Bash): scripts/check_no_cd_prefix.sh
 *   PreToolUse (Bash): scripts/check_no_local_coverage.sh
 *   PreToolUse (Bash): scripts/check_enforce_parallel_tests.sh
 *   PreToolUse (Bash): scripts/check_no_bulk_edit.py (shell in-place only)
 *   PreToolUse (Edit|Write): scripts/check_mock_spec_ratchet.py
 *   PreToolUse (Edit|Write): scripts/check_no_edit_migration.sh
 *   PreToolUse (Edit|Write): scripts/check_no_edit_baseline.sh
 *   PreToolUse (Edit|Write): scripts/check_no_em_dashes_hook.sh
 *   PreToolUse (Edit|Write): scripts/check_pre_pr_review_triage_gate.sh
 *   PreToolUse (Edit|Write): scripts/check_no_throttle_override_creation.sh
 *   PreToolUse (Edit|Write): scripts/check_no_audit_scratch_scripts.sh
 *   PreToolUse (Edit|Write): scripts/check_no_client_state_persistence_hook.sh
 *   PostToolUse (Edit|Write): scripts/check_web_design_system.py
 *   PostToolUse (Edit|Write): scripts/check_backend_regional_defaults.py
 *   PostToolUse (Edit|Write): scripts/run_edit_time_gates.py
 *   PostToolUse (Bash): scripts/record_push_throttle.sh
 *   PostToolUse (Bash): scripts/rewarm_mypy_after_sync.sh
 *
 * These committed scripts are the single source of truth for the
 * shared hook rules, so OpenCode (this plugin) and Claude Code
 * (.claude/settings.json) enforce identical gates: block-pr-create via
 * check_no_pr_create.sh, no-cd-prefix via check_no_cd_prefix.sh,
 * no-local-coverage via check_no_local_coverage.sh, and
 * enforce-parallel-tests via check_enforce_parallel_tests.sh.
 */

import type { Plugin } from "@opencode-ai/plugin";
import { spawnSync, execSync } from "child_process";

/** Discriminated result of running a hook script.
 *
 * Callers MUST treat ``"error"`` exactly like ``"deny"`` (fail closed);
 * otherwise a hook script crash or timeout silently opens the gate that
 * the hook is meant to guard. */
type HookOutcome =
  | { outcome: "allow" }
  | { outcome: "deny"; reason: string }
  | { outcome: "error"; reason: string };

const _DENY_PATTERN = /\b(block(?:ed|s)?|den(?:y|ied|ies))\b/i;

/** Outer bound on the shutdown daemon stop.
 *
 * The script bounds each `dmypy stop` itself, so this only covers a stall
 * before or between those calls. Without it a wedged interpreter would leave
 * the dispose handler awaiting a promise that never settles. */
const _DAEMON_STOP_TIMEOUT_MS = 60000;

function _stdoutString(value: string | null | undefined): string {
  return typeof value === "string" ? value : "";
}

function _parseEnvelope(raw: string): HookOutcome | null {
  try {
    const parsed = JSON.parse(raw);
    const decision = parsed?.hookSpecificOutput?.permissionDecision;
    if (decision === "deny") {
      const reason = parsed?.hookSpecificOutput?.permissionDecisionReason
        || "Hook denied this action";
      return { outcome: "deny", reason };
    }
    if (decision === "allow") {
      return { outcome: "allow" };
    }
    return null;
  } catch {
    return null;
  }
}

function _parseLegacy(raw: string): HookOutcome {
  // Pre-structured-envelope hook scripts print free-form text. Match any
  // inflection of ``block`` / ``deny`` (including the literal
  // ``"Hook denied this action"`` fallback emitted when the script exits
  // with status 2 but no stdout) so we never silently treat a denial as
  // allow. Empty stdout on a zero exit is an allow.
  if (raw.length === 0) {
    return { outcome: "allow" };
  }
  if (_DENY_PATTERN.test(raw)) {
    return { outcome: "deny", reason: raw };
  }
  return { outcome: "allow" };
}

function runHookScript(
  scriptPath: string,
  toolInput: Record<string, unknown>,
  timeoutMs: number = 10000,
  toolName?: string,
): HookOutcome {
  // Pick the interpreter from the script extension. Bash scripts run via
  // ``bash``; Python scripts run via ``python3`` so the shebang is honoured
  // on Windows where ``./script.py`` would not resolve, and on modern Linux
  // distros that ship only ``python3`` (no unversioned ``python``). Add new
  // extensions here if a hook script in another language is introduced.
  const interpreter = scriptPath.endsWith(".py") ? "python3" : "bash";
  let result: ReturnType<typeof spawnSync>;
  try {
    const envelope: Record<string, unknown> = { tool_input: toolInput };
    if (toolName) {
      envelope.tool_name = toolName;
    }
    const input = JSON.stringify(envelope);
    result = spawnSync(interpreter, [scriptPath], {
      input,
      timeout: timeoutMs,
      encoding: "utf-8",
      stdio: ["pipe", "pipe", "pipe"],
    });
  } catch (error: unknown) {
    const err = error as { message?: string };
    return {
      outcome: "error",
      reason: `${scriptPath} failed to execute: ${err.message ?? "unknown error"}`,
    };
  }
  if (result.error) {
    // ``result.error`` is set on timeout (ETIMEDOUT) or spawn failures.
    // Fail closed: the hook is guarding something, and we refuse to
    // guess at the outcome on infrastructure failure.
    return {
      outcome: "error",
      reason: `${scriptPath} failed: ${result.error.message}`,
    };
  }
  const stdout = _stdoutString(result.stdout as string | null);
  const stderr = _stdoutString(result.stderr as string | null);
  if (result.status === 2) {
    // Status 2 is the hook-contract "deny" exit code. Prefer a structured
    // ``hookSpecificOutput`` envelope on stdout, then fall back to free-text
    // stdout, then to stderr (Python hooks write the human-readable reason
    // there), then to the synthetic "Hook denied this action" string.
    const envelope = _parseEnvelope(stdout);
    if (envelope && envelope.outcome !== "allow") {
      return envelope;
    }
    if (stdout.length > 0) {
      return { outcome: "deny", reason: stdout };
    }
    if (stderr.length > 0) {
      return { outcome: "deny", reason: stderr };
    }
    return { outcome: "deny", reason: "Hook denied this action" };
  }
  if (result.status !== 0) {
    return {
      outcome: "error",
      reason:
        `${scriptPath} exited with status ${String(result.status)}`
        + (stderr.length > 0 ? `: ${stderr}` : ""),
    };
  }
  // Status 0: prefer the structured envelope, fall back to the legacy
  // free-text regex. Either way we cannot return ``null``; silence on
  // a zero exit is an allow.
  const envelope = _parseEnvelope(stdout);
  return envelope ?? _parseLegacy(stdout);
}

/** Convert a hook outcome into a deny reason or ``null`` for allow.
 *
 * Errors are surfaced as denials with a prefix so the failure mode is
 * visible in the raised error; this is the fail-closed guarantee. */
function denyReasonFromOutcome(outcome: HookOutcome): string | null {
  if (outcome.outcome === "allow") {
    return null;
  }
  if (outcome.outcome === "error") {
    return `Hook execution failed (fail-closed): ${outcome.reason}`;
  }
  return outcome.reason;
}

export const SynthOrgHooks: Plugin = async ({ $, worktree }) => {
  return {
    tool: {
      execute: {
        before: async (input, output) => {
          // Edit / Write PreToolUse hooks: bulk-edit guard / migration /
          // baseline / triage-gate lock
          if (input.tool === "edit" || input.tool === "write") {
            const filePath = typeof output.args?.file_path === "string"
              ? output.args.file_path as string
              : "";

            // Bulk-edit guard runs first so the operator-authored Python
            // script is the single source of truth for the rule (matches
            // the Claude Code config in .claude/settings.json).
            if (input.tool === "edit") {
              const editArgs = (output.args ?? {}) as Record<string, unknown>;
              const bulkOutcome = runHookScript(
                "scripts/check_no_bulk_edit.py",
                editArgs,
                5000,
                "Edit",
              );
              const bulkDeny = denyReasonFromOutcome(bulkOutcome);
              if (bulkDeny) {
                throw new Error(bulkDeny);
              }
            }

            const filePathInput = { file_path: filePath } as Record<string, unknown>;
            const args = (output.args ?? {}) as Record<string, unknown>;

            // Mock-spec ratchet: blocks edits that would increase the
            // gate's CATCH count in any tests/*.py file, and edits that
            // weaken scripts/check_mock_spec.py. The hook needs the
            // full Edit / Write payload (file_path + old_string /
            // new_string / content) so it runs before the
            // file-path-only checks below.
            {
              const ratchetInput = { ...filePathInput } as Record<string, unknown>;
              if (typeof args.old_string === "string") {
                ratchetInput.old_string = args.old_string;
              }
              if (typeof args.new_string === "string") {
                ratchetInput.new_string = args.new_string;
              }
              if (typeof args.replace_all === "boolean") {
                ratchetInput.replace_all = args.replace_all;
              }
              if (typeof args.content === "string") {
                ratchetInput.content = args.content;
              }
              const outcome = runHookScript(
                "scripts/check_mock_spec_ratchet.py",
                ratchetInput,
                10000,
                input.tool === "edit" ? "Edit" : "Write",
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // Order must match `.claude/settings.json` PreToolUse Edit|Write:
            //   migration, baseline, em-dash (richer payload), triage-gate.
            // The em-dash hook runs between baseline and triage-gate to enforce
            // content-correctness before the workflow-lock fires.
            for (const script of [
              "scripts/check_no_edit_migration.sh",
              "scripts/check_no_edit_baseline.sh",
            ]) {
              const outcome = runHookScript(
                script,
                filePathInput,
                5000,
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // check_no_em_dashes_hook.sh: inspects the candidate content
            // before it lands on disk (mirrors scripts/check_no_em_dashes.py).
            const emDashInput = { ...filePathInput } as Record<string, unknown>;
            if (typeof args.content === "string") {
              emDashInput.content = args.content;
            }
            if (typeof args.new_string === "string") {
              emDashInput.new_string = args.new_string;
            }
            {
              const outcome = runHookScript(
                "scripts/check_no_em_dashes_hook.sh",
                emDashInput,
                5000,
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            {
              const outcome = runHookScript(
                "scripts/check_pre_pr_review_triage_gate.sh",
                filePathInput,
                5000,
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // check_no_throttle_override_creation.sh for Edit|Write:
            // blocks creation of files that would override the push-throttle
            // gate (e.g. allowlist files, fake clock helpers). Mirrors the
            // corresponding hook in .claude/settings.json so OpenCode does
            // not become a side door past a Claude-Code-enforced rule.
            {
              const outcome = runHookScript(
                "scripts/check_no_throttle_override_creation.sh",
                filePathInput,
                5000,
                input.tool === "edit" ? "Edit" : "Write",
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // check_no_audit_scratch_scripts.sh: during a /codebase-audit run
            // (marker _audit/.audit-run-active present), blocks agents writing
            // helper *.py / *.sh scripts to the project root or scripts/. Inert
            // otherwise, so normal development is unaffected. Mirrors the
            // corresponding hook in .claude/settings.json.
            {
              const outcome = runHookScript(
                "scripts/check_no_audit_scratch_scripts.sh",
                filePathInput,
                5000,
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // check_no_client_state_persistence_hook.sh: block client-side state
            // persistence (localStorage / sessionStorage / indexedDB / zustand
            // persist) in web/src/ before the write lands. The dashboard is a
            // pure API consumer; the backend is the single source of truth.
            // Mirrors the corresponding hook in .claude/settings.json.
            {
              // Pass the candidate text so the hook can inspect the pending
              // edit (Write ``content`` / Edit ``new_string``); with file_path
              // alone the script reads no content and cannot block a newly
              // introduced localStorage / persist usage before it lands.
              const persistenceInput = { ...filePathInput } as Record<
                string,
                unknown
              >;
              if (typeof args.content === "string") {
                persistenceInput.content = args.content;
              }
              if (typeof args.new_string === "string") {
                persistenceInput.new_string = args.new_string;
              }
              const outcome = runHookScript(
                "scripts/check_no_client_state_persistence_hook.sh",
                persistenceInput,
                5000,
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }
          }

          // Only the remaining bash / shell checks apply below
          if (input.tool === "bash" || input.tool === "shell") {
            const command = (output.args?.command as string) ?? "";

            // Bulk-edit guard for shell commands: defers to the Python script
            // so the rule lives in one place (sed -i, awk -i inplace, perl -i,
            // perl -pi, gawk -i inplace). Bulk edits require explicit user
            // approval; the Edit tool with replace_all=false is the sanctioned
            // per-occurrence path. This check runs BEFORE other Bash hooks
            // (push_rebased, baseline_update, bash_no_write, git_c_cwd) so a
            // bulk-edit block fails fast.
            const bulkOutcome = runHookScript(
              "scripts/check_no_bulk_edit.py",
              { command },
              5000,
              "Bash",
            );
            const bulkDeny = denyReasonFromOutcome(bulkOutcome);
            if (bulkDeny) {
              throw new Error(bulkDeny);
            }

            // block-pr-create / no-cd-prefix / no-local-coverage /
            // enforce-parallel-tests: defer to the committed scripts so the
            // rule lives in one place and OpenCode stays in lockstep with
            // .claude/settings.json. Reimplementing any of these as an inline
            // regex here would give the rule two homes that drift apart, and
            // the stricter copy then blocks a documented, correct command.
            for (const script of [
              "scripts/check_no_repush_after_failure.sh",
              "scripts/check_no_cmd_pager_pipe.sh",
              "scripts/check_no_pr_create.sh",
              "scripts/check_no_git_no_verify.sh",
              "scripts/check_no_cd_prefix.sh",
              "scripts/check_no_local_coverage.sh",
              "scripts/check_enforce_parallel_tests.sh",
            ]) {
              const outcome = runHookScript(script, { command }, 5000, "Bash");
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // check_push_throttle.sh + check_ci_before_push.sh + check_push_rebased.sh:
            // run unconditionally on git push so the OpenCode plugin enforces
            // the same pre-push gates that .claude/settings.json applies under
            // Claude Code. Throttle gate comes first (cheap fail-fast on the
            // record), then CI-before-push (waits for the latest run), then
            // rebase check.
            //
            // Match the ``git push`` token pair case-insensitively with word
            // boundaries so spacing variants and shell aliases ("  git   PUSH
            // origin -f") still trigger the gates. A plain
            // ``command.includes("git push")`` was bypassable by any extra
            // whitespace or upper-case form.
            if (/(?:^|\s)git\s+push(?:\s|$)/i.test(command)) {
              for (const script of [
                "scripts/check_push_throttle.sh",
                "scripts/check_ci_before_push.sh",
                "scripts/check_push_rebased.sh",
              ]) {
                const outcome = runHookScript(
                  script,
                  { command },
                  15000,
                );
                const denyReason = denyReasonFromOutcome(outcome);
                if (denyReason) {
                  throw new Error(denyReason);
                }
              }
            }

            // check_no_throttle_override_creation.sh for Bash: blocks shell
            // invocations that would create a file capable of overriding the
            // push-throttle record (e.g. ``rm`` against the record, ``echo``
            // a fake throttle file). Mirrored from the matching Bash entry
            // in .claude/settings.json.
            {
              const outcome = runHookScript(
                "scripts/check_no_throttle_override_creation.sh",
                { command },
                5000,
                "Bash",
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // check_no_baseline_update.sh: block --update-baseline /
            // --refresh-baseline invocations on gate scripts.  Invoke
            // unconditionally because aliases / subprocess wrappers could
            // hide the literal flag tokens.
            {
              const outcome = runHookScript(
                "scripts/check_no_baseline_update.sh",
                { command },
                5000,
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // check_bash_no_write.sh: block file writes via Bash
            const bashWriteOutcome = runHookScript(
              "scripts/check_bash_no_write.sh",
              { command },
              5000,
            );
            const bashWriteDeny = denyReasonFromOutcome(bashWriteOutcome);
            if (bashWriteDeny) {
              throw new Error(bashWriteDeny);
            }

            // check_git_c_cwd.sh: block unnecessary git -C to cwd
            if (command.includes("git") && command.includes("-C")) {
              const outcome = runHookScript(
                "scripts/check_git_c_cwd.sh",
                { command },
                5000,
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }
          }
        },
        after: async (input, output) => {
          // record_push_throttle.sh PostToolUse for Bash: records a successful
          // git push timestamp so subsequent ``check_push_throttle.sh``
          // invocations can rate-limit pushes per round. Mirrors the matching
          // PostToolUse entry in .claude/settings.json. The record script
          // itself filters non-push commands, so invoking it on every Bash
          // PostToolUse is correct and matches the Claude Code config.
          if (input.tool === "bash" || input.tool === "shell") {
            const command = (output.args?.command as string) ?? "";
            const outcome = runHookScript(
              "scripts/record_push_throttle.sh",
              { command },
              5000,
              "Bash",
            );
            const denyReason = denyReasonFromOutcome(outcome);
            if (denyReason) {
              throw new Error(denyReason);
            }
            // rewarm_mypy_after_sync.sh: a `uv sync` invalidates the resident
            // dmypy graph, so re-warm it off the push path. Fail-open like the
            // SessionEnd counterpart -- it is housekeeping, and the script
            // itself declines unless a daemon is already resident.
            //
            // `runHookScript` sends only `tool_input`, so the script's
            // did-the-sync-succeed check sees no signal and re-warms either
            // way. That is the harmless direction: a sync that failed left the
            // old environment in place, so the worst case is one wasted
            // background rebuild, against a slow push if it were skipped.
            runHookScript(
              "scripts/rewarm_mypy_after_sync.sh",
              { command },
              10000,
              "Bash",
            );
            return;
          }
          if (input.tool !== "edit" && input.tool !== "write") {
            return;
          }
          const filePath = typeof output.args?.file_path === "string"
            ? output.args.file_path as string
            : "";

          // Both audit scripts (web_design_system, backend_regional_defaults)
          // enter hook-mode only when they see a ``tool_input.file_path`` JSON
          // payload on stdin (``--hook`` CLI is equivalent but harder to spell
          // portably); without it they either dump usage or silently scan the
          // whole tree. Pass the ``filePath`` explicitly so the scripts validate
          // exactly the file that was just edited / written.
          const hookPayload = JSON.stringify({
            tool_input: { file_path: filePath },
          });

          // All three audits below run via `python3` and pinned to `worktree`,
          // for the reasons the daemon-shutdown handler at the bottom of this
          // file already documents: a relative script path resolved against the
          // plugin process's own directory can miss the script entirely or
          // reach a sibling checkout's copy, and `python3` is what resolves on
          // distros that ship no unversioned `python`.
          //
          // `err.stdout` is read FIRST because every one of these scripts
          // prints its findings to stdout, and Node's execSync puts child
          // stdout on `error.stdout` while `error.message` carries only
          // "Command failed: ..." -- reading message first loses the findings.
          const runAudit = (script: string, timeout: number, label: string) => {
            try {
              execSync(`python3 ${script}`, {
                input: hookPayload,
                timeout,
                encoding: "utf-8",
                cwd: worktree,
              });
            } catch (error: unknown) {
              const err = error as {
                message?: string;
                stdout?: string;
                stderr?: string;
              };
              const errMsg = err.stdout || err.stderr || err.message
                || "Unknown error";
              throw new Error(`${label} failed for ${filePath}: ${errMsg}`);
            }
          };

          // check_web_design_system.py: validate design tokens on web file edits
          if (filePath.includes("web/src/")) {
            runAudit(
              "scripts/check_web_design_system.py",
              10000,
              "Design system check",
            );
          }

          // check_backend_regional_defaults.py: backend regional-defaults audit
          if (filePath.includes("src/synthorg/") && filePath.endsWith(".py")) {
            runAudit(
              "scripts/check_backend_regional_defaults.py",
              10000,
              "Backend regional-defaults check",
            );
          }

          // run_edit_time_gates.py: run the file-scoped convention gates that
          // would otherwise only fire whole-tree at pre-push. No path filter
          // here on purpose -- the dispatcher owns the routing table and exits
          // 0 for anything no gate scopes to, so duplicating its scope in this
          // condition would be a second place to keep in sync.
          runAudit(
            "scripts/run_edit_time_gates.py",
            30000,
            "Edit-time convention gates",
          );
        },
      },
    },

    // Counterpart to the SessionEnd hook in .claude/settings.json. OpenCode
    // exposes no session-end key, so this keys off the server instance for
    // this directory being disposed: past that point nothing here is running,
    // so releasing the worktree's mypy daemons is safe by construction.
    //
    // Deliberately fail-open, unlike every guard above: this reclaims memory
    // and releases the handle that makes a worktree undeletable on Windows,
    // and refusing to shut down because housekeeping failed would be worse
    // than skipping it. If it never fires, the daemon's own idle timeout still
    // reaps it, so this is the fast path rather than the guarantee.
    event: async ({ event }) => {
      if (event.type !== "server.instance.disposed") {
        return;
      }
      // Awaited rather than execSync: a synchronous call here would block the
      // plugin event loop for as long as the stop takes, on the one path
      // where everything else is trying to shut down.
      //
      // Pinned to `worktree` because the command names the script relatively:
      // inheriting the plugin process's directory would either miss the
      // script or reach a sibling checkout and stop ITS daemons, which is
      // worse than not running at all.
      const stop = $`uv run python scripts/run_affected_mypy.py --stop`
        .cwd(worktree)
        .quiet()
        .nothrow();
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          stop,
          new Promise<void>((resolve) => {
            timer = setTimeout(resolve, _DAEMON_STOP_TIMEOUT_MS);
          }),
        ]);
      } catch {
        // Best-effort: see above.
      } finally {
        clearTimeout(timer);
      }
    },
  };
};

export default SynthOrgHooks;
