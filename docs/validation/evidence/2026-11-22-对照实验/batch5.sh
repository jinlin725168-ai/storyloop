#!/usr/bin/env bash
# Batch 5: C-arm repeat for the divergent bench samples.
X=/tmp/claude-1000/-home-jinlin-work-storyloop/b732ce42-84a4-4d19-998b-c5058363bc01/scratchpad/xp
python3 $X/run_c.py case dogfood6-design-revision-findings -r2 &
python3 $X/run_c.py case dogfood3-stash-git-safe -r2 &
wait
