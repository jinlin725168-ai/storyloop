# StoryLoop Implementation Plan

> Use the executing-plans and test-driven-development workflow; continue in this task with independent review. No When edits, no worktrees, no push or deployment.

**Goal:** Deliver a standalone, standard-library Python engineering toolkit where humans and AI co-create stories and acceptance boundaries, then a deterministic runner collects real RED/GREEN evidence, invokes implementation and independent QA, and produces a product-facing report.

**Architecture:** Instance root contains product.json, checks.json, config.json, brief.md, workspace/ (implementation), qa/ (trusted tests), and .storyloop/ (approval/state/evidence). Product discussion and QA preparation use structured agent outputs. Approval freezes semantic inputs and executable QA. Run requires health checks and criterion-level coverage, records verified expected failures, repairs within a budget, reruns regression, obtains independent QA, and leaves manual criteria awaiting human acceptance. It never treats missing/skipped/malformed results as success. The controller owns completion. Hash guards detect drift, but this is not an OS security boundary; default Codex adapter uses read-only mode for product/QA and workspace-write only for implementation.

**Tech Stack:** Python >=3.11, standard library, unittest, JSON, HTML report; optional preinstalled Codex CLI or custom argv agent. No dependencies need installing for demo/tests.

## Shared contracts

- product.json: {version:1, goal:str, non_goals:[str], questions:[{id:str,text:str,options:[str]}], stories:[{id:str,title:str,as_a:str,want:str,so_that:str,rules:[str],out_of_scope:[str],depends_on:[story id],acceptance:[{id:str,given:str,when:str,then:str,kind:'automated'|'manual'}]}]}.
- checks.json: {version:1,checks:[{id:str,story_id:str,kind:'health'|'acceptance'|'regression',criteria:[acceptance id],command:[str],expected_tests:[str],timeout_seconds:int}]}.
- config.json: {version:1,adapter:'codex'|'command'|'manual',agent_command:[str],max_attempts:3,max_seconds:1800,agent_timeout_seconds:300,check_timeout_seconds:60,repeated_failure_limit:2}.
- Check stdout must be one JSON object: {protocol:'storyloop.check.v1',results:[{id:str,status:'pass'|'fail'|'error'|'skip',message:str}]}. IDs must match expected_tests exactly. Exit 0 iff all pass; exit 1 iff behavioral failure without error/skip; all other cases block. A skipped test is never RED/GREEN. Check commands expand {python}, {workspace}, {qa}. No shell=True.
- agents.invoke(project:Path,role:str,payload:dict)->dict. Roles product,prepare,implement,review. Fresh process each invocation. Product response is product object. Prepare response {checks:checks object,qa_files:[{path:str,content:str}],workspace_files:[{path:str,content:str}]}. Implement response {summary:str,questions:[str]}; review response {verdict:'pass'|'fail'|'needs_input',summary:str,findings:[{criterion_id:str,message:str}],questions:[str]}. Agent response persisted only after validation and redact sensitive output. Do not log full CLI events. Generic command receives {role,project/workspace paths,payload} on stdin and emits final JSON stdout. Codex invocation uses installed CLI help, sandbox and output-last-message.
- storage.initialize(project:Path, goal:str='')->None creates fresh instance only; load_json/write_json, digest_tree(paths), approval_digest(project), workspace_digest(project), locked(project) helpers.
- models.validate_product(value), validate_checks(value,product), validate_config(value) return validated dict or raise StoryLoopError.
- discovery.discuss(project,message)->dict; prepare(project)->dict. Product changes invalidate approvals. AI questions must be explicitly resolved by discussion before approve.
- engine.approve(project)->dict; run(project,story_id=None)->dict; accept(project,story_id,note)->dict; status(project)->dict. Uses agents.invoke, verification.run_check(project,check), fresh durable evidence, interrupted RUNNING can resume. Run status PASSED (auto) or AWAITING_ACCEPTANCE (manual), BLOCKED, NEEDS_INPUT, FAILED. Product acceptance separate from automated verification. accept requires fresh matching successful evidence and nonempty note. User may accept automated stories too.
- report.render(project)->Path writes .storyloop/report.html safely escaped, read-only static report. No local HTTP server needed.
- CLI python -m storyloop init PATH --goal TEXT; discuss PATH --message TEXT; prepare PATH; approve PATH; run PATH [--story ID]; status PATH [--json]; accept PATH --story ID --note TEXT; report PATH; demo PATH. Nonzero when failed/blocked/needs_input, no misleading success.

## Tasks and evidence

1. Tests first for domain validation, check protocol, RED/environment discrimination, missing coverage, contract/workspace drift, budgets, regression, manual acceptance and lock behavior. Run tests and preserve initial failing result before implementing.
2. Implement storage, validation, process execution and deterministic engine. Small focused files (under 500 lines), functions generally under 50 lines.
3. Implement product/QA/agent integration and CLI using interfaces above. Product and QA are structured output, do not execute model-generated scripts during preparation. Path traversal/symlinks prohibited; no overwrite of existing workspace files during preparation.
4. Provide a dependency-free scripted demo adapter that genuinely edits a tiny target implementation after a real failing check, then demonstrates passing external tests and independent QA. Label it a scripted demonstration, not real AI verification.
5. End-to-end CLI tests and fault injection: skipped/malformed/fake-green checks blocked, immutable contract enforced, prior story regression repaired, repeated failure budget respected, stale manual acceptance rejected, interrupted resume correct. Independent spec and code quality review.
6. Chinese quickstart, architecture with Mermaid, custom adapter/check integration, trust boundary and limitations, research source links. No hard-coded credentials, no read of auth files, no automatic dependency install.
