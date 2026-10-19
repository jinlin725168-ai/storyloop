"""Offline Claude boundary: record argv/stdin and replay the scenario."""
import json
from pathlib import Path
import sys

root = Path.cwd()
scenario = json.loads((root / 'scenario.json').read_text(encoding='utf-8'))
(root / 'cli-request.json').write_text(json.dumps({'argv': sys.argv[1:], 'prompt': sys.stdin.read()}), encoding='utf-8')
if scenario.get('exit'):
    raise SystemExit(scenario['exit'])
print(json.dumps({'type': 'result', 'subtype': 'success', 'structured_output': scenario['result'],
                  'num_turns': 1, 'modelUsage': {'fixture-strong': {}}, 'usage': {'input_tokens': 7}}))
