#!/usr/bin/env bash
cd /tmp/claude-1000/-home-jinlin-work-storyloop/b732ce42-84a4-4d19-998b-c5058363bc01/scratchpad/xp
for s in query-stdin insert-type-option; do
  echo "== S $s $(date +%T)"; /home/jinlin/work/l3/venv-xp/bin/python -P run_s.py $s 2>&1 | tail -3
done
echo "== done $(date +%T)"
