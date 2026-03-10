# Claude Code Instructions

## Git workflow (mandatory)

1. **Before starting any work:** switch to main, pull latest.
   ```bash
   git checkout main && git pull
   ```

2. **Create a new branch** for every change (feature, fix, anything).
   ```bash
   git checkout -b <descriptive-branch-name>
   ```

3. **Commit and push** the branch to remote when done. Never push directly to main.
   ```bash
   git push -u origin <branch-name>
   ```

4. **Stop there.** The user reviews and merges via GitHub. Do not merge locally.

5. **After the user confirms the merge:** go back to main, pull, then start the next branch.
   ```bash
   git checkout main && git pull
   ```

Never commit directly to main. Never merge branches locally. Always wait for the user's go-ahead before starting the next piece of work.
