#!/usr/bin/env bash
# Batch 2: bench Cases. S = engine main cab0220 via bench (1 attempt each); C = plain Claude Code with the contract.
W=/home/jinlin/work/storyloop-xp; V=/home/jinlin/work/storyloop-bench-venv; O=/tmp/claude-1000/-home-jinlin-work-storyloop/b732ce42-84a4-4d19-998b-c5058363bc01/scratchpad/xp/bench-out; mkdir -p $O
echo "== S bench $(date +%T)"
cd $O && PYTHONPATH=$W $V/bin/python -P -m storyloop bench --json run dogfood3-stash-git-safe dogfood6-design-revision-findings dogfood10-repair-next-path \
  --case-version 2 --adapter claude --model opus --scope base-only --suite-source generated --attempts-per-side 1 \
  --base-engine-commit cab0220b6218b0c2a1bd2c8e461a13657c0c492f --engine-repository $W \
  --repository storyloop-dogfood6=/home/jinlin/work/storyloop-dogfood6 --repository storyloop-dogfood3=/home/jinlin/work/storyloop-dogfood3 \
  --engine-python $V/bin/python --gold-python /home/jinlin/work/storyloop-dogfood-venv-main/bin/python --cases-root $W/bench/cases \
  --agent-command /tmp/claude-1000/-home-jinlin-work-storyloop/b732ce42-84a4-4d19-998b-c5058363bc01/scratchpad/xp/claude-agent-xp.sh '{model}' --output-root $O --i-accept-cost --yes < /dev/null > $O/bench.log 2>&1
echo "bench exit $?"
cd /tmp/claude-1000/-home-jinlin-work-storyloop/b732ce42-84a4-4d19-998b-c5058363bc01/scratchpad/xp
for c in dogfood3-stash-git-safe dogfood6-design-revision-findings dogfood10-repair-next-path; do
  echo "== C $c $(date +%T)"; /home/jinlin/work/l3/venv-xp/bin/python -P run_c.py case $c 2>&1 | tail -3
done
echo "== done $(date +%T)"
