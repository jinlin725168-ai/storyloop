"""Validate immutable v1 bench Case sources before calibration or execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .models import StoryLoopError, require
from .changes import contract_digest

SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ID = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


def load_case(source: Path) -> dict:
    """Read one frozen Case and check every declared byte and reference."""
    source = Path(source)
    require(source.is_dir() and not source.is_symlink(), "Case directory is unavailable or linked")
    source = source.resolve()
    manifest_bytes = _file(source, "case.json")
    manifest = _json(manifest_bytes, "case.json")
    _manifest(manifest, source)
    contract_bytes = _file(source, "contract.json")
    gold_bytes = _file(source, "gold.json")
    _hash(contract_bytes, manifest["contract_sha256"], "contract.json")
    _hash(gold_bytes, manifest["gold_sha256"], "gold.json")
    contract = _json(contract_bytes, "contract.json")
    gold = _json(gold_bytes, "gold.json")
    _contract(contract)
    suite_hash = hashlib.sha256()
    paths = []
    for item in manifest["suite_files"]:
        require(isinstance(item, dict) and set(item) == {"path", "sha256"}, "Invalid suite file entry")
        path = item["path"]
        require(isinstance(path, str) and path.startswith("checks/"), "Suite file must be under checks/")
        content = _file(source, path)
        _hash(content, item["sha256"], path)
        suite_hash.update(path.encode("utf-8") + b"\0" + content)
        paths.append(path)
    require(paths == sorted(set(paths)), "Suite files must be sorted and unique")
    _hash(suite_hash.digest(), manifest["suite_sha256"], "suite", already_digest=True)
    _gold(gold, source, contract)
    _check_inventory(source, paths)
    case_hash = hashlib.sha256(manifest_bytes + contract_bytes + gold_bytes + suite_hash.digest()).hexdigest()
    return {"source": source, "manifest": manifest, "contract": contract, "gold": gold,
            "case_hash": case_hash, "gold_hash": manifest["gold_sha256"]}


def _manifest(value: dict, source: Path) -> None:
    fields = {"schema_version", "case_id", "case_version", "repository_id", "base_commit",
              "solution_commit", "contract_sha256", "suite_sha256", "gold_sha256",
              "suite_files", "allowed", "legacy_seam"}
    require(set(value) == fields, "case.json fields are missing or unknown")
    require(value["schema_version"] == 1 and value["case_version"] == 1,
            "Only frozen v1 Cases are supported")
    require(isinstance(value["case_id"], str) and ID.fullmatch(value["case_id"])
            and source.parent.name == value["case_id"] and source.name == "v1", "Invalid Case identity")
    require(isinstance(value["repository_id"], str) and ID.fullmatch(value["repository_id"]),
            "Invalid repository_id")
    require(all(isinstance(value[key], str) and COMMIT.fullmatch(value[key])
                for key in ("base_commit", "solution_commit")), "Invalid source interval")
    require(value["base_commit"] != value["solution_commit"], "Source interval is empty")
    require(all(isinstance(value[key], str) and SHA.fullmatch(value[key])
                for key in ("contract_sha256", "suite_sha256", "gold_sha256")), "Invalid Case hash")
    require(isinstance(value["suite_files"], list) and value["suite_files"], "Suite files are required")
    require(value["legacy_seam"] is True, "v1 Case must mark legacy_seam")
    allowed = value["allowed"]
    require(isinstance(allowed, dict) and set(allowed) == {"adapters", "models", "max_calls", "max_cost_usd"},
            "Invalid allowed execution policy")
    for key in ("adapters", "models"):
        require(isinstance(allowed[key], list) and allowed[key] and all(
            isinstance(item, str) and item.strip() for item in allowed[key]), f"Invalid {key}")
    require(type(allowed["max_calls"]) is int and allowed["max_calls"] > 0, "Invalid max_calls")
    cost = allowed["max_cost_usd"]
    require(cost is None or type(cost) in (int, float) and cost > 0, "Invalid max_cost_usd")


def _contract(value: dict) -> None:
    require(set(value) == {"change_id", "product", "approval"}, "Invalid approved contract")
    approval = value["approval"]
    require(isinstance(approval, dict) and all(approval.get(key) for key in
            ("contract_digest", "approved_at", "approved_by")), "Contract lacks approval evidence")
    require(approval["contract_digest"] == contract_digest(value["product"]),
            "Approved contract digest does not match product")


def _gold(value: dict, source: Path, contract: dict) -> None:
    require(set(value) == {"schema_version", "evidence", "requirements", "checks"}
            and value["schema_version"] == 1, "Invalid gold schema")
    evidence_ids = set()
    for item in value["evidence"]:
        require(isinstance(item, dict) and set(item) == {"evidence_id", "path", "sha256"},
                "Invalid Gold evidence")
        require(isinstance(item["evidence_id"], str) and item["evidence_id"] not in evidence_ids,
                "Duplicate Gold evidence")
        _hash(_file(source, item["path"]), item["sha256"], item["path"])
        evidence_ids.add(item["evidence_id"])
    check_ids, kinds = set(), set()
    for check in value["checks"]:
        require(isinstance(check, dict) and set(check) == {"check_id", "kind", "argv", "timeout_seconds"},
                "Invalid Gold check")
        require(isinstance(check["check_id"], str) and check["check_id"] not in check_ids,
                "Duplicate Gold check")
        require(check["kind"] in {"F2P", "P2P"}, "Unknown Gold check kind")
        _command(check["argv"], source)
        require(type(check["timeout_seconds"]) is int and 1 <= check["timeout_seconds"] <= 600,
                "Invalid Gold timeout")
        check_ids.add(check["check_id"])
        kinds.add(check["kind"])
    require(kinds == {"F2P", "P2P"}, "Gold requires F2P and P2P checks")
    criteria = {item["id"] for story in contract["product"]["stories"]
                for item in story["acceptance"]}
    requirement_ids = set()
    for item in value["requirements"]:
        require(isinstance(item, dict) and set(item) == {"requirement_id", "evidence_ids", "check_ids"},
                "Invalid Gold requirement")
        ident = item["requirement_id"]
        require(ident in criteria and ident not in requirement_ids, "Unknown or duplicate requirement")
        require(isinstance(item["evidence_ids"], list) and item["evidence_ids"]
                and set(item["evidence_ids"]) <= evidence_ids, "Requirement lacks source evidence")
        require(isinstance(item["check_ids"], list) and item["check_ids"]
                and set(item["check_ids"]) <= check_ids, "Requirement lacks Gold checks")
        requirement_ids.add(ident)
    require(requirement_ids == criteria and check_ids == {check for item in value["requirements"]
                                                          for check in item["check_ids"]},
            "Gold must map every requirement and check")


def _command(argv: object, source: Path) -> None:
    require(isinstance(argv, list) and len(argv) >= 2 and argv[0] in {"{python}", "{storyloop_harness}"},
            "Gold commands must use a trusted Python launcher")
    require(all(isinstance(arg, str) and arg and "\0" not in arg for arg in argv), "Invalid Gold argv")
    require("-c" not in argv, "Inline Gold code is not frozen")
    if argv[0] == "{python}":
        first = argv[1] if argv[1] != "-B" else argv[2] if len(argv) > 2 else ""
        require(first.startswith("checks/") or first == "-m", "Gold Python target must be frozen")
        if first == "-m":
            index = argv.index("-m")
            require(len(argv) > index + 1 and argv[index + 1] == "unittest",
                    "Only unittest module execution is allowed")
    for arg in argv[1:]:
        if arg.endswith(".py") and "/" in arg:
            _relative(source, arg)


def _check_inventory(source: Path, paths: list[str]) -> None:
    checks = source / "checks"
    require(checks.is_dir() and not checks.is_symlink(), "checks/ is missing or linked")
    actual = [path.relative_to(source).as_posix() for path in checks.rglob("*") if path.is_file()]
    require(sorted(actual) == paths, "checks/ contains undeclared or missing files")
    require(not any(path.is_symlink() for path in checks.rglob("*")), "checks/ contains a link")


def _file(source: Path, value: str) -> bytes:
    path = _relative(source, value)
    require(path.is_file(), f"Missing Case file: {value}")
    return path.read_bytes()


def _relative(source: Path, value: object) -> Path:
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value,
            "Case path must be safe POSIX relative text")
    relative = PurePosixPath(value)
    require(not relative.is_absolute() and ".." not in relative.parts and relative.as_posix() == value,
            "Case path escapes source")
    path = source.joinpath(*relative.parts)
    require(not any(part.is_symlink() for part in (path, *path.parents) if part == source or source in part.parents),
            "Case path contains a symbolic link")
    require(path.resolve().is_relative_to(source), "Case path escapes source")
    return path


def _hash(content: bytes, expected: object, label: str, *, already_digest: bool = False) -> None:
    require(isinstance(expected, str) and SHA.fullmatch(expected), f"Invalid SHA-256 for {label}")
    actual = content.hex() if already_digest else hashlib.sha256(content).hexdigest()
    require(actual == expected, f"SHA-256 mismatch: {label}")


def _json(content: bytes, label: str) -> dict:
    try:
        value = json.loads(content)
    except (UnicodeError, ValueError) as exc:
        raise StoryLoopError(f"Invalid JSON: {label}") from exc
    require(isinstance(value, dict), f"JSON object required: {label}")
    return value
