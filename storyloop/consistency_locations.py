"""Restricted plan JSON references; this module never reads arbitrary files."""
import re

FIELDS = {'summary', 'constraints', 'regression', 'migration', 'dependencies', 'principle_checks'}


def validate_plan_location(plan: dict, location: str) -> bool:
    """Accept an existing node below an allowed option field, without JSON pointer escapes."""
    if not isinstance(location, str) or not location.startswith('plan:'):
        return False
    parts = location[5:].split('/')
    if len(parts) < 2 or parts[1] not in FIELDS:
        return False
    options = [item for item in plan.get('options', []) if item.get('id') == parts[0]]
    if len(options) != 1:
        return False
    node = options[0]
    for segment in parts[1:]:
        if isinstance(node, dict) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and re.fullmatch(r'0|[1-9][0-9]*', segment):
            if len(segment) > 8 or int(segment) >= len(node):
                return False
            node = node[int(segment)]
        else:
            return False
    return True
