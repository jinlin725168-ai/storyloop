"""Build the frozen seven-section report snapshot before report persistence."""
from pathlib import Path

from .report_evidence import collect_evidence
from .report_focus import build_focus
from .report_items import effective_tier, item
from .report_reconciliation import reconcile
from .report_sections import conclusion, header, process, proof


TITLES = ('头部', '结论', '审查重点', '证据', '过程', '成本', '记忆')


def build_presentation(project: Path, change: dict, report: dict) -> dict:
    """Freeze bounded sources without modifying acceptance gates or the original report."""
    evidence = collect_evidence(project, change, report)
    reconciliation = reconcile(project, change, report, evidence)
    groups = [header(change, report, evidence), conclusion(change, report, reconciliation),
              build_focus(change, report, evidence), proof(report, evidence),
              process(change, report, evidence), [item('cost', evidence.get('telemetry'))],
              [item('memory', reason='MEMORY_MODULE_NOT_IMPLEMENTED')]]
    sections = {str(index): {'title': title, 'items': items}
                for index, (title, items) in enumerate(zip(TITLES, groups))}
    reconciliation['checks'].append({'id': 'sections', 'status': 'MATCH',
                                      'reason': 'all seven reader sections are present',
                                      'expected': list('0123456'), 'actual': list(sections)})
    sections['1']['items'] = [item('reconciliation', reconciliation) if entry['id'] == 'reconciliation'
                               else entry for entry in sections['1']['items']]
    return {'version': 1, 'tier': effective_tier(change, report, evidence),
            'sections': sections, 'reconciliation': reconciliation}
