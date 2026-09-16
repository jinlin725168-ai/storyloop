"""Read-only, escaped, portable product and QA report."""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
from .engine import status
from .storage import load_json

LABELS = {"DRAFT": "待梳理", "PENDING": "待执行", "RUNNING": "执行中", "PASSED": "自动验收通过",
          "ACCEPTED": "人工已验收", "AWAITING_ACCEPTANCE": "等待人工验收", "STALE": "证据已过期",
          "BLOCKED": "执行受阻", "NEEDS_INPUT": "需要业务澄清", "FAILED": "未通过", "INTERRUPTED": "已中断"}
CSS = '''body{font:16px/1.65 system-ui,sans-serif;background:#f3f5f7;color:#17212b;margin:0}
main{max-width:1080px;margin:auto;padding:38px 24px}h1{font-size:36px;letter-spacing:-1px;margin:8px 0}
h2{font-size:24px}h3{font-size:18px}section{background:white;border:1px solid #dce2e8;border-radius:12px;padding:24px;margin:20px 0}
.badge{display:inline-block;background:#e5edf6;padding:3px 12px;border-radius:16px;font-size:14px}
.PASSED,.ACCEPTED,.GREEN{background:#dff3e8}.FAILED,.BLOCKED,.RED{background:#ffe3df}.STALE,.AWAITING_ACCEPTANCE{background:#fff0cc}
small,.muted{color:#526575}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:10px 8px;border-bottom:1px solid #e4e9ee;vertical-align:top}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:12px;border-radius:6px;font-size:13px}a{color:#165ca0}
summary{cursor:pointer;font-weight:600}ul{padding-left:22px}.notice{border-left:4px solid #dba645;background:#fff8e6;padding:12px 16px}footer{margin:25px 0;color:#526575}'''


def e(value: object) -> str:
    return escape(str(value), quote=True)


def badge(value: str) -> str:
    return f'<span class="badge {e(value)}">{e(LABELS.get(value, value))}</span>'


def render(project: Path) -> Path:
    """Render a static report; it has no scripts, forms, server or remote assets."""
    project = Path(project).resolve()
    product = load_json(project / "product.json")
    checks = load_json(project / "checks.json")["checks"]
    state = status(project)
    pieces = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
              '<meta name="viewport" content="width=device-width,initial-scale=1">',
              '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">',
              f'<title>StoryLoop · 验收报告</title><style>{CSS}</style><main>',
              '<div class="muted">STORYLOOP / PRODUCT & QA</div>', f'<h1>{e(product.get("goal") or "开始梳理你的产品")}</h1>',
              badge(state["status"]), '<p class="muted">以用户故事为单位，展示验收边界、执行结果和可追踪证据。</p>']
    if (project / ".storyloop" / "demo.json").exists():
        pieces.append('<p class="notice">脚本演示：确实运行外部测试并修改代码，不代表已验证真实 AI 自治能力。</p>')
    pieces.append(_questions(product))
    for story in product["stories"]:
        pieces.append(_story(story, state["stories"].get(story["id"], {}), checks))
    pieces.append(_commands(checks))
    pieces.append('<footer>自动验收通过仅适用于报告生成时的代码与验收版本。代码变化后请重新运行。<br>')
    pieces.append('验收程序属于可信执行代码，批准前应检查其内容；指纹校验不是操作系统隔离。</footer></main></html>')
    path = project / ".storyloop" / "report.html"
    path.write_text("\n".join(pieces), encoding="utf-8")
    return path


def _questions(product: dict) -> str:
    questions = product.get("questions", [])
    if not questions:
        return ''
    items = ''.join(f'<li><b>{e(q["id"])}</b> {e(q["text"])}<br><small>{e(" / ".join(q["options"]))}</small></li>' for q in questions)
    return f'<section><h2>需要你裁决的业务问题</h2><ul>{items}</ul></section>'


def _story(story: dict, record: dict, checks: list) -> str:
    pieces = [f'<section><small>{e(story["id"])}</small><h2>{e(story["title"])}</h2>', badge(record.get("status", "DRAFT")),
              f'<p>作为{e(story["as_a"])}，我希望{e(story["want"])}，以便{e(story["so_that"])}。</p>',
              '<h3>业务规则</h3><ul>' + ''.join(f'<li>{e(rule)}</li>' for rule in story["rules"]) + '</ul>',
              f'<p><b>本期不包含：</b>{e("、".join(story["out_of_scope"]) or "未指定")}</p>',
              '<h3>验收场景</h3><table><tr><th>条目</th><th>场景与预期</th><th>验证结果</th></tr>']
    for criterion in story["acceptance"]:
        evidence = [r for r in record.get("last_checks", []) if criterion["id"] in r["criteria"]]
        outcome = '人工验收' if criterion["kind"] == "manual" else '/'.join(r["outcome"] for r in evidence) or '未执行'
        pieces.append(f'<tr><td>{e(criterion["id"])}</td><td>假如 {e(criterion["given"])}<br>当 {e(criterion["when"])}<br>那么 {e(criterion["then"])}</td><td>{e(outcome)}</td></tr>')
    pieces.append('</table>')
    pieces.append(f'<p>实现尝试：{e(record.get("attempts",0))} · 执行耗时：{e(round(record.get("elapsed_seconds",0),2))} 秒 · 初始状态：{e(record.get("baseline_kind","未执行"))}</p>')
    if record.get("reason"):
        pieces.append(f'<p class="notice">{e(record["reason"])}</p>')
    if record.get("accepted_note"):
        pieces.append(f'<p><b>人工验收记录：</b>{e(record["accepted_note"])}</p>')
    if record.get("review"):
        pieces.append('<details><summary>独立 QA 结果</summary><pre>' + e(json.dumps(record["review"], ensure_ascii=False, indent=2)) + '</pre></details>')
    pieces.append('<details><summary>执行证据</summary><ul>')
    for path in record.get("evidence", []):
        safe = Path(path)
        if safe.parts and safe.parts[0] == ".storyloop" and ".." not in safe.parts:
            pieces.append(f'<li><a href="{e(safe.relative_to(".storyloop").as_posix())}">{e(safe.name)}</a></li>')
    return ''.join(pieces) + '</ul></details></section>'


def _commands(checks: list) -> str:
    rows = []
    for check in checks:
        rows.append(f'<tr><td>{e(check["id"])}</td><td>{e(", ".join(check["criteria"]) or "环境/回归")}</td><td><pre>{e(json.dumps(check["command"], ensure_ascii=False))}</pre></td></tr>')
    return '<section><details><summary>批准前检查：验收命令与映射</summary><p>测试源文件位于项目 qa/；这里的命令会被实际执行。</p><table><tr><th>检查</th><th>覆盖</th><th>命令参数</th></tr>' + ''.join(rows) + '</table></details></section>'
