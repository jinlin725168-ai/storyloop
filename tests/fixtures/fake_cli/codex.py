"""Offline Codex boundary: record argv/stdin and replay the scenario."""
import json
from pathlib import Path
import sys

root = Path.cwd()
scenario = json.loads((root / 'scenario.json').read_text(encoding='utf-8'))
(root / 'cli-request.json').write_text(json.dumps({'argv': sys.argv[1:], 'prompt': sys.stdin.read()}), encoding='utf-8')
if scenario.get('exit'):
    raise SystemExit(scenario['exit'])
output = Path(sys.argv[sys.argv.index('--output-last-message') + 1])
output.write_text(json.dumps(scenario['result']), encoding='utf-8')
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 7, 'output_tokens': 3}}))
