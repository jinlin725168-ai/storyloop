#!/usr/bin/env bash
# fresh_repo.sh <source repo> <commit> <dest>: a one-commit repo of the base tree (no history, so no
# way to read the upstream solution or later commits).
set -euo pipefail
src=$1; rev=$2; dest=$3
mkdir -p "$dest"
git -C "$src" archive "$rev" | tar -x -C "$dest"
git -C "$dest" init -q -b main
git -C "$dest" config user.name "xp-user"; git -C "$dest" config user.email "xp@example.invalid"
git -C "$dest" add -A
git -C "$dest" commit -q -m "base $rev"
