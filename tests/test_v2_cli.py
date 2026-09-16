"""Backward-compatible v2 CLI and strict configuration loading tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.cli import dispatch, main, parser
from storyloop.loop_config_io import load_loop_config, load_stage_payloads
from storyloop.models import StoryLoopError
from tests.test_stage_config import loop_config


class V2ConfigIoTests(unittest.TestCase):
    def test_json_content_is_accepted_from_yaml_without_yaml_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "loop.yaml").write_text(json.dumps(loop_config()), encoding="utf-8")
            (project / "payload.json").write_text('{"foundation":{"goal":"safe"}}',
                                                   encoding="utf-8")
            self.assertEqual(load_loop_config(project)["version"], 2)
            self.assertEqual(load_stage_payloads(project, "payload.json")["foundation"]
                             ["goal"], "safe")

    def test_real_yaml_duplicate_json_and_unsafe_paths_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            for content in ("version: 2\n", '{"version":2,"version":2}'):
                (project / "loop.yaml").write_text(content, encoding="utf-8")
                with self.assertRaises(StoryLoopError):
                    load_loop_config(project)
            with self.assertRaises(StoryLoopError):
                load_loop_config(project, "../outside.yaml")


class V2CliTests(unittest.TestCase):
    def test_parser_exposes_stage_commands_without_changing_v1_run(self):
        v1 = parser().parse_args(["run", ".", "--story", "ST-1"])
        v2 = parser().parse_args([
            "stage-run", ".", "--config", "custom.json", "--payload", "input.json",
            "--stage", "foundation", "--agent", "manual", "--json",
        ])
        self.assertEqual((v1.command, v1.story), ("run", "ST-1"))
        self.assertEqual((v2.command, v2.stage, v2.agent),
                         ("stage-run", "foundation", "manual"))

    def test_stage_run_and_resume_route_flags_to_coordinator(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "loop.yaml").write_text(json.dumps(config), encoding="utf-8")
            (project / "payload.json").write_text("{}", encoding="utf-8")
            with patch("storyloop.v2_cli.run_stage_graph",
                       return_value={"status": "PROJECT_GREEN"}) as run:
                result = dispatch(parser().parse_args([
                    "stage-resume", str(project), "--payload", "payload.json",
                    "--stage", "foundation", "--agent", "manual",
                    "--acknowledge-unsafe",
                ]))
        self.assertEqual(result["status"], "PROJECT_GREEN")
        options = run.call_args.kwargs
        self.assertEqual(options["target_stage"], "foundation")
        self.assertEqual(options["requested_adapter"], "manual")
        self.assertTrue(options["resume_waiting"])
        self.assertTrue(options["acknowledge_unsafe"])

    def test_stage_status_is_read_only_and_exit_codes_are_honest(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "loop.yaml").write_text(json.dumps(loop_config()), encoding="utf-8")
            with patch("storyloop.v2_cli.read_stage_status",
                       return_value={"status": "NEEDS_INPUT"}) as status:
                args = parser().parse_args(["stage-status", str(project), "--json"])
                self.assertEqual(dispatch(args)["status"], "NEEDS_INPUT")
                self.assertEqual(main(["stage-status", str(project), "--json"]), 2)
            self.assertEqual(status.call_count, 2)


if __name__ == "__main__":
    unittest.main()
