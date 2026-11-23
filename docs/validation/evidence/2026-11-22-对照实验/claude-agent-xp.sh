#!/usr/bin/env bash
# Experiment adapter: $1 = model; the Claude adapter from the engine checkout under test (main cab0220).
exec /usr/bin/env PYTHONPATH=/home/jinlin/work/storyloop-xp /home/jinlin/work/storyloop-bench-venv/bin/python -P -m storyloop.claude_agent --model "$1" --tier-model "strong=$1" --tier-model "standard=$1"
