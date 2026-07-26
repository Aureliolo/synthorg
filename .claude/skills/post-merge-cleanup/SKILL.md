# Post-Merge Cleanup

Run this after squash-merging a PR to clean up the local repo.

## Steps

1. Switch to main and pull latest:
   ```bash
   git checkout main && git pull
   ```

2. Prune remote tracking branches that no longer exist on the remote:
   ```bash
   git fetch --prune
   ```

3. Delete local branches whose remote tracking branch is gone:

   First check which branches are gone using the plumbing command:

   ```bash
   git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads | grep '\[gone\]$'
   ```

   If no gone branches exist, skip this step. Otherwise, delete each one individually:

   ```bash
   git branch -D <branch-name>
   ```

   If a gone branch has an associated worktree, stop that worktree's mypy daemons and then remove the worktree, before deleting the branch:

   ```bash
   uv run --project <path> python <path>/scripts/run_affected_mypy.py --stop
   git worktree remove <path>
   ```

   The `--stop` runs first because a live daemon keeps an open handle on the worktree's `.venv` interpreter, which on Windows makes the directory undeletable: `git worktree remove` then fails with `Invalid argument`, an error that looks nothing like its cause. It is a no-op when no daemon is running, so run it unconditionally. This mirrors step 5 of `/worktree cleanup`.

   If the daemon belongs to a session that is already gone, `--stop` cannot reach it (the status file is in the worktree being removed). Find the holder and stop it directly, then remove the leftover directory:

   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
     Where-Object { $_.CommandLine -like '*<worktree-dir-name>*' } |
     ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
   ```

   Match on the specific worktree path, never on `mypy.dmypy` alone: other worktrees (and a push in flight) have their own daemons that must survive. If `git worktree remove` already dropped the registration but left the directory, delete the directory and run `git worktree prune`.

   Avoid piped bulk deletion (e.g., via `xargs`) to reduce the risk of accidental destructive operations. Use explicit `git branch -D branch1 branch2` calls instead.

4. Check for any remaining non-main local branches and report them. Do NOT delete branches that still have a remote; only report them.

5. Confirm the workspace is clean with `git status`.
