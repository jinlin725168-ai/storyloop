"""Report v2 stores no presentation; tests that inspect sections rebuild it as `report --full` does."""
from pathlib import Path


def with_presentation(root, report: dict) -> dict:
    if "presentation" in report:
        return report
    from storyloop.changes import load_change
    from storyloop.report_presentation import build_presentation
    return dict(report, presentation=build_presentation(Path(root), load_change(Path(root), report["change_id"]), report))
