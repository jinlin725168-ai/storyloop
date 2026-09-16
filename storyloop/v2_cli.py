"""Isolated command surface for the Stage-oriented v2 engine."""

from __future__ import annotations

import argparse
from pathlib import Path

from .coordinator_state import load_or_create, locked_coordinator
from .loop_config_io import load_loop_config, load_stage_payloads
from .stage_coordinator import run_stage_graph

STAGE_COMMANDS = {"stage-run", "stage-status", "stage-resume"}


def add_parsers(subparsers) -> None:
    """Register v2 commands without altering the legacy v1 arguments."""
    descriptions = {
        "stage-run": "执行 v2 Stage DAG",
        "stage-status": "只读查看 v2 Stage DAG 状态",
        "stage-resume": "显式恢复等待中的 v2 Stage DAG",
    }
    for command, description in descriptions.items():
        child = subparsers.add_parser(command, help=description)
        child.add_argument("project", type=Path)
        child.add_argument("--config", default="loop.yaml")
        child.add_argument("--payload")
        child.add_argument("--stage")
        child.add_argument("--json", action="store_true")
        if command != "stage-status":
            child.add_argument("--agent")
        if command == "stage-resume":
            child.add_argument("--acknowledge-unsafe", action="store_true")


def dispatch(args: argparse.Namespace) -> dict:
    """Load bounded inputs and route exactly one v2 command."""
    project = args.project.resolve()
    config = load_loop_config(project, args.config)
    payloads = load_stage_payloads(project, args.payload, config)
    if args.command == "stage-status":
        return read_stage_status(project, config, payloads, args.stage)
    options = {"target_stage": args.stage, "requested_adapter": args.agent}
    if args.command == "stage-resume":
        options.update(resume_waiting=True,
                       acknowledge_unsafe=args.acknowledge_unsafe)
    return run_stage_graph(project, config, payloads, **options)


def read_stage_status(project: Path, config: dict, payloads: dict,
                      target_stage: str | None) -> dict:
    """Validate and return coordinator state without writing or executing."""
    with locked_coordinator(project):
        return load_or_create(project, config, payloads, target_stage)
