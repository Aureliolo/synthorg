/**
 * SynthOrg hooks plugin for OpenCode.
 *
 * Mirrors the Claude Code hooks defined in .claude/settings.json and
 * .claude/settings.local.json by calling the same shell scripts.
 *
 * Committed Claude Code hooks (from .claude/settings.json):
 *   PreToolUse (Bash): scripts/check_push_rebased.sh
 *   PreToolUse (Bash): scripts/check_no_atlas_rehash.sh
 *   PreToolUse (Bash): scripts/check_no_baseline_update.sh
 *   PreToolUse (Bash): scripts/check_bash_no_write.sh
 *   PreToolUse (Bash): scripts/check_git_c_cwd.sh
 *   PreToolUse (Bash | Edit): scripts/check_no_bulk_edit.py
 *   PreToolUse (Edit|Write): scripts/check_mock_spec_ratchet.py
 *   PreToolUse (Edit|Write): scripts/check_no_edit_migration.sh
 *   PreToolUse (Edit|Write): scripts/check_no_edit_baseline.sh
 *   PreToolUse (Edit|Write): scripts/check_no_em_dashes_hook.sh
 *   PreToolUse (Edit|Write): scripts/check_pre_pr_review_triage_gate.sh
 *   PostToolUse (Edit|Write): scripts/check_web_design_system.py
 *   PostToolUse (Edit|Write): scripts/check_backend_regional_defaults.py
 *
 * Hookify rules enforced via this plugin (from .claude/hookify.*.md):
 *   block-pr-create: blocks direct `gh pr create`
 *   enforce-parallel-tests: enforces `-n 8` with pytest
 *   no-cd-prefix: blocks `cd` prefix in Bash commands
 *   no-local-coverage: blocks `--cov` flags locally
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
  // ``bash``; Python scripts run via ``python3`` so the shebang is honored
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

export const SynthOrgHooks: Plugin = async ({ client, $, app }) => {
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
          }

          // Only the remaining bash / shell checks apply below
          if (input.tool === "bash" || input.tool === "shell") {
            const command = (output.args?.command as string) ?? "";

            // Bulk-edit guard for shell commands: defers to the Python script
            // so the rule lives in one place (sed -i, awk -i inplace, perl -i,
            // perl -pi, gawk -i inplace). Bulk edits require explicit user
            // approval; the Edit tool with replace_all=false is the sanctioned
            // per-occurrence path. This check runs BEFORE other Bash hooks
            // (push_rebased, atlas_rehash, bash_no_write, git_c_cwd) so a
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

            // block-pr-create: block direct gh pr create
            if (/gh\s+pr\s+create/i.test(command)) {
              throw new Error(
                "PR creation blocked. Use `/pre-pr-review` instead; it runs automated checks + review agents + fixes before creating the PR. For trivial or docs-only changes: `/pre-pr-review quick` skips agents but still runs automated checks.",
              );
            }

            // enforce-parallel-tests: enforce -n 8 with pytest
            if (
              /(?:^|\s)(?:pytest|run\s+pytest|python\s+-m\s+pytest)\b/i.test(command) &&
              !/-n 8/.test(command)
            ) {
              throw new Error(
                "Always use `-n 8` with pytest for parallel execution. Add `-n 8` to your pytest command. Never run tests sequentially or with `-n auto` (32 workers causes crashes and is slower due to contention).",
              );
            }

            // no-cd-prefix: block cd prefix in Bash commands (with optional leading whitespace)
            if (/^\s*cd\s+/i.test(command)) {
              throw new Error(
                "BLOCKED: Do not use `cd` in Bash commands; it poisons the cwd for all subsequent calls. The working directory is ALREADY set to the project root. Run commands directly. For Go commands: use `go -C cli <command>`. For subdir tools without a `-C`/`--prefix` equivalent: use `bash -c \"cd <dir> && <cmd>\"`.",
              );
            }

            // no-local-coverage: block --cov flags locally
            if (
              /(?:^|\s)(?:pytest|run\s+pytest|python\s+-m\s+pytest)\b/i.test(command) &&
              /--cov\b/.test(command)
            ) {
              throw new Error(
                "Do not run pytest with coverage locally; CI handles it. Coverage adds 20-40% overhead. Remove `--cov`, `--cov-report`, and `--cov-fail-under` from your command.",
              );
            }

            // check_push_rebased.sh: block push if branch is behind main
            if (command.includes("git push")) {
              const outcome = runHookScript(
                "scripts/check_push_rebased.sh",
                { command },
                15000,
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // check_no_atlas_rehash.sh: block `atlas migrate hash` rehash.
            // We invoke the hook for every bash command: a ``command.includes("atlas")``
            // prefilter would let wrapper invocations (``migrate_hash``,
            // ``migrate.hash``, shell aliases, subprocess wrappers) bypass the
            // gate because those strings never contain the literal token
            // ``atlas``. The script itself is the authoritative filter and
            // exits 0 quickly for unrelated commands.
            {
              const outcome = runHookScript(
                "scripts/check_no_atlas_rehash.sh",
                { command },
                5000,
              );
              const denyReason = denyReasonFromOutcome(outcome);
              if (denyReason) {
                throw new Error(denyReason);
              }
            }

            // check_no_baseline_update.sh: block --update-baseline /
            // --refresh-baseline invocations on gate scripts. Like the atlas
            // hook above we invoke unconditionally because aliases /
            // subprocess wrappers could hide the literal flag tokens.
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

          // check_web_design_system.py: validate design tokens on web file edits
          if (filePath.includes("web/src/")) {
            try {
              execSync(
                `python scripts/check_web_design_system.py`,
                {
                  input: hookPayload,
                  timeout: 10000,
                  encoding: "utf-8",
                },
              );
            } catch (error: unknown) {
              const err = error as { message?: string; stderr?: string };
              const errMsg = err.message || err.stderr || "Unknown error";
              throw new Error(`Design system check failed for ${filePath}: ${errMsg}`);
            }
          }

          // check_backend_regional_defaults.py: backend regional-defaults audit
          if (filePath.includes("src/synthorg/") && filePath.endsWith(".py")) {
            try {
              execSync(
                `python scripts/check_backend_regional_defaults.py`,
                {
                  input: hookPayload,
                  timeout: 10000,
                  encoding: "utf-8",
                },
              );
            } catch (error: unknown) {
              const err = error as { message?: string; stderr?: string };
              const errMsg = err.message || err.stderr || "Unknown error";
              throw new Error(`Backend regional-defaults check failed for ${filePath}: ${errMsg}`);
            }
          }
        },
      },
    },
  };
};

export default SynthOrgHooks;
