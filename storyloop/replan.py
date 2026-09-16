"""Bounded, crash-safe replan receipts for repeated Stage failures."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from .json_contract import dumps_strict_json
from .models import StoryLoopError, fields, identifier, require
from .safe_json_file import read_bounded_object
from .stage_agents import (
    adapter_control_root, consume_manual_ack, invoke_selected_adapter,
)
from .stage_state import resume_after_replan
from .storage import write_json

PROTOCOL = "loop.replan.receipt.v2"
MAX_REPLAN_BYTES = 16 * 1024
DIGEST = re.compile(r"[0-9a-f]{64}")
Invoker = Callable[..., dict]


def replan_receipt_path(project: Path, state: dict) -> Path:
    """Return the immutable identity path for one exhausted retry batch."""
    stage = identifier(state.get("stage"), "stage id")
    identity = _identity(state)
    return adapter_control_root(project) / "replan" / f"{stage}.{identity}.json"


def replan_stage(
    project: Path, config: dict, state: dict, upstream_handoffs: list[dict],
    requested_adapter: str | None = None,
    environ: Mapping[str, str] | None = None,
    resolver=None,
    invoker: Invoker = invoke_selected_adapter,
) -> dict:
    """Replan one repeated-failure batch and preserve all consumed budgets."""
    require(state.get("status") == "REPEATED_FAILURE",
            "Only a repeated failure can be replanned")
    payload = _safe_payload(state, config, upstream_handoffs)
    path = replan_receipt_path(project, state)
    if path.exists():
        receipt = read_bounded_object(path, MAX_REPLAN_BYTES, "Replan receipt")
        _validate_receipt(receipt, state)
        _consume_manual_receipt(project, receipt)
        return resume_after_replan(state, config)
    options = {"requested": requested_adapter, "environ": environ}
    if resolver is not None:
        options["resolver"] = resolver
    response = invoker(
        Path(project).resolve(), config, "replan", state["stage"], payload, **options,
    )
    receipt = {
        "protocol": PROTOCOL, "identity": _identity(state),
        "stage": state["stage"], "status": "completed",
        "request_id": response.get("request_id"),
    }
    _validate_receipt(receipt, state)
    write_json(path, receipt, max_bytes=MAX_REPLAN_BYTES)
    _consume_manual_receipt(project, receipt)
    return resume_after_replan(state, config)


def _safe_payload(state: dict, config: dict,
                  upstream_handoffs: list[dict]) -> dict:
    require(isinstance(upstream_handoffs, list), "Upstream handoffs must be a list")
    judges = [item["id"] for stage in config["stages"]
              if stage["id"] == state["stage"] for item in stage["judges"]]
    payload = {
        "protocol": "loop.replan.input.v2", "stage": state["stage"],
        "failure": {
            "code": state["last_failure_code"],
            "fingerprint": state["last_failure_fingerprint"],
            "judges": judges,
        },
        "budget": {"attempts_started": state["attempts_started"],
                   "consumed_ms": state["consumed_ms"]},
        "upstream": [_safe_handoff(item) for item in upstream_handoffs],
    }
    dumps_strict_json(payload, "Replan payload", MAX_REPLAN_BYTES, sort_keys=True)
    return payload


def _safe_handoff(value: dict) -> dict:
    fields(value, {"stage", "result_digest", "changes", "judges"})
    identifier(value["stage"], "upstream stage")
    require(isinstance(value["result_digest"], str)
            and DIGEST.fullmatch(value["result_digest"]), "Invalid upstream digest")
    require(isinstance(value["changes"], list) and len(value["changes"]) <= 1000,
            "Invalid upstream changes")
    require(isinstance(value["judges"], list) and len(value["judges"]) <= 300,
            "Invalid upstream Judges")
    for change in value["changes"]:
        fields(change, {"path", "kind"})
        require(isinstance(change["path"], str) and 0 < len(change["path"]) <= 1000
                and "\\" not in change["path"] and ".." not in change["path"].split("/"),
                "Invalid upstream change path")
        require(change["kind"] in {"added", "modified", "deleted"},
                "Invalid upstream change kind")
    for judge in value["judges"]:
        fields(judge, {"id", "outcome"})
        identifier(judge["id"], "upstream judge")
        require(judge["outcome"] in {"RED", "EXISTING_GREEN", "GREEN"},
                "Invalid upstream Judge outcome")
    return {"stage": value["stage"], "result_digest": value["result_digest"],
            "changes": [dict(item) for item in value["changes"]],
            "judges": [dict(item) for item in value["judges"]]}


def _consume_manual_receipt(project: Path, receipt: dict) -> None:
    consume_manual_ack(project, {
        "protocol": "loop.manual.ack.v2", "stage": receipt["stage"],
        "role": "replan", "request_id": receipt["request_id"],
    })


def _identity(state: dict) -> str:
    names = ("project_id", "config_digest", "payload_digest", "stage",
             "attempts_started", "consumed_ms", "last_failure_code",
             "last_failure_fingerprint")
    value = {name: state.get(name) for name in names}
    encoded = dumps_strict_json(value, "Replan identity", MAX_REPLAN_BYTES,
                                sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_receipt(receipt: dict, state: dict) -> None:
    fields(receipt, {"protocol", "identity", "stage", "status", "request_id"})
    require(receipt["protocol"] == PROTOCOL and receipt["status"] == "completed",
            "Invalid replan receipt")
    require(receipt["identity"] == _identity(state)
            and receipt["stage"] == state["stage"], "Stale replan receipt")
    request_id = receipt["request_id"]
    require(isinstance(request_id, str) and DIGEST.fullmatch(request_id),
            "Invalid replan request identity")
