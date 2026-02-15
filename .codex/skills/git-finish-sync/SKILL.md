---
name: git-finish-sync
description: Use when a task is complete and changes should be committed and pushed safely with a consistent template, including status checks and upstream handling.
---

# Git Finish Sync

Use this skill at the end of implementation work to standardize `commit & push`.

## When to use

- The user asks to commit/push.
- Work is complete for this turn and changes are ready to publish.

## Required flow

1. Confirm repository root and branch:
   - `git rev-parse --show-toplevel`
   - `git branch --show-current`
2. Review pending changes:
   - `git status --short`
   - `git diff --stat`
3. Stage all intended files:
   - `git add -A`
4. Commit with template:
   - Subject: `<type>: <summary>`
   - Body:
     - `Context: ...`
     - `Changes: ...`
     - `Validation: ...`
5. Push to remote:
   - If upstream exists: `git push`
   - If no upstream: `git push -u origin <branch>`

## Commit message template

```text
<type>: <summary>

Context:
- why this change was needed

Changes:
- key implementation point 1
- key implementation point 2

Validation:
- command/result
```

## Shortcut script

Run:

```bash
bash .codex/skills/git-finish-sync/scripts/git_finish_sync.sh "<type>: <summary>" "Validation: <cmd/result>"
```

The script stages all changes, creates the commit with a standard body, and pushes to origin.
