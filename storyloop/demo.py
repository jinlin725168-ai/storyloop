"""A transparent scripted fixture demonstrating real external RED/GREEN checks."""
from __future__ import annotations

import sys
from pathlib import Path
from .storage import initialize, write_json, load_json

CHECK_SOURCE = '''import importlib.util, json, sys
from pathlib import Path
workspace = Path(sys.argv[1])
mode = sys.argv[2]
results = []
try:
    spec = importlib.util.spec_from_file_location("booking", workspace / "booking.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
except Exception:
    print(json.dumps({"protocol":"storyloop.check.v1","results":[{"id":mode,"status":"error","message":"Application cannot load"}]}))
    sys.exit(2)

def verify():
    if mode == "health":
        assert callable(module.can_book), "Expected callable booking interface"
    elif mode == "overlap":
        assert module.can_book([(600,660)], 630,690) is False, "Overlapping booking must be rejected"
        assert module.can_book([(600,660)], 610,620) is False, "Contained booking must be rejected"
    elif mode == "adjacent":
        assert module.can_book([(600,660)], 660,720) is True, "Adjacent booking must be allowed"
    elif mode == "invalid":
        assert module.can_book([], 660,600) is False, "Reversed interval must be rejected"
        assert module.can_book([], 600,600) is False, "Zero-length interval must be rejected"
try:
    verify()
    result = {"id":mode,"status":"pass","message":"Observed expected booking behavior"}
except AssertionError as exc:
    result = {"id":mode,"status":"fail","message":str(exc)}
except Exception:
    result = {"id":mode,"status":"error","message":"Unexpected application error"}
print(json.dumps({"protocol":"storyloop.check.v1","results":[result]}))
sys.exit(0 if result["status"] == "pass" else 1 if result["status"] == "fail" else 2)
'''


def create_demo(project: Path) -> None:
    """Create only the packaged demo; no external agent or network is invoked."""
    initialize(project, "员工预约会议室，避免预约时段冲突。")
    criteria = []
    examples = [("AC-1", "已有 10:00—11:00 的预约", "预约 10:30—11:30", "拒绝重叠预约"),
                ("AC-2", "已有 10:00—11:00 的预约", "预约 11:00—12:00", "允许相邻预约"),
                ("AC-3", "任意会议室", "提交结束时间不晚于开始时间的预约", "拒绝无效时段")]
    for key, given, when, then in examples:
        criteria.append({"id": key, "given": given, "when": when, "then": then, "kind": "automated"})
    criteria.append({"id": "AC-4", "given": "阅读规则说明", "when": "查看例子", "then": "产品负责人确认时段边界符合业务", "kind": "manual"})
    story = {"id": "ST-1", "title": "预约冲突判断", "as_a": "员工", "want": "预约空闲时段",
             "so_that": "会议不会撞时间", "rules": ["采用左闭右开时段，相邻不冲突", "结束时间必须晚于开始时间"],
             "out_of_scope": ["数据库持久化", "并发提交事务", "登录界面", "周期预约"], "depends_on": [], "acceptance": criteria}
    write_json(project / "product.json", {"version": 1, "goal": "会议室预约时段规则", "non_goals": story["out_of_scope"], "questions": [], "stories": [story]})
    (project / "qa" / "booking_check.py").write_text(CHECK_SOURCE, encoding="utf-8")
    (project / "workspace" / "booking.py").write_text('def can_book(existing, start, end):\n    return True\n', encoding="utf-8")
    _configure(project)


def _configure(project: Path) -> None:
    checks = []
    for index, mode in enumerate(["health", "overlap", "adjacent", "invalid"]):
        checks.append({"id": "CHECK-" + mode, "story_id": "ST-1", "kind": "health" if index == 0 else "acceptance",
                       "criteria": [] if index == 0 else [f"AC-{index}"],
                       "command": ["{python}", "{qa}/booking_check.py", "{workspace}", mode],
                       "expected_tests": [mode], "timeout_seconds": 10})
    write_json(project / "checks.json", {"version": 1, "checks": checks})
    config = load_json(project / "config.json")
    config.update(adapter="command", agent_command=[sys.executable, str(Path(__file__).with_name("demo_agent.py"))])
    write_json(project / "config.json", config)
    write_json(project / ".storyloop" / "demo.json", {"scripted": True,
               "description": "脚本演示：真实外部测试与代码修改；不代表真实 AI 能力验证。"})
