#!/usr/bin/env bash
# git_push_retry.sh -- rebase this run's commit onto the moved branch and push.
#
# Shared by every workflow that commits back to the repo. It replaces the
# per-workflow loop `git pull --rebase && git push`, retried three times, which
# could not survive a conflict: every derived artifact is -merge in
# .gitattributes, so when another run had already committed the same generated
# file, the rebase conflicted identically on all three attempts and the job
# failed (proposals-intel, 2026-09-29T01:43Z).
#
# Now, the same handling pipeline.yml carries inline since run #244:
#   - push rejected, no conflict  -> abort, wait, retry (up to 3 times)
#   - conflict in generated files -> scripts/resolve_generated_conflicts.py takes
#     "ours" (during a rebase that is UPSTREAM, the newer copy), regenerates
#     what it safely can, and the rebase continues. Anything it defers is
#     rebuilt by the next pipeline run.
#   - conflict in anything else   -> source of record, append-only, or
#     hand-maintained: never auto-resolved. Abort and fail for a person.
#
# Exit 0 when pushed (or when nothing was left to push), 1 otherwise.
# Usage: bash scripts/git_push_retry.sh   (needs GITHUB_REF_NAME)

set -u
BRANCH="${GITHUB_REF_NAME:?GITHUB_REF_NAME is not set}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for i in 1 2 3; do
  if git pull --rebase --autostash origin "$BRANCH"; then
    git push && exit 0
  elif [ -n "$(git diff --name-only --diff-filter=U)" ]; then
    echo "::group::Rebase conflict on attempt $i -- resolving generated paths"
    git diff --name-only --diff-filter=U
    if python "$HERE/resolve_generated_conflicts.py"; then
      echo "::endgroup::"
      if GIT_EDITOR=true git rebase --continue; then
        echo "::warning::Generated-file conflict resolved to the upstream copy; anything the resolver could not rebuild here refreshes on the next run."
        git push && exit 0
      elif git diff --cached --quiet && [ -z "$(git diff --name-only --diff-filter=U)" ]; then
        # Taking upstream's copy left this run's commit empty: main already
        # has everything it would have pushed.
        git rebase --skip >/dev/null 2>&1 || git rebase --abort 2>/dev/null || true
        if [ -z "$(git log "origin/$BRANCH..HEAD" --oneline)" ]; then
          echo "::warning::Upstream already has newer copies of every file this run changed; nothing left to push."
          exit 0
        fi
        git push && exit 0
      fi
    else
      echo "::endgroup::"
      echo "::error::Rebase conflict in a non-generated file (source of record, append-only, or hand-maintained). Not auto-resolved; the list is above."
      git rebase --abort 2>/dev/null || true
      exit 1
    fi
  fi
  echo "Push attempt $i failed (another workflow moved the branch); retrying in 10s"
  git rebase --abort 2>/dev/null || true
  sleep 10
done
echo "Push failed after 3 attempts. If branch protection is enabled on $BRANCH, allow github-actions[bot] to push or merge via PR."
exit 1
