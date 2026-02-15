#!/usr/bin/env bash
set -euo pipefail

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not inside a git repository."
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 \"<type>: <summary>\" [\"Validation: <cmd/result>\"]"
  exit 1
fi

commit_subject="$1"
validation_line="${2:-Validation: not specified}"
branch="$(git branch --show-current)"

if [[ -z "${branch}" ]]; then
  echo "Could not determine current branch."
  exit 1
fi

if [[ -z "$(git status --porcelain)" ]]; then
  echo "No changes to commit."
  exit 0
fi

git add -A
git commit -m "${commit_subject}" -m $'Context:\n- finalize completed work and sync with remote\n\nChanges:\n- apply all staged modifications for this task\n\n'"${validation_line}"

if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  git push
else
  git push -u origin "${branch}"
fi

echo "Committed and pushed branch '${branch}'."
