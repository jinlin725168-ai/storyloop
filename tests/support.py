"""Shared fixtures: a minimal valid product contract."""


def product(manual=False):
    return {"version": 1, "goal": "Reserve a meeting room without overlap", "non_goals": [],
            "questions": [], "stories": [{"id": "ST-1", "title": "Reserve a room", "as_a": "employee",
            "want": "reserve a free room", "so_that": "meetings do not collide", "rules": ["No overlap"],
            "out_of_scope": [], "depends_on": [], "acceptance": [{"id": "AC-1", "given": "a reservation exists",
            "when": "another overlaps", "then": "reject it", "kind": "manual" if manual else "automated"}]}]}

