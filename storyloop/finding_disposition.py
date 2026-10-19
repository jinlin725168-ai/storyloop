"""Engine-owned classification of review evidence."""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from .models import StoryLoopError, require


def dispose(findings, thresholds, plan_decisions) -> dict:
    """Classify findings in priority order, retaining stable references for verification."""
    fix, focus = thresholds.get('fix', 80), thresholds.get('focus', 50)
    require(type(fix) is int and type(focus) is int and 0 <= focus <= fix <= 100,
            'Invalid review_thresholds')
    result = {key: [] for key in ('fix', 'stop', 'focus', 'human', 'legacy_issue', 'out_of_scope')}
    result.update(dropped=0, checklist_missing=[])
    for index, original in enumerate(findings):
        item = dict(original)
        item.setdefault('id', 'F-' + hashlib.sha256(json.dumps(
            [index, original], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16])
        confidence = item.get('confidence', 100)
        require(type(confidence) is int and 0 <= confidence <= 100, 'Invalid finding confidence')
        if item.get('introduced', True) is False:
            item['confidence'] = 0
            bucket = 'legacy_issue'
        elif item.get('target') == 'out_of_scope':
            bucket = 'out_of_scope'
        elif confidence >= fix:
            bucket = 'stop' if item.get('target') in ('oracle', 'contract') else 'fix'
        elif confidence >= focus:
            bucket = 'focus'
        elif item.get('severity') in ('P0', 'P1'):
            bucket = 'human'
        else:
            result['dropped'] += 1
            continue
        result[bucket].append(item)
    return result


def check_checklist(review, decisions):
    """Require exactly one checklist row for each approved decision."""
    rows = review.get('checklist', [])
    counts = Counter(row.get('decision') for row in rows)
    missing = [decision for decision in decisions if counts[decision] != 1]
    if missing or set(counts) - set(decisions):
        raise StoryLoopError('Review checklist must cover each approved decision exactly once',
                             code='AGENT_CONTRACT_INVALID', details={'checklist_missing': missing, 'review_semantic': True})
    return missing
