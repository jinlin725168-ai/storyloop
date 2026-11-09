"""Deterministic links from approved public entry to oracle and design paths."""

from .models import fields, identifier, require, text
from .path_security import repository_relative


def new_format(product: dict) -> bool:
    """The story field itself is the format marker; old contracts stay old."""
    return any("verification_entries" in story for story in product.get("stories", []))


def entry_owners(product: dict) -> dict[str, str]:
    return {entry["id"]: story["id"] for story in product["stories"]
            for entry in story.get("verification_entries", [])}


def validate_entry_paths(option: dict, product: dict, manifest: dict) -> None:
    """Require the selected option to cover each oracle-used automatic entry and AC."""
    if not new_format(product):
        return
    owners = entry_owners(product)
    criterion_stories = {criterion["id"]: story["id"] for story in product["stories"]
                         for criterion in story["acceptance"] if criterion["kind"] == "automated"}
    paths = set(option["touched"] + option["creates"])
    rows = option.get("entry_paths")
    require(isinstance(rows, list) and bool(rows), "Selected option needs entry_paths")
    covered = set()
    for row in rows:
        fields(row, {"entry_id", "criterion_ids", "path", "via"})
        entry_id = identifier(row["entry_id"], "entry path id")
        require(entry_id in owners, "Plan entry_paths references unknown verification entry")
        criteria = row["criterion_ids"]
        require(isinstance(criteria, list) and bool(criteria)
                and all(isinstance(item, str) for item in criteria)
                and len(criteria) == len(set(criteria)), "Plan entry_paths needs unique criterion_ids")
        for criterion_id in criteria:
            require(criterion_id in criterion_stories and criterion_stories[criterion_id] == owners[entry_id],
                    "Plan entry_paths references a cross-story or manual criterion")
            covered.add((criterion_id, entry_id))
        path = repository_relative(row["path"], "entry path")
        require(path in paths, "Plan entry path is outside touched/creates")
        text(row["via"], "entry path via")
    required = set()
    for test in manifest["tests"]:
        for criterion_id in test["criteria"]:
            require(criterion_id in criterion_stories, "Oracle references unknown automatic criterion")
            for entry_id in test["entry_ids"]:
                require(entry_id in owners, "Oracle references unknown verification entry")
                if owners[entry_id] == criterion_stories[criterion_id]:
                    required.add((criterion_id, entry_id))
    require(required <= covered, "Plan entry_paths does not cover every oracle-used entry and AC")
