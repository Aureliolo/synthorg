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

   First check which branches are gone:

   ```bash
   git branch -vv | grep '\[.*: gone\]'
   ```

   If no gone branches exist, skip this step. Otherwise, delete each one individually:

   ```bash
   git branch -D <branch-name>
   ```

   Do NOT use a piped `xargs` command — it triggers unnecessary permission prompts. Use separate `git branch -D` calls for each gone branch (can be combined in one call: `git branch -D branch1 branch2`).

4. Check for any remaining non-main local branches and report them. Do NOT delete branches that still have a remote — only report them.

5. Confirm the workspace is clean with `git status`.
