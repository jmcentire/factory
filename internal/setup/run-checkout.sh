#!/usr/bin/env bash
# factory-setup: a Factory run's own checkout, so the run never commits in the human's.
#   start  <target> <run> <base> <branch>  clone <target> into <target>/.factory/checkouts/<run>
#   check  <target> <run>                  fail if <target>'s commits moved, or origin's run
#                                          branch has commits the run's checkout lacks
#   accept <target> <run>                  re-baseline after the human says a change was theirs
#   finish <target> <run>                  delete the run's checkout once all of it is pushed
#   list   <target>                        each run's checkout and rebuildable run bulk, whether it
#                                          is safe to delete, and the disk's free space; deletes nothing
#   clean  <target> <run>                  finish, plus the run's rebuildable bulk, on the human's yes
set -euo pipefail
die() { echo "run-checkout: $*" >&2; exit 1; }
usage="usage: run-checkout.sh start <target> <run> <base> <branch> | check|accept|finish|clean <target> <run> | list <target>"
if [ "${1-}" = list ]; then [ $# -eq 2 ] || die "$usage"; else [ $# -ge 3 ] || die "$usage"; fi
cmd=$1 run=${3-}
T=$(cd "$2" && pwd -P) || die "no directory $2"
[ "$cmd" = list ] || case $run in '' | .* | */*) die "bad run name: $run" ;; esac
[ "$(git -C "$T" rev-parse --show-toplevel 2>/dev/null)" = "$T" ] || die "$T is not a repository root"
W=$T/.factory/checkouts/$run S=$T/.factory/checkouts/$run.human R=$T/.factory/runs/$run
# A finished run's rebuildable bulk: the validation check copies and each lane's agent and
# kindex homes and snapshots. Its evidence, artifacts, reviews, ledgers and lane logs stay.
bulk() {
  local d
  for d in "$1/validation" "$1/tmux-lanes/snapshots" "$1"/tmux-lanes/*-agent-home "$1"/tmux-lanes/*-kindex-home; do
    [ ! -e "$d" ] && [ ! -L "$d" ] || printf '%s\n' "$d"
  done
}
# 0 = live, 1 = confirmed inactive/no run directory, 2 = unknown. Client or
# metadata failures never grant permission to remove a run's work.
live() {
  local sock client diagnostic status
  [ ! -L "$1" ] || return 2
  [ -d "$1" ] || return 1
  [ -f "$1/harness.json" ] && [ ! -L "$1/harness.json" ] || return 2
  sock=$("${FACTORY_PYTHON:-python3}" -I - "$1/harness.json" 2>/dev/null <<'PY'
import json, re, sys
with open(sys.argv[1]) as stream:
    value = json.load(stream)["tmux_socket"]
if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
    raise ValueError("invalid tmux socket")
print(value)
PY
  ) || return 2
  case "$sock" in '' | *[!A-Za-z0-9._-]*) return 2 ;; esac
  client=$(type -P -- "${FACTORY_TMUX:-tmux}") || return 2
  case "$client" in /*) ;; *) client="$PWD/$client" ;; esac
  status=0
  diagnostic=$(LC_ALL=C "$client" -L "$sock" has-session -t "=$2" 2>&1) || status=$?
  [ "$status" -ne 0 ] || return 0
  [ "$status" -eq 1 ] || return 2
  case "$diagnostic" in
    "no server running on "* | "can't find session: "* | \
    "error connecting to "*" (No such file or directory)" | \
    "error connecting to "*" (Connection refused)") return 1 ;;
    *) return 2 ;;
  esac
}
# Every git command on $W first proves $W is its own repository: otherwise git would walk up
# into the human's .git, or a symlink would point it somewhere else.
own() { [ "$(git -C "$W" rev-parse --show-toplevel 2>/dev/null)" = "$W" ] || die "$W is not the run's own checkout"; }

# What the human's checkout looks like: the branch and commit it is on, whether the run's
# branch exists there, and any commit carrying a FactoryRun trailer in its local branches or HEAD history (reflogs keep a commit
# that was made there and then reset away).
# A git failure aborts rather than reading as "unchanged".
snapshot() {
  local v
  echo "for $1"
  echo "branch $(git -C "$T" symbolic-ref -q HEAD || echo detached)"
  echo "head $(git -C "$T" rev-parse -q --verify HEAD || echo none)"
  echo "run-branch $(git -C "$T" rev-parse -q --verify "refs/heads/$1" || echo absent)"
  v=$(git -C "$T" log -g --branches HEAD -E --grep='^FactoryRun: ' --format=%H) || die "git log failed in $T"
  echo "run-commits" $(printf '%s\n' "$v" | sort -u)
}

# Why $W cannot be deleted, or nothing when all of it is on origin.
unpushed() {
  local v
  v=$(git -C "$W" --no-optional-locks status --porcelain=v1 --untracked-files=all --ignore-submodules=none) || { echo "an unreadable git status"; return; }
  [ -z "$v" ] || { echo "uncommitted changes"; return; }
  v=$(git -C "$W" stash list) || { echo "an unreadable stash list"; return; }
  [ -z "$v" ] || { echo "stashes"; return; }
  # A successful push updates the origin/* ref, so this lists every commit never pushed.
  v=$(git -C "$W" rev-list HEAD --branches --tags --not --remotes) || { echo "unreadable commits"; return; }
  [ -z "$v" ] || echo "commits no origin branch holds"
}

remove_checkout() {
  own
  local why
  why=$(unpushed)
  [ -z "$why" ] || die "$W has $why; keeping it"
  rm -rf -- "$W" "$S"
  echo "run-checkout: removed $W"
}

check() {
  [ -f "$S" ] || die "no snapshot for run $run; it was not started with run-checkout.sh"
  own
  local branch now saved found=0
  branch=$(sed -n 's/^for //p' "$S")
  now=$(snapshot "$branch")
  saved=$(cat "$S")
  if [ "$now" != "$saved" ]; then
    {
      echo "run-checkout: commits in the human's checkout $T moved since run $run started."
      diff <(echo "$saved") <(echo "$now") || true
      echo "A changed run-commits line means a Factory commit is now in its branches (made there,"
      echo "or pulled in by the human). Stop and show the human this; continue only once they"
      echo "confirm every change was theirs (then: run-checkout.sh accept $T $run)."
    } >&2
    exit 1
  fi
  # A push to the PR branch from anywhere but the run's checkout (the human's checkout
  # included) leaves origin ahead of it.
  git -C "$W" ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null || found=$?
  case $found in
  0)
    git -C "$W" fetch -q origin "+refs/heads/$branch:refs/remotes/origin/$branch"
    git -C "$W" merge-base --is-ancestor "refs/remotes/origin/$branch" HEAD ||
      die "origin's $branch has commits $W lacks: something other than this run pushed to it. Stop and show the human; once they confirm, git -C $W merge --ff-only origin/$branch and check again."
    ;;
  2) ;;
  *) die "cannot reach origin from $W" ;;
  esac
  echo "run-checkout: $T unchanged since run $run started"
}

case $cmd in
start)
  [ $# -eq 5 ] || die "usage: run-checkout.sh start <target> <run> <base> <branch>"
  base=$4 branch=$5
  git check-ref-format --branch "$branch" >/dev/null || die "bad branch name: $branch"
  git check-ref-format --branch "$base" >/dev/null || die "bad base name: $base"
  [ ! -e "$W" ] && [ ! -L "$W" ] && [ ! -e "$S" ] && [ ! -L "$S" ] ||
    die "$W exists: an earlier run used the name $run; pick a new one"
  url=$(git -C "$T" remote get-url origin) || die "$T has no origin remote"
  case $url in /* | *:*) ;; *) url=$(cd "$T" && cd "$url" && pwd -P) || die "cannot resolve origin $url" ;; esac
  # A partial clone's copy would miss blobs once its promisor remote is gone.
  [ -z "$(git -C "$T" config --get-regexp '^(extensions\.partialclone|remote\..*\.promisor)$')" ] ||
    die "$T is a partial clone; make the run's checkout by hand with git clone <origin url>"
  # Keep .factory/ out of the human's git status and git add.
  X=$(git -C "$T" rev-parse --path-format=absolute --git-path info/exclude)
  if ! git -C "$T" check-ignore -q ".factory/checkouts/$run"; then
    mkdir -p "${X%/*}"
    [ ! -s "$X" ] || [ -z "$(tail -c1 "$X")" ] || echo >>"$X" # keep their last rule intact
    echo /.factory/ >>"$X"
  fi
  snap=$(snapshot "$branch")
  mkdir -p "$T/.factory/checkouts"
  [ "$(cd "$T/.factory/checkouts" && pwd -P)" = "$T/.factory/checkouts" ] || die "$T/.factory/checkouts is a symlink"
  mkdir "$W" || die "$W exists: another start took the name $run; pick a new one"
  # A failed start removes only what it created, so the same name can be retried.
  trap 'rm -rf -- "$W" "$S"; exit 1' ERR INT TERM
  printf '%s\n' "$snap" >"$S"
  # A clone, not a worktree: nothing shared with the human's refs, index, stash or
  # worktree list, and no remote left that points back at their checkout.
  git clone -q --no-checkout --no-tags --origin checkout "$T" "$W"
  git -C "$W" remote remove checkout
  git -C "$W" remote add origin "$url"
  # The harness writes .factory/ relative to its cwd; keep it out of the run's commits.
  mkdir -p "$W/.git/info" && echo /.factory/ >>"$W/.git/info/exclude"
  git -C "$W" fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base"
  from=$base found=0
  git -C "$W" ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null || found=$?
  case $found in
  0) # an open PR's branch continues where it is
    from=$branch
    git -C "$W" fetch -q origin "+refs/heads/$branch:refs/remotes/origin/$branch" ;;
  2) ;; # not on origin yet
  *) false ;; # origin unreachable: fail (and clean up), never silently restart from base
  esac
  git -C "$W" switch -q --discard-changes --no-track -C "$branch" "refs/remotes/origin/$from"
  # The clone copied the human's current branch; drop it so it can never be pushed.
  git -C "$W" for-each-ref --format='%(refname:lstrip=2)' refs/heads | while read -r b; do
    [ "$b" = "$branch" ] || git -C "$W" branch -q -D "$b"
  done
  [ ! -f "$W/.gitmodules" ] || git -C "$W" submodule update -q --init --recursive
  trap - ERR INT TERM
  echo "$W"
  ;;
check) check ;;
accept)
  [ -f "$S" ] || die "no snapshot for run $run"
  branch=$(sed -n 's/^for //p' "$S")
  snap=$(snapshot "$branch")
  printf '%s\n' "$snap" >"$S"
  echo "run-checkout: re-baselined $T for run $run"
  ;;
finish) remove_checkout ;;
clean)
  [ ! -L "$R" ] || die "$R is a symlink; keeping it"
  [ ! -d "$R" ] || [ "$(cd "$R" && pwd -P)" = "$R" ] || die "$R is not under $T; keeping it"
  state=0
  live "$R" "$run" || state=$?
  case "$state" in
    0) die "run $run still has a live tmux session; keeping it" ;;
    1) ;;
    *) die "run $run tmux liveness is unknown; check FACTORY_TMUX, FACTORY_PYTHON and harness.json; keeping it" ;;
  esac
  # Deletes only inside the run's own folder under <target>, never through a symlink.
  [ ! -L "$R" ] || die "$R is a symlink; keeping it"
  [ ! -d "$R" ] || [ "$(cd "$R" && pwd -P)" = "$R" ] || die "$R is not under $T; keeping it"
  if [ -e "$W" ]; then remove_checkout; else rm -rf -- "$S"; fi
  [ ! -d "$R" ] || bulk "$R" | while IFS= read -r b; do
    [ ! -L "$b" ] || die "$b is a symlink; keeping it"
    rm -rf -- "$b"
    echo "run-checkout: removed $b"
  done
  ;;
list)
  for run in $(ls "$T/.factory/checkouts" "$T/.factory/runs" 2>/dev/null | grep -v -e ':$' -e '[.]human$' -e '^$' | sort -u); do
    case $run in .* | *.json | *.manifest | *.lock) continue ;; esac
    W=$T/.factory/checkouts/$run R=$T/.factory/runs/$run
    b=()
    if [ -d "$R" ]; then while IFS= read -r d; do b+=("$d"); done < <(bulk "$R"); fi
    [ -d "$W" ] || [ ${#b[@]} -gt 0 ] || continue
    co=- state=ready
    if [ -d "$W" ]; then
      co=$(du -sh -- "$W" 2>/dev/null | awk '{print $1}' || true)
      if [ "$(git -C "$W" rev-parse --show-toplevel 2>/dev/null)" = "$W" ]; then
        why=$(unpushed)
        [ -z "$why" ] || state="keep: $why"
      else
        state="keep: not the run's own checkout"
      fi
    fi
    liveness=0
    live "$R" "$run" || liveness=$?
    case "$liveness" in
      0) state="keep: live tmux session" ;;
      1) ;;
      *) state="keep: tmux liveness unknown" ;;
    esac
    rb=-
    [ ${#b[@]} -eq 0 ] || rb=$(du -shc -- "${b[@]}" 2>/dev/null | tail -1 | awk '{print $1}' || true)
    echo "$run  checkout ${co:-?}  run bulk ${rb:-?}  $state"
  done
  df -h "$T" | awk 'NR==2 {print "free on this disk: " $4}'
  ;;
*) die "unknown command: $cmd" ;;
esac
